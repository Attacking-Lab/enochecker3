import asyncio

import httpx
import pytest

from enochecker3 import Enochecker, HavocCheckerTaskMessage
from enochecker3.mock import MockAsyncMongoClient
from enochecker3.types import CheckerTaskResult, OfflineException


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
