"""Crawler us.edu.pl: sitemap jako ziarno + BFS po linkach do zadanej glebokosci.

Pobieranie stron odbywa sie rownolegle (ThreadPoolExecutor).
Crawl jest inkrementalny: wysyla naglowki warunkowe (If-None-Match /
If-Modified-Since) i pomija strony, ktore zwroca 304 Not Modified.
"""
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple

import httpx
import trafilatura
from lxml import etree, html as lxml_html


class Page(NamedTuple):
    """Wynik odwiedzenia jednej strony przez crawler."""
    url: str
    status: str                  # "modified" | "unchanged" | "error"
    text: str | None             # tekst tylko gdy status == "modified"
    etag: str | None
    last_modified: str | None
    links: list[str]             # linki na stronie (z cache przy "unchanged")

SITEMAP = "https://us.edu.pl/sitemap.xml"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

FALLBACK_URLS = [
    "https://usnet.us.edu.pl/",
    "https://usnet.us.edu.pl/siec/serwer-vpn/",
    "https://eduroam.us.edu.pl/",
    # dziekanaty wydziałów
    "https://us.edu.pl/wydzial/wh/dziekanat/",
    "https://us.edu.pl/wydzial/wnp/student/dziekanat/",
    "https://us.edu.pl/wydzial/wns/wydzial/struktura/administracja/dziekanat-2/",
    "https://us.edu.pl/wydzial/wnst/studia/dziekanaty/",
    "https://us.edu.pl/student/nowy-student/nowy-student-pierwsze-kroki/dziekanaty/dziekanat-wydzialu-prawa-i-administracji/",
    "https://us.edu.pl/wydzial/wsne/ksztalcenie/student-wydzialu/dziekanat/",
    "https://us.edu.pl/student/nowy-student/nowy-student-pierwsze-kroki/dziekanaty/dziekanat-wydzialu-teologicznego/",
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

# Pod-sitemapy pomijane w calosci (brak wartosci dla RAG: zalaczniki, galerie,
# tagi, kategorie, listy organizatorow itp.). ~73% sitemapy us.edu.pl to zalaczniki.
_SITEMAP_SKIP = ("attachment", "multimedia", "_tag", "categories", "kategorie",
                 "organizers", "target_group", "post-archive")

# Priorytet pod-sitemap (mniejsza liczba = crawlowane wczesniej). Reszta = 50.
_SITEMAP_PRIORITY = {"page": 0, "komunikaty": 1, "jednostki": 2, "post": 10, "event": 20}


def _skip(url: str) -> bool:
    low = url.lower().split("?")[0]
    return any(low.endswith(ext) for ext in _SKIP_EXT)


def _sitemap_allowed(url: str) -> bool:
    return not any(bad in url for bad in _SITEMAP_SKIP)


def _sitemap_rank(url: str) -> int:
    for key, rank in _SITEMAP_PRIORITY.items():
        if key in url:
            return rank
    return 50


def _parse_sitemap_xml(content: bytes, limit: int) -> list[str]:
    try:
        root = etree.fromstring(content)
    except etree.XMLSyntaxError:
        print("UWAGA: sitemap.xml nie jest poprawnym XML-em")
        return []
    nested = [el.text for el in root.findall(".//sm:sitemap/sm:loc", NS) if el.text]
    if nested:
        # Odfiltruj bezwartosciowe pod-sitemapy i posortuj wg priorytetu (page -> ...).
        nested = sorted((u for u in nested if _sitemap_allowed(u)), key=_sitemap_rank)
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


def _fetch(url: str, cache_entry: dict | None) -> Page:
    """Pobiera strone z naglowkami warunkowymi.

    Zwraca Page ze statusem:
    - "unchanged": serwer odpowiedzial 304 (tresc bez zmian) -> linki z cache
    - "modified":  pobrano swieza tresc
    - "error":     blad sieci / HTTP 4xx/5xx
    """
    headers = dict(HEADERS)
    if cache_entry:
        if cache_entry.get("etag"):
            headers["If-None-Match"] = cache_entry["etag"]
        if cache_entry.get("last_modified"):
            headers["If-Modified-Since"] = cache_entry["last_modified"]

    try:
        resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    except Exception as e:
        print(f"  pomijam {url}: {e}")
        return Page(url, "error", None, None, None, [])

    if resp.status_code == 304:
        cached_links = (cache_entry or {}).get("links") or []
        return Page(url, "unchanged", None, None, None, cached_links)

    if resp.status_code >= 400:
        print(f"  pomijam {url}: HTTP {resp.status_code}")
        return Page(url, "error", None, None, None, [])

    text = trafilatura.extract(
        resp.content.decode("utf-8", errors="replace"),
        include_comments=False,
        include_tables=True,
    )
    links = _links_from_html(resp.content, url)
    return Page(
        url,
        "modified",
        text,
        resp.headers.get("etag"),
        resp.headers.get("last-modified"),
        links,
    )


def crawl(
    limit: int = 2000,
    max_depth: int = 2,
    workers: int = 6,
    batch_sleep: float = 0.3,
    cache: dict[str, dict] | None = None,
):
    """Generator: BFS po us.edu.pl. Yields Page.

    Strony pobierane sa rownolegle w paczkach po `workers` sztuk.
    `cache` mapuje url -> {etag, last_modified, links} z poprzedniego crawla;
    pozwala pomijac niezmienione strony (304) i kontynuowac BFS z cache'owanych
    linkow. `batch_sleep` to przerwa miedzy paczkami (grzecznosc wobec serwera).
    """
    cache = cache or {}
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    # Kolejnosc crawlowania: najpierw priorytetowe seedy z FALLBACK_URLS
    # (usnet, eduroam, dziekanaty, strony ogolne), potem reszta z sitemapy.
    # Kolejnosc na liscie FALLBACK_URLS = priorytet (gora = wczesniej).
    for url in FALLBACK_URLS + _sitemap_urls(limit):
        url = url.rstrip("/")
        if url not in seen:
            seen.add(url)
            queue.append((url, 0))

    visited = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and visited < limit:
            # Pobierz paczke URL-i z kolejki
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < workers:
                batch.append(queue.popleft())

            futures = {
                pool.submit(_fetch, url, cache.get(url)): depth
                for url, depth in batch
            }

            for future in as_completed(futures):
                depth = futures[future]
                page = future.result()
                if page.status == "error":
                    continue

                visited += 1
                yield page

                # Kontynuuj BFS: linki ze swiezej strony lub z cache (przy 304)
                if depth < max_depth:
                    for link in page.links:
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
