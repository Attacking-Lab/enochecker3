import os
import secrets

import pymongo
import pytest
import pytest_asyncio
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from enochecker3 import ChainDB, MumbleException
from enochecker3.mock import MockAsyncMongoClient


async def _make_chain_collection(mongo) -> AsyncCollection:
    db: AsyncDatabase = mongo["enochecker3_tests"]
    chain_collection: AsyncCollection = db["chain_db"]

    await chain_collection.create_index(
        [("task_chain_id", pymongo.ASCENDING), ("key", pymongo.ASCENDING)],
        name="task_chain_index",
        unique=True,
    )

    return chain_collection


@pytest_asyncio.fixture
async def collection():
    """In-memory mock collection, no MongoDB server required."""
    mongo = MockAsyncMongoClient()
    yield await _make_chain_collection(mongo)
    await mongo.close()


@pytest_asyncio.fixture
async def real_collection():
    """Collection backed by a real MongoDB server, needs --with_mongodb."""
    mongo: AsyncMongoClient = AsyncMongoClient(os.getenv("MONGO_HOST", "localhost"))
    yield await _make_chain_collection(mongo)
    await mongo.close()


@pytest_asyncio.fixture
async def chaindb(collection):
    task_chain_id = secrets.token_hex(8)
    return ChainDB(collection, task_chain_id)


VALUES = [
    123,
    [1, "asd", b"xyz"],
    {
        "complex": 123,
        "object": b"asd",
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("val", VALUES)
async def test_basic(chaindb, val):
    await chaindb.set("asd", val)

    assert await chaindb.get("asd") == val


@pytest.mark.asyncio
async def test_missing_key(chaindb):
    with pytest.raises(MumbleException):
        await chaindb.get("asd")


@pytest.mark.asyncio
@pytest.mark.mongodb
@pytest.mark.parametrize("val", VALUES)
async def test_basic_real_mongodb(real_collection, val):
    """Confirms the in-memory mock matches real MongoDB for the same ChainDB usage."""
    task_chain_id = secrets.token_hex(8)
    chaindb = ChainDB(real_collection, task_chain_id)

    await chaindb.set("asd", val)

    assert await chaindb.get("asd") == val


@pytest.fixture
def recorded_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # set_tracer_provider() is process-global and one-shot, so patch the lookup
    # ChainDB performs rather than installing a provider for the whole suite
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "val,attribute",
    [
        ({"x": 1, "y": 2}, '{"x": 1, "y": 2}'),
        ([123, "asd"], '[123, "asd"]'),
        (["a", "b"], ("a", "b")),
    ],
)
async def test_value_recorded_as_span_attribute(
    chaindb, recorded_spans, caplog, val, attribute
):
    """Values otel rejects must still reach the span, without warning noise."""
    with caplog.at_level("WARNING", logger="opentelemetry"):
        await chaindb.set("asd", val)
        assert await chaindb.get("asd") == val

    assert [
        r.getMessage() for r in caplog.records if r.name.startswith("opentelemetry")
    ] == []

    spans = recorded_spans.get_finished_spans()
    assert [s.name for s in spans] == ["ChainDB.set(asd)", "ChainDB.get(asd)"]
    for span in spans:
        assert span.attributes is not None
        assert span.attributes["chaindb.value.asd"] == attribute
