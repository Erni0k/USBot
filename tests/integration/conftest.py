"""Postgres z pgvector w kontenerze (testcontainers) z kilkunastoma fragmentami.

Embeddingi są przygotowane w fixture, bez pobierania modelu: każdy „temat” to osobna
oś przestrzeni 1024-wymiarowej, a wektor fragmentu to znormalizowana kombinacja osi
(`vec()`), więc odległości kosinusowe są znane z góry. Bez Dockera testy są pomijane.
"""
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg
import pytest

from usbot.adapters.pgvector import connect

ROOT = Path(__file__).resolve().parents[2]
DIM = 1024
IMAGE = "pgvector/pgvector:pg16"

# Osie tematów
DZIEKANAT, GODZINY, LOKALIZACJA, EDUROAM, STYPENDIA, USOS, REKRUTACJA, ELS, POZA = range(9)

WNP = "https://us.edu.pl/wydzial/wnp/dziekanat/"
WNP_EN = "https://us.edu.pl/student/en/wydzial/wnp/dziekanat/"
EDUROAM_URL = "https://eduroam.us.edu.pl/konfiguracja/"
STYPENDIA_URL = "https://us.edu.pl/student/stypendia/"
USOS_URL = "https://us.edu.pl/student/usos/"
ELS_URL = "https://us.edu.pl/student/legitymacja/"
NEWS_URL = "https://us.edu.pl/rekrutacja-2018/"
REKRUTACJA_URL = "https://us.edu.pl/kandydat/rekrutacja/"


def vec(axes: dict[int, float]) -> list[float]:
    """Znormalizowany wektor z wagami na podanych osiach, np. vec({DZIEKANAT: 1, GODZINY: .2})."""
    v = [0.0] * DIM
    for axis, w in axes.items():
        v[axis] = w
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def cos_dist(a: list[float], b: list[float]) -> float:
    return 1 - sum(x * y for x, y in zip(a, b))


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


OLD = datetime.now(timezone.utc) - timedelta(days=3 * 365)

# (url, tytuł, typ, data publikacji, treść bez prefiksu, embedding)
CHUNKS = [
    (WNP, "Dziekanat WNP", "wydzial", None, "Godziny pracy: pon–pt 10–14",
     vec({DZIEKANAT: 1, GODZINY: 0.2})),
    (WNP, "Dziekanat WNP", "wydzial", None, "Pokój 112, ul. Bankowa 9",
     vec({DZIEKANAT: 1, LOKALIZACJA: 0.5})),
    (WNP, "Dziekanat WNP", "wydzial", None, "Kontakt: dziekanat.wnp@us.edu.pl",
     vec({DZIEKANAT: 1, LOKALIZACJA: 0.7})),
    # kopia pierwszego fragmentu pod /student/en/ — ta sama treść i embedding, dłuższy URL
    (WNP_EN, "Dziekanat WNP", "student", None, "Godziny pracy: pon–pt 10–14",
     vec({DZIEKANAT: 1, GODZINY: 0.2})),
    (EDUROAM_URL, "Eduroam", "static", None, "Krok 1: wybierz sieć eduroam",
     vec({EDUROAM: 1})),
    (EDUROAM_URL, "Eduroam", "static", None, "Krok 2: login@us.edu.pl i hasło",
     vec({EDUROAM: 1, LOKALIZACJA: 0.3})),
    (STYPENDIA_URL, "Stypendia", "student", None, "Stypendium socjalne — wniosek w USOSweb",
     vec({STYPENDIA: 1})),
    (STYPENDIA_URL, "Stypendia", "student", None, "Stypendium rektora dla najlepszych",
     vec({STYPENDIA: 1, REKRUTACJA: 0.4})),
    (USOS_URL, "USOS", "student", None, "Rejestracja na przedmioty w systemie",
     vec({USOS: 1})),
    # skrót tylko tutaj, a wektorowo fragment jest daleko od pytania o niego
    (ELS_URL, "Legitymacja", "student", None, "Elektroniczna legitymacja studencka ELS",
     vec({ELS: 1})),
    (NEWS_URL, "Rekrutacja 2018", "news", OLD, "Ruszyła rekrutacja na studia",
     vec({REKRUTACJA: 1, STYPENDIA: 0.1})),
    (REKRUTACJA_URL, "Rekrutacja", "static", None, "Rekrutacja na studia: terminy",
     vec({REKRUTACJA: 1, STYPENDIA: 0.25})),
    (REKRUTACJA_URL, "Rekrutacja", "static", None, "Dokumenty do rekrutacji",
     vec({REKRUTACJA: 1, USOS: 0.5})),
    ("https://us.edu.pl/kampus/", "Kampus", "static", None, "Mapa kampusu",
     vec({LOKALIZACJA: 1})),
    ("https://us.edu.pl/biblioteka/", "CINiBA", "static", None, "Godziny otwarcia biblioteki",
     vec({GODZINY: 1, LOKALIZACJA: 0.2})),
]


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    """Adres bazy w świeżym kontenerze pgvector/pgvector:pg16 ze schematem z infra/init.sql."""
    if not _docker_available():
        pytest.skip("Docker niedostępny — testy integracyjne pominięte")
    try:  # testcontainers ≥ 4.14 przeniosło moduł do .community
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer(IMAGE, username="test", password="test", dbname="test",
                           driver=None) as pg:
        url = pg.get_connection_url()
        # Schemat jak przy pierwszym starcie kontenera z docker-compose.yml
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute((ROOT / "infra" / "init.sql").read_text(encoding="utf-8"))
        with connect(url) as conn:
            for page_url, title, page_type, published, body, emb in CHUNKS:
                content = f"{title}\n{body}"  # prefiks jak w ingestii
                conn.execute(
                    "INSERT INTO chunks (url, title, page_type, published_at, content, "
                    "content_hash, body_hash, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (page_url, title, page_type, published, content, _h(content), _h(body),
                     np.array(emb, dtype=np.float32)),
                )
        yield url
