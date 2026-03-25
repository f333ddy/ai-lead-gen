# Standard library
import os
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urljoin

# Third-party
import langid
import tldextract
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Local imports
import date_utils as du
from utils.scrapingbee import get_scrapingbee_client, get_soup

load_dotenv()

NACS_TODAY_URL = os.getenv("NACS_TODAY_URL")

def get_nacs_meta(
        nacs_today_url: str = NACS_TODAY_URL,
) -> List[Dict]:
    client = get_scrapingbee_client()
    soup = get_soup(client, 
                    nacs_today_url, 
                    render_js=False, 
                    wait=1000)

    documents: List[Dict] = []
    seen_urls = set()
    paragraphs = soup.select(".main-content p")
    # published_at always at midnight, per NACS schema.org found on news pages
    published_at = du.get_today_midnight_formatted()
    for p_tag in paragraphs:
        link_tag = p_tag.select_one("a.homelinks[href]")
        if not link_tag:
            continue
        document_url = link_tag.get("href", "").strip()

        if document_url in seen_urls:
            continue
        title = link_tag.get_text(" ", strip=True)
        if not document_url or not title:
            continue

        seen_urls.add(document_url)

        document = {
            "published_at": published_at,
            "discovered_at": du.DATE_NOW,
            "title": title,
            "url": document_url,
            "source_domain": tldextract.extract(NACS_TODAY_URL).domain,
            "source_name": "NACS",
            "document_type": "news"
        }
        documents.append(document)

    return documents

def get_nacs_content(url: str) -> str:
    client = get_scrapingbee_client()
    soup = get_soup(client, 
                    url, 
                    render_js=False, 
                    wait=1000)

    content_div = soup.select_one("div.nacs-page-content")
    if not content_div:
        return ""

    content_parts: List[str] = []
    for p_tag in content_div.select("p"):
        txt = p_tag.get_text(" ", strip=True)
        if txt:
            content_parts.append(txt)
    return "\n\n".join(content_parts)

def get_nacs_documents() -> List[Dict]:
    documents = get_nacs_meta()
    for doc in documents:
        content = get_nacs_content(doc["url"])
        language, confidence = langid.classify(content) if content else ("unknown", 0.0)

        doc["content"] = content
        doc["language"] = language
        doc["language_confidence"] = confidence
    
    print(f"Total articles from NACS: {len(documents)}")
    return documents