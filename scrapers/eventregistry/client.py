# Standard library
import requests
import os
from datetime import datetime, timezone
# Third-party
from dotenv import load_dotenv

load_dotenv()

EVENT_REGISTRY_ARTICLES_URL = os.getenv("EVENT_REGISTRY_ARTICLES_URL")
DATE_NOW = datetime.now(timezone.utc)

def fetch_articles_page(payload: dict) -> dict:
    response = requests.post(EVENT_REGISTRY_ARTICLES_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json().get("articles", {})