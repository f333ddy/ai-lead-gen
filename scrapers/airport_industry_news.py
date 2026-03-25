# Standard library
import os
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urljoin
# Third-party
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import langid
import tldextract
# Local import
from utils.scrapingbee import get_scrapingbee_client, get_soup

load_dotenv()

SCRAPING_BEE_API_KEY = os.getenv("SCRAPING_BEE_API_KEY")
AIRPORT_INDUSTRY_NEWS_URL = os.getenv("AIRPORT_INDUSTRY_NEWS_URL")

def _build_page_url(index_url: str, page: int) -> str:
    if page == 1:
        return index_url.rstrip("/") + "/"
    return f"{index_url.rstrip('/')}/page/{page}/"

def get_airport_industry_news_meta(
    feed_url: str = AIRPORT_INDUSTRY_NEWS_URL
) -> List[Dict]:
    client = get_scrapingbee_client()
    documents: List[Dict] = []
    page = 1

    while True:
        page_url = _build_page_url(feed_url, page)
        soup = get_soup(client, page_url)
        cards = soup.select("ul.cards > li.cards__item")
        if not cards:
            break
        
        stop = False

        for card in cards:
            link_tag = card.select_one("a.cards__link--post[href]")
            time_tag = card.select_one("time.card__timestamp")
            title_tag = card.select_one("h3.cards__heading")
            
            if not link_tag or not title_tag or not time_tag:
                continue
            article_url = urljoin(feed_url, link_tag["href"].strip())
            datetime_str = time_tag.get("datetime", "").strip()

            if not datetime_str:
                continue

            published_at = datetime.fromisoformat(datetime_str)

            if published_at.date() < datetime.now(timezone.utc).date():
                stop = True
                break
            document = {
                "published_at": published_at,
                "discovered_at": datetime.now(timezone.utc),
                "title": title_tag.get_text(" ", strip=True),
                "url": article_url,
                "source_domain": tldextract.extract(feed_url).domain,
                "source_name": "Airport Industry News",
                "document_type": "news"
            }
            documents.append(document)
        
        if stop:
            break
        page += 1
    return documents

def get_airport_industry_news_content(url: str) -> str:
    client = get_scrapingbee_client()
    soup = get_soup(client, url)

    content_parts: List[str] = []
    body = soup.select_one("article.article__body")

    if body:
        for p in body.select("p"):
            txt = p.get_text(" ", strip=True)
            if txt:
                content_parts.append(txt)
    return "\n\n".join(content_parts)

def get_airport_industry_documents(
    feed_url: str = AIRPORT_INDUSTRY_NEWS_URL
) -> List[Dict]:
    documents = get_airport_industry_news_meta(feed_url)

    for document in documents:
        content = get_airport_industry_news_content(document["url"])
        language, confidence = langid.classify(content) if content else ("unknown", 0.0)
        document["content"] = content
        document["language"] = language
        document["language_confidence"] = confidence
    return documents