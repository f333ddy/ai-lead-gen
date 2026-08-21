"""Postgres persistence for the lead-gen pipeline (Neon `neondb`).

Scope: the `raw_documents` write path. `enriched_documents`, `companies`,
`document_team_buckets` and `pipeline_runs` follow in later passes.

Connection comes from DATABASE_URL in .env. The endpoint is a Neon pooled
host with sslmode=require, so short-lived connections are the intended
pattern -- open one per phase rather than holding one for the whole run.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Sequence, Set, Tuple
from uuid import UUID

import psycopg
from dotenv import load_dotenv

import date_utils as du

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# 11 params per row keeps us far under Postgres' 65535 bound-parameter ceiling
# while still collapsing a few hundred documents into a handful of round trips.
UPSERT_CHUNK_SIZE = 250

RAW_DOCUMENT_COLUMNS: Tuple[str, ...] = (
    "url",
    "title",
    "content",
    "published_at",
    "discovered_at",
    "scraper_source",
    "source_name",
    "source_domain",
    "document_type",
    "language",
    "language_confidence",
)

# Columns we refresh when a url is seen again. COALESCE so a re-scrape that
# fails to extract content can't wipe a good earlier value. url is the
# conflict key; discovered_at and scraper_source record the first sighting and
# deliberately never move.
_IMMUTABLE_ON_CONFLICT = ("url", "discovered_at", "scraper_source")
_RAW_DOCUMENT_UPDATE_SET = ",\n                    ".join(
    f"{column} = COALESCE(EXCLUDED.{column}, raw_documents.{column})"
    for column in RAW_DOCUMENT_COLUMNS
    if column not in _IMMUTABLE_ON_CONFLICT
)


class RawDocumentError(ValueError):
    """A scraped document can't satisfy raw_documents' NOT NULL columns."""


def get_connection() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set - expected in .env")
    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=20,
        application_name="ai-lead-gen",
    )


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _chunked(rows: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _raw_document_row(document: Dict[str, Any]) -> Tuple[Any, ...]:
    """Build the insert tuple for one scraped document.

    Only `url` and `scraper_source` are NOT NULL without a default, so those
    are the only two that can reject a row.
    """
    url = (document.get("url") or "").strip()
    if not url:
        raise RawDocumentError(f"no url (title={document.get('title')!r})")

    scraper_source = (document.get("scraper_source") or "").strip()
    if not scraper_source:
        raise RawDocumentError(f"no scraper_source for {url}")

    return (
        url,
        document.get("title"),
        document.get("content"),
        du.to_utc_datetime(document.get("published_at")),
        du.to_utc_datetime(document.get("discovered_at")) or du.get_now_utc(),
        scraper_source,
        document.get("source_name"),
        document.get("source_domain"),
        document.get("document_type") or "news",
        document.get("language"),
        document.get("language_confidence"),
    )


def upsert_raw_documents(
    conn: psycopg.Connection,
    documents: Iterable[Dict[str, Any]],
    *,
    commit: bool = True,
) -> Dict[str, UUID]:
    """Upsert scraped documents keyed on url. Returns {url: raw_document_id}.

    Deduplicates by url within the batch before hitting the DB: two scrapers
    can surface the same article (a PR Newswire release also carried by
    EventRegistry), and Postgres rejects an ON CONFLICT DO UPDATE that would
    touch the same row twice in one statement.

    Documents that can't satisfy the NOT NULL columns are reported and skipped
    rather than failing the batch -- one malformed card shouldn't cost us the
    rest of the run.
    """
    rows: List[Tuple[Any, ...]] = []
    seen: Set[str] = set()
    rejected: List[str] = []

    for document in documents:
        try:
            row = _raw_document_row(document)
        except RawDocumentError as exc:
            rejected.append(str(exc))
            continue
        if row[0] in seen:
            continue
        seen.add(row[0])
        rows.append(row)

    if rejected:
        print(f"raw_documents: skipped {len(rejected)} document(s) missing required fields")
        for reason in rejected[:10]:
            print(f"  - {reason}")
        if len(rejected) > 10:
            print(f"  ... and {len(rejected) - 10} more")

    if not rows:
        return {}

    columns = ", ".join(RAW_DOCUMENT_COLUMNS)
    placeholder = "(" + ", ".join(["%s"] * len(RAW_DOCUMENT_COLUMNS)) + ")"

    url_to_id: Dict[str, UUID] = {}
    inserted = 0
    updated = 0

    with conn.cursor() as cur:
        for chunk in _chunked(rows, UPSERT_CHUNK_SIZE):
            values = ", ".join([placeholder] * len(chunk))
            cur.execute(
                f"""
                INSERT INTO raw_documents ({columns})
                VALUES {values}
                ON CONFLICT (url) DO UPDATE SET
                    {_RAW_DOCUMENT_UPDATE_SET},
                    updated_at = now()
                RETURNING url, id, (xmax = 0) AS was_inserted
                """,
                [value for row in chunk for value in row],
            )
            for url, row_id, was_inserted in cur.fetchall():
                url_to_id[url] = row_id
                if was_inserted:
                    inserted += 1
                else:
                    updated += 1

    if commit:
        conn.commit()

    print(
        f"raw_documents: {inserted} inserted, {updated} updated "
        f"({len(rows)} unique url(s) submitted)"
    )
    return url_to_id


def upsert_raw_document(
    conn: psycopg.Connection,
    document: Dict[str, Any],
    *,
    commit: bool = True,
) -> UUID | None:
    """Upsert a single scraped document. Returns its raw_documents id.

    The per-document form, for use inside the enrichment loop where the raw
    row and the AI response are written together. Returns None if the document
    can't satisfy the NOT NULL columns.
    """
    url_to_id = upsert_raw_documents(conn, [document], commit=commit)
    return next(iter(url_to_id.values()), None)


def _evidence_quotes(gate_response: Dict[str, Any]) -> List[str]:
    """Flatten the gate's evidence_spans [{quote: str}] into text[]."""
    spans = gate_response.get("evidence_spans") or []
    quotes = []
    for span in spans:
        quote = (span or {}).get("quote") if isinstance(span, dict) else None
        if quote:
            quotes.append(quote)
    return quotes


def insert_enriched_document(
    conn: psycopg.Connection,
    document: Dict[str, Any],
    gate_response: Dict[str, Any],
    *,
    raw_document_id: UUID | None = None,
    commit: bool = True,
) -> UUID:
    """Persist one AI-augmented signal. Returns the enriched_documents id.

    Both eligible and ineligible rows are written -- `eligible` and
    `confidence` are NOT NULL with no default, so an ineligible document is a
    real row with eligible=false, not an omission. That's also what lets the
    QA digest read from the DB instead of an in-memory list.

    Idempotent by delete-then-insert on raw_document_id, inside one
    transaction. There is no UNIQUE (raw_document_id) yet, so ON CONFLICT has
    no arbiter to use; once that migration lands this becomes a plain upsert.

    Deliberately NOT written: `industries` (deferred, see HANDOFF-RESPONSE.md
    §11.A1) and `company_id` (geocoding not built yet).
    """
    extracted = gate_response.get("extracted") or {}

    if raw_document_id is None:
        raw_document_id = document.get("raw_document_id")

    url = (document.get("url") or extracted.get("article_link") or "").strip()
    if not url:
        raise ValueError("cannot persist enriched document without a url")

    # Prefer the scraped timestamp over the AI's free-text doc_date.
    published_at = du.to_utc_datetime(document.get("published_at"))
    doc_date = published_at.date().isoformat() if published_at else extracted.get("doc_date")

    with conn.cursor() as cur:
        if raw_document_id is not None:
            cur.execute(
                "DELETE FROM enriched_documents WHERE raw_document_id = %s",
                (raw_document_id,),
            )
        cur.execute(
            """
            INSERT INTO enriched_documents (
                raw_document_id, url, title, summary, company,
                amount_usd, fiscal_year, doc_date, article_link,
                eligible, confidence, triggers, evidence_quotes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                raw_document_id,
                url,
                extracted.get("title") or document.get("title"),
                extracted.get("summary"),
                extracted.get("company"),
                extracted.get("amount_usd"),
                extracted.get("fiscal_year"),
                doc_date,
                url,
                bool(gate_response.get("eligible")),
                float(gate_response.get("confidence") or 0.0),
                gate_response.get("triggers") or [],
                _evidence_quotes(gate_response),
            ),
        )
        enriched_id = cur.fetchone()[0]

    if commit:
        conn.commit()
    return enriched_id


def attach_raw_document_ids(
    documents: Iterable[Dict[str, Any]],
    url_to_id: Dict[str, UUID],
) -> int:
    """Stamp raw_document_id onto each scraped dict, in place.

    This is what later carries the FK into enriched_documents -- today the gate
    returns only the AI's `extracted` block, which has no handle back to its
    source row.
    """
    attached = 0
    for document in documents:
        url = (document.get("url") or "").strip()
        row_id = url_to_id.get(url)
        if row_id is not None:
            document["raw_document_id"] = row_id
            attached += 1
    return attached


def fetch_already_enriched(
    conn: psycopg.Connection,
    documents: Iterable[Dict[str, Any]],
) -> Set[str]:
    """Return urls to skip, treating amendable documents differently.

    News articles are immutable: once enriched, re-enriching is pure waste, so
    seeing the url is reason enough to skip. Solicitations are not. SAM amends
    a notice in place -- same url, `PostedDate` re-stamped, and the amendment
    text prepended to the description. Skipping on url alone means an amendment
    that finally states the scope is never evaluated, which loses exactly the
    leads that improve.

    So for documents carrying `document_type = 'solicitation'`, a url is only
    skipped when what we already enriched is at least as new as what we now
    hold. Every other document type keeps the original url-only behavior.

    `doc_date` is a text column holding an ISO date, so a lexicographic
    comparison orders it correctly; it is written from published_at by
    insert_enriched_document.
    """
    amendable: Dict[str, str] = {}
    immutable: Set[str] = set()

    for document in documents:
        url = (document.get("url") or "").strip()
        if not url:
            continue
        if (document.get("document_type") or "") != "solicitation":
            immutable.add(url)
            continue
        published_at = du.to_utc_datetime(document.get("published_at"))
        incoming = published_at.date().isoformat() if published_at else ""
        # Keep the newest date if the same url appears twice in one batch.
        if incoming > amendable.get(url, ""):
            amendable[url] = incoming

    skip: Set[str] = set()

    if immutable:
        skip |= fetch_enriched_urls(conn, immutable)

    if amendable:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT url, COALESCE(doc_date, '') FROM enriched_documents
                WHERE url = ANY(%s)
                """,
                (sorted(amendable),),
            )
            for url, stored_date in cur.fetchall():
                # No stored date means we cannot prove it is stale -- skip, so
                # a missing date can never cause endless re-enrichment.
                if not amendable[url] or stored_date >= amendable[url]:
                    skip.add(url)

    return skip


def fetch_enriched_urls(conn: psycopg.Connection, urls: Iterable[str]) -> Set[str]:
    """Return the subset of `urls` that already have an enriched_documents row.

    This is the cost lever from persisting. Today the scrapers' only dedupe is
    a per-run in-memory set, so every run re-pays OpenAI for articles that were
    already enriched on a previous run.
    """
    unique = sorted({(url or "").strip() for url in urls if (url or "").strip()})
    if not unique:
        return set()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT url FROM enriched_documents WHERE url = ANY(%s)",
            (unique,),
        )
        return {row[0] for row in cur.fetchall()}
