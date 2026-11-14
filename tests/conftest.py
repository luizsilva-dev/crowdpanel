from pathlib import Path

import pytest

from crowdpanel.db import Database
from crowdpanel.loader import load_personas, load_scenario

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "coffee-subscription"


@pytest.fixture
def scenario():
    return load_scenario(EXAMPLE / "scenario.yaml")


@pytest.fixture
def personas():
    return load_personas(EXAMPLE / "personas.yaml")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()
