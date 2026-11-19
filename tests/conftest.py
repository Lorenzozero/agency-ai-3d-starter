import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HLAB_CAPS_MODE"] = "enforce"      # i test provano il rifiuto; in produzione il default e' warn finche' non verificato dal vivo
if os.environ.get("HLAB_REAL") != "1":
    os.environ["HLAB_FAKE_EMBED"] = "1"      # test veloci senza modello; HLAB_REAL=1 usa il modello vero


@pytest.fixture(autouse=True)
def lab_dirs(tmp_path, monkeypatch):
    from hlab import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "MEMORY_DIR", tmp_path / "data" / "memory")
    monkeypatch.setattr(config, "EVENTS_DB", tmp_path / "data" / "events.db")
    return tmp_path
