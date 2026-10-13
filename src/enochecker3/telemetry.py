import json
import logging
import os
import re
from typing import Any, Iterator, Optional, cast

import httpx
from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
    OTLPLogExporter as GrpcLogExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as HttpLogExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpSpanExporter,
)
from opentelemetry.instrumentation.httpx import RequestInfo, ResponseInfo
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs._internal.export import (  # type: ignore[attr-defined]
    BatchLogRecordProcessor,
    LogRecordExporter,
    LogRecordProcessor,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (  # type: ignore[attr-defined]
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanProcessor,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, SpanKind, Tracer, _Links
from opentelemetry.util import types
from opentelemetry.util._decorator import _agnosticcontextmanager

from enochecker3.logging import SNIP_SUFFIX, parse_size
from enochecker3.telemetry_attributes import OpenTelemetryCommonAttributesContext


_ATTRIBUTE_SCALAR_TYPES = (bool, str, bytes, int, float)

# The size of an attribute carrying a checker's own data -- a ChainDB value, a
# piece of task metadata -- is whatever the checker put there, and a crypto
# service puts keys, ciphertexts and proofs there. Rendered onto a span those
# reach hundreds of kilobytes each, and a batch of them exceeds what a
# collector accepts on one export, which costs the whole batch rather than the
# oversized attribute. Every value is therefore cut to this many bytes.
SPAN_ATTRIBUTE_LIMIT_ENV_VAR = "ENOCHECKER_SPAN_ATTRIBUTE_LIMIT"
DEFAULT_SPAN_ATTRIBUTE_LIMIT = "4K"


def span_attribute_limit() -> int:
    """The byte bound on one span attribute value; `0` lifts it."""
    return parse_size(
        os.environ.get(SPAN_ATTRIBUTE_LIMIT_ENV_VAR) or DEFAULT_SPAN_ATTRIBUTE_LIMIT
    )


def _attribute_size(value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode())
    return len(str(value).encode())


def _truncate_str(value: str, limit: int) -> str:
    keep = limit - len(SNIP_SUFFIX)
    if keep <= 0:
        return value.encode()[:limit].decode("utf-8", errors="ignore")
    return value.encode()[:keep].decode("utf-8", errors="ignore") + SNIP_SUFFIX


def _truncate_bytes(value: bytes, limit: int) -> bytes:
    suffix = SNIP_SUFFIX.encode()
    keep = limit - len(suffix)
    if keep <= 0:
        return value[:limit]
    # The round trip drops a character the cut split in half, which otel would
    # otherwise fail to decode.
    return value[:keep].decode("utf-8", errors="ignore").encode() + suffix


def _is_attribute_scalar(value: Any) -> bool:
    if isinstance(value, bytes):
        # otel decodes bytes attributes and warns when that fails
        try:
            value.decode()
        except UnicodeDecodeError:
            return False
        return True
    return isinstance(value, _ATTRIBUTE_SCALAR_TYPES)


def as_span_attribute(value: Any) -> types.AttributeValue:
    """Coerce a value into something otel accepts as a span attribute.

    Otel takes only scalars and homogeneous sequences of them, and drops
    anything else with a warning on the `opentelemetry.attributes` logger.
    Values it would reject are rendered as JSON instead of turning into noise.

    Anything longer than `span_attribute_limit()` is cut to it and marked with
    a `... <SNIP>` suffix. A sequence cannot carry that marker without breaking
    the element type otel requires it to keep, so an oversized one is rendered
    as a single marked string instead.
    """
    limit = span_attribute_limit()
    if _is_attribute_scalar(value):
        if limit and _attribute_size(value) > limit:
            if isinstance(value, bytes):
                return _truncate_bytes(value, limit)
            return _truncate_str(cast(str, value), limit)
        return cast(types.AttributeValue, value)
    if isinstance(value, (list, tuple)):
        element_types = {type(e) for e in value if e is not None}
        if (
            len(element_types) <= 1
            and all(e is None or _is_attribute_scalar(e) for e in value)
            and not (limit and sum(_attribute_size(e) for e in value) > limit)
        ):
            return cast(types.AttributeValue, list(value))
    try:
        rendered = json.dumps(value, default=repr)
    except (TypeError, ValueError):
        rendered = repr(value)
    if limit and _attribute_size(rendered) > limit:
        return _truncate_str(rendered, limit)
    return rendered


def _setup_logging(resource: Resource, log_exporter: LogRecordExporter) -> None:
    provider = LoggerProvider(resource=resource)
    if os.environ.get("OTEL_EXPORTER_DISABLE_BATCH", default=False):
        processor: LogRecordProcessor = SimpleLogRecordProcessor(log_exporter)
    else:
        processor = BatchLogRecordProcessor(log_exporter)
    provider.add_log_record_processor(processor)
    set_logger_provider(provider)
    logging.root.addFilter(CommonAttributesLogFilter())
    # INFO rather than DEBUG because file logging raises the root logger to
    # DEBUG, and those records are meant for the local files, not for export.
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    handler.addFilter(DropOtelErrorsLogFilter())
    logging.root.addHandler(handler)


def _setup_tracing(resource: Resource, span_exporter: SpanExporter) -> None:
    provider = CommonAttributesTracerProvider(sampler=ALWAYS_ON, resource=resource)
    if os.environ.get("OTEL_EXPORTER_DISABLE_BATCH", default=False):
        processor: SpanProcessor = SimpleSpanProcessor(span_exporter)
    else:
        processor = BatchSpanProcessor(span_exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)


_telemetry_initialized = False

# The values the OpenTelemetry specification defines for
# OTEL_EXPORTER_OTLP_PROTOCOL that the Python exporters offer; http/json is
# not one of them.
_OTLP_PROTOCOLS = ("grpc", "http/protobuf")


def _span_exporter(
    protocol: str, endpoint: Optional[str], headers: dict[str, str]
) -> SpanExporter:
    if protocol == "grpc":
        return GrpcSpanExporter(endpoint=endpoint, headers=headers)
    return HttpSpanExporter(endpoint=endpoint, headers=headers)


def _log_exporter(
    protocol: str, endpoint: Optional[str], headers: dict[str, str]
) -> LogRecordExporter:
    if protocol == "grpc":
        return GrpcLogExporter(endpoint=endpoint, headers=headers)
    return HttpLogExporter(endpoint=endpoint, headers=headers)


def otlp_target(signal: str) -> tuple[Optional[str], str]:
    """The endpoint and protocol one signal exports to, from the standard
    OTEL_EXPORTER_OTLP_* variables: the signal-specific name wins over the
    generic one, and the protocol defaults to gRPC.

    Letting the two signals part ways is what allows a checker to send
    traces to a collector that speaks gRPC only and logs to one that speaks
    HTTP only. An HTTP endpoint is used verbatim, so it has to carry its
    signal path (/v1/traces, /v1/logs); a gRPC one is host and port.
    """
    endpoint = os.environ.get(
        f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT"
    ) or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    protocol = (
        os.environ.get(f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "grpc"
    )
    if protocol not in _OTLP_PROTOCOLS:
        raise ValueError(
            f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL {protocol!r} is not one of "
            f"{list(_OTLP_PROTOCOLS)}"
        )
    return endpoint, protocol


def setup_telemetry(component: str) -> None:
    global _telemetry_initialized

    # Resolved before the switch below, and discarded: attributes are cut
    # whether or not anything is exported, so a malformed bound has to fail the
    # checker at startup rather than inside the first task that records one.
    span_attribute_limit()

    # The generic endpoint is the switch: without it nothing is exported, no
    # matter what the signal-specific variables say.
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or _telemetry_initialized:
        return

    # create() and not the constructor: it merges OTEL_RESOURCE_ATTRIBUTES,
    # which is how a deployment stamps where a checker runs onto every span
    # and log record. The explicit service.name still wins.
    resource = Resource.create({SERVICE_NAME: component})

    headers = {}
    otlp_key = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS_AUTHORIZATION", None)
    if otlp_key:
        headers["authorization"] = otlp_key

    traces_endpoint, traces_protocol = otlp_target("TRACES")
    _setup_tracing(resource, _span_exporter(traces_protocol, traces_endpoint, headers))

    logs_endpoint, logs_protocol = otlp_target("LOGS")
    _setup_logging(resource, _log_exporter(logs_protocol, logs_endpoint, headers))

    _telemetry_initialized = True


class CommonAttributesLogFilter(logging.Filter):
    """Add common attributes to each log message"""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (
            OpenTelemetryCommonAttributesContext.current_attributes().to_dict().items()
        ):
            setattr(record, key, value)
        return True


class DropOtelErrorsLogFilter(logging.Filter):
    """Drop all otel related errors from the log output, so that we won't have feedback loops."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


class CommonAttributesTracer(Tracer):
    """Wrap tracers to add common attributes to spans"""

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer

    @classmethod
    def add_span_attributes(cls, span: Span) -> Span:
        # when tracing is disabled, trying to add attributes fails
        if not span.is_recording():
            return span
        span = cast(SdkSpan, span)
        for k, v in (
            OpenTelemetryCommonAttributesContext.current_attributes().to_dict().items()
        ):
            if not span.attributes or k not in span.attributes:
                span.set_attribute(k, v)
        return span

    def start_span(
        self,
        name: str,
        context: Optional[Context] = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: types.Attributes = None,
        links: _Links = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> Span:
        span = self._tracer.start_span(
            name,
            context,
            kind,
            attributes,
            links,
            start_time,
            record_exception,
            set_status_on_exception,
        )
        return self.add_span_attributes(span)

    @_agnosticcontextmanager
    def start_as_current_span(
        self,
        name: str,
        context: Optional[Context] = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: types.Attributes = None,
        links: _Links = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> Iterator[Span]:
        with self._tracer.start_as_current_span(
            name,
            context,
            kind,
            attributes,
            links,
            start_time,
            record_exception,
            set_status_on_exception,
            end_on_exit,
        ) as span:  # type: Span
            yield self.add_span_attributes(span)


class CommonAttributesTracerProvider(TracerProvider):
    """Wrap tracer providers to add common attributes to spans"""

    def get_tracer(
        self,
        instrumenting_module_name: str,
        instrumenting_library_version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Optional[types.Attributes] = None,
    ) -> Tracer:
        return CommonAttributesTracer(
            super().get_tracer(
                instrumenting_module_name,
                instrumenting_library_version,
                schema_url,
                attributes,
            )
        )


def _nop(*args: Any, **kwargs: Any) -> None:
    pass


_ID_PATH_SEGMENT = re.compile(
    r"(?<=/)(?:"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9]+"
    r"|[0-9a-fA-F]{16,}"
    r")(?=/|$)"
)


def request_span_name(request: RequestInfo) -> str:
    """Name an outgoing request after its route rather than its URL.

    A span name ends up as a metric label wherever these spans are turned
    into metrics, so it has to come from a set that is bounded before the
    request is made. A URL is not: it carries the team's own address, and
    checkers routinely put a freshly generated id in the path or the query.
    Naming spans after it opened one metric per request, each holding a
    single measurement -- three million of them on the 2026 finals stage,
    which is more than the metrics backend accepts for a whole event.

    Only the path survives, with id-shaped segments replaced. The address is
    already a dimension of every task, and the full URL stays on the span as
    `http.url`, so nothing is lost from a trace.
    """
    return f"{request.method.decode()} {_ID_PATH_SEGMENT.sub('{id}', request.url.path)}"


async def async_request_hook(span: Span, request: RequestInfo) -> None:
    span.update_name(request_span_name(request))
    if request.headers:
        for k, v in request.headers.items():
            span.set_attribute(f"http.headers.{k}", v)


async def async_response_hook(
    span: Span,
    request: RequestInfo,
    response: ResponseInfo,
) -> None:
    pass


def instrument_httpx_without_propagation(client: httpx.AsyncClient) -> None:
    """
    We do not want to transport traceparent headers towards services (fingerprinting etc).
    There's no setting so we must nop out the injection.
    """
    from opentelemetry.instrumentation import httpx as httpx_instrumentation

    httpx_instrumentation._inject_propagation_headers = _nop  # ty: ignore[invalid-assignment]
    httpx_instrumentation.HTTPXClientInstrumentor.instrument_client(
        client,
        request_hook=async_request_hook,
        response_hook=async_response_hook,
    )
