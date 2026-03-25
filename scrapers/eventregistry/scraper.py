# Standard library
from typing import Dict, List
# Third-party
# None
# Local imports
from .client import fetch_articles_page
from .payloads import build_articles_payload
from .transforms import extract_documents

def get_eventregistry_documents(days_back: int = 0) -> List[Dict]:
    first_payload = build_articles_payload(page=1, days_back=days_back)
    first_articles = fetch_articles_page(first_payload)

    all_documents: List[Dict] = extract_documents(first_articles)
    total_pages = first_articles.get("pages", 1)
    
    for page in range(2, total_pages + 1):
        payload = build_articles_payload(page=page, days_back=days_back)
        page_articles = fetch_articles_page(payload)
        all_documents.extend(extract_documents(page_articles))
    return all_documents