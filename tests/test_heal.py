import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from hlab import factory_heal as fh  # noqa: E402

BROKEN = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;font-family:sans-serif} section{min-height:400px;padding:20px} .wide{width:900px;background:#ddd}</style></head>
<body><section><h1>Ristorante Prova</h1><p>Benvenuti</p><div class="wide">tabella larga</div><img src="nope.png" alt="x"></section>
<script>funzioneInesistente();</script></body></html>"""
FIXED_EDITS = [
    {"file": "index.html", "search": '<div class="wide">', "replace": '<div class="wide" style="max-width:100%">'},
    {"file": "index.html", "search": '<img src="nope.png" alt="x">', "replace": ""},
    {"file": "index.html", "search": "funzioneInesistente();", "replace": ""},
]


@pytest.fixture
def site(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    (d / "index.html").write_text(BROKEN, encoding="utf-8")
    try:
        asyncio.run(fh.scan(d))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium non disponibile: {type(e).__name__}")
    return d


def run(c):
    return asyncio.run(c)


def test_scan_finds_script_layout_and_image_problems(site, tmp_path):
    r = run(fh.scan(site, shots_dir=tmp_path / "shots"))
    kinds = {(i["kind"], i["viewport"]) for i in r["issues"]}
    assert ("js_error", "desktop") in kinds and ("broken_image", "mobile") in kinds and ("overflow_x", "mobile") in kinds
    assert r["score"] > 10 and Path(r["shots"]["mobile"]).stat().st_size > 1000


def test_heal_applies_a_good_patch_on_a_copy_and_shows_the_diff(site):
    async def patcher(report, work):
        return FIXED_EDITS
    res = run(fh.heal(site, patcher))
    assert res["healed"] and res["dopo"]["punteggio"] == 0 and res["prima"]["punteggio"] > 10
    assert "funzioneInesistente" in (site / "index.html").read_text(encoding="utf-8")        # l'originale non si tocca
    assert "-<script>funzioneInesistente" in res["diff"] or "funzioneInesistente" in res["diff"]
    assert res["tentativi"][0]["esito"] == "tenuta"
    assert "funzioneInesistente" not in (Path(res["work_dir"]) / "index.html").read_text(encoding="utf-8")


def test_a_patch_that_makes_things_worse_is_reverted(site):
    async def harmful(report, work):
        return [{"file": "index.html", "search": "<p>Benvenuti</p>", "replace": '<p>Benvenuti</p><img src="altra-rotta.png"><script>peggio();</script>'}]
    res = run(fh.heal(site, harmful, max_attempts=2))
    assert not res["healed"] and res["dopo"]["punteggio"] == res["prima"]["punteggio"]
    assert all(t["esito"].startswith("annullata") for t in res["tentativi"])
    assert "peggio" not in (Path(res["work_dir"]) / "index.html").read_text(encoding="utf-8")


def test_unsafe_or_ambiguous_edits_are_ignored(site):
    async def sneaky(report, work):
        return [{"file": "../fuori.html", "search": "a", "replace": "b"}, {"file": "index.html", "search": "a", "replace": "b"},      # non univoco
                {"file": "logo.png", "search": "x", "replace": "y"}, {"file": "index.html", "search": "non-esiste", "replace": "y"}]
    res = run(fh.heal(site, sneaky, max_attempts=1))
    assert "nessuna modifica applicabile" in res["tentativi"][0]["esito"] and res["diff"] == ""


def test_no_patch_and_clean_site_paths(site, tmp_path):
    async def none(report, work):
        return []
    assert run(fh.heal(site, none))["tentativi"][0]["esito"] == "nessuna patch proposta"
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "index.html").write_text("<!doctype html><meta name=viewport content='width=device-width'><section style='min-height:300px'><h1>Ok</h1></section>")

    async def must_not_be_called(report, work):
        raise AssertionError("il sito e' pulito")
    res = run(fh.heal(clean, must_not_be_called))
    assert res["healed"] and res["tentativi"] == [] and res["prima"]["punteggio"] == 0


def test_llm_patcher_parses_json_even_in_code_fences_and_survives_garbage(site):
    async def good(prompt, system):
        assert "PROBLEMI" in prompt and "index.html" in prompt
        return "Ecco:\n```json\n" + json.dumps({"edits": FIXED_EDITS}) + "\n```"

    async def garbage(prompt, system):
        return "non so cosa fare"
    report = run(fh.scan(site))
    assert run(fh.llm_patcher(good)(report, site))[0]["file"] == "index.html"
    assert run(fh.llm_patcher(garbage)(report, site)) == []
    res = run(fh.heal(site, fh.llm_patcher(good)))
    assert res["healed"]
