# Standary library
import os
from datetime import datetime, timezone
# Third-party
import langid
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import tldextract

# Local imports
import date_utils as du
from utils.scrapingbee import get_scrapingbee_client, get_soup

load_dotenv()

SCRAPING_BEE_API_KEY = os.getenv("SCRAPING_BEE_API_KEY")

def get_prnewswire_content(url):
    client = get_scrapingbee_client()
    soup = get_soup(client, url)
    content = "\n\n".join(p.get_text(strip=True) for p in soup.select("section.release-body p"))
    return content

def get_prnewswire_meta(feed_url: str):
    documents = []
    seen_urls = set()
    client = get_scrapingbee_client()
    page = 1
    page_size = 100

    while True:
        print(f"Start to parse page {page}")

        response = client.get(
            f"{feed_url}?page={page}&pagesize={page_size}",
            params={
                "render_js": True,
                "wait": 6000,
            },
        )

        soup = BeautifulSoup(response.text, "html.parser")

        # Adjust selector if needed
        news_cards = soup.select("div.newsCards article, div.newsCards > div")
        if not news_cards:
            break

        stop = False
        page_added = 0

        for news_card in news_cards:
            h3_tag = news_card.select_one("h3")
            time_tag = news_card.select_one("h3 small")
            link_tag = news_card.select_one("a[href]")

            if not h3_tag or not time_tag or not link_tag:
                continue

            time_str = time_tag.get_text(" ", strip=True)

            if not du.is_today_prnewswire_date(time_str):
                stop = True
                break

            # Extract full title from h3 and remove time
            full_text = h3_tag.get_text(" ", strip=True)
            title = full_text.replace(time_str, "", 1).strip()

            url = link_tag.get("href", "").strip()
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            page_added += 1

            document = {
                "published_at": du.format_date_prnewswire(time_str),
                "discovered_at": du.DATE_NOW,
                "title": title,
                "url": url,
                "source_domain": tldextract.extract(feed_url).domain,
                "source_name": "PR Newswire",
                "document_type": "news",
            }

            documents.append(document)

        if stop or page_added == 0:
            break

        print(f"Finished parsing page {page}")
        page += 1
        if page == 2:
            break
    return documents

def get_prnewswire_documents():
    article_list_url = "https://www.prnewswire.com/news-releases/news-releases-list/"
    documents = get_prnewswire_meta(article_list_url)

    filtered_documents = []

    for document in documents:
        content = get_prnewswire_content(document["url"])
        if not content:
            continue

        language, confidence = langid.classify(content)

        if language != "en":
            continue

        document["content"] = content
        document["language"] = language
        document["language_confidence"] = confidence

        filtered_documents.append(document)

    return filtered_documents