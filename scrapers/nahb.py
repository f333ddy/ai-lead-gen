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
from utils.scrapingbee import get_scrapingbee_client, get_soup
import date_utils as du
from datetime import date

load_dotenv()

NAHB_FEED_URL = os.getenv("NAHB_FEED_URL")

def _build_page_url(base_url: str, first: int) -> str:
    if first == 0:
        return base_url
    return f"https://www.nahb.org/blog/blog-search#first={first}&sort=%40cz95xpublishz95xdate%20descending&numberOfResults=25"
    

def get_nahb_meta(feed_url: str) -> List[Dict]:
    client = get_scrapingbee_client()
    discovered_at = datetime.now(timezone.utc)
    documents: List[Dict] = []
    seen_urls = set()
    first = 0
    page_size = 25

    while True:
        page_url = _build_page_url(feed_url, first)
        soup = get_soup(client, page_url, render_js=True, wait=3000)
        
        cards = soup.select("div.search-card__text-content")
        if not cards: 
            break
        stop = False
        page_added = 0

        for card in cards:
            link_tag = card.select_one("div.search-card__title a[href]")
            date_tag = card.select_one("span.search-card__pub-date")

            if not link_tag or not date_tag:
                continue

            href = link_tag.get("href", "").strip()
            title = link_tag.get_text(" ", strip=True)
            date_text = date_tag.get_text(" ", strip=True)

            if not href or not title or not date_text:
                continue
            
            
            if not du.is_nahb_date(date_text):
                stop = True
                break
            
            document_url = urljoin("https://www.nahb.org", href)

            if document_url in seen_urls:
                continue
            seen_urls.add(document_url)

            page_added += 1

            document = {
                "published_at": du.format_date_nahb(date_text),
                "discovered_at": discovered_at,
                "title": title,
                "url": document_url,
                "source_domain": tldextract.extract(feed_url).domain,
                "source_name": "NAHB",
                "document_type": "news"
            }
            documents.append(document)
        if stop or page_added == 0:
            break
        first += page_size
    return documents

def get_nahb_content(url: str) -> Dict:
    client = get_scrapingbee_client()
    soup = get_soup(client, url, render_js=True, wait=3000)
    content = "\n\n".join(p.get_text(strip=True) for p in soup.select(".rich-text p"))
    return content

def get_nahb_documents() -> List[Dict]:
    documents = get_nahb_meta(NAHB_FEED_URL)

    for doc in documents:
        content = get_nahb_content(doc["url"])
        language, confidence = langid.classify(content)
        doc["content"] = content
        doc["language"] = language
        doc["language_confidence"] = confidence
    return documents
