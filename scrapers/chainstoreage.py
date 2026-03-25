# Standard library
import json
import os
import re
from typing import Dict, List
from urllib.parse import urljoin

# Third-party
import langid
import tldextract
from dotenv import load_dotenv

# Local imports
from utils.scrapingbee import get_scrapingbee_client, get_soup
import date_utils as du

load_dotenv()

FEED_URL = os.getenv("CHAIN_STORE_AGE_FEED_URL")


def _build_page_url(base_url: str, page: int) -> str:
    if page == 1:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}page={page}"


def _extract_title(document_soup, fallback_title_tag) -> str:
    page_title_tag = document_soup.select_one("h1")
    if page_title_tag:
        return page_title_tag.get_text(" ", strip=True)
    return fallback_title_tag.get_text(" ", strip=True)


def _extract_date_published(document_soup) -> str:
    for script in document_soup.select('script[type="application/ld+json"]'):
        raw = script.get_text(strip=True)
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]

        for item in candidates:
            if not isinstance(item, dict):
                continue

            date_published = item.get("datePublished")
            if date_published:
                return str(date_published).strip()

    return ""


def _extract_content(document_soup) -> str:
    content_parts: List[str] = []

    for node in document_soup.select("div.eiq-paragraph p, div.eiq-paragraph li"):
        txt = node.get_text(" ", strip=True)
        if txt:
            content_parts.append(" ".join(txt.split()))

    if content_parts:
        return "\n\n".join(content_parts)

    for script in document_soup.select('script[type="application/ld+json"]'):
        raw = script.get_text(strip=True)
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]

        for item in candidates:
            if not isinstance(item, dict):
                continue

            article_body = item.get("articleBody")
            if not article_body:
                continue

            content = str(article_body)
            content = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", content)
            content = " ".join(content.split())
            return content

    return ""


def get_chainstoreage_documents() -> List[Dict]:
    client = get_scrapingbee_client()
    documents: List[Dict] = []
    seen_urls = set()
    page = 1

    while True:
        page_url = _build_page_url(FEED_URL, page)
        soup = get_soup(client, page_url, render_js=True, wait=6000, premium_proxy=True)

        # Support both old teaser cards and newer card__content cards
        cards = soup.select("div.teaser-card__content, div.card__content")
        if not cards:
            break

        stop = False
        page_added = 0

        for card in cards:
            link_tag = card.select_one("a[href]")
            title_tag = card.select_one(
                "h3.teaser-card__heading, h2.card__heading, h2, h3"
            )

            if not link_tag or not title_tag:
                continue

            href = link_tag.get("href", "").strip()
            if not href:
                continue

            document_url = urljoin(FEED_URL, href)

            if document_url in seen_urls:
                continue

            document_soup = get_soup(client, document_url, render_js=True, wait=6000)

            date_text = _extract_date_published(document_soup)
            if not date_text:
                continue

            if not du.is_today_chainstoreage_date(date_text):
                stop = True
                break

            title = _extract_title(document_soup, title_tag)
            content = _extract_content(document_soup)
            language, confidence = (
                langid.classify(content) if content else ("unknown", 0.0)
            )

            seen_urls.add(document_url)
            page_added += 1

            document = {
                "published_at": du.format_date_chainstoreage(date_text),
                "discovered_at": du.DATE_NOW,
                "title": title,
                "url": document_url,
                "content": content,
                "language": language,
                "language_confidence": confidence,
                "source_domain": tldextract.extract(FEED_URL).domain,
                "source_name": "Chain Store Age",
                "document_type": "news",
            }

            documents.append(document)

        if stop or page_added == 0:
            break

        page += 1

    return documents