"""
In-memory MongoDB mock for testing checkers without a real MongoDB server.

Wraps ``mongomock`` (a synchronous, in-process MongoDB fake) behind the same
async surface as pymongo's ``AsyncMongoClient`` / ``AsyncDatabase`` /
``AsyncCollection``, so it can be passed anywhere those are expected --
most notably as ``Enochecker(..., mongo_client=MockAsyncMongoClient())``.

Since mongomock has no native async API yet, method calls are simply
executed synchronously and wrapped in a coroutine; there is no real I/O to
await on top of an in-memory dict, so this adds no meaningful overhead.

Requires the "testing" extra (``enochecker3[testing]``), which pulls in
``mongomock``.
"""

import functools
from typing import Any, Iterator, List, Optional

import mongomock
import mongomock.collection
import mongomock.database


class _AsyncCursor:
    def __init__(self, cursor: mongomock.collection.Cursor) -> None:
        self._cursor = cursor

    def __aiter__(self) -> "_AsyncCursor":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._cursor)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length: Optional[int] = None) -> List[Any]:
        docs: Iterator[Any] = iter(self._cursor)
        if length is not None:
            docs = (doc for _, doc in zip(range(length), docs))
        return list(docs)

    def __getattr__(self, name: str) -> Any:
        # Chainable cursor methods (sort, limit, skip, ...) return a Cursor.
        attr = getattr(self._cursor, name)
        if not callable(attr):
            return attr

        @functools.wraps(attr)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            if isinstance(result, mongomock.collection.Cursor):
                return _AsyncCursor(result)
            return result

        return wrapper


class _AsyncProxy:
    """Wraps a synchronous mongomock object behind an async-compatible API."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getitem__(self, name: str) -> "_AsyncProxy":
        return _AsyncProxy(self._wrapped[name])

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._wrapped, name)

        if isinstance(
            attr, (mongomock.database.Database, mongomock.collection.Collection)
        ):
            return _AsyncProxy(attr)

        if not callable(attr):
            return attr

        @functools.wraps(attr)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            if isinstance(result, mongomock.collection.Cursor):
                return _AsyncCursor(result)
            if isinstance(
                result,
                (mongomock.database.Database, mongomock.collection.Collection),
            ):
                return _AsyncProxy(result)
            return result

        return wrapper


class MockAsyncMongoClient(_AsyncProxy):
    """
    Drop-in in-memory replacement for ``pymongo.AsyncMongoClient``.

    Backed by ``mongomock``, so it needs no MongoDB server, docker, or
    network access -- state lives purely in the Python process and starts
    out empty for every instance. Useful for unit-testing checkers built
    with `Enochecker` and for exercising `ChainDB` directly::

        checker = Enochecker(
            "myservice", 1337, mongo_client=MockAsyncMongoClient()
        )

    Only covers what checkers realistically need (finds, updates, indexes,
    ...); it is not a faithful reimplementation of the full MongoDB feature
    set, so behavior can diverge from a real server for advanced queries.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(mongomock.MongoClient(*args, **kwargs))
