"""Crawler us.edu.pl: sitemap jako ziarno + BFS po linkach do zadanej glebokosci.

Pobieranie stron odbywa sie rownolegle (ThreadPoolExecutor).
Crawl jest inkrementalny: wysyla naglowki warunkowe (If-None-Match /
If-Modified-Since) i pomija strony, ktore zwroca 304 Not Modified.
"""
import re
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx
import trafilatura
from lxml import etree, html as lxml_html


class Page(NamedTuple):
    """Wynik odwiedzenia jednej strony przez crawler."""
    url: str
    status: str                  # "modified" | "unchanged" | "error"
    text: str | None             # tekst tylko gdy status == "modified"; naglowki jako "## ..."
    etag: str | None
    last_modified: str | None
    links: list[str]             # linki na stronie (z cache przy "unchanged")
    # Metadane strony - tylko gdy status == "modified"
    title: str | None = None              # <title> bez nazwy serwisu
    page_type: str | None = None          # student | wydzial | static | news | event
    published_at: datetime | None = None  # data publikacji (aktualnosci, wydarzenia)

SITEMAP = "https://us.edu.pl/sitemap.xml"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

_DZIEKANATY = "https://us.edu.pl/student/nowy-student/nowy-student-pierwsze-kroki/dziekanaty/"

FALLBACK_URLS = [
    "https://usnet.us.edu.pl/",
    "https://usnet.us.edu.pl/siec/serwer-vpn/",
    "https://usnet.us.edu.pl/uslugi-sieciowe/konta-studenckie/",
    "https://eduroam.us.edu.pl/",
    "https://eduroam.us.edu.pl/konfiguracja/",
    # dziekanaty wydziałów (strony wydziałów + portal studenta)
    "https://us.edu.pl/wydzial/wh/dziekanat/",
    "https://us.edu.pl/wydzial/wnp/student/dziekanat/",
    "https://us.edu.pl/wydzial/wns/wydzial/struktura/administracja/dziekanat/",
    "https://us.edu.pl/wydzial/wnst/studia/dziekanaty/",
    "https://us.edu.pl/wydzial/wsne/ksztalcenie/student-wydzialu/dziekanat/",
    "https://us.edu.pl/wydzial/wpia/wydzial/kontakt/",
    "https://us.edu.pl/wydzial/wtl/administracja/dziekanat/",
    _DZIEKANATY,
    _DZIEKANATY + "dziekanat-wydzialu-humanistycznego/",
    _DZIEKANATY + "dziekanat-wydzialu-nauk-przyrodniczych/",
    _DZIEKANATY + "dziekanat-wydzialu-nauk-spolecznych/",
    _DZIEKANATY + "dziekanat-wydzialu-nauk-scislych-i-technicznych/",
    _DZIEKANATY + "dziekanat-wydzialu-prawa-i-administracji/",
    _DZIEKANATY + "dziekanat-wydzialu-sztuki-i-nauk-o-edukacji/",
    _DZIEKANATY + "dziekanat-wydzialu-teologicznego/",
    _DZIEKANATY + "dziekanat-szkoly-filmowej-im-krzysztofa-kieslowskiego/",
    # strony ogólne
    "https://us.edu.pl/",
    "https://us.edu.pl/student/",
    "https://us.edu.pl/kandydat/",
    "https://us.edu.pl/kontakt/",
]

ALLOWED_DOMAINS = (
    "https://us.edu.pl",
    "https://eduroam.us.edu.pl",
    "https://usnet.us.edu.pl",
)

# Sekcje najcenniejsze dla studentów. Ich podstrony trafiają do kolejki priorytetowej,
# niezależnie od tego, gdzie je znaleziono. Portal studenta to osobna instalacja
# WordPressa, której nie ma w sitemap.xml — bez tego BFS dochodził do niej dopiero
# po ~10 tys. URL-i z sitemapy, czyli zwykle już po wyczerpaniu CRAWL_LIMIT.
PRIORITY_PREFIXES = (
    "https://us.edu.pl/student/",
    "https://eduroam.us.edu.pl",
    "https://usnet.us.edu.pl",
)

# Grzecznosc wobec serwerow: maks. liczba rownoleglych zadan do domeny (pozostale
# domeny: `workers`) i minimalny odstep miedzy startami zadan do tej samej domeny.
# usnet odpowiada po ~11 s - wiecej rownoleglych zadan tylko by go dociazylo.
HOST_LIMITS = {"usnet.us.edu.pl": 2}
HOST_DELAY = 0.1  # s

_SKIP_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
             ".zip", ".doc", ".docx", ".xls", ".xlsx", ".mp4", ".mp3"}

# Fragmenty URL-i bez wartości dla RAG: API i pliki WordPressa, kanały RSS,
# listy kategorii/tagów, galerie zdjęć, załączniki, duplikat /pl/ portalu studenta.
_SKIP_PATTERNS = ("/wp-json", "/wp-content/", "/wp-includes/", "xmlrpc.php", "/feed/",
                  "/comments/", "/category/", "/tag/", "/multimedia/", "/attachment/",
                  "/student/pl/", "?")

# Elementy usuwane przed ekstrakcja tekstu. Baner cookies (wtyczka Complianz) jest
# ukryty, ale siedzi w HTML kazdej strony - na krotkich stronach trafilatura brala
# go za tresc glowna (~13% fragmentow w bazie). Naglowek z menu i paskiem WCAG
# trafial do tekstu, gdy trafilatura nie znalazla tresci i brala cala strone.
# Wyjatek: "cmplz-document" to tresc strony polityki prywatnosci, nie baner.
_BOILERPLATE_XPATH = ('//*[@id="cmplz-cookiebanner-container"]'
                      ' | //*[contains(@class, "cmplz-") and not(contains(@class, "cmplz-document"))]'
                      ' | //header | //footer | //nav | //script | //style | //noscript')
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCK_TAGS = ("p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol",
               *_HEADING_TAGS)

# Naglowek markdown: "## Tekst". Hashtag bez spacji ("#FunduszeUE") nie jest naglowkiem.
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*\s*$")

# Nazwy serwisow doklejane na koncu <title> - bez wartosci dla tresci fragmentu.
_TITLE_SITE_SUFFIX = re.compile(
    r"\s+[|–—«-]\s+(?:Uniwersytet Śląski w Katowicach|Informacje, wiadomości i komunikaty)$")

_MONTHS = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
           "sierpnia", "września", "października", "listopada", "grudnia")
_DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_DATE_WORD = re.compile(r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r") (\d{4})\b", re.IGNORECASE)
_BLOG_DATE = re.compile(r"/blog/(\d{4})/(\d{2})/(\d{2})/")

# Strony zalacznikow WordPressa (zdjecie lub PDF z podpisem) - bez tresci dla RAG.
# Ich adres nie zawiera /attachment/ (np. .../sale-wykladowe/dsc00582/), wiec
# rozpoznajemy je po klasie <body>. Bylo ich w bazie ~490, prawie same z banerem cookies.
_ATTACHMENT_BODY = re.compile(rb'<body[^>]*\bclass="(?:[^"]*\s)?attachment[\s"]')

# Pod-sitemapy pomijane w calosci (brak wartosci dla RAG: zalaczniki, galerie,
# tagi, kategorie, listy organizatorow itp.). ~73% sitemapy us.edu.pl to zalaczniki.
_SITEMAP_SKIP = ("attachment", "multimedia", "_tag", "categories", "kategorie",
                 "organizers", "target_group", "post-archive")

# Priorytet pod-sitemap (mniejsza liczba = crawlowane wczesniej). Reszta = 50.
_SITEMAP_PRIORITY = {"page": 0, "komunikaty": 1, "jednostki": 2, "post": 10, "event": 20}


def _skip(url: str) -> bool:
    low = url.lower()
    return (any(low.split("?")[0].endswith(ext) for ext in _SKIP_EXT)
            or any(p in low for p in _SKIP_PATTERNS)
            or low.rstrip("/").endswith("/feed"))


def excluded(url: str) -> bool:
    """Czy URL jest wykluczony przez obecne reguły crawlera (do czyszczenia bazy)."""
    return not any(url.startswith(d) for d in ALLOWED_DOMAINS) or _skip(url)


def _is_priority(url: str) -> bool:
    # Porownanie z koncowym "/", zeby /student/ nie lapalo np. /studentka-z-...
    url = url.rstrip("/") + "/"
    return any(url.startswith(p.rstrip("/") + "/") for p in PRIORITY_PREFIXES)


def _main_text(tree) -> str:
    """Caly tekst elementu <main>, z podzialem na linie po elementach blokowych.

    Naglowki h1-h6 zapisujemy jak w markdownie ("## Tekst"), zeby chunking mogl
    dzielic tekst po sekcjach. Puste naglowki (ozdobniki WPBakery) sa pomijane.
    """
    main = tree.xpath("//main")
    if not main:
        return ""
    for h in list(main[0].iter(*_HEADING_TAGS)):
        text = " ".join(h.text_content().split())
        for child in list(h):
            h.remove(child)
        h.text = f"\n{'#' * int(h.tag[1])} {text}" if text else ""
    for el in main[0].iter(*_BLOCK_TAGS):
        el.tail = "\n" + (el.tail or "")
    lines = (" ".join(line.split()) for line in main[0].text_content().splitlines())
    return "\n".join(line for line in lines if line)


def _clean_markdown(md: str | None) -> str:
    """Upraszcza markdown z trafilatury: zostawia naglowki, usuwa ozdobniki.

    Pogrubienia (**) to na stronach WPBakery prawie kazda linia - w embeddingu
    to tylko szum. Puste naglowki ("#####") trafilatura robi z ozdobnych h5.
    """
    lines = []
    for line in (md or "").replace("**", "").splitlines():
        line = " ".join(line.split())
        if not line or re.fullmatch(r"#+", line):
            continue
        m = HEADING_RE.match(line)
        lines.append(f"{m.group(1)} {m.group(2)}" if m else line)
    return "\n".join(lines)


def _title(tree) -> str | None:
    """Tytul strony z <title> (albo <h1>), bez nazwy serwisu.

    "Dziekanat | Wydział Humanistyczny | Uniwersytet Śląski w Katowicach"
    -> "Dziekanat | Wydział Humanistyczny". Nazwa wydzialu zostaje - to ona
    odroznia dziekanaty. Bierzemy <title> z <head>: w <body> sa ikony SVG z
    wlasnym <title> ("Zwiększ tekst").
    """
    raw = tree.findtext(".//head/title") or ""
    if not raw.strip():
        h1 = tree.xpath("//h1")
        raw = h1[0].text_content() if h1 else ""
    title = " ".join(raw.split())
    while m := _TITLE_SITE_SUFFIX.search(title):
        title = title[:m.start()]
    return title or None


def _page_type(url: str, tree) -> str:
    """Typ strony: news / event / student / wydzial / static.

    Aktualnosci rozpoznajemy po typie wpisu WordPressa z klasy <body>
    (single-post, single-komunikaty, single-event) - to ten sam typ, od ktorego
    pochodzi nazwa pod-sitemapy (post-, komunikaty-, event-sitemap). Klasa dziala
    tez dla stron spoza sitemapy: komunikatow portalu studenta i wpisow na
    stronach wydzialow, znalezionych przez BFS. Reszta - po adresie.
    """
    classes = set(" ".join(tree.xpath("//body/@class")).split()) if tree is not None else set()
    path = urlsplit(url).path
    if "single-event" in classes or path.startswith("/event/"):
        return "event"
    if (classes & {"single-post", "single-komunikaty"} or "/komunikaty/" in path
            or _BLOG_DATE.search(path)):
        return "news"
    if url.startswith("https://us.edu.pl/student/"):
        return "student"
    if path.startswith("/wydzial/"):
        return "wydzial"
    return "static"


def _date_from_text(text: str) -> datetime | None:
    """Pierwsza data z poczatku tresci: "18.09.2020" albo "18 września 2020"."""
    head = text[:300]
    m = _DATE_NUM.search(head)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DATE_WORD.search(head)
        if not m:
            return None
        day, month, year = int(m.group(1)), _MONTHS.index(m.group(2).lower()) + 1, int(m.group(3))
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _published_at(url: str, tree, text: str | None, page_type: str) -> datetime | None:
    """Data publikacji: <meta article:published_time>, <time>, adres bloga usnet, tresc.

    Strony informacyjne WordPressa tez maja published_time - to data utworzenia
    strony (czesto 2019), a nie aktualnosc tresci. Kara za wiek w retrieve()
    dotyczy wiec tylko news/event; date z tresci szukamy tylko dla nich.
    """
    for value in (tree.xpath('//meta[@property="article:published_time"]/@content')
                  + tree.xpath("//time/@datetime")):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = _BLOG_DATE.search(urlsplit(url).path)
    if m:
        return datetime(*map(int, m.groups()), tzinfo=timezone.utc)
    if text and page_type in ("news", "event"):
        return _date_from_text(text)
    return None


class Extracted(NamedTuple):
    text: str | None
    title: str | None
    page_type: str
    published_at: datetime | None


def _extract(content: bytes, url: str) -> Extracted:
    """Wyciaga tresc i metadane strony.

    Tresc: trafilatura (markdown z naglowkami) po usunieciu banera, menu i stopki.
    Gdy strona ma <main>, trafilatura dostaje tylko jego zawartosc - inaczej na
    stronach bez tresci zbierala cale <body> (pasek COVID, linki "przejdz do tresci").
    Trafilatura wybiera jeden blok tresci i na stronach portalu (np. akademiki)
    gubi wiekszosc tekstu. Jesli zwrocila mniej niz polowe tego, co jest w <main>,
    bierzemy caly <main> (tez z naglowkami).
    """
    try:
        tree = lxml_html.fromstring(content)
    except Exception:
        text = trafilatura.extract(content.decode("utf-8", errors="replace"), include_comments=False,
                                   include_tables=True, output_format="markdown")
        return Extracted(_clean_markdown(text) or None, None, _page_type(url, None), None)

    title = _title(tree)
    page_type = _page_type(url, tree)
    for el in tree.xpath(_BOILERPLATE_XPATH):
        el.drop_tree()
    main = tree.xpath("//main")
    if main:
        html = f"<html><body>{lxml_html.tostring(main[0], encoding='unicode')}</body></html>"
    else:
        html = lxml_html.tostring(tree, encoding="unicode")
    text = _clean_markdown(trafilatura.extract(html, include_comments=False, include_tables=True,
                                               output_format="markdown"))
    main_text = _main_text(tree)
    if len(main_text) > 2 * len(text):
        text = main_text
    return Extracted(text or None, title, page_type, _published_at(url, tree, text, page_type))


def _extract_text(content: bytes) -> str | None:
    return _extract(content, "").text


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


def _fetch(url: str, cache_entry: dict | None, client: httpx.Client | None = None) -> Page:
    """Pobiera strone z naglowkami warunkowymi.

    `client` - wspolny klient HTTP (utrzymuje polaczenia miedzy zadaniami);
    bez niego kazde zadanie otwiera nowe polaczenie.

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
        resp = (client or httpx).get(url, headers=headers, timeout=30, follow_redirects=True)
    except Exception as e:
        print(f"  pomijam {url}: {e}")
        return Page(url, "error", None, None, None, [])

    if resp.status_code == 304:
        cached_links = (cache_entry or {}).get("links") or []
        return Page(url, "unchanged", None, None, None, cached_links)

    if resp.status_code >= 400:
        print(f"  pomijam {url}: HTTP {resp.status_code}")
        return Page(url, "error", None, None, None, [])

    if _ATTACHMENT_BODY.search(resp.content):
        return Page(url, "error", None, None, None, [])

    ext = _extract(resp.content, url)
    links = _links_from_html(resp.content, url)
    return Page(
        url,
        "modified",
        ext.text,
        resp.headers.get("etag"),
        resp.headers.get("last-modified"),
        links,
        ext.title,
        ext.page_type,
        ext.published_at,
    )


def crawl(
    limit: int = 2000,
    max_depth: int = 2,
    workers: int = 6,
    host_delay: float = HOST_DELAY,
    cache: dict[str, dict] | None = None,
):
    """Generator: BFS po us.edu.pl. Yields Page.

    W locie jest stale do `workers` zadan - gdy jedno sie skonczy, od razu
    wysylane jest nastepne. Wczesniej strony szly paczkami i jedna wolna strona
    z usnet (~11 s) blokowala cala paczke, a us.edu.pl odpowiada w ~0.2 s.
    Grzecznosc wobec serwerow: najwyzej HOST_LIMITS[domena] (domyslnie `workers`)
    rownoleglych zadan na domene i co najmniej `host_delay` s miedzy startami
    kolejnych zadan do tej samej domeny.

    `cache` mapuje url -> {etag, last_modified, links} z poprzedniego crawla;
    pozwala pomijac niezmienione strony (304) i kontynuowac BFS z cache'owanych
    linkow.
    """
    cache = cache or {}
    seen: set[str] = set()
    # Kolejki per domena (domena -> FIFO), zeby URL-e zablokowanej domeny (np. usnet
    # przy limicie zadan) nie wstrzymywaly pobierania z pozostalych.
    priority: dict[str, deque[tuple[str, int]]] = defaultdict(deque)
    queue: dict[str, deque[tuple[str, int]]] = defaultdict(deque)

    def enqueue(url: str, depth: int, force_priority: bool = False) -> None:
        url = url.rstrip("/")
        if url in seen or _skip(url):
            return
        seen.add(url)
        level = priority if force_priority or _is_priority(url) else queue
        level[urlsplit(url).hostname].append((url, depth))

    # Kolejnosc crawlowania: najpierw kolejka priorytetowa - seedy z FALLBACK_URLS
    # (usnet, eduroam, dziekanaty, strony ogolne) oraz wszystkie znalezione podstrony
    # PRIORITY_PREFIXES - potem reszta z sitemapy i BFS.
    # Kolejnosc na liscie FALLBACK_URLS = priorytet (gora = wczesniej).
    for url in FALLBACK_URLS:
        enqueue(url, 0, force_priority=True)
    for url in _sitemap_urls(limit):
        enqueue(url, 0)

    visited = 0
    in_flight: dict[Future, tuple[str, int]] = {}  # zadanie -> (domena, glebokosc)
    active: Counter[str] = Counter()               # zadania w locie per domena
    next_start: dict[str, float] = {}              # najwczesniejszy start nastepnego zadania

    def host_ready(host: str, now: float) -> bool:
        return (active[host] < HOST_LIMITS.get(host, workers)
                and next_start.get(host, 0.0) <= now)

    def pick(now: float) -> tuple[str, str, int] | None:
        """Nastepny URL do pobrania: (domena, url, glebokosc) lub None.

        Kolejka priorytetowa idzie pierwsza. Zwykla jest uzywana, gdy domeny z
        priorytetowej czekaja (limit/przerwa), ale tylko jesli budzet `limit`
        wystarczy jeszcze na wszystkie oczekujace URL-e priorytetowe.
        """
        n_priority = sum(len(dq) for dq in priority.values())
        budget = limit - visited - len(in_flight)
        for level in (priority, queue):
            if level is queue and budget <= n_priority:
                break
            for host, dq in level.items():
                if dq and host_ready(host, now):
                    return host, *dq.popleft()
        return None

    ready: list[Page] = []
    with httpx.Client(limits=httpx.Limits(max_connections=workers)) as client, \
            ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            # Uzupelnij wolne miejsca, zanim oddamy strony (ingest liczy wtedy
            # embeddingi, a zadania w tle sie pobieraja)
            now = time.monotonic()
            while len(in_flight) < workers and visited + len(in_flight) < limit:
                nxt = pick(now)
                if nxt is None:
                    break
                host, url, depth = nxt
                active[host] += 1
                next_start[host] = now + host_delay
                in_flight[pool.submit(_fetch, url, cache.get(url), client)] = (host, depth)

            yield from ready
            ready = []

            # Gdy jakas domena czeka tylko na przerwe, obudz sie na czas jej konca
            now = time.monotonic()
            delays = [next_start[h] - now for level in (priority, queue)
                      for h, dq in level.items() if dq and next_start.get(h, 0.0) > now]
            timeout = min(delays) if delays else None
            if not in_flight:
                if visited >= limit or not any(dq for level in (priority, queue)
                                               for dq in level.values()):
                    break
                if timeout:
                    time.sleep(timeout)
                continue

            done, _ = wait(in_flight, timeout=timeout, return_when=FIRST_COMPLETED)
            for future in done:
                host, depth = in_flight.pop(future)
                active[host] -= 1
                page = future.result()
                if page.status == "error":
                    continue

                visited += 1
                ready.append(page)

                # Kontynuuj BFS: linki ze swiezej strony lub z cache (przy 304)
                if depth < max_depth:
                    for link in page.links:
                        enqueue(link, depth + 1)


# --- stare API (kompatybilnosc wsteczna) ---

def get_urls(sitemap_url: str = SITEMAP, limit: int = 200) -> list[str]:
    return _sitemap_urls(limit, sitemap_url)


def fetch_text(url: str) -> str | None:
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return _extract_text(resp.content)
    except Exception as e:
        print(f"Blad pobierania {url}: {e}")
        return None
