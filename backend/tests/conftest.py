import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

SAMPLE = ROOT / "amostras" / "ed425bf6-20260518_Otimizeplan.xlsx"


@pytest.fixture
def conn(tmp_path):
    from app import db as dbmod

    c = dbmod.init_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def sample_path():
    return str(SAMPLE)
