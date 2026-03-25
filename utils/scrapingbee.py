# Standard library
import os
# Third-party
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from scrapingbee import ScrapingBeeClient

load_dotenv()

SCRAPING_BEE_API_KEY = os.getenv("SCRAPING_BEE_API_KEY")


def get_scrapingbee_client() -> ScrapingBeeClient:
    if not SCRAPING_BEE_API_KEY:
        raise ValueError("SCRAPING_BEE_API_KEY is not set")
    return ScrapingBeeClient(api_key=SCRAPING_BEE_API_KEY)


def get_soup(
    client: ScrapingBeeClient,
    url: str,
    *,
    render_js: bool = False,
    wait: int = 6000,
    premium_proxy: bool = False
) -> BeautifulSoup:
    response = client.get(
        url,
        params={
            "render_js": render_js,
            "wait": wait,
            "premium_proxy": premium_proxy
        },
    )
    if response.status_code != 200:
        raise ValueError(f"Failed to fetch {url}: status {response.status_code}")
    return BeautifulSoup(response.text, "html.parser")