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
    "dataType": ["news", "pr"],
    "forceMaxDateTimeWindow": 7,
    "resultType": "articles",
    "articlesSortBy": "date",
    "apiKey": "",
}
'''