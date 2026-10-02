import asyncio

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


async def _http_server_with_upstream_header(value: str) -> asyncio.Server:
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

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_down_upstream(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    server = await _http_server_with_upstream_header("down")
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            await client.get("/")

        with pytest.raises(OfflineException):
            await checker._call_havoc(_havoc_task())


@pytest.mark.asyncio
async def test_haproxy_mode_allows_up_upstream(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    server = await _http_server_with_upstream_header("up")
    port = server.sockets[0].getsockname()[1]
    async with server:
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
    server = await _http_server_with_upstream_header("down")
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            response = await client.get("/")
            assert response.status_code == 200

        result = await checker._call_havoc(_havoc_task())

    assert result.result == CheckerTaskResult.OK


async def _slow_http_server_with_upstream_header(
    value: str, delay: float
) -> asyncio.Server:
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

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_router_slower_than_httpx_default(
    monkeypatch,
):
    """A down backend makes the router slow, so the 503 must still be read.

    httpx's 5s default read timeout would cut the response off before the
    X-CTFROUTE-Upstream header arrives and report Mumble instead.
    """
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    server = await _slow_http_server_with_upstream_header("down", delay=5.5)
    port = server.sockets[0].getsockname()[1]
    async with server:
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


async def _closing_server() -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _greet_then_close_server() -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        writer.write(b"hi\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_haproxy_mode_reports_offline_for_tcp_close_without_data(monkeypatch):
    monkeypatch.setenv("ENOCHECKER_HAPROXY_MODE", "1")
    server = await _closing_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
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
    server = await _greet_then_close_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
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
    server = await _closing_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, _writer = socket
            await reader.readuntil(b"\n")

        with pytest.raises(MumbleException):
            await checker._call_havoc(_havoc_task())
