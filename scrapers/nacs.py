# Standard library
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin

# Third-party
import langid
import tldextract
from requests.utils import requote_uri

from dotenv import load_dotenv

# Local imports
import date_utils as du
from utils.scrapingbee import get_scrapingbee_client, get_soup

load_dotenv()

# convenience.org was rebuilt as a client-rendered app in 2026. The old
# /Media/Daily/Today endpoint and its .main-content / a.homelinks markup are
# both gone; news now lives under /stay-current/news and requires render_js.
# Deliberately does NOT fall back to the old NACS_TODAY_URL: that variable
# holds the dead /Media/Daily/Today endpoint, which still returns 200 with an
# empty shell, so falling back to it fails silently with zero articles.
NACS_NEWS_URL = os.getenv("NACS_NEWS_URL") or "https://www.convenience.org/stay-current/news"
BASE_URL = "https://www.convenience.org"
SCRAPER_SOURCE = "nacs"

# Articles live at /stay-current/news/{yyyy}/{month-name}/{d}/{slug}, so the
# publish date is recoverable from the URL alone. That lets us date-filter
# before fetching any article page -- each one costs a rendered request.
_ARTICLE_PATH = re.compile(r"/stay-current/news/(\d{4})/([A-Za-z]+)/(\d{1,2})/.+")

# "Aug 03, 2026 | 2 min read" byline that sits above the body copy.
_BYLINE = re.compile(r"^\w{3,9}\s+\d{1,2},\s+\d{4}\s*\|\s*\d+\s*min read$", re.IGNORECASE)
_CHROME_TEXT = {"share", "read more", "read here"}


def _parse_article_date(href: str) -> Optional[datetime]:
    """Recover the publish date from the article path. None if it doesn't match."""
    match = _ARTICLE_PATH.search(href)
    if not match:
        return None

    year, month_name, day = match.groups()
    for fmt in ("%B", "%b"):
        try:
            month = datetime.strptime(month_name.strip().capitalize(), fmt).month
            break
        except ValueError:
            continue
    else:
        return None

    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def get_nacs_meta(feed_url: str = NACS_NEWS_URL, days_back: int = 0) -> List[Dict]:
    """Collect today's article URLs and titles from the news index.

    Titles are taken from the longest anchor text per URL: each card renders
    both a title link and a "Read More" link to the same href, and the title
    is always the longer of the two.
    """
    client = get_scrapingbee_client()
    soup = get_soup(client, feed_url, render_js=True, wait=9000)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()

    # url -> (published_at, best title seen so far)
    candidates: Dict[str, tuple] = {}
    for link_tag in soup.select('a[href*="/stay-current/news/"]'):
        href = (link_tag.get("href") or "").strip()
        published_at = _parse_article_date(href)
        if not published_at:
            continue
        if published_at.date() < cutoff:
            continue

        document_url = requote_uri(urljoin(BASE_URL, href))
        title = link_tag.get_text(" ", strip=True)
        if title.lower() in _CHROME_TEXT:
            title = ""

        existing = candidates.get(document_url)
        if existing is None or len(title) > len(existing[1]):
            candidates[document_url] = (published_at, title)

    documents: List[Dict] = []
    for document_url, (published_at, title) in candidates.items():
        documents.append(
            {
                "published_at": published_at,
                "discovered_at": du.get_now_utc(),
                "title": title,
                "url": document_url,
                "scraper_source": SCRAPER_SOURCE,
                "source_domain": tldextract.extract(BASE_URL).domain,
                "source_name": "NACS",
                "document_type": "news",
            }
        )

    documents.sort(key=lambda d: d["published_at"], reverse=True)
    return documents


def get_nacs_content(url: str) -> Dict[str, Optional[str]]:
    """Fetch one article. Returns its body text and the <h1> title."""
    client = get_scrapingbee_client()
    soup = get_soup(client, url, render_js=True, wait=6000)

    # Ordered most- to least-specific. Never select on text volume alone: the
    # OneTrust cookie panels (.ot-*) carry more <p> text than the article does.
    wrapper = None
    for selector in (".article-details-page-wrapper", ".main_card", ".content"):
        wrapper = soup.select_one(selector)
        if wrapper:
            break
    if not wrapper:
        return {"content": "", "title": None}

    content_parts: List[str] = []
    for p_tag in wrapper.select("p"):
        txt = p_tag.get_text(" ", strip=True)
        if not txt or _BYLINE.match(txt) or txt.lower() in _CHROME_TEXT:
            continue
        content_parts.append(" ".join(txt.split()))

    h1_tag = soup.select_one("h1")
    return {
        "content": "\n\n".join(content_parts),
        "title": h1_tag.get_text(" ", strip=True) if h1_tag else None,
    }


def get_nacs_documents(days_back: int = 0) -> List[Dict]:
    documents = get_nacs_meta(days_back=days_back)

    enriched: List[Dict] = []
    for doc in documents:
        article = get_nacs_content(doc["url"])
        content = article["content"]
        if not content:
            print(f"NACS: no content extracted, skipping {doc['url']}")
            continue

        # The article <h1> beats the index anchor text, which can be a CTA.
        if article["title"]:
            doc["title"] = article["title"]

        language, confidence = langid.classify(content)
        doc["content"] = content
        doc["language"] = language
        doc["language_confidence"] = confidence
        enriched.append(doc)

    print(f"Total articles from NACS: {len(enriched)}")
    return enriched
