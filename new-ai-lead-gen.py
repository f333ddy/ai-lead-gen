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
from eligibility_schema import build_eligibility_schema
from prompts import GATE_SYSTEM, GATE_USER_TMPL
from event_registry import ARTICLES_PAYLOAD
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from email.utils import parsedate_to_datetime

from scrapers.eventregistry import get_eventregistry_documents
from scrapers.airport_industry_news import get_airport_industry_documents
from scrapers.chainstoreage import get_chainstoreage_documents
from scrapers.nacs import get_nacs_documents
from scrapers.nahb import get_nahb_documents
from scrapers.prnewswire import get_prnewswire_documents

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
    print(json.dumps(articles, indent=2))
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

def call_gate(text: str, article_link: Optional[str], title: str) -> Dict[str, Any]:
    """Call the Responses API with a JSON Schema (Structured Outputs)."""
    prompt = GATE_USER_TMPL.format(text=text, article_link=article_link or "", title=title, industries=industries)
    rsp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": GATE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": ELIGIBILITY_SCHEMA
        }
    )
    message = rsp.choices[0].message
    #print("Message: ")
    #print(json.dumps(message.model_dump(), indent=2))
    return json.loads(message.content)

def test_run_eligibility_gate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for obj in items:
        content = obj.get("content", "")
        link = obj.get("url", None)
        title = obj.get("title", "")
        print(f"Analyzing {title}...")

        #text = html_to_text(raw_html)

        content = call_gate(content, link, title)
        extracted = content.setdefault("extracted", {})
        eligible = bool(content.get("eligible"))
        confidence = float(content.get("confidence", 0.0))

        if eligible and confidence >= CONFIDENCE_THRESHOLD:
            results.append(extracted)
        else:
            FILTERED_RESULTS.append(extracted)
    return results

def send_html_email(to_emails: List[str], subject: str, html_body: str, from_email: str = "marketing@lavi.com", hostname: str = "domain.lavi.com") -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(hostname, 25) as server:
        server.send_message(msg)


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
        #emails.add("perryk@lavi.com")
        #emails.add("will.geller@lavi.com")
        emails.add("federico.aguilar@lavi.com")
        print("Emails found: ")
        print(json.dumps(list(emails), indent=2))

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

        print(f"Email sent to team {team_id} ({len(emails)} recipients).")

def send_filtered_email():
    print("Going to send out filtered email...")
    emails = set()
    emails.add("federico.aguilar@lavi.com")
    emails.add("will.geller@lavi.com")
    emails.add("perryk@lavi.com")
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
    docs = docs_event_registry + docs_airport_industry + docs_chainstoreage_docs + docs_nacs + docs_nahb + docs_prnewswire

    #docs = docs_event_registry + docs_airport_industry + docs_chainstoreage_docs + docs_nacs + docs_nahb
    print("Scrapers done!")
    
    out = test_run_eligibility_gate(docs)
    raw_industry_team_mappings = get_hubspot_raw_industry_team_mappings()
    industry_to_teams_map = build_industry_to_teams_map(raw_industry_team_mappings)
    team_buckets = bucket_articles_by_team(out, industry_to_teams_map)
    test_send_emails_to_teams(team_buckets)
    send_filtered_email()
