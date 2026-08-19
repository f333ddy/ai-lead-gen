"""Resolve companies named by eligible signals, then link them back.

Runs after enrichment, on its own schedule. Two independent passes:

PASS 1 -- resolve (paid, per unique company string)
  1. Group eligible `enriched_documents` rows still missing a `company_id` by
     their **exact** company string -- {"Pilot Company": [enriched_id, ...]}.
  2. Reuse an existing company when that same string was resolved on an earlier
     run; only a genuinely new string reaches OpenAI.
  3. One web-search-backed call returns the HQ street address + firmographics.
  4. Insert the company and link its documents immediately, with
     `google_place_id`, `latitude` and `longitude` left NULL.

PASS 2 -- geocode (cheap, idempotent, retryable)
  5. Sweep companies that have an address but no `place_id`, geocode each, and
     fill in the three NULL columns.
  6. If the resolved `place_id` already belongs to another row, that row wins:
     documents are re-pointed to it and the duplicate is deleted.

The split is deliberate. Geocoding used to run between the AI call and the
insert, so a Google failure discarded work that had already been paid for. Now
the expensive result is committed before anything cheap can fail, and pass 2
can be re-run for free until it succeeds.

Identity is Google's `place_id` once known: two spellings of one company
("Walmart", "Wal-Mart Stores, Inc.") resolve to the same headquarters address
and therefore the same place_id, so they converge on one row without any string
normalization. Until then identity falls back to the company name, which is
what makes step 6 necessary.

Note the direction of the Google call: address -> place_id via the Geocoding
API, NOT company name -> place via Places Text Search. A text search for a
chain returns whichever store ranks first that day, which would mint a new
company row on every run.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import UUID

import psycopg
import requests
from dotenv import load_dotenv
from openai import OpenAI

import db

load_dotenv()

OPENAI_MODEL = "gpt-5.2"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Server-side Geocoding only. The Maps JavaScript key is for browser map
# rendering and is deliberately not read here.
GEOCODING_API_KEY = os.getenv("GOOGLE_CLOUD_GEOCODING_PLATFORM_API_KEY")
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GEOCODE_TIMEOUT = 20

# A stable, unambiguous address used only to prove the key works before the run
# spends anything. Any ROOFTOP-precision address would do.
PREFLIGHT_ADDRESS = "1600 Amphitheatre Parkway, Mountain View, CA 94043"

GEOCODE_MAX_ATTEMPTS = 3
GEOCODE_RETRY_BASE_SECONDS = 2.0

# Worth another attempt. OVER_QUERY_LIMIT and UNKNOWN_ERROR are transient by
# Google's own definition. REQUEST_DENIED is usually permanent (wrong key) but
# is also what a freshly-changed IP restriction returns while it propagates --
# the same address gets accepted and rejected minutes apart, so retrying is
# worth the few seconds. INVALID_REQUEST is excluded: a malformed request will
# stay malformed.
_RETRYABLE_STATUSES = {"OVER_QUERY_LIMIT", "UNKNOWN_ERROR", "REQUEST_DENIED"}

# Below this we still create the company row -- its documents need something to
# point at -- but leave the firmographic columns NULL rather than store a guess.
PROFILE_CONFIDENCE_THRESHOLD = 0.70

# Geocoder precision. APPROXIMATE means Google matched a city or region rather
# than a building, and that place_id CANNOT be used as company identity: two
# unrelated companies whose addresses both degrade to "Bentonville, Arkansas"
# would resolve to the same city place_id and be silently merged into one
# company. Rejecting it costs us a null address; accepting it corrupts rows.
_UNUSABLE_LOCATION_TYPES = {"APPROXIMATE"}


class GeocodeError(RuntimeError):
    """The Geocoding API refused the request (bad key, quota, malformed)."""


class _GeocodeRetry(Exception):
    """Internal: this attempt failed transiently. Never escapes geocode_address."""


def domain_from_website(website: Optional[str]) -> Optional[str]:
    """Reduce a website URL to its registrable hostname, or None."""
    if not website:
        return None
    candidate = website.strip()
    if not candidate:
        return None
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


COMPANY_PROFILE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "legal_name": {"type": ["string", "null"]},
        "hq_street_address": {"type": ["string", "null"]},
        "website": {"type": ["string", "null"]},
        "ticker": {"type": ["string", "null"]},
        "employee_count": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "found",
        "legal_name",
        "hq_street_address",
        "website",
        "ticker",
        "employee_count",
        "confidence",
        "sources",
    ],
    "additionalProperties": False,
}

COMPANY_SYSTEM = (
    "You research company firmographics. You have a web search tool -- use it. "
    "Every field you return must be supported by a page you actually retrieved "
    "in this conversation.\n\n"
    "Hard rules:\n"
    "- Never supply a value from memory. If search did not confirm it, return null.\n"
    "- A plausible-looking address is worse than null: a wrong address geocodes "
    "successfully and produces a confidently wrong map pin with no error signal.\n"
    "- hq_street_address must be the corporate headquarters, NOT a store, branch, "
    "plant or regional office. For a single-site operator (casino, airport, "
    "stadium, clinic) the site itself is the headquarters.\n"
    "- hq_street_address must be a COMPLETE street address -- street number, "
    "street, city, state/region, postal code, country where known. A city-only "
    "answer like 'Bentonville, Arkansas' is NOT acceptable; return null instead. "
    "It geocodes to the city centroid, which is unusable downstream.\n"
    "- sources must list the URLs you actually retrieved. If sources is empty, "
    "found must be false.\n"
    "- If the name is too ambiguous to identify one company, set found=false and "
    "leave every field null rather than picking a likely candidate.\n"
    "- employee_count: approximate total headcount as an integer. ticker: the "
    "bare symbol for a publicly traded company, null if private.\n"
    "Return only JSON matching the schema."
)

COMPANY_USER_TMPL = """Identify this company and return its firmographic profile.

COMPANY NAME (as extracted from a news article, may be abbreviated or informal):
{company}

Search the web to confirm the company's identity and the complete street address
of its corporate headquarters. Set found=false with null fields if you cannot
confirm which company this is, or if it is a government body, regulator or grant
program rather than a company.

Return JSON only."""


def resolve_hq_profile(company_name: str) -> Dict[str, Any]:
    """One web-search-backed lookup for a single company name.

    Uses the Responses API because the web_search tool lives there -- a Chat
    Completions call has no search capability, so the model would answer from
    training memory and invent an address that geocodes cleanly.
    """
    rsp = client.responses.create(
        model=OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": COMPANY_SYSTEM},
            {"role": "user", "content": COMPANY_USER_TMPL.format(company=company_name)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "CompanyProfile",
                "schema": COMPANY_PROFILE_SCHEMA,
                "strict": True,
            }
        },
    )
    return json.loads(rsp.output_text)


def _geocode_once(address: str) -> Optional[Dict[str, Any]]:
    """One Geocoding API attempt.

    Returns the resolved place on success and None when the address is a real
    miss. Raises _GeocodeRetry for anything worth trying again, GeocodeError
    for a permanent failure.
    """
    response = requests.get(
        GEOCODE_URL,
        params={"address": address, "key": GEOCODING_API_KEY},
        timeout=GEOCODE_TIMEOUT,
    )

    # Split by class rather than using raise_for_status: a 5xx is Google having
    # a bad moment, a 4xx is our request being wrong. Only the first is worth
    # repeating.
    if response.status_code >= 500:
        raise _GeocodeRetry(f"HTTP {response.status_code}")
    if response.status_code >= 400:
        raise GeocodeError(f"HTTP {response.status_code}: {response.text[:200]}")

    payload = response.json()
    status = payload.get("status")

    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        detail = f"{status}: {payload.get('error_message') or 'no detail'}"
        if status in _RETRYABLE_STATUSES:
            raise _GeocodeRetry(detail)
        raise GeocodeError(detail)

    result = (payload.get("results") or [None])[0]
    if not result:
        return None

    geometry = result.get("geometry") or {}
    location = geometry.get("location") or {}
    location_type = geometry.get("location_type")

    if location_type in _UNUSABLE_LOCATION_TYPES:
        print(f"    geocode too coarse ({location_type}) - treating as unresolved")
        return None

    return {
        "place_id": result.get("place_id"),
        "formatted_address": result.get("formatted_address"),
        "latitude": location.get("lat"),
        "longitude": location.get("lng"),
        "location_type": location_type,
        "partial_match": bool(result.get("partial_match")),
    }


def geocode_address(address: str) -> Optional[Dict[str, Any]]:
    """Resolve an address to place_id + coordinates + canonical address.

    Retries transient failures up to GEOCODE_MAX_ATTEMPTS with exponential
    backoff. Returns None when Google finds nothing, or when the match is too
    coarse to identify a building (see _UNUSABLE_LOCATION_TYPES) -- both are
    real answers, not failures, so neither is retried. Raises GeocodeError once
    the attempts are exhausted or on a permanent failure.
    """
    if not GEOCODING_API_KEY:
        raise GeocodeError(
            "GOOGLE_CLOUD_GEOCODING_PLATFORM_API_KEY is not set - expected in .env"
        )

    last_error: Optional[Exception] = None
    for attempt in range(1, GEOCODE_MAX_ATTEMPTS + 1):
        try:
            return _geocode_once(address)
        except (_GeocodeRetry, requests.RequestException) as exc:
            last_error = exc
            if attempt == GEOCODE_MAX_ATTEMPTS:
                break
            delay = GEOCODE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(
                f"    geocode attempt {attempt}/{GEOCODE_MAX_ATTEMPTS} failed "
                f"({exc}) - retrying in {delay:.0f}s"
            )
            time.sleep(delay)

    raise GeocodeError(
        f"failed after {GEOCODE_MAX_ATTEMPTS} attempts - {last_error}"
    )


def preflight_geocoder() -> None:
    """Prove the Geocoding key works before the run spends anything.

    A wrong key, a missing key, or an IP restriction that excludes this host all
    fail identically at the first real geocode -- by which point pass 1 has paid
    OpenAI for every company it processed. One throwaway lookup up front turns
    that into a sub-second abort costing nothing.

    Raises GeocodeError on any failure; the caller is expected not to catch it.
    """
    print("company_sync: geocoder preflight...")
    try:
        result = geocode_address(PREFLIGHT_ADDRESS)
    except GeocodeError as exc:
        raise GeocodeError(
            f"geocoder preflight failed, aborting before any OpenAI spend - {exc}"
        ) from exc

    if not result or not result.get("place_id"):
        raise GeocodeError(
            f"geocoder preflight resolved nothing for {PREFLIGHT_ADDRESS!r} - "
            "the key answered but the result was unusable"
        )
    print(f"company_sync: preflight ok ({result['place_id']})")


def group_eligible_documents(
    conn: psycopg.Connection,
) -> "OrderedDict[str, List[UUID]]":
    """Group unlinked eligible signals by their exact company string.

    Returns {company_string: [enriched_document_id, ...]}, ordered by first
    appearance so a --limit run is deterministic.

    Keyed on `id` rather than `raw_document_id`: the latter is nullable, and a
    NULL there would drop the document from the final UPDATE silently.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, btrim(company)
            FROM enriched_documents
            WHERE eligible = true
              AND company_id IS NULL
              AND company IS NOT NULL
              AND btrim(company) <> ''
            ORDER BY enriched_at
            """
        )
        rows = cur.fetchall()

    groups: "OrderedDict[str, List[UUID]]" = OrderedDict()
    for doc_id, company in rows:
        groups.setdefault(company, []).append(doc_id)

    print(
        f"company_sync: {len(rows)} unlinked eligible document(s) "
        f"-> {len(groups)} distinct company string(s)"
    )
    return groups


def find_company_for_company_string(
    conn: psycopg.Connection, company: str
) -> Optional[UUID]:
    """Find the company a previous run already resolved this string to.

    This is the skip that makes a mixed state cheap: once "Pilot Company" has
    been resolved, any later document carrying that same string reuses the
    answer instead of paying for another web search.

    It reads `enriched_documents` rather than `companies` on purpose.
    `companies.name` holds the model's confirmed legal name ("Pilot Travel
    Centers LLC"), which will not match the article's string ("Pilot Company").
    The already-linked document carries both, so it is the only place the
    string -> company mapping exists.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT company_id
            FROM enriched_documents
            WHERE company_id IS NOT NULL
              AND btrim(company) = btrim(%s)
            ORDER BY enriched_at
            LIMIT 1
            """,
            (company,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def find_company_by_place_id(
    conn: psycopg.Connection, place_id: str
) -> Optional[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies WHERE google_place_id = %s",
            (place_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def find_unresolved_company_by_name(
    conn: psycopg.Connection, name: str
) -> Optional[UUID]:
    """Fallback key for rows that have no place_id yet.

    `companies_google_place_id_key` is a *partial* unique index
    (WHERE google_place_id IS NOT NULL), so it does not constrain rows without
    one. Without this lookup a company we never manage to resolve would insert
    a fresh row on every run. Case-insensitive exact name.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM companies
            WHERE google_place_id IS NULL
              AND lower(btrim(name)) = lower(btrim(%s))
            ORDER BY created_at
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _trusted_profile(profile: Optional[Dict[str, Any]]) -> bool:
    """A profile is usable only if the model found it, cited it, and is sure."""
    return bool(
        profile
        and profile.get("found")
        and float(profile.get("confidence") or 0.0) >= PROFILE_CONFIDENCE_THRESHOLD
        and (profile.get("sources") or [])
    )


def insert_company_from_profile(
    conn: psycopg.Connection,
    *,
    fallback_name: str,
    profile: Optional[Dict[str, Any]],
) -> UUID:
    """Create the company row from the AI profile alone. No geocoding.

    `google_place_id`, `latitude` and `longitude` are left NULL for the sweep to
    fill. Committing the paid result before touching Google is the whole point
    of the split: a Google problem can no longer discard it.
    """
    if not _trusted_profile(profile):
        existing = find_unresolved_company_by_name(conn, fallback_name)
        if existing is not None:
            return existing
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO companies (name) VALUES (%s) RETURNING id",
                (fallback_name,),
            )
            return cur.fetchone()[0]

    name = (profile.get("legal_name") or fallback_name).strip()
    website = (profile.get("website") or "").strip() or None
    domain = domain_from_website(website)

    existing = find_unresolved_company_by_name(conn, name)
    if existing is not None:
        return existing

    values = (
        name,
        domain,
        (profile.get("hq_street_address") or "").strip() or None,
        website,
        (profile.get("ticker") or "").strip() or None,
        profile.get("employee_count"),
    )

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO companies
                        (name, domain, formatted_address, website, ticker, employee_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    values,
                )
                return cur.fetchone()[0]
    except psycopg.errors.UniqueViolation:
        # companies_domain_key is also unique: a parent and its subsidiary can
        # share a corporate domain. Reuse that row rather than fail the group.
        if not domain:
            raise
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM companies WHERE domain = %s", (domain,))
            row = cur.fetchone()
        if not row:
            raise
        print(f"    domain {domain} already claimed - reusing existing company row")
        return row[0]


def assign_company_to_documents(
    conn: psycopg.Connection, company_id: UUID, doc_ids: List[UUID]
) -> int:
    """Stamp company_id onto every document in one group."""
    if not doc_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE enriched_documents
            SET company_id = %s, updated_at = now()
            WHERE id = ANY(%s)
            """,
            (company_id, doc_ids),
        )
        return cur.rowcount


def merge_company(
    conn: psycopg.Connection, *, duplicate_id: UUID, canonical_id: UUID
) -> int:
    """Fold `duplicate_id` into `canonical_id`. Returns documents re-pointed.

    Needed because identity is the company name until a place_id exists. Two
    spellings of one company can therefore create two rows, and the sweep only
    discovers it when both geocode to the same place. The already-resolved row
    wins; documents move to it and the duplicate is deleted.

    Documents are re-pointed before the delete so the FK is never dangling.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE enriched_documents
            SET company_id = %s, updated_at = now()
            WHERE company_id = %s
            """,
            (canonical_id, duplicate_id),
        )
        moved = cur.rowcount
        cur.execute("DELETE FROM companies WHERE id = %s", (duplicate_id,))
    return moved


def geocode_pending_companies(
    conn: psycopg.Connection, *, limit: Optional[int] = None
) -> Dict[str, int]:
    """Pass 2: fill place_id + coordinates for companies that have an address.

    Idempotent and free of AI cost, so it can be re-run until it succeeds. A
    company whose address does not resolve is simply left for the next run.
    """
    stats = {"pending": 0, "geocoded": 0, "unresolved": 0, "merged": 0}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, formatted_address
            FROM companies
            WHERE google_place_id IS NULL
              AND formatted_address IS NOT NULL
              AND btrim(formatted_address) <> ''
            ORDER BY created_at
            """
        )
        pending = cur.fetchall()

    if limit is not None:
        pending = pending[:limit]

    stats["pending"] = len(pending)
    if not pending:
        print("company_sync: geocode sweep - nothing pending")
        return stats

    print(f"company_sync: geocode sweep - {len(pending)} company row(s) pending")

    for company_id, name, address in pending:
        print(f"  {name!r}")
        print(f"    address: {address}")
        geo = geocode_address(address)

        if geo is None or not geo.get("place_id"):
            stats["unresolved"] += 1
            print("    unresolved - leaving for a later run")
            continue

        if geo["partial_match"]:
            print("    warning: Google reported a partial address match")

        owner = find_company_by_place_id(conn, geo["place_id"])
        if owner is not None and owner != company_id:
            moved = merge_company(
                conn, duplicate_id=company_id, canonical_id=owner
            )
            conn.commit()
            stats["merged"] += 1
            print(
                f"    place_id already held by {owner} - merged, "
                f"{moved} document(s) re-pointed"
            )
            continue

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies
                SET google_place_id = %s,
                    formatted_address = %s,
                    latitude = %s,
                    longitude = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    geo["place_id"],
                    geo["formatted_address"] or address,
                    geo["latitude"],
                    geo["longitude"],
                    company_id,
                ),
            )
        conn.commit()
        stats["geocoded"] += 1
        print(f"    place_id: {geo['place_id']} ({geo['location_type']})")

    print(
        f"company_sync: geocode sweep - {stats['geocoded']} resolved, "
        f"{stats['merged']} merged, {stats['unresolved']} still pending"
    )
    return stats


def resolve_pending_companies(
    conn: psycopg.Connection, *, limit: Optional[int] = None, dry_run: bool = False
) -> Dict[str, int]:
    """Pass 1: one paid lookup per genuinely new company string."""
    stats = {"groups": 0, "created": 0, "reused": 0, "no_address": 0, "documents": 0}

    groups = group_eligible_documents(conn)
    if not groups:
        print("company_sync: no unlinked eligible documents")
        return stats

    pending = list(groups.items())
    if limit is not None:
        skipped = max(0, len(pending) - limit)
        pending = pending[:limit]
        if skipped:
            print(f"company_sync: --limit {limit} leaves {skipped} group(s) for a later run")

    if dry_run:
        for company, doc_ids in pending:
            known = find_company_for_company_string(conn, company)
            tag = "known" if known else "NEW"
            print(f"  [{tag}] {company!r} ({len(doc_ids)} doc(s))")
        print(f"company_sync: dry run, {len(pending)} group(s), no API calls made")
        return stats

    for company, doc_ids in pending:
        stats["groups"] += 1
        print(f"  {company!r} ({len(doc_ids)} doc(s))")

        company_id = find_company_for_company_string(conn, company)
        if company_id is not None:
            stats["reused"] += 1
            print("    string already resolved on an earlier run - no lookup needed")
        else:
            try:
                profile = resolve_hq_profile(company)
            except Exception as exc:  # network, rate limit, malformed JSON
                print(f"    lookup failed ({exc.__class__.__name__}: {exc})")
                profile = None

            if _trusted_profile(profile):
                address = (profile.get("hq_street_address") or "").strip()
                print(f"    address: {address or 'none confirmed'}")
                if not address:
                    stats["no_address"] += 1
            else:
                stats["no_address"] += 1
                print("    no confirmed profile - creating name-only row")

            company_id = insert_company_from_profile(
                conn, fallback_name=company, profile=profile
            )
            stats["created"] += 1

        linked = assign_company_to_documents(conn, company_id, doc_ids)
        stats["documents"] += linked
        conn.commit()
        print(f"    linked {linked} document(s) to {company_id}")

    print(
        f"company_sync: resolve pass - {stats['created']} created, "
        f"{stats['reused']} reused, {stats['no_address']} without an address, "
        f"{stats['documents']} document(s) linked"
    )
    return stats


def sync_companies(
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    geocode_only: bool = False,
) -> Dict[str, Dict[str, int]]:
    """Run both passes. Preflight first so a bad key costs nothing."""
    result: Dict[str, Dict[str, int]] = {}

    if not dry_run:
        preflight_geocoder()

    with db.connection() as conn:
        if not geocode_only:
            result["resolve"] = resolve_pending_companies(
                conn, limit=limit, dry_run=dry_run
            )
        if not dry_run:
            result["geocode"] = geocode_pending_companies(conn)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="resolve at most N new company strings this run (cost control)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the grouping without calling OpenAI or Google",
    )
    parser.add_argument(
        "--geocode-only",
        action="store_true",
        help="skip the paid resolve pass; only geocode companies already stored",
    )
    args = parser.parse_args()
    sync_companies(
        limit=args.limit, dry_run=args.dry_run, geocode_only=args.geocode_only
    )


if __name__ == "__main__":
    main()
