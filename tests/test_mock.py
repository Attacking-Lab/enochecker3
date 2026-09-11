import inspect
import os

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from enochecker3.mock import MockAsyncMongoClient


@pytest_asyncio.fixture
async def collection() -> AsyncCollection:
    """In-memory mock collection, no MongoDB server required."""
    mongo = MockAsyncMongoClient()
    yield mongo["enochecker3_tests"]["mock_find"]
    await mongo.close()


@pytest_asyncio.fixture
async def real_collection() -> AsyncCollection:
    """Collection backed by a real MongoDB server, needs --with_mongodb."""
    mongo: AsyncMongoClient = AsyncMongoClient(os.getenv("MONGO_HOST", "localhost"))
    coll = mongo["enochecker3_tests"]["mock_find"]
    await coll.delete_many({})
    yield coll
    await mongo.close()


def test_find_is_not_a_coroutine_function() -> None:
    """Mirrors real pymongo: AsyncCollection.find() is synchronous, unlike
    the other CRUD methods (find_one, replace_one, ...)."""
    collection = MockAsyncMongoClient()["db"]["coll"]

    assert not inspect.iscoroutinefunction(collection.find)


@pytest.mark.asyncio
async def test_find_returns_cursor_usable_with_async_for(collection: AsyncCollection):
    await collection.insert_one({"key": "a", "value": 1})
    await collection.insert_one({"key": "b", "value": 2})

    cursor = collection.find({})
    assert not inspect.isawaitable(cursor)

    docs = [doc async for doc in cursor]

    assert sorted(doc["key"] for doc in docs) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.mongodb
async def test_find_returns_cursor_usable_with_async_for_real_mongodb(
    real_collection: AsyncCollection,
):
    """Confirms the in-memory mock matches real MongoDB for find()'s calling
    convention: synchronous, returning a cursor you `async for` over."""
    await real_collection.insert_one({"key": "a", "value": 1})
    await real_collection.insert_one({"key": "b", "value": 2})

    cursor = real_collection.find({})
    assert not inspect.isawaitable(cursor)

    docs = [doc async for doc in cursor]

    assert sorted(doc["key"] for doc in docs) == ["a", "b"]
