import os
import re
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any, Optional, Set, Iterable
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()
# pip install openai beautifulsoup4
from openai import OpenAI
from pydantic import BaseModel
from datetime import date, timedelta
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

# Global Variables
KEYWORDS = [
    # funding signals
    "appropriation", "allocation", "allocated", "funding approved",
    "grant awarded", "grant approval", "bond measure", "capital budget",
    "capital improvement plan", "CIP",
    # project signals
    "renovation", "remodel", "expansion", "build-out", "facility upgrade", "project"
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
print("Industries: ")
print(json.dumps(industries, indent=2))
ELIGIBILITY_SCHEMA = build_eligibility_schema(industries)


def update_payload_api_key():
    api_key = os.getenv("EVENTREGISTRY_API_KEY")
    ARTICLES_PAYLOAD["apiKey"] = api_key

def update_payload_date_range():
    today = date.today()
    to_date = today
    from_date = today - timedelta(days=0)
    date_range = {
        "dateStart": from_date.isoformat(),
        "dateEnd": to_date.isoformat(),
        "lang": "eng"
    }

    ARTICLES_PAYLOAD["query"]["$query"]["$and"].append(date_range)

def get_eventregistry_articles() -> List[Dict]:
    url = "https://eventregistry.org/api/v1/article/getArticles"
    update_payload_api_key()
    update_payload_date_range()

    def fetch_articles_page(page: int) -> Dict:
        payload = copy.deepcopy(ARTICLES_PAYLOAD)
        payload["articlesPage"] = page
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("articles", {})

    def extract_articles(articles_obj: Dict) -> List[Dict]:
        print(f"Fetched page {articles_obj.get('page', 1)} of {articles_obj.get('pages', 1)}")
        return [
            {
                "title": a.get("title"),
                "link": a.get("url"),
                "description": a.get("body"),
            }
            for a in articles_obj.get("results", [])
        ]

    first_articles = fetch_articles_page(1)
    all_articles: List[Dict] = extract_articles(first_articles)
    total_pages = first_articles.get("pages", 1)

    for page in range(2, total_pages + 1):
        all_articles.extend(extract_articles(fetch_articles_page(page)))

    print(f"Total article count received: {len(all_articles)}")    
    # return all_articles
    #Remove when moving to production
    return all_articles[:10]

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

def send_html_email(to_emails: List[str], subject: str, html_body: str, from_email: str = "marketing@lavi.com", hostname: str = "domain.lavi.com") -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(hostname, 25) as server:
        server.send_message(msg)


def run_eligibility_gate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for obj in items:
        raw_html = obj.get("description", "")
        link = obj.get("link", None)
        title = obj.get("title", "")

        text = html_to_text(raw_html)

        # Call the gate
        content = call_gate(text, link, title)
        # Enforce business policy / defaults
        extracted = content.setdefault("extracted", {})
        # extracted.setdefault("article_link", link)
        eligible = bool(content.get("eligible"))
        confidence = float(content.get("confidence", 0.0))
        '''
        print("Content")
        print(json.dumps(content, indent=2))
        '''
        print("...parsing")
        # Only include decisions that are eligible AND above confidence threshold
        if eligible and confidence >= CONFIDENCE_THRESHOLD:
            print("Eligible content:")
            print(json.dumps(extracted, indent=2))
            #decision = {"content": content}
            results.append(extracted)

    return results

def test_run_eligibility_gate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for obj in items:
        raw_html = obj.get("description", "")
        link = obj.get("link", None)
        title = obj.get("title", "")

        text = html_to_text(raw_html)

        # Call the gate
        content = call_gate(text, link, title)
        # Enforce business policy / defaults
        extracted = content.setdefault("extracted", {})
        # extracted.setdefault("article_link", link)
        eligible = bool(content.get("eligible"))
        confidence = float(content.get("confidence", 0.0))
        print("Results of AI Analysis")
        print(json.dumps(content, indent=2))
        extracted["is_filtered"] = True
        extracted["is_included"] = False
        if eligible:
            #print("Eligible content:")
            #print(json.dumps(content, indent=2))
            extracted["is_filtered"] = False
        results.append(extracted)
    return results

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

def test_bucket_articles_by_team(
    articles: List[dict],
    industry_to_teams_map: Dict[str, Set[str]],
) -> Dict[str, List[dict]]:
    team_buckets: Dict[str, List[dict]] = defaultdict(list)
    all_team_ids: Set[str] = set()
    for teams in industry_to_teams_map.values():
        all_team_ids.update(teams)
    for article in articles:
        industries = article.get("industries", []) or []
        matching_team_ids: Set[str] = set()
        for industry in industries:
            teams_for_industry = industry_to_teams_map.get(industry)
            if teams_for_industry:
                matching_team_ids.update(teams_for_industry)
        for team_id in all_team_ids:
            article_for_team = dict(article)
            article_for_team.setdefault("is_included", False)
            if team_id in matching_team_ids:
                article_for_team["is_included"] = True
            team_buckets[team_id].append(article_for_team)
    for team_id, bucket in team_buckets.items():
        bucket.sort(
            key=lambda a: (
                not a.get("is_included", True),
                a.get("is_filtered", False),   
            )
        )
    return dict(team_buckets)

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
    # return data.get("email")  # HubSpot returns "email" at root level
    return "federico.aguilar@lavi.com" 

def send_emails_to_teams(team_buckets):
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
        # emails.add("perryk@lavi.com")
        print("Emails found: ")
        print(json.dumps(list(emails), indent=2))

        # Send email
        team_name = team_id_to_name[team_id]
        subject = f"[News Digest] Team {team_name}"
        template = Template(template_str)
        html_body = template.render(
            title= subject,
            intro_text="Here is your report:",
            rows=articles
        )
        send_html_email(
            to_emails=list(emails),
            subject=subject,
            html_body=html_body,
        )

        print(f"Email sent to team {team_id} ({len(emails)} recipients).")

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
        print("Emails found: ")
        print(json.dumps(list(emails), indent=2))

        # Send email
        team_name = team_id_to_name[team_id]
        subject = f"[News Digest] Team {team_name}"
        template = Template(test_template_str)
        print(json.dumps(articles, indent=2))
        html_body = template.render(
            title= subject,
            intro_text="Here is your report:",
            rows=articles
        )
        send_html_email(
            to_emails=list(emails),
            subject=subject,
            html_body=html_body,
        )

        print(f"Email sent to team {team_id} ({len(emails)} recipients).")

if __name__ == "__main__":
    INDUSTRY_VALUE_TO_LABEL = build_hubspot_industries_label_to_value_map()
    print("INDUSTRY_VALUE_TO_LABEL")
    print(json.dumps(INDUSTRY_VALUE_TO_LABEL, indent=2))
    # industries = get_hubspot_industries()
    articles = get_eventregistry_articles()
    print("Got all event registry articles")
    # out = run_eligibility_gate(articles)
    out = test_run_eligibility_gate(articles)

    print("out: ")
    print(json.dumps(out, indent=2))
    raw_industry_team_mappings = get_hubspot_raw_industry_team_mappings()
    print("Raw industry teamp mappings: ")
    print(json.dumps(raw_industry_team_mappings, indent=2))
    industry_to_teams_map = build_industry_to_teams_map(raw_industry_team_mappings)
    #print("Industry to teams map: ")
    #print(json.dumps(industry_to_teams_map, indent=2))
    # Returns {"distributor": ["1453", "0549"]}
    # team_buckets = bucket_articles_by_team(out, industry_to_teams_map)
    team_buckets = test_bucket_articles_by_team(out, industry_to_teams_map)
    print("Team buckets: ")
    print(json.dumps(team_buckets, indent=2))
    #How should we handle len(out) == 0? New template or no email?
    #send_emails_to_teams(team_buckets)
    test_send_emails_to_teams(team_buckets)
