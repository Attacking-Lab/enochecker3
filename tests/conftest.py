import pytest

from enochecker3 import Enochecker
from enochecker3.mock import MockAsyncMongoClient


@pytest.fixture
def checker() -> Enochecker:
    return Enochecker("asd", 1234, mongo_client=MockAsyncMongoClient())


def pytest_addoption(parser):
    parser.addoption(
        "--with_mongodb", action="store_true", help="Run the tests with the mongodb"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "mongodb: mark test as requiring MongoDB")
    config.addinivalue_line(
        "markers", "e2e: mark test as an end-to-end checker/service round trip"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--with_mongodb"):
        return
    skip_mongodb = pytest.mark.skip(reason="need --with_mongodb option to run")
    for item in items:
        if "mongodb" in item.keywords:
            item.add_marker(skip_mongodb)
