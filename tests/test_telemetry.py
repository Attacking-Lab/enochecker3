import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import RequestInfo
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from enochecker3 import (
    Enochecker,
    InternalErrorException,
    MumbleException,
    OfflineException,
    PutflagCheckerTaskMessage,
    TaskMetadata,
    telemetry,
)
from enochecker3.logging import SNIP_SUFFIX
from enochecker3.telemetry import (
    SPAN_ATTRIBUTE_LIMIT_ENV_VAR,
    as_span_attribute,
    request_span_name,
    span_attribute_limit,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        # accepted by otel as-is
        ("asd", "asd"),
        (123, 123),
        (1.5, 1.5),
        (True, True),
        (b"xyz", b"xyz"),
        ([], []),
        (["a", "b"], ["a", "b"]),
        ((1, 2), [1, 2]),
        (["a", None], ["a", None]),
        # rejected by otel, rendered as JSON instead
        ({"a": 1}, '{"a": 1}'),
        ((123, "asd"), '[123, "asd"]'),
        ([True, 1], "[true, 1]"),
        ([[1], [2]], "[[1], [2]]"),
        (None, "null"),
        (b"\xff", "\"b'\\\\xff'\""),
        ({"a": b"xyz"}, '{"a": "b\'xyz\'"}'),
    ],
)
def test_as_span_attribute(value, expected):
    assert as_span_attribute(value) == expected


def test_as_span_attribute_unserializable():
    circular = {}
    circular["self"] = circular

    assert as_span_attribute(circular) == repr(circular)


@pytest.fixture(autouse=True)
def default_attribute_limit(monkeypatch):
    monkeypatch.delenv(SPAN_ATTRIBUTE_LIMIT_ENV_VAR, raising=False)


def test_long_string_is_cut_to_the_limit():
    limit = span_attribute_limit()
    attribute = as_span_attribute("a" * (limit * 2))

    assert len(attribute.encode()) == limit
    assert attribute.endswith(SNIP_SUFFIX)


def test_long_bytes_stay_bytes_and_decode():
    limit = span_attribute_limit()
    attribute = as_span_attribute(b"a" * (limit * 2))

    assert isinstance(attribute, bytes)
    assert len(attribute) == limit
    assert attribute.decode().endswith(SNIP_SUFFIX)


def test_cut_does_not_split_a_character():
    limit = span_attribute_limit()
    # The cut lands mid-character for every odd offset of a two-byte character.
    attribute = as_span_attribute("\u00e4" * limit)

    assert len(attribute.encode()) <= limit
    attribute.encode().decode()


def test_rendered_value_is_cut_to_the_limit():
    """A ChainDB tuple of keys and proofs is what made this necessary."""
    limit = span_attribute_limit()
    attribute = as_span_attribute(("uid", b"\xff" * limit, "game", b"\xff" * limit))

    assert len(attribute.encode()) == limit
    assert attribute.endswith(SNIP_SUFFIX)


def test_oversized_sequence_becomes_one_marked_string():
    limit = span_attribute_limit()
    attribute = as_span_attribute(["a" * limit, "b" * limit])

    assert isinstance(attribute, str)
    assert len(attribute.encode()) == limit
    assert attribute.endswith(SNIP_SUFFIX)


def test_sequence_within_the_limit_stays_a_sequence():
    assert as_span_attribute(["a", "b"]) == ["a", "b"]


@pytest.mark.parametrize("limit,expected", [("1K", 1024), ("64", 64), ("1M", 1048576)])
def test_limit_is_overridable(monkeypatch, limit, expected):
    monkeypatch.setenv(SPAN_ATTRIBUTE_LIMIT_ENV_VAR, limit)

    assert span_attribute_limit() == expected
    assert len(as_span_attribute("a" * (expected * 2)).encode()) == expected


def test_zero_lifts_the_limit(monkeypatch):
    monkeypatch.setenv(SPAN_ATTRIBUTE_LIMIT_ENV_VAR, "0")
    value = "a" * (span_attribute_limit() + 100_000)

    assert as_span_attribute(value) == value


def test_malformed_limit_fails_at_startup(monkeypatch):
    monkeypatch.setenv(SPAN_ATTRIBUTE_LIMIT_ENV_VAR, "lots")

    with pytest.raises(ValueError, match="lots"):
        telemetry.setup_telemetry("MyChecker")


@pytest.mark.parametrize(
    "method,url,expected",
    [
        # the shapes the 2026 checkers produce
        (
            b"POST",
            "http://10.60.8.2:9300/api/process"
            "?filename=0075582b-9fa0-405d-8a7c-c3b2db21cca8&meta=base&tool=Converter",
            "POST /api/process",
        ),
        (
            b"POST",
            "http://10.60.9.2:9300/api/publish"
            "/0a7aa21c-d757-4b67-8c71-88c08a6d5199/9f5b87ff-b4aa-4cb0-9ced-457c19f43693",
            "POST /api/publish/{id}/{id}",
        ),
        (
            b"GET",
            "http://10.60.36.2:9300/api/metadata/2e7bf0e3-dc4b-4dbf-b7d3-406650eb6594/base",
            "GET /api/metadata/{id}/base",
        ),
        # id shapes other than a uuid
        (b"GET", "http://10.60.1.2:9000/users/42/notes", "GET /users/{id}/notes"),
        (b"GET", "http://10.60.1.2:9000/o/deadbeefdeadbeef42", "GET /o/{id}"),
        # nothing id-shaped to replace
        (b"POST", "http://10.60.8.2:9300/api/login", "POST /api/login"),
        (b"GET", "http://10.60.1.2:9000/v1/items", "GET /v1/items"),
        (b"GET", "http://10.60.1.2:9000", "GET /"),
    ],
)
def test_request_span_name(method, url, expected):
    request = RequestInfo(
        method=method,
        url=httpx.URL(url),
        headers=None,
        stream=None,
        extensions=None,
    )

    assert request_span_name(request) == expected


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4317"}, ("http://a:4317", "grpc")),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4317",
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "https://b/otlp/v1/logs",
                "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/protobuf",
            },
            ("https://b/otlp/v1/logs", "http/protobuf"),
        ),
        (
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            },
            ("http://a:4317", "http/protobuf"),
        ),
    ],
)
def test_otlp_target_logs(monkeypatch, env, expected):
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert telemetry.otlp_target("LOGS") == expected


def test_otlp_target_rejects_unknown_protocol(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "http/json")
    with pytest.raises(ValueError, match="http/json"):
        telemetry.otlp_target("LOGS")


def test_resource_takes_env_attributes(monkeypatch):
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES", "k8s.cluster.name=c1,deployment.environment=prod"
    )
    monkeypatch.setenv("OTEL_SERVICE_NAME", "ignored")
    resource = telemetry.Resource.create({telemetry.SERVICE_NAME: "MyChecker"})
    assert resource.attributes["k8s.cluster.name"] == "c1"
    assert resource.attributes["deployment.environment"] == "prod"
    assert resource.attributes[telemetry.SERVICE_NAME] == "MyChecker"


@pytest.fixture
def recorded_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # set_tracer_provider() is process-global and one-shot, so patch the lookup
    # the fastapi instrumentation performs when the app is built
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    yield exporter
    provider.shutdown()


def _task_payload():
    return PutflagCheckerTaskMessage(
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
        flag="test",
    ).model_dump()


def _span_attributes(recorded_spans):
    attributes = {}
    for span in recorded_spans.get_finished_spans():
        attributes.update(span.attributes or {})
    return attributes


@pytest.mark.parametrize(
    "raised,expected_result",
    [
        (None, "OK"),
        (MumbleException("broken"), "MUMBLE"),
        (OfflineException("gone"), "OFFLINE"),
        (InternalErrorException("boom"), "INTERNAL_ERROR"),
    ],
)
def test_result_metadata_lands_on_the_span(recorded_spans, raised, expected_result):
    """Whatever a task collects explains its result, including when it failed."""
    checker = Enochecker("test_service", 8080)

    @checker.putflag(0)
    async def putflag_test(metadata: TaskMetadata) -> str:
        metadata["attempts"] = 3
        metadata["nested"] = {"k": [1, 2]}
        if raised is not None:
            raise raised
        return "attack_info"

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    client = TestClient(checker.app)
    response = client.post("/", json=_task_payload())
    response.raise_for_status()
    assert response.json()["result"] == expected_result

    attributes = _span_attributes(recorded_spans)
    assert attributes["enochecker.result.metadata.attempts"] == 3
    assert attributes["enochecker.result.metadata.nested"] == '{"k": [1, 2]}'


def test_no_metadata_attributes_without_metadata(recorded_spans):
    checker = Enochecker("test_service", 8080)

    @checker.putflag(0)
    async def putflag_test() -> str:
        return "attack_info"

    @checker.getflag(0)
    async def getflag_test() -> None:
        pass

    client = TestClient(checker.app)
    client.post("/", json=_task_payload()).raise_for_status()

    assert not [
        key
        for key in _span_attributes(recorded_spans)
        if key.startswith("enochecker.result.metadata.")
    ]
