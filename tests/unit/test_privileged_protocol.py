from __future__ import annotations

import pytest

from kitup._privileged.protocol import Request, Response


def test_request_round_trip():
    request = Request("write", {"path": "/tmp/x", "content": "abc"})

    line = request.encode()

    assert line == '{"action":"write","params":{"path":"/tmp/x","content":"abc"}}'
    assert Request.decode(line) == request


def test_request_decode_defaults_params():
    request = Request.decode('{"action":"stat"}')

    assert request == Request("stat", {})
    with pytest.raises(ValueError, match="action"):
        Request.decode('{"params": {}}')


def test_request_rejects_non_object_and_bad_params():
    with pytest.raises(ValueError, match="JSON object"):
        Request.decode("[]")
    with pytest.raises(ValueError, match="params"):
        Request("stat", "not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="params"):
        Request.decode('{"action": "stat", "params": []}')


def test_response_success_and_empty():
    assert Response(result={"exists": True}).encode() == '{"result":{"exists":true}}'
    assert Response().encode() == "{}"
    assert Response.decode('{"result": {"exists": true}}') == Response(
        result={"exists": True}
    )


def test_response_error():
    line = Response(error="ValueError: no").encode()

    assert line == '{"error":"ValueError: no"}'
    assert Response.decode(line).error == "ValueError: no"
    assert Response.decode(line).result is None


def test_response_fatal_error():
    line = Response(error="request too large", fatal=True).encode()

    assert line == '{"error":"request too large","fatal":true}'
    response = Response.decode(line)
    assert response.error == "request too large"
    assert response.fatal is True


def test_malformed_json_raises_value_error():
    with pytest.raises(ValueError, match="invalid JSON"):
        Request.decode("not json")
    with pytest.raises(ValueError, match="invalid JSON"):
        Response.decode("not json")


def test_response_rejects_non_string_error():
    with pytest.raises(ValueError, match="error"):
        Response.decode('{"error": 1}')


