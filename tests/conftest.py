import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fixture import build_fixture  # noqa: E402

from poland_transit_mcp.city import City  # noqa: E402


@pytest.fixture(scope="session")
def city_dir(tmp_path_factory) -> Path:
    return build_fixture(tmp_path_factory.mktemp("testowo"))


@pytest.fixture(scope="session")
def city(city_dir) -> City:
    return City.open(city_dir, "testowo")
