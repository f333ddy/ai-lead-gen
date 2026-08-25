from __future__ import annotations
import os
import re
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any, Optional, Set, Iterable
import xml.etree.ElementTree as ET
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()
# pip install openai beautifulsoup4
from openai import OpenAI
from pydantic import BaseModel
from datetime import date, timedelta, datetime
import requests
import copy
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
from jinja2 import Template
from email_template import template_str, test_template_str
from eligibility_schema import build_eligibility_schema, build_solicitation_schema
from prompts import GATE_SYSTEM, GATE_USER_TMPL
from prompts_solicitation import (
    SOLICITATION_GATE_SYSTEM,
    SOLICITATION_GATE_USER_TMPL,
)
from event_registry import ARTICLES_PAYLOAD
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from email.utils import parsedate_to_datetime

import db
from scrapers.eventregistry import get_eventregistry_documents
from scrapers.airport_industry_news import get_airport_industry_documents
from scrapers.chainstoreage import get_chainstoreage_documents
from scrapers.nacs import get_nacs_documents
from scrapers.nahb import get_nahb_documents
from scrapers.prnewswire import get_prnewswire_documents
from scrapers.samgov import get_samgov_documents

# Global Variables
KEYWORDS = [
    # funding signals
    "appropriation", "allocation", "allocated", "funding approved",
    "grant awarded", "grant approval", "bond measure", "capital budget",
    "capital improvement plan", "CIP",
    # project signals
    "renovation", "remodel", "expansion", "build-out", "facility upgrade", "project",
    # procurement
    "RFP", "RFQ", "solicitation", "procurement",
]
# CONFIG
OPENAI_MODEL = "gpt-5.2"  # fast + supports structured outputs
CONFIDENCE_THRESHOLD = 0.80
USE_PREFILTER = True  # toggle the cheap keyword/regex prefilter to save tokens
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
HUBSPOT_INDUSTRIES: List[str] = []
INDUSTRY_VALUE_TO_LABEL: Dict[str, str] = {}
FILTERED_RESULTS: List[Dict[str, Any]] = []

# EMAIL ROUTING
# Defaults to development on purpose: a missing, empty or misspelled APP_ENV
# must never blast a real sales team. Only the exact string "production"
# enables real delivery.
APP_ENV = (os.getenv("APP_ENV") or "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
# Every message is redirected here while in development.
DEV_EMAIL_RECIPIENT = (os.getenv("DEV_EMAIL_RECIPIENT") or "federico.aguilar@lavi.com").strip()
# Who gets the [QA Review] digest in production. Comma-separated.
QA_EMAIL_RECIPIENTS = [
    email.strip()
    for email in (
        os.getenv("QA_EMAIL_RECIPIENTS")
        or "federico.aguilar@lavi.com,will.geller@lavi.com,perryk@lavi.com"
    ).split(",")
    if email.strip()
]

def build_hubspot_industries_label_to_value_map():
    url = "https://api.hubapi.com/crm/v3/properties/2-54755382/industry"
    token = os.getenv("HUBSPOT_AUTHORIZATION")
    if not token:
        print("HUBSPOT_AUTHORIZATION environment variable is not set")
        INDUSTRY_VALUE_TO_LABEL = {}
        return INDUSTRY_VALUE_TO_LABEL

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except requests.RequestException as e:
        body = getattr(res, "text", "")
        print(f"Error calling HubSpot: {e} - body: {body}")
        INDUSTRY_VALUE_TO_LABEL = {}
        return INDUSTRY_VALUE_TO_LABEL

    data = res.json()

    INDUSTRY_VALUE_TO_LABEL = {
        opt["value"]: opt["label"]
        for opt in data.get("options", [])
        if opt.get("value") and opt.get("label")
    }
    return INDUSTRY_VALUE_TO_LABEL


def get_hubspot_industries() -> List[str]:
    url = "https://api.hubapi.com/crm/v3/properties/2-54755382/industry"
    token = os.getenv("HUBSPOT_AUTHORIZATION")
    if not token:
        print("HUBSPOT_AUTHORIZATION environment variable is not set")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except requests.RequestException as e:
        body = getattr(res, "text", "")
        print(f"Error calling HubSpot: {e} - body: {body}")
        return []

    data = res.json()
    # HubSpot returns options under: data["options"] (list of {label, value, ...})
    return [opt["label"] for opt in data.get("options", []) if opt.get("label")]

industries =  get_hubspot_industries()
ELIGIBILITY_SCHEMA = build_eligibility_schema(industries)
# Solicitations are judged by a separate prompt/schema pair: the news gate
# rejects government buyers outright, which would fail nearly every SAM.gov
# notice. See prompts_solicitation.py for the reasoning.
SOLICITATION_SCHEMA = build_solicitation_schema(industries)

def get_hubspot_raw_industry_team_mappings():
    url = "https://api.hubapi.com/crm/v3/objects/2-54755382?limit=100&properties=industry,team"
    token = os.getenv("HUBSPOT_AUTHORIZATION")
    if not token:
        print("HUBSPOT_AUTHORIZATION environment variable is not set")
        return []
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    res = requests.get(url, headers=headers)
    try:
        res.raise_for_status()
    except requests.HTTPError as e:
        print(f"Error calling HubSpot: {e} - body: {res.text}")
        return []
    data = res.json()
    return data


def build_industry_to_teams_map(raw_industry_team_mappings: dict) -> Dict[str, Set[str]]:
    industry_to_teams_map: Dict[str, Set[str]] = {}
    print("Inside build_industry_to_teams_map")
    for record in raw_industry_team_mappings.get("results", []) or []:
        print("Got record")
        props = record.get("properties", {}) or {}

        industry_value = props.get("industry")  # e.g. "HRS000"
        team_value = props.get("team")          # e.g. "team_id_58816923"
        print("Props:")
        print(json.dumps(props, indent=2))
        if not industry_value or not team_value:
            continue

        industry_label = INDUSTRY_VALUE_TO_LABEL.get(industry_value)
        if not industry_label:
            # Skip if we can't translate internal value -> label
            # (or set industry_label = industry_value if you'd rather keep it)
            continue

        team_id = team_value.removeprefix("team_id_")
        print(f"Got industr label {industry_label} for team_id {team_id}")

        industry_to_teams_map.setdefault(industry_label, set()).add(team_id)

    return industry_to_teams_map

def bucket_articles_by_team(
    articles: List[dict],
    industry_to_teams_map: Dict[str, Set[str]],
) -> Dict[str, List[dict]]:
    team_buckets: Dict[str, List[dict]] = defaultdict(list)
    print("articles_receved_for_buckets: ")
    # default=str so a non-serializable value can never crash the run here.
    print(json.dumps(articles, indent=2, default=str))
    for article in articles:
        # Local set for this article only
        team_buckets_to_add_article_to: Set[str] = set()

        # Get industries from nested structure
        industries = article.get("industries", []) or []

        # For each industry, look up teams in the map
        for industry in industries:
            teams_for_industry = industry_to_teams_map.get(industry)
            if not teams_for_industry:
                continue
            # Add all team_ids for this industry to the local set
            team_buckets_to_add_article_to.update(teams_for_industry)

        # Once we've collected all teams for this article,
        # add the article to each of those buckets
        for team_id in team_buckets_to_add_article_to:
            team_buckets[team_id].append(article)
    
    return dict(team_buckets)

# HTML CLEANUP
def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    return text

def call_gate(
    text: str,
    article_link: Optional[str],
    title: str,
    document_type: str = "news",
) -> Dict[str, Any]:
    """Call the Responses API with a JSON Schema (Structured Outputs).

    `document_type` selects the prompt/schema pair. It defaults to "news" so
    the six existing scrapers keep their exact behavior; only documents a
    scraper explicitly marks "solicitation" take the SAM.gov path.
    """
    if document_type == "solicitation":
        system_prompt = SOLICITATION_GATE_SYSTEM
        user_template = SOLICITATION_GATE_USER_TMPL
        schema = SOLICITATION_SCHEMA
    else:
        system_prompt = GATE_SYSTEM
        user_template = GATE_USER_TMPL
        schema = ELIGIBILITY_SCHEMA

    prompt = user_template.format(
        text=text, article_link=article_link or "", title=title, industries=industries
    )
    rsp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": schema
        }
    )
    message = rsp.choices[0].message
    #print("Message: ")
    #print(json.dumps(message.model_dump(), indent=2))
    return json.loads(message.content)

def test_run_eligibility_gate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist each scraped document, enrich it, and persist the signal.

    By the time a document reaches here it already carries every raw_documents
    field, so both rows are written from this one loop. Order matters: the raw
    row is committed *before* call_gate, so an OpenAI outage or a mid-loop
    crash still leaves the scrape on record for the next run to pick up.

    Returns the AI's `extracted` block as before -- with enriched_document_id
    stamped on so team bucketing can write document_team_buckets later.
    """
    results: List[Dict[str, Any]] = []
    gate_failures: List[str] = []

    with db.connection() as conn:
        # Not fetch_enriched_urls: solicitations get amended in place, so a
        # url-only skip would silently drop the amendment that finally states
        # the scope. News documents still skip on url alone.
        already_enriched = db.fetch_already_enriched(conn, items)
        if already_enriched:
            print(f"Skipping {len(already_enriched)} document(s) enriched on a previous run")

        for obj in items:
            url = (obj.get("url") or "").strip()
            if url in already_enriched:
                continue

            content = obj.get("content", "")
            link = obj.get("url", None)
            title = obj.get("title", "")
            print(f"Analyzing {title}...")

            raw_document_id = db.upsert_raw_document(conn, obj)

            try:
                gate_response = call_gate(
                    content, link, title, document_type=obj.get("document_type") or "news"
                )
            except Exception as exc:
                # One bad document must not cost us the rest of the run. A
                # single SAM.gov day is ~284 gate calls, so a transient rate
                # limit or timeout is a matter of when, not if -- and an
                # unhandled one here would silently truncate the digest at
                # whatever document it happened to hit.
                #
                # Safe to skip: the raw row is already committed above and no
                # enriched row is written, so fetch_already_enriched will not
                # skip this document and the next run picks it up.
                gate_failures.append(title or url)
                print(f"Gate call FAILED for {url}: {exc}")
                continue

            extracted = gate_response.setdefault("extracted", {})
            eligible = bool(gate_response.get("eligible"))
            confidence = float(gate_response.get("confidence", 0.0))

            try:
                # Stored as str, not UUID: this dict is JSON-dumped for debug
                # output and rendered into the email templates.
                extracted["enriched_document_id"] = str(
                    db.insert_enriched_document(
                        conn, obj, gate_response, raw_document_id=raw_document_id
                    )
                )
            except Exception as exc:
                # A persistence failure shouldn't cost us the digest for the
                # rest of the run -- log it and keep going.
                conn.rollback()
                print(f"Failed to persist enriched document for {url}: {exc}")

            if eligible and confidence >= CONFIDENCE_THRESHOLD:
                results.append(extracted)
            else:
                FILTERED_RESULTS.append(extracted)

    if gate_failures:
        # Surfaced rather than swallowed: a short digest with no explanation is
        # indistinguishable from a quiet day. These retry on the next run.
        print(
            f"Gate failed on {len(gate_failures)} document(s) -- they were skipped "
            "and will be retried next run:"
        )
        for name in gate_failures[:10]:
            print(f"  - {name}")
        if len(gate_failures) > 10:
            print(f"  ... and {len(gate_failures) - 10} more")

    return results

def resolve_email_recipients(to_emails: Iterable[str]) -> List[str]:
    """Final say on who actually receives a message.

    Development redirects everything to DEV_EMAIL_RECIPIENT, so a test run can
    never reach a real sales team no matter what the caller passed in.

    An empty intended list sends nothing in either mode -- otherwise a dev run
    would deliver mail that production would have skipped, which defeats the
    point of testing against it.
    """
    intended = sorted({email.strip() for email in to_emails if email and email.strip()})
    if not intended:
        return []
    if IS_PRODUCTION:
        return intended
    return [DEV_EMAIL_RECIPIENT] if DEV_EMAIL_RECIPIENT else []


def _dev_mode_banner(intended: List[str]) -> str:
    """Show who the message would have reached in production."""
    listed = ", ".join(intended) if intended else "(no recipients resolved)"
    return (
        '<div style="background:#fff3cd;border:1px solid #ffe08a;padding:10px 12px;'
        'margin-bottom:14px;font-family:sans-serif;font-size:13px;color:#5c4400;">'
        "<strong>DEVELOPMENT MODE</strong> &mdash; redirected to you. "
        f"In production this would have gone to: {listed}"
        "</div>"
    )


def send_html_email(to_emails: List[str], subject: str, html_body: str, from_email: str = "marketing@lavi.com", hostname: str = "domain.lavi.com") -> None:
    intended = sorted({email.strip() for email in to_emails if email and email.strip()})
    recipients = resolve_email_recipients(intended)

    if not recipients:
        print(f"No recipients resolved for {subject!r} - skipping send.")
        return

    if not IS_PRODUCTION:
        subject = f"[DEV] {subject}"
        html_body = _dev_mode_banner(intended) + html_body

    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(hostname, 25) as server:
        server.send_message(msg)

    if IS_PRODUCTION:
        print(f"Sent {subject!r} to {len(recipients)} recipient(s).")
    else:
        print(
            f"[DEV] Sent {subject!r} to {recipients[0]} "
            f"(production would have sent to {len(intended)}: {', '.join(intended) or 'none'})"
        )


def get_all_teams():
    url = "https://api.hubapi.com/settings/v3/users/teams"
    token = os.getenv("HUBSPOT_AUTHORIZATION")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    res = requests.get(url, headers=headers)
    data = res.json()
    return data["results"]

def get_user_email(user_id: str) -> str:
    url = f"https://api.hubapi.com/settings/v3/users/{user_id}"
    token = os.getenv("HUBSPOT_AUTHORIZATION")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.get(url, headers=headers)
    resp.raise_for_status()

    data = resp.json()
    print("Email from hubspot: ")
    print(data.get("email"))
    print(json.dumps(data, indent=2))
    return data.get("email")  # HubSpot returns "email" at root level

def test_send_emails_to_teams(team_buckets):
    all_teams = get_all_teams()
    print("Got teams")
    #print(json.dumps(all_teams, indent=2))
    teams_by_id = {team["id"]: team for team in all_teams}
    team_id_to_name = {team["id"]: team["name"] for team in all_teams}

    for team_id, articles in team_buckets.items():
        team = teams_by_id.get(team_id)
        if not team:
            print(f"Team ID {team_id} not found in HubSpot teams list.")
            continue
        print(f"Current team: {team}")
        # Collect all user IDs for that team
        user_ids = set(team.get("userIds", [])) | set(team.get("secondaryUserIds", []))

        # Get all user emails
        emails = set()
        for uid in user_ids:
            email = get_user_email(uid)
            if email:
                emails.add(email)
        if not emails:
            print(f"No users found for team {team_id}, skipping email.")
            continue
        # No hardcoded QA additions here -- development redirects everything to
        # DEV_EMAIL_RECIPIENT inside send_html_email, and production should
        # reach the real team only.
        print("Emails found: ")
        print(json.dumps(sorted(emails), indent=2))

        # Send email
        team_name = team_id_to_name[team_id]
        count = len(articles)
        plural = "Opportunities" if count != 1 else "Opportunity"
        subject = f"[Business Signals] Team {team_name} - {count} New {plural} Identified"
        template = Template(test_template_str)
        print(json.dumps(articles, indent=2))
        html_body = template.render(
            title= subject,
            intro_text="Below are newly identified business signals that may indicate near-term opportunities for your team.",
            rows=articles
        )
        send_html_email(
            to_emails=list(emails),
            subject=subject,
            html_body=html_body,
        )

        print(f"Digest for team {team_id} dispatched ({len(emails)} intended recipient(s)).")

def send_filtered_email():
    print("Going to send out filtered email...")
    emails = set(QA_EMAIL_RECIPIENTS)
    count = len(FILTERED_RESULTS)
    plural = "Articles" if count != 1 else "Article"
    subject = f"[QA Review] {count} {plural} Filtered by Eligiblity Gate"
    template = Template(test_template_str)
    html_body = template.render(
        title = subject,
        intro_text = "The AI model has evaluated and filtered out the articles below based on current eligibility criteria. Please review to identify potential false negatives or rule gaps.",
        rows=FILTERED_RESULTS
    )
    send_html_email(
        to_emails = list(emails),
        subject=subject,
        html_body=html_body
    )

if __name__ == "__main__":
    INDUSTRY_VALUE_TO_LABEL = build_hubspot_industries_label_to_value_map()
    print("Starting scrapers...")
    print("Starting eventregistry")
    docs_event_registry = get_eventregistry_documents()
    print("Starting airport industry")
    docs_airport_industry = get_airport_industry_documents()
    print("Starting chainstoreage")
    docs_chainstoreage_docs = get_chainstoreage_documents()
    print("Starting NACS")
    docs_nacs = get_nacs_documents()
    print("Starting NAHB")
    docs_nahb = get_nahb_documents()
    print("Starting PR Newswire")
    docs_prnewswire = get_prnewswire_documents()
    print("Starting SAM.gov")
    # days_back=1 is required, not a preference: the bulk extract is cut
    # nightly around 03:30 UTC and contains data through the *previous* day,
    # so days_back=0 legitimately returns zero rows.
    #
    # Isolated so a SAM.gov failure cannot cost us the news digest. The
    # scraper raises rather than returning a partial date range, and that
    # deserves a loud log line, not a dead pipeline.
    try:
        docs_samgov = get_samgov_documents(days_back=1)
    except Exception as exc:
        print(f"SAM.gov scrape FAILED, continuing without it: {exc}")
        docs_samgov = []
    docs = docs_event_registry + docs_airport_industry + docs_chainstoreage_docs + docs_nacs + docs_nahb + docs_prnewswire + docs_samgov
    print("Scrapers done!")
    
    out = test_run_eligibility_gate(docs)
    raw_industry_team_mappings = get_hubspot_raw_industry_team_mappings()
    industry_to_teams_map = build_industry_to_teams_map(raw_industry_team_mappings)
    team_buckets = bucket_articles_by_team(out, industry_to_teams_map)
    test_send_emails_to_teams(team_buckets)
    send_filtered_email()