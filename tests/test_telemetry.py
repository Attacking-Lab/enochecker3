import httpx
import pytest
from opentelemetry.instrumentation.httpx import RequestInfo

from enochecker3 import telemetry
from enochecker3.telemetry import as_span_attribute, request_span_name


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
