from typing import Dict, List

import date_utils as du
def extract_documents(res: Dict) -> List[Dict]:
    return [
        {
            "published_at": article.get("dateTime") or article.get("date"),
            "discovered_at": du.DATE_NOW,
            "title": article.get("title"),
            "url": article.get("url"),
            "content": article.get("body"),
            "source_name": article.get("source", {}).get("title"),
            "source_domain": article.get("source", {}).get("uri"),
            "document_type": "news"
        }
        for article in res.get("results", [])
        if not article.get("isDuplicate", False)
    ]