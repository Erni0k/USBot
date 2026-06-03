"""Crawler us.edu.pl: sitemap jako ziarno + BFS po linkach do zadanej glebokosci.

Pobieranie stron odbywa sie rownolegle (ThreadPoolExecutor).
"""
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import trafilatura
from lxml import etree, html as lxml_html

SITEMAP = "https://us.edu.pl/sitemap.xml"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

FALLBACK_URLS = [
    "https://usnet.us.edu.pl/",
    "https://eduroam.us.edu.pl/",
    # dziekanaty wydziałów
    "https://us.edu.pl/wydzial/wh/dziekanat/",
    "https://us.edu.pl/wydzial/wnst/dziekanat/",
    "https://us.edu.pl/wydzial/wpia/dziekanat/",
    "https://us.edu.pl/wydzial/wmfich/dziekanat/",
    "https://us.edu.pl/wydzial/winm/dziekanat/",
    "https://us.edu.pl/wydzial/wt/dziekanat/",
    "https://us.edu.pl/wydzial/wsne/dziekanat/",
    "https://us.edu.pl/wydzial/wnoz/dziekanat/",
    # strony ogólne
    "https://us.edu.pl/",
    "https://us.edu.pl/student/",
    "https://us.edu.pl/wydzialy/",
    "https://us.edu.pl/kandydat/",
    "https://us.edu.pl/nauka/",
    "https://us.edu.pl/kontakt/",
]

ALLOWED_DOMAINS = (
    "https://us.edu.pl",
    "https://eduroam.us.edu.pl",
    "https://usnet.us.edu.pl",
)

_SKIP_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
             ".zip", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3"}


def _skip(url: str) -> bool:
    low = url.lower().split("?")[0]
    return any(low.endswith(ext) for ext in _SKIP_EXT)


def _parse_sitemap_xml(content: bytes, limit: int) -> list[str]:
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        print("UWAGA: sitemap.xml nie jest poprawnym XML-em")
        return []
    nested = [el.text for el in root.findall(".//sm:sitemap/sm:loc", NS) if el.text]
    if nested:
        urls: list[str] = []
        for sm_url in nested:
            try:
                resp = httpx.get(sm_url, headers=HEADERS, timeout=30, follow_redirects=True)
                urls.extend(_parse_sitemap_xml(resp.content, limit))
            except Exception as e:
                print(f"Pomijam pod-sitemape {sm_url}: {e}")
            if len(urls) >= limit:
                break
        return urls[:limit]
    return [el.text for el in root.findall(".//sm:url/sm:loc", NS) if el.text][:limit]


def _sitemap_urls(limit: int, sitemap_url: str = SITEMAP) -> list[str]:
    try:
        resp = httpx.get(sitemap_url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        if "html" in resp.headers.get("content-type", "").lower():
            return FALLBACK_URLS[:limit]
        urls = _parse_sitemap_xml(resp.content, limit)
        if urls:
            print(f"Znaleziono {len(urls)} URL-i w sitemapie")
            return urls
        return FALLBACK_URLS[:limit]
    except Exception as e:
        print(f"Blad sitemapy: {e} - uzywam fallback URL-i")
        return FALLBACK_URLS[:limit]


def _links_from_html(content: bytes, base_url: str) -> list[str]:
    try:
        tree = lxml_html.fromstring(content)
        tree.make_links_absolute(base_url)
        links = []
        for href in tree.xpath("//a/@href"):
            href = href.split("#")[0].rstrip("/")
            if any(href.startswith(d) for d in ALLOWED_DOMAINS) and not _skip(href):
                links.append(href)
        return list(set(links))
    except Exception:
        return []


def _fetch(url: str) -> tuple[str, bytes | None]:
    """Pobiera strone. Zwraca (url, content) lub (url, None) przy bledzie."""
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return url, resp.content
    except Exception as e:
        print(f"  pomijam {url}: {e}")
        return url, None


def crawl(limit: int = 2000, max_depth: int = 2, workers: int = 6, batch_sleep: float = 0.3):
    """Generator: BFS po us.edu.pl. Yields (url, text).

    Strony pobierane sa rownolegle w paczkach po `workers` sztuk.
    `batch_sleep` to przerwa miedzy paczkami (grzecznosc wobec serwera).
    """
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    for url in _sitemap_urls(limit) + FALLBACK_URLS:
        url = url.rstrip("/")
        if url not in seen:
            seen.add(url)
            queue.append((url, 0))

    yielded = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and yielded < limit:
            # Pobierz paczke URL-i z kolejki
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < workers:
                batch.append(queue.popleft())

            futures = {pool.submit(_fetch, url): (url, depth) for url, depth in batch}

            for future in as_completed(futures):
                url, depth = futures[future]
                _, content = future.result()
                if content is None:
                    continue

                text = trafilatura.extract(
                    content.decode("utf-8", errors="replace"),
                    include_comments=False,
                    include_tables=True,
                )
                if text:
                    yield url, text
                    yielded += 1

                if depth < max_depth:
                    for link in _links_from_html(content, url):
                        link = link.rstrip("/")
                        if link not in seen:
                            seen.add(link)
                            queue.append((link, depth + 1))

            time.sleep(batch_sleep)


# --- stare API (kompatybilnosc wsteczna) ---

def get_urls(sitemap_url: str = SITEMAP, limit: int = 200) -> list[str]:
    return _sitemap_urls(limit, sitemap_url)


def fetch_text(url: str) -> str | None:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return trafilatura.extract(resp.text, include_comments=False, include_tables=True)
    except Exception as e:
        print(f"Blad pobierania {url}: {e}")
        return None
