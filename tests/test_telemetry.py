import pytest

from enochecker3.telemetry import as_span_attribute


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
