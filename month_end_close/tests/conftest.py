import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import generate_data  # noqa: E402
from tools import ledger  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    db = generate_data.build(tmp_path / "t.db", csv_dir=None)
    c = ledger.connect(db)
    yield c
    c.close()
