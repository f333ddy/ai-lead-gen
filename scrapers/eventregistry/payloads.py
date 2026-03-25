# Standard library
import copy
import os
from datetime import date, timedelta

ARTICLES_PAYLOAD = {
    "query": {
        "$query": {
            "$and": [
                {
                    "$or": [
                        {"sourceUri": "constructiondive.com"},
                        {"sourceUri": "fooddive.com"},
                        {"sourceUri": "grocerydive.com"},
                        {"sourceUri": "manufacturingdive.com"},
                        {"sourceUri": "restaurantdive.com"},
                        {"sourceUri": "retaildive.com"},
                        {"sourceUri": "supplychaindive.com"},
                        {"sourceUri": "truckingdive.com"}
                    ]
                }
            ]
        }
    },
    "dataType": ["news", "pr"],
    "forceMaxDateTimeWindow": 7,
    "resultType": "articles",
    "articlesSortBy": "date",
    "apiKey": "",
}

'''
ARTICLES_PAYLOAD = {
    "query": {
        "$query": {
            "$and": [
                {
                    "$or": [
                        {"sourceUri": "bankingdive.com"},
                        {"sourceUri": "biopharmadive.com"},
                        {"sourceUri": "constructiondive.com"},
                        {"sourceUri": "esgdive.com"},
                        {"sourceUri": "fooddive.com"},
                        {"sourceUri": "grocerydive.com"},
                        {"sourceUri": "healthcaredive.com"},
                        {"sourceUri": "highereddive.com"},
                        {"sourceUri": "manufacturingdive.com"},
                        {"sourceUri": "medtechdive.com"},
                        {"sourceUri": "restaurantdive.com"},
                        {"sourceUri": "retaildive.com"},
                        {"sourceUri": "smartcitiesdive.com"},
                        {"sourceUri": "supplychaindive.com"},
                        {"sourceUri": "truckingdive.com"},
                        {"sourceUri": "utilitydive.com"},
                        {"sourceUri": "wastedive.com"},
                    ]
                }
            ]
        }
    },
}
'''

def build_articles_payload(*, page: int, days_back: int = 0) -> dict:
    payload = copy.deepcopy(ARTICLES_PAYLOAD)
    payload["apiKey"] = os.getenv("EVENTREGISTRY_API_KEY")
    payload["articlesPage"] = page

    today = date.today()
    from_date = today - timedelta(days=days_back)

    payload["query"]["$query"]["$and"].append({
        "dateStart": from_date.isoformat(),
        "dateEnd": today.isoformat(),
        "lang": "eng",
    })

    return payload