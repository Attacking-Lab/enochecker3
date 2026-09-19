import asyncio

import httpx
import pytest

from enochecker3 import Enochecker, HavocCheckerTaskMessage, MumbleException, TaskMetadata
from enochecker3.enochecker import AsyncSocket
from enochecker3.mock import MockAsyncMongoClient


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


async def _echo_line_server() -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readline()
        writer.write(b"ok\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def _http_server() -> asyncio.Server:
    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi"
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


@pytest.mark.asyncio
async def test_socket_flow_recorded() -> None:
    server = await _echo_line_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, writer = socket
            writer.write(b"hello\n")
            await writer.drain()
            await reader.readline()

        result = await checker._call_havoc(_havoc_task())

    flows = result.metadata["flows"]
    assert len(flows) == 1
    flow = flows[0]
    assert flow["protocol"] == "tcp"
    assert flow["dst_ip"] == "127.0.0.1"
    assert flow["dst_port"] == port
    assert flow["src_ip"] == "127.0.0.1"
    assert isinstance(flow["src_port"], int)
    assert flow["end"] is not None and flow["end"] >= flow["start"]


@pytest.mark.asyncio
async def test_http_client_flow_recorded() -> None:
    server = await _http_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(client: httpx.AsyncClient) -> None:
            response = await client.get("/")
            assert response.status_code == 200

        result = await checker._call_havoc(_havoc_task())

    flows = result.metadata["flows"]
    assert len(flows) == 1
    flow = flows[0]
    assert flow["protocol"] == "tcp"
    assert flow["dst_port"] == port
    assert isinstance(flow["src_port"], int)
    assert flow["end"] is not None and flow["end"] >= flow["start"]


@pytest.mark.asyncio
async def test_flow_recorded_when_checker_raises() -> None:
    """Flows opened before a checker method fails must still reach the result.

    Without this, an exception (e.g. MumbleException) would discard whatever
    flows had already been recorded, leaving the engine unable to see any
    traffic for a failed run.
    """
    server = await _echo_line_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(socket: AsyncSocket) -> None:
            reader, writer = socket
            writer.write(b"hello\n")
            await writer.drain()
            await reader.readline()
            raise MumbleException("service said something wrong")

        with pytest.raises(MumbleException) as exc_info:
            await checker._call_havoc(_havoc_task())

    flows = exc_info.value.metadata["flows"]
    assert len(flows) == 1
    assert flows[0]["dst_port"] == port
    assert flows[0]["end"] is not None


@pytest.mark.asyncio
async def test_socket_and_http_flows_coexist() -> None:
    server = await _http_server()
    port = server.sockets[0].getsockname()[1]
    async with server:
        checker = Enochecker("svc", port, mongo_client=MockAsyncMongoClient())

        @checker.havoc(0)
        async def havoc(
            client: httpx.AsyncClient, socket: AsyncSocket, metadata: TaskMetadata
        ) -> None:
            await client.get("/")
            reader, writer = socket
            writer.write(b"GET / HTTP/1.0\r\n\r\n")
            await writer.drain()
            await reader.read()
            # Both open connections are visible while the method still runs.
            assert len(metadata["flows"]) == 2

        result = await checker._call_havoc(_havoc_task())

    flows = result.metadata["flows"]
    assert len(flows) == 2
    assert {f["dst_port"] for f in flows} == {port}
    assert all(f["end"] is not None and f["end"] >= f["start"] for f in flows)
