"""Pobieranie listy URL-i z sitemapy i wyciąganie czystego tekstu ze stron."""
import httpx
import trafilatura
from lxml import etree

SITEMAP = "https://us.edu.pl/sitemap.xml"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HEADERS = {"User-Agent": "US-Chatbot/0.1 (projekt studencki; kontakt: zespol@example.com)"}


def get_urls(sitemap_url: str = SITEMAP, limit: int = 200) -> list[str]:
    """Zwraca listę URL-i. Obsługuje też sitemap index (sitemapy zagnieżdżone)."""
    resp = httpx.get(sitemap_url, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    root = etree.fromstring(resp.content)

    # sitemap index -> zejdź poziom niżej
    nested = [el.text for el in root.findall(".//sm:sitemap/sm:loc", NS)]
    if nested:
        urls: list[str] = []
        for sm in nested:
            urls.extend(get_urls(sm, limit=limit))
            if len(urls) >= limit:
                break
        return urls[:limit]

    urls = [el.text for el in root.findall(".//sm:url/sm:loc", NS)]
    return urls[:limit]


def fetch_text(url: str) -> str | None:
    """Pobiera stronę i zwraca sam tekst treści (bez menu/stopki)."""
    resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return trafilatura.extract(resp.text, include_comments=False, include_tables=True)
