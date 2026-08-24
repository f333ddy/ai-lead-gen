# Standard library
import collections
import csv
import html
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

# Third-party
import langid
import requests
import tldextract
from dotenv import load_dotenv

# Local imports
import date_utils as du

load_dotenv()

# SAM.gov publishes every contract opportunity as one public S3 object -- no
# auth, no API key, no JS rendering. This replaces what would otherwise be a
# ~1,179-request/day crawl of the Angular search UI (which is also robots.txt
# Disallow: /search/) with a single GET.
FULL_CSV_URL = os.getenv("SAMGOV_CSV_URL") or (
    "https://falextracts.s3.amazonaws.com/"
    "Contract%20Opportunities/datagov/ContractOpportunitiesFullCSV.csv"
)
BASE_URL = "https://sam.gov"
SCRAPER_SOURCE = "samgov"

# The file is ~241 MB but sorted newest-first by PostedDate, so a run reads
# only as far back as the requested cutoff and stops. Nothing here caps the
# date range -- the stop condition is the data, not a byte budget.
#
# This budget exists solely as a backstop: if SAM ever stops sorting
# newest-first, the date stop would never fire and one run would pull the whole
# 241 MB. It scales with the requested range so it can never bind on a
# legitimate request. Measured consumption is 5-8 MB per day of data
# (days_back=1 -> 8 MB, 3 -> 20 MB, 5 -> 25 MB), so 24 MB/day is 3-4x headroom.
STREAM_BYTES_PER_DAY = 24 * 1024 * 1024
STREAM_BYTES_BASE = 32 * 1024 * 1024


def _stream_budget(days_back: int) -> int:
    """Backstop byte budget for the requested range, never a range limit."""
    return STREAM_BYTES_BASE + max(0, days_back) * STREAM_BYTES_PER_DAY


# The extract spans 2008 to today, so reading it head-first is only correct
# while it stays sorted newest-first. If that ever flipped to oldest-first, or
# if S3 stopped regenerating the file, every row would look stale, the tolerance
# would trip within the first couple of MB, and the run would report "0
# documents" -- indistinguishable from a legitimately quiet day. So the first
# data row is checked instead: the head of a healthy file is always within a day
# or two of now, and anything older means the feed, not the day, is the problem.
MAX_FEED_AGE_DAYS = 7


class SamGovStaleFeedError(RuntimeError):
    """The newest row in the extract is far older than it should be.

    Catches a flipped sort order, a frozen S3 object, or a wrong URL -- each of
    which would otherwise masquerade as a day with no opportunities.
    """


class SamGovTruncatedError(RuntimeError):
    """The stream budget was exhausted before the date cutoff was reached.

    Raised rather than returning partial results: a short date range that
    looks like a successful run would quietly ship an incomplete digest, which
    is worse than a visible failure.
    """

# Ordering is reliable at day granularity but not strictly monotonic row to row,
# so one stale row is not proof we have passed the cutoff. Keep reading until
# this many consecutive stale rows confirm it (~a few hundred KB of insurance).
STALE_ROW_TOLERANCE = 20

# Notice types that represent work we can still influence or win. Early-stage
# notices are kept deliberately: responding to a Sources Sought shapes the
# eventual solicitation, which is worth more than arriving once the bid is
# already written.
BIDDABLE_TYPES = frozenset({
    "Sources Sought",
    "Presolicitation",
    "Special Notice",
    "Solicitation",
    "Combined Synopsis/Solicitation",
    # DoD-only and rare -- absent from multi-day samples, so both the short
    # form and SAM's parenthesized variant are listed rather than guessed at.
    "Intent to Bundle Requirements",
    "Intent to Bundle Requirements (DoD-Funded)",
})

# Excluded because the decision is already made or the process is unrelated.
# A Justification (J&A) is posted within 30 days *after* the sole-source award
# under FAR, so it is never actionable -- unlike the "intent to sole source"
# language that shows up in Special Notice, which is.
EXCLUDED_TYPES = frozenset({
    "Award Notice",
    "Justification",
    "Sale of Surplus Property",
})

# Only these two are true bid documents, so only these close. ResponseDeadLine
# is 100% populated for both (verified), so the check never silently passes.
# Early-stage notices carry deadlines for responding to the research request,
# not for bidding, so a lapsed one there is not a reason to drop the lead.
DEADLINE_ENFORCED_TYPES = frozenset({"Solicitation", "Combined Synopsis/Solicitation"})

# A Special Notice is a catch-all: industry days, RFIs, draft solicitations --
# and "notice of intent to sole source", which is a narrow, time-boxed chance
# to submit a capability statement before a non-competitive award locks up.
_SOLE_SOURCE = re.compile(
    r"sole\s*[-\s]?source|intent\s+to\s+sole|only\s+one\s+responsible\s+source"
    r"|non-?competitive\s+award",
    re.IGNORECASE,
)

# How a notice should be worked, which is not the same as whether it is
# relevant. Passed to the gate so it can judge posture, and kept on the
# document so the digest can sort urgent sole-source windows to the top.
STAGE_MARKET_RESEARCH = "market_research"
STAGE_EARLY_POSITIONING = "early_positioning"
STAGE_OPEN_BID = "open_bid"
STAGE_SOLE_SOURCE_URGENT = "sole_source_urgent"
STAGE_INFORMATIONAL = "informational"

_STAGE_GUIDANCE = {
    STAGE_MARKET_RESEARCH: (
        "Agency is still deciding whether and how to compete this requirement. "
        "Responding shapes the eventual solicitation. Not a bid."
    ),
    STAGE_EARLY_POSITIONING: (
        "A solicitation is expected but not yet issued. Early positioning "
        "opportunity, often via an Interested Vendors List. Not a bid."
    ),
    STAGE_OPEN_BID: "Active bid document with an open response deadline.",
    STAGE_SOLE_SOURCE_URGENT: (
        "URGENT: agency signals intent to award without competition. Narrow "
        "window to submit a capability statement and force competition."
    ),
    STAGE_INFORMATIONAL: (
        "Informational notice (industry day, RFI, or draft for comment). "
        "Useful for pipeline forecasting and relationship building."
    ),
}

# Coarse stage-1 relevance gate. This is NOT a fit test -- it removes the DoD
# parts/electronics bulk that dominates the file (aircraft components,
# connectors, fasteners) and leaves roughly 18% of rows for the AI gate to
# judge. Heavy civil work (roads, dams, sewers) still survives this and is
# expected to be rejected downstream.
RELEVANT_NAICS = frozenset({
    "236220",  # Commercial and Institutional Building Construction
    "237990",  # Other Heavy and Civil Engineering Construction
    "238190",  # Other Foundation, Structure, and Building Exterior Contractors
    "238210",  # Electrical Contractors
    "238220",  # Plumbing, Heating, and Air-Conditioning Contractors
    "238290",  # Other Building Equipment Contractors
    "238390",  # Other Building Finishing Contractors
    "238990",  # All Other Specialty Trade Contractors
    "332323",  # Ornamental and Architectural Metal Work Manufacturing
    "337127",  # Institutional Furniture Manufacturing
    "337215",  # Showcase, Partition, Shelving, and Locker Manufacturing
    "339950",  # Sign Manufacturing
    "423210",  # Furniture Merchant Wholesalers
    "488119",  # Other Airport Operations
    "561621",  # Security Systems Services (except Locksmiths)
})

# PSC is hierarchical, so prefixes do the work. Y1/Z1/Z2 are construction and
# real-property alteration; 56 is fencing/barriers; 71 is furniture and
# shelving; 99 is signs and displays; 63 is alarm and security systems.
RELEVANT_PSC_PREFIXES = ("Y1", "Z1", "Z2", "56", "71", "99", "63")

# Electronic and virtual queuing (Qtrac, kiosks, check-in displays, appointment
# scheduling) is a Lavi product line, and it is invisible to the codes above:
# these notices land under IT and computer NAICS (541511, 541519, 334118,
# 513210, 518210) and IT PSCs (7A20, 7E20, DA01, 70). An audit of 29 real
# queuing solicitations found the code allowlist alone dropped 24 of them,
# including a sole-source notice for Lavi's own QTrac product.
#
# Whitelisting all of 541519 would drag in the entire federal IT pipeline, so
# relevance is rescued by product language instead -- narrow terms that do not
# appear in unrelated IT work. Word boundaries matter: "kiosk" alone is fine,
# but bare "queue" would match storage and message queues in software notices,
# so queue terms are anchored to a physical-service context.
_QUEUING_SIGNAL = re.compile(
    r"\bqueu(?:e|ing|eing)\s+(?:management|system|solution|display|kiosk|line)"
    r"|\b(?:virtual|electronic|patient|customer|visitor)\s+queu"
    r"|\bqtrac\b|\bq-?flow\b|\bqmatic\b"
    r"|\btake[-\s]?a[-\s]?number\b"
    r"|\b(?:check[-\s]?in|self[-\s]?service|appointment|information|wayfinding)\s+kiosk"
    r"|\bkiosk(?:s)?\b.*\b(?:queu|check[-\s]?in|appointment|lobby|waiting)"
    r"|\b(?:now serving|wait[-\s]?time)\s+(?:display|sign|board)"
    r"|\bpatient\s+flow\b|\bcustomer\s+flow\b|\bvisitor\s+management\s+system\b",
    re.IGNORECASE,
)

# The physical product lines, which scatter across codes just as badly as
# queuing does. Turnstiles were the worst case found: 14 real notices spread
# over 11 NAICS and 11 PSC values, several of them blank.
_PRODUCT_SIGNAL = re.compile(
    # NOTE: turnstiles, optical turnstiles and speed gates are deliberately
    # ABSENT. Lavi does not sell them (confirmed on lavi.com -- passive
    # barriers only). An earlier version hunted for them and recovered ~53
    # turnstile notices that were never leads. Turnstile work that is also a
    # building renovation still arrives via the NAICS/PSC allowlist, where the
    # AI gate can judge it on the renovation scope rather than the turnstile.
    r"\bwayfinding\b|\bway[-\s]finding\b"
    r"|\b(?:directional|overhead|interior|exterior|monument)\s+sign"
    r"|\bsignage\b"
    r"|\bslatwall\b|\bgondola\b|\bmerchandis(?:ing|er)\s+(?:fixture|display|bowl)"
    r"|\bstanchion|\bhandrail|\bhand\s+rail|\bguard\s?rail|\brailing"
    r"|\bcrowd\s+control|\bretractable\s+belt|\bbelt\s+barrier"
    r"|\bqueue\s+(?:line|barrier|rail)|\bpedestrian\s+(?:barrier|control|guidance)"
    r"|\begress\s+gate|\bbarrier\s+gate",
    re.IGNORECASE,
)

# Domain guards for the overloaded words above. Derived from the audit, not
# guessed: PSC 20 alone accounted for 32 ship-deck "stanchion" notices.
_WRONG_DOMAIN_TEXT = re.compile(
    r"\b(?:shipboard|ship'?s|deck|marine|boat|hull|vessel|submarine|galley|"
    r"topside|bulkhead)\b"
    r"|\b(?:tank|armored|humvee|truck|trailer|aircraft|helicopter|airframe|"
    r"fuselage|locomotive|railcar)\b"
    r"|\b(?:hot\s?dog|cafeteria|steam\s?table|salad\s+bar|food\s+service|"
    r"serving\s+line)\b",
    re.IGNORECASE,
)
# Ship/marine (20), aerospace (15,16), vehicular (23,24,25), food prep (73).
_WRONG_DOMAIN_PSC = ("20", "15", "16", "23", "24", "25", "73")
# Transportation equipment manufacturing -- ships, boats, aircraft, vehicles.
_WRONG_DOMAIN_NAICS = ("336",)

# Column order is positional in the extract; index by name so a future column
# insertion cannot silently shift every field.
_EXPECTED_COLUMNS = 47

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Leading PSC-category artifacts SAM prepends to some titles: "Z--ROMO 326235
# Rehabilitate...", "71--Tables with power...". The letter duplicates the PSC
# column, so it is noise in both the prompt and the digest email.
_TITLE_PREFIX = re.compile(r"^[A-Z0-9]{1,3}--\s*")
_TAG = re.compile(r"<[^>]+>")


def _load_code_labels() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load the NAICS and PSC decode maps built by scripts/build_code_lookups.py.

    Bare codes ("236220", "Y1JZ") are weak tokens for the eligibility gate;
    the decoded titles are the strongest structured signal we have about a
    solicitation's scope of work, so a missing map is a hard failure rather
    than a silent degradation.
    """
    naics_path = _DATA_DIR / "naics_codes.json"
    psc_path = _DATA_DIR / "psc_codes.json"
    missing = [p.name for p in (naics_path, psc_path) if not p.exists()]
    if missing:
        raise RuntimeError(
            f"missing code lookup(s): {', '.join(missing)} -- "
            "run: python scripts/build_code_lookups.py"
        )
    with naics_path.open(encoding="utf-8") as handle:
        naics = json.load(handle)
    with psc_path.open(encoding="utf-8") as handle:
        psc = json.load(handle)
    return naics, psc


NAICS_LABELS, PSC_LABELS = _load_code_labels()


class _CountingReader(io.RawIOBase):
    """Wraps the raw response so we can enforce MAX_STREAM_BYTES while streaming.

    Subclasses RawIOBase rather than duck-typing read(): io.BufferedReader
    requires the full readable/seekable/writable surface.
    """

    def __init__(self, stream, budget: int) -> None:
        super().__init__()
        self._stream = stream
        self._budget = budget
        self.bytes_read = 0

    def readinto(self, buffer) -> int:
        if self.bytes_read >= self._budget:
            return 0
        chunk = self._stream.read(len(buffer))
        if not chunk:
            return 0
        self.bytes_read += len(chunk)
        buffer[: len(chunk)] = chunk
        return len(chunk)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def writable(self) -> bool:
        return False


def _normalize_text(value: str) -> str:
    """Unescape entities and tidy whitespace without shortening the text.

    Unlike every other scraper here, the CSV Description arrives as plain text
    -- only ~2 rows in 2,700 carry HTML tags -- so BeautifulSoup would earn
    nothing. Entities are common though (~9% of rows), and left alone they
    reach the model as literal "&nbsp;". Paragraph breaks are preserved;
    nothing is truncated.
    """
    if not value:
        return ""
    text = html.unescape(value)
    if "<" in text and ">" in text:
        text = _TAG.sub(" ", text)
        text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # Collapse runs of blank lines to a single paragraph break.
    out: List[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _clean_title(value: str) -> str:
    return _TITLE_PREFIX.sub("", " ".join((value or "").split())).strip()


def _decode_naics(code: str) -> Optional[str]:
    code = (code or "").strip()
    return NAICS_LABELS.get(code) if code else None


def _decode_psc(code: str) -> Optional[str]:
    code = (code or "").strip()
    return PSC_LABELS.get(code) if code else None


def _is_relevant(row: Dict[str, str]) -> bool:
    """Stage-1 gate: does this notice deserve an AI call?

    Three paths. The code allowlist catches facility work. The queuing and
    product keyword rescues catch the rest, because a per-product audit found
    that turnstiles, wayfinding and store fixtures scatter across dozens of
    unrelated service and manufacturing codes no allowlist can enumerate --
    14 real turnstile notices alone carried 11 different NAICS and 11
    different PSC values.

    The keyword paths are domain-guarded. Federal procurement overloads our
    vocabulary badly: "stanchion" usually means a ship's deck railing post,
    "handrail" a grab rail on an armored vehicle, "sneeze guard" a cafeteria
    steam table. Those are correctly excluded by domain, not by word.
    """
    naics = (row.get("NaicsCode") or "").strip()
    psc = (row.get("ClassificationCode") or "").strip()
    if naics in RELEVANT_NAICS:
        return True
    if psc and psc.startswith(RELEVANT_PSC_PREFIXES):
        return True

    haystack = f"{row.get('Title', '')} {row.get('Description', '')}"

    # Queuing terms are specific enough to skip the domain guard.
    if _QUEUING_SIGNAL.search(haystack):
        return True

    if not _PRODUCT_SIGNAL.search(haystack):
        return False
    if _WRONG_DOMAIN_TEXT.search(haystack):
        return False
    if psc.startswith(_WRONG_DOMAIN_PSC) or naics.startswith(_WRONG_DOMAIN_NAICS):
        return False
    return True


def _parse_posted_date(value: str) -> Optional[date]:
    text = (value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_deadline(value: str) -> Optional[datetime]:
    """Parse ResponseDeadLine, e.g. '2026-09-02T16:30:00-04:00'.

    Returns None when absent or unparseable -- callers treat that as "cannot
    prove this closed" and keep the notice rather than dropping it.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _classify_stage(row: Dict[str, str]) -> str:
    """Decide how a notice should be worked, from its type and text."""
    notice_type = (row.get("Type") or "").strip()

    if notice_type == "Sources Sought":
        return STAGE_MARKET_RESEARCH
    if notice_type == "Presolicitation" or notice_type.startswith(
        "Intent to Bundle Requirements"
    ):
        return STAGE_EARLY_POSITIONING
    if notice_type in DEADLINE_ENFORCED_TYPES:
        return STAGE_OPEN_BID
    if notice_type == "Special Notice":
        haystack = f"{row.get('Title', '')} {row.get('Description', '')}"
        if _SOLE_SOURCE.search(haystack):
            return STAGE_SOLE_SOURCE_URGENT
        return STAGE_INFORMATIONAL
    return STAGE_INFORMATIONAL


def _place_of_performance(row: Dict[str, str]) -> str:
    """Where the work happens -- never the buying office.

    PopCity/PopState are only ~40% populated, but the 99.8%-populated
    State/City columns describe the contracting office instead. Substituting
    them would put the wrong location in front of the model.
    """
    city = (row.get("PopCity") or "").strip()
    state = (row.get("PopState") or "").strip()
    # A literal "0" shows up in ~10 rows/day where the city is unknown.
    if city in {"0", "00", "N/A", "TBD"}:
        city = ""
    parts = [part for part in (city, state) if part]
    return ", ".join(parts) if parts else "not specified"


def _build_content(row: Dict[str, str], stage: str) -> str:
    """Assemble the prose block the eligibility gate reads.

    A SAM row has no natural article body, so `content` is synthesized rather
    than copied from one column. The header lines exist because the
    Description alone is often too thin to judge: decoded NAICS/PSC plus the
    buying organization (National Park Service implies visitor centers, VA
    implies clinic waiting rooms) frequently carry the fit signal on their own.

    Contact details are deliberately absent -- they are lead-delivery data and
    never change the eligibility verdict, so including them would be pure
    token cost plus needless PII exposure.
    """
    naics_code = (row.get("NaicsCode") or "").strip()
    psc_code = (row.get("ClassificationCode") or "").strip()
    naics_label = _decode_naics(naics_code)
    psc_label = _decode_psc(psc_code)

    lines = [
        f"NOTICE TYPE: {(row.get('Type') or '').strip() or 'unknown'}",
        # The gate cannot judge posture from the type string alone: a Sources
        # Sought and a Solicitation are both relevant but call for completely
        # different action, and only one of them is a bid.
        f"OPPORTUNITY STAGE: {stage} -- {_STAGE_GUIDANCE.get(stage, '')}".rstrip(" -"),
        f"TITLE: {_clean_title(row.get('Title', ''))}",
    ]
    if naics_code:
        lines.append(
            f"WORK CATEGORY (NAICS {naics_code}): {naics_label or 'unrecognized code'}"
        )
    if psc_code:
        lines.append(
            f"WORK CATEGORY (PSC {psc_code}): {psc_label or 'unrecognized code'}"
        )

    org = " > ".join(
        part for part in (
            (row.get("Department/Ind.Agency") or "").strip(),
            (row.get("Sub-Tier") or "").strip(),
            (row.get("Office") or "").strip(),
        ) if part
    )
    if org:
        lines.append(f"BUYING ORGANIZATION: {org}")

    lines.append(f"PLACE OF PERFORMANCE: {_place_of_performance(row)}")
    lines.append(
        f"SET-ASIDE: {(row.get('SetASide') or '').strip() or 'none (full and open)'}"
    )
    deadline = (row.get("ResponseDeadLine") or "").strip()[:10]
    if deadline:
        lines.append(f"RESPONSE DEADLINE: {deadline}")

    description = _normalize_text(row.get("Description", ""))
    lines.append("")
    lines.append("SCOPE OF WORK:")
    # Amendment history arrives folded into the front of Description
    # ("Amendment 2: ... Amendment 1: ..."), so change context comes free.
    lines.append(description or "(none provided in notice; see attachments)")
    return "\n".join(lines)


def _open_csv_stream(csv_path: Optional[str], budget: int):
    """Yield (text_stream, closer, byte_counter) for a local file or the live URL."""
    if csv_path:
        handle = open(csv_path, encoding="cp1252", errors="replace", newline="")
        return handle, handle.close, None

    response = requests.get(FULL_CSV_URL, stream=True, timeout=180)
    response.raise_for_status()
    response.raw.decode_content = True
    counter = _CountingReader(response.raw, budget)
    # newline="" is required: descriptions contain embedded CR/LF inside
    # quoted fields, and universal-newline translation corrupts them.
    stream = io.TextIOWrapper(
        io.BufferedReader(counter, buffer_size=1 << 20),
        encoding="cp1252",
        errors="replace",
        newline="",
    )
    return stream, response.close, counter


def _iter_rows(
    csv_path: Optional[str], budget: int
) -> Iterator[Tuple[Dict[str, str], Optional[int]]]:
    """Stream the extract as dict rows, newest first.

    Yields (row, bytes_read_so_far). The caller decides when to stop, which is
    what keeps a daily run to a few MB of a 241 MB file.
    """
    stream, close, counter = _open_csv_stream(csv_path, budget)
    try:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration:
            return
        if len(header) != _EXPECTED_COLUMNS:
            print(
                f"SAM.gov: unexpected column count {len(header)} "
                f"(expected {_EXPECTED_COLUMNS}) -- extract layout may have changed"
            )
        for values in reader:
            if len(values) != len(header):
                continue
            yield dict(zip(header, values)), (counter.bytes_read if counter else None)
    finally:
        try:
            close()
        except Exception:
            pass


def _collapse_duplicate_notices(documents: List[Dict]) -> List[Dict]:
    """Collapse notices that are the same procurement posted more than once.

    SAM lets one solicitation appear under several notice ids -- we see groups
    of three sharing a Sol# with byte-identical descriptions, ~10% of a day's
    rows. Each would otherwise cost a full enrichment call for the same lead.

    Only exact content matches within one Sol# are collapsed, so two notices
    that genuinely describe different lots or scopes both survive. The dropped
    urls are kept on the survivor rather than discarded.
    """
    kept: List[Dict] = []
    first_by_key: Dict[Tuple[str, int], Dict] = {}
    collapsed = 0

    for document in documents:
        solicitation = document.get("solicitation_number") or ""
        if not solicitation:
            kept.append(document)
            continue
        key = (solicitation, hash(document["content"]))
        survivor = first_by_key.get(key)
        if survivor is None:
            first_by_key[key] = document
            kept.append(document)
            continue
        survivor.setdefault("duplicate_urls", []).append(document["url"])
        collapsed += 1

    if collapsed:
        print(
            f"SAM.gov: collapsed {collapsed} duplicate notice(s) sharing a "
            "solicitation number with identical content"
        )
    return kept


def get_samgov_documents(
    days_back: int = 0,
    *,
    csv_path: Optional[str] = None,
    apply_relevance_filter: bool = True,
) -> List[Dict]:
    """Collect SAM.gov notices posted or amended within `days_back` days.

    `PostedDate` is re-stamped when a notice is amended, so filtering on it
    yields posted-or-amended-today rather than only brand-new notices -- which
    is what the equivalent API query (sort=-modifiedDate) would return.

    Covers the full biddable range, not just live solicitations: Sources Sought
    and Presolicitation notices arrive before the bid is written, which is when
    a requirement can still be shaped. Award Notice, Justification and Sale of
    Surplus Property are dropped as already-decided or unrelated.

    `csv_path` reads a local extract instead of the live URL, which is how the
    tests run offline against tests/fixtures/samgov_sample.csv.
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days_back)).date()

    budget = _stream_budget(days_back)

    documents: List[Dict] = []
    by_url: Dict[str, Dict] = {}
    stage_counts: Dict[str, int] = collections.Counter()
    unknown_types: Dict[str, int] = collections.Counter()
    dropped_closed = 0
    seen_rows = 0
    stale_streak = 0
    dropped_type = 0
    dropped_relevance = 0
    dropped_stale = 0
    truncated = False
    bytes_read = 0

    checked_freshness = csv_path is not None  # local fixtures are intentionally dated

    for row, counter in _iter_rows(csv_path, budget):
        seen_rows += 1

        if seen_rows == counter:
            print("On last seen row")

        if counter is not None:
            bytes_read = counter
            if counter >= budget:
                truncated = True
                break

        posted = _parse_posted_date(row.get("PostedDate", ""))
        if posted is None:
            continue

        if not checked_freshness:
            checked_freshness = True
            age = (now.date() - posted).days
            if age > MAX_FEED_AGE_DAYS:
                raise SamGovStaleFeedError(
                    f"newest row in the extract is dated {posted} ({age} days old). "
                    "A healthy extract is regenerated nightly and starts within a "
                    "day or two of now, so this points at a frozen S3 object, a "
                    "changed URL, or the file no longer being sorted newest-first "
                    "-- not a quiet day. Refusing to report an empty result."
                )

        if posted < cutoff:
            dropped_stale += 1
            stale_streak += 1
            # The file is sorted newest-first, so a sustained run of older rows
            # means everything after this point is older too.
            if stale_streak >= STALE_ROW_TOLERANCE:
                break
            continue
        stale_streak = 0

        notice_type = (row.get("Type") or "").strip()
        if notice_type in EXCLUDED_TYPES:
            dropped_type += 1
            continue
        if notice_type not in BIDDABLE_TYPES:
            # Fail open on an unrecognized type: SAM renaming one must not
            # silently delete a whole category of leads. Logged for triage.
            unknown_types[notice_type] += 1

        # Only real bid documents expire. An early-stage notice's deadline is
        # for responding to market research, not for bidding.
        if notice_type in DEADLINE_ENFORCED_TYPES:
            deadline = _parse_deadline(row.get("ResponseDeadLine", ""))
            if deadline is not None and deadline < now:
                dropped_closed += 1
                continue

        if apply_relevance_filter and not _is_relevant(row):
            dropped_relevance += 1
            continue

        url = (row.get("Link") or "").strip()
        if not url:
            continue

        stage = _classify_stage(row)
        stage_counts[stage] += 1
        content = _build_content(row, stage)
        language, confidence = langid.classify(content) if content else ("unknown", 0.0)

        document = {
            # raw_documents contract
            "published_at": datetime(
                posted.year, posted.month, posted.day, tzinfo=timezone.utc
            ),
            "discovered_at": du.get_now_utc(),
            "title": _clean_title(row.get("Title", "")),
            "url": url,
            "content": content,
            "language": language,
            "language_confidence": confidence,
            "scraper_source": SCRAPER_SOURCE,
            "source_domain": tldextract.extract(BASE_URL).domain,
            "source_name": "SAM.gov",
            "document_type": "solicitation",
            # Structured extras. raw_documents has no column for these yet
            # (a JSONB source_metadata migration is still pending), and
            # db._raw_document_row ignores unknown keys, so carrying them here
            # is safe and keeps them available for filtering and the digest.
            "notice_id": (row.get("NoticeId") or "").strip(),
            "solicitation_number": (row.get("Sol#") or "").strip(),
            "notice_type": notice_type,
            "opportunity_stage": stage,
            # Sole-source windows close fast and are the one stage where a
            # late response costs the whole opportunity -- surfaced as its own
            # flag so the digest can sort these to the top.
            "is_urgent": stage == STAGE_SOLE_SOURCE_URGENT,
            "is_bid_document": notice_type in DEADLINE_ENFORCED_TYPES,
            "naics_code": (row.get("NaicsCode") or "").strip(),
            "naics_label": _decode_naics(row.get("NaicsCode", "")),
            "psc_code": (row.get("ClassificationCode") or "").strip(),
            "psc_label": _decode_psc(row.get("ClassificationCode", "")),
            "set_aside": (row.get("SetASide") or "").strip(),
            "response_deadline": (row.get("ResponseDeadLine") or "").strip(),
            "place_of_performance": _place_of_performance(row),
            "agency": (row.get("Department/Ind.Agency") or "").strip(),
            "sub_tier": (row.get("Sub-Tier") or "").strip(),
            "office": (row.get("Office") or "").strip(),
            "primary_contact_name": (row.get("PrimaryContactFullname") or "").strip(),
            "primary_contact_email": (row.get("PrimaryContactEmail") or "").strip(),
            "primary_contact_phone": (row.get("PrimaryContactPhone") or "").strip(),
        }

        # A notice can appear twice across an amendment boundary; keep the
        # newest row for a given url.
        existing = by_url.get(url)
        if existing is None or document["published_at"] > existing["published_at"]:
            by_url[url] = document

    documents = _collapse_duplicate_notices(
        sorted(by_url.values(), key=lambda d: d["published_at"], reverse=True)
    )

    if truncated:
        # Never return a short date range as if it were complete: this feeds a
        # digest email, and a partial run that looks successful is worse than a
        # failed one. Reaching here means the newest-first sort assumption is
        # probably broken, since the budget is 3-4x measured consumption.
        raise SamGovTruncatedError(
            f"exhausted the {budget // (1024 * 1024)} MB stream budget after "
            f"{seen_rows} rows without reaching the {cutoff} cutoff, so the date "
            "range is incomplete. Either the extract is no longer sorted "
            "newest-first, or STREAM_BYTES_PER_DAY needs raising for a range "
            f"this large (days_back={days_back})."
        )

    if unknown_types:
        print(
            "SAM.gov: NOTE unrecognized notice type(s) kept for triage -- "
            + ", ".join(f"{name!r} x{count}" for name, count in unknown_types.items())
        )

    read_note = f", {bytes_read / (1024 * 1024):.1f} MB read" if bytes_read else ""
    # Every scanned row is accounted for, so the counts reconcile against
    # `rows scanned` and a silent loss shows up as a gap rather than hiding.
    print(
        f"SAM.gov: {len(documents)} document(s) posted/amended on or after {cutoff} "
        f"({seen_rows} rows scanned{read_note})"
    )
    print(
        f"SAM.gov: of {seen_rows} rows -- {dropped_stale} older than cutoff, "
        f"{dropped_type} already-decided/unrelated, {dropped_closed} past deadline, "
        f"{dropped_relevance} failed NAICS/PSC relevance, "
        f"{len(documents)} kept as documents"
    )
    if stage_counts:
        breakdown = ", ".join(
            f"{stage}={stage_counts[stage]}"
            for stage in sorted(stage_counts, key=lambda s: -stage_counts[s])
        )
        print(f"SAM.gov: by opportunity stage -- {breakdown}")
    return documents
