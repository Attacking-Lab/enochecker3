"""Record the network connections a checker opens into ``metadata["flows"]``.

Each flow is a dict with the connection 5-tuple plus the Unix timestamps
(seconds, UTC) at which the connection was opened and closed::

    {
        "protocol": "tcp",
        "src_ip": "10.0.0.1", "src_port": 51234,
        "dst_ip": "10.0.0.2", "dst_port": 80,
        "start": 1699999999.123, "end": 1699999999.456,
    }

The dict is appended when the connection opens (so a flow shows up even for a
connection that never cleanly closes) and its ``end`` is filled in place when
the connection is torn down.
"""

import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from httpcore import AsyncNetworkBackend, AsyncNetworkStream
from httpcore._backends.base import SOCKET_OPTION

from .types import TaskMetadata

Flow = Dict[str, Any]
Address = Optional[Tuple[str, int]]


def _now() -> float:
    return time.time()


def record_flow(
    metadata: TaskMetadata,
    protocol: str,
    src: Address,
    dst: Address,
    start: float,
) -> Flow:
    """Append an open flow to ``metadata["flows"]`` and return it.

    The returned dict has ``end=None``; stamp it via :func:`close_flow` when the
    connection is torn down.
    """
    flow: Flow = {
        "protocol": protocol,
        "src_ip": src[0] if src else None,
        "src_port": src[1] if src else None,
        "dst_ip": dst[0] if dst else None,
        "dst_port": dst[1] if dst else None,
        "start": start,
        "end": None,
    }
    flows: List[Flow] = metadata.setdefault("flows", [])
    flows.append(flow)
    return flow


def close_flow(flow: Flow) -> None:
    if flow.get("end") is None:
        flow["end"] = _now()


class _FlowRecordingStream(AsyncNetworkStream):
    """Wrap an httpcore stream, tying one :data:`Flow` to the socket lifetime.

    A TLS upgrade (``start_tls``) reuses the same underlying socket, so the new
    stream shares this stream's flow; whichever wrapper is closed first stamps
    the end timestamp (guarded against a double stamp).
    """

    def __init__(
        self,
        inner: AsyncNetworkStream,
        metadata: TaskMetadata,
        start: float,
        flow: Optional[Flow] = None,
    ) -> None:
        self._inner = inner
        if flow is None:
            flow = record_flow(
                metadata,
                "tcp",
                inner.get_extra_info("client_addr"),
                inner.get_extra_info("server_addr"),
                start,
            )
        self._metadata = metadata
        self._flow = flow

    async def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return await self._inner.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        await self._inner.write(buffer, timeout)

    async def aclose(self) -> None:
        close_flow(self._flow)
        await self._inner.aclose()

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> AsyncNetworkStream:
        upgraded = await self._inner.start_tls(ssl_context, server_hostname, timeout)
        return _FlowRecordingStream(
            upgraded, self._metadata, self._flow["start"], self._flow
        )

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _FlowRecordingBackend(AsyncNetworkBackend):
    """Delegate to a real httpcore backend, recording every TCP connection."""

    def __init__(self, inner: AsyncNetworkBackend, metadata: TaskMetadata) -> None:
        self._inner = inner
        self._metadata = metadata

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        start = _now()
        stream = await self._inner.connect_tcp(
            host, port, timeout, local_address, socket_options
        )
        return _FlowRecordingStream(stream, self._metadata, start)

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[SOCKET_OPTION]] = None,
    ) -> AsyncNetworkStream:
        # Not a network 5-tuple; left unrecorded.
        return await self._inner.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def record_http_flows(client: Any, metadata: TaskMetadata) -> None:
    """Make an ``httpx.AsyncClient`` record the TCP connections its pool opens.

    Reaches into the transport's httpcore connection pool to wrap the network
    backend -- httpx exposes no public hook for connection-level events, so this
    is coupled to httpx/httpcore internals and no-ops if their shape changes.
    """
    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
        return
    pool._network_backend = _FlowRecordingBackend(backend, metadata)
