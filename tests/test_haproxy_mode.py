import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from enochecker3 import Enochecker, HavocCheckerTaskMessage
from enochecker3.enochecker import AsyncSocket
from enochecker3.mock import MockAsyncMongoClient
from enochecker3.types import CheckerTaskResult, MumbleException, OfflineException


def _havoc_task() -> HavocCheckerTaskMessage:
    return HavocCheckerTaskMessage(
        task_id=0,
        address="127.0.0.1",
        team_id=0,
        team_name="team_name",
        current_round_id=0,
        related_round_id=0,
        variant_id=0,
        timeout=15000,
        round_length=60000,
        task_chain_id="task_chain_id",
    )


_Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@asynccontextmanager
async def _serving(handle: _Handler) -> AsyncIterator[int]:
    """Serve `handle` on an ephemeral port, leaving no connection behind.

    asyncio.Server.wait_closed() only awaits live connection handlers from
    Python 3.12 on, so a handler still mid-sleep when a test ends keeps its
    accepted socket alive and -W error turns the eventual ResourceWarning
    into a failure in whichever later test the collection lands on.
    Cancelling the handlers before wait_closed() also keeps teardown from
    waiting out a deliberate hang on the versions that do await them.
    """
    connections: list[tuple[asyncio.Task[None], asyncio.StreamWriter]] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        connections.append((task, writer))
        await handle(reader, writer)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        for task, writer in connections:
            task.cancel()
            writer.close()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*(task for task, _ in connections), return_exceptions=True)
        await asyncio.gather(
            *(writer.wait_closed() for _, writer in connections),
            return_exceptions=True,
        )


def _upstream_header_handler(value: str) -> _Handler:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n"
            f"X-CTFROUTE-Upstream: {value}\r\n\r\nhi".encode()
        )
        await writer.drain()
        writer.close()

    return handle


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_down_upstream(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_upstream_header_handler("down")) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_allows_up_upstream(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_upstream_header_handler("up")) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            response = await client.get("/")
            assert response.status_code == 200

        result = await checker._call_havoc(_havoc_task())

    assert result.result == CheckerTaskResult.OK


@pytest.mark.asyncio
async def test_down_upstream_ignored_when_haproxy_mode_disabled(monkeypatch):
    monkeypatch.delenv("ENOCHECKER_HAPROXY_MODE", raising=False)
    async with _serving(_upstream_header_handler("down")) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            response = await client.get("/")
            assert response.status_code == 200

        result = await checker._call_havoc(_havoc_task())

    assert result.result == CheckerTaskResult.OK


def _slow_upstream_header_handler(value: str, delay: float) -> _Handler:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(delay)
        writer.write(
            f"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 2\r\n"
            f"Connection: close\r\nX-CTFROUTE-Upstream: {value}\r\n\r\nhi".encode()
        )
        await writer.drain()
        writer.close()

    return handle


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_router_slower_than_httpx_default(
    monkeypatch,
):
    """A down backend makes the router slow, so the 503 must still be read.

    httpx's 5s default read timeout would cut the response off before the
    X-CTFROUTE-Upstream header arrives and report Mumble instead.
    """
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_slow_upstream_header_handler("down", delay=5.5)) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_havoc_task())


@pytest.mark.asyncio
async def test_injected_client_read_timeout_follows_task_timeout():
    checker = Enochecker("svc", 1234, mongo_client=MockAsyncMongoClient())
    captured: list[httpx.Timeout] = []

    @checker.havoc(0)
    async def havoc(client: httpx.AsyncClient) -> None:
        captured.append(client.timeout)

    task = _havoc_task()
    task.timeout = 42000
    await checker._call_havoc(task)

    assert captured[0].read == 42.0
    assert captured[0].connect == 5.0


# Long enough to outlive the short task budget below.
_HANG_S = 2.0


def _short_havoc_task() -> HavocCheckerTaskMessage:
    task = _havoc_task()
    # 2.5s less TIMEOUT_BUFFER leaves the method half a second to run, so the
    # timeout branch is reached without the test waiting on it.
    task.timeout = 2500
    return task


async def _silent_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    await asyncio.sleep(_HANG_S)
    writer.close()


async def _trickle_then_hang_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"HTTP/1.1 200 OK\r\n")
    await writer.drain()
    await asyncio.sleep(_HANG_S)
    writer.close()


async def _greet_then_hang_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"hi\n")
    await writer.drain()
    await asyncio.sleep(_HANG_S)
    writer.close()


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_http_timeout_without_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_silent_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_short_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_reports_mumble_for_http_timeout_after_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_trickle_then_hang_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_short_havoc_task())


@pytest.mark.asyncio
async def test_http_timeout_without_data_is_mumble_when_haproxy_mode_disabled(
    monkeypatch,
):
    monkeypatch.delenv("ENOCHECKER_HAPROXY_MODE", raising=False)
    async with _serving(_silent_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_short_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_socket_timeout_without_data(
    monkeypatch,
):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_silent_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _ = socket
            await reader.readuntil(b"\n")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_short_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_reports_mumble_for_socket_timeout_after_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_greet_then_hang_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _ = socket
            await reader.readuntil(b"\n")
            await reader.readuntil(b"\n")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_short_havoc_task())


async def _closing_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.close()


async def _greet_then_close_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    writer.write(b"hi\n")
    await writer.drain()
    writer.close()


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_tcp_close_without_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_closing_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _writer = socket
            await reader.readuntil(b"\n")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_keeps_mumble_for_tcp_close_after_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    async with _serving(_greet_then_close_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _writer = socket
            await reader.readline()
            # A second read past the server's close, with no more data coming,
            # is the abrupt-close case that should stay a MumbleException.
            await reader.readuntil(b"\n")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_havoc_task())


@pytest.mark.asyncio
async def test_tcp_close_without_data_stays_mumble_when_haproxy_mode_disabled(
    monkeypatch,
):
    monkeypatch.delenv("ENOCHECKER_HAPROXY_MODE", raising=False)
    async with _serving(_closing_handler) as port:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _writer = socket
            await reader.readuntil(b"\n")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_havoc_task())
