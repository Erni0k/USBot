"""Pobieranie listy URL-i z sitemapy i wyciąganie czystego tekstu ze stron."""
import httpx
import trafilatura
from lxml import etree

SITEMAP = "https://us.edu.pl/sitemap.xml"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Strony które warto zaindeksować ręcznie jeśli sitemap zawiedzie
FALLBACK_URLS = [
    "https://us.edu.pl/",
    "https://us.edu.pl/student/",
    "https://us.edu.pl/wydzialy/",
    "https://us.edu.pl/kandydat/",
    "https://us.edu.pl/nauka/",
    "https://us.edu.pl/kontakt/",
]


def _parse_sitemap_xml(content: bytes, limit: int) -> list[str]:
    """Parsuje XML sitemapy. Zwraca listę URL-i."""
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        # Serwer zwrócił HTML zamiast XML (np. redirect, captcha, błąd)
        print("UWAGA: sitemap.xml nie jest poprawnym XML-em - używam fallback URL-i")
        return []

    # sitemap index -> zejdź poziom niżej
    nested = [el.text for el in root.findall(".//sm:sitemap/sm:loc", NS) if el.text]
    if nested:
        urls: list[str] = []
        for sm_url in nested:
            try:
                resp = httpx.get(sm_url, headers=HEADERS, timeout=30, follow_redirects=True)
                urls.extend(_parse_sitemap_xml(resp.content, limit))
            except Exception as e:
                print(f"Pomijam pod-sitemapę {sm_url}: {e}")
            if len(urls) >= limit:
                break
        return urls[:limit]

    urls = [el.text for el in root.findall(".//sm:url/sm:loc", NS) if el.text]
    return urls[:limit]


def get_urls(sitemap_url: str = SITEMAP, limit: int = 200) -> list[str]:
    """Pobiera URL-e z sitemapy. Jeśli sitemap nie zadziała, używa fallback listy."""
    try:
        resp = httpx.get(sitemap_url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()

        # Sprawdź czy dostaliśmy XML czy HTML
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type.lower():
            print(f"UWAGA: {sitemap_url} zwrócił HTML zamiast XML - używam fallback URL-i")
            return FALLBACK_URLS[:limit]

        urls = _parse_sitemap_xml(resp.content, limit)
        if not urls:
            print("Sitemap pusta lub nieczytalna - używam fallback URL-i")
            return FALLBACK_URLS[:limit]

        print(f"Znaleziono {len(urls)} URL-i w sitemapie")
        return urls

    except Exception as e:
        print(f"Błąd pobierania sitemapy: {e} - używam fallback URL-i")
        return FALLBACK_URLS[:limit]


def fetch_text(url: str) -> str | None:
    """Pobiera stronę i zwraca sam tekst treści (bez menu/stopki)."""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return trafilatura.extract(resp.text, include_comments=False, include_tables=True)
    except Exception as e:
        print(f"Błąd pobierania {url}: {e}")
        return None