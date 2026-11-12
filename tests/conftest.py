import subprocess
import sys
from pathlib import Path

import pytest

from solution.db import connect
from solution.schemas import load_config
from solution.security import Vault

REPO = Path(__file__).resolve().parents[1]
SCHEMAS = REPO / "assignment-a-data" / "schemas"
GENERATOR = REPO / "assignment-a-data" / "tools" / "generate_data.py"


@pytest.fixture(scope="session")
def cfg():
    return load_config(SCHEMAS)


@pytest.fixture
def vault():
    return Vault(b"test-secret-0123456789abcdef")


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "t.sqlite"))
    yield c
    c.close()


@pytest.fixture(scope="session")
def make_data(tmp_path_factory):
    """Generate synthetic data for a seed once per session (small scale)."""
    cache = {}

    def _make(seed: int) -> Path:
        if seed not in cache:
            out = tmp_path_factory.mktemp(f"data_{seed}")
            subprocess.run([sys.executable, str(GENERATOR), "--seed", str(seed), "--scale", "small",
                            "--out", str(out)], check=True, capture_output=True)
            cache[seed] = out
        return cache[seed]

    return _make
