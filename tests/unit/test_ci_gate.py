"""Bramka CI (evaluate.py --check) i dobór stron do mini-indeksu (build_fixture.py)."""
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "evaluation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = script("evaluate")
fixture = script("build_fixture")


def row(hit, rr):
    return {"scores": {5: {"hit": hit}}, "rr": rr}


def test_metryki_bramki():
    assert evaluate.gate_metrics([row(1.0, 1.0), row(0.0, 0.5)]) == {"hit@5": 0.5, "mrr": 0.75}


def test_wynik_rowny_progowi_przechodzi():
    rows = evaluate.check_thresholds({"hit@5": 0.8, "mrr": 0.7}, {"hit@5": 0.8, "mrr": 0.7})
    assert all(r["ok"] for r in rows)


def test_spadek_ponizej_progu():
    rows = evaluate.check_thresholds({"hit@5": 0.79, "mrr": 0.9}, {"hit@5": 0.8, "mrr": 0.7})
    assert [r["ok"] for r in rows] == [False, True]


def test_metryka_bez_wyniku_to_porazka():
    rows = evaluate.check_thresholds({}, {"hit@5": 0.8})
    assert rows == [{"metric": "hit@5", "value": None, "threshold": 0.8, "ok": False}]


def test_raport_markdown():
    rows = evaluate.check_thresholds({"hit@5": 0.5}, {"hit@5": 0.8})
    text = evaluate.render_check(rows, questions=57, top_k=8, timing="Czas: 1 s.")
    assert "❌" in text and "| hit@5 | 0.500 | 0.800 |" in text
    assert "wyższe niż na produkcji" in text and "Czas: 1 s." in text


def test_plik_progow_ma_metryki_bramki():
    thresholds = json.loads((ROOT / "evaluation" / "thresholds.json").read_text(encoding="utf-8"))
    assert set(thresholds["min"]) == {"hit@5", "mrr"}
    for name, minimum in thresholds["min"].items():
        assert 0 < minimum <= thresholds["measured"][name]


URLS = [f"https://us.edu.pl/s{i}" for i in range(50)]


def test_wybor_stron_powtarzalny_i_niezalezny_od_kolejnosci():
    labeled = {"us.edu.pl/s3"}
    first = fixture.select_pages(URLS, labeled, 5, seed=1)
    second = fixture.select_pages(list(reversed(URLS)), labeled, 5, seed=1)
    assert first == second
    assert first[0] == ["https://us.edu.pl/s3"]
    assert len(first[1]) == 5 and "https://us.edu.pl/s3" not in first[1]


def test_oznaczona_strona_rozpoznana_mimo_ukosnika():
    assert fixture.select_pages(URLS, {"us.edu.pl/s7"}, 1, seed=1)[0] == ["https://us.edu.pl/s7"]


def test_za_malo_stron_na_dystraktory():
    with pytest.raises(SystemExit, match="Za mało"):
        fixture.select_pages(URLS[:3], set(), 5, seed=1)


def test_sql_cytuje_apostrofy_i_null():
    sql = fixture.render_sql([("https://us.edu.pl/o'k", None, None, "", "h", "b", "[0.1]")])
    assert sql == ["INSERT INTO chunks (url, page_type, published_at, content, content_hash, body_hash, embedding) "
                   "VALUES ('https://us.edu.pl/o''k',NULL,NULL,'','h','b','[0.1]');"]


def test_sql_dzieli_na_paczki():
    rows = [("u", None, None, "", "h", "b", "[0]")] * (fixture.INSERT_BATCH + 1)
    assert len(fixture.render_sql(rows)) == 2


def test_plik_jest_powtarzalny(tmp_path):
    a, b = tmp_path / "a.sql.gz", tmp_path / "b.sql.gz"
    fixture.write_fixture(a, ["-- x"], ["INSERT;"])
    fixture.write_fixture(b, ["-- x"], ["INSERT;"])
    assert a.read_bytes() == b.read_bytes()
    assert gzip.decompress(a.read_bytes()).decode() == "-- x\nINSERT;\n"


def test_mini_indeks_nie_zawiera_tresci_stron():
    with gzip.open(fixture.FIXTURE, "rt", encoding="utf-8") as f:
        text = f.read()
    assert "@us.edu.pl" not in text
