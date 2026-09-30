import httpx
import pytest
from opentelemetry.instrumentation.httpx import RequestInfo

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
