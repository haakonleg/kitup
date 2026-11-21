import json

import pytest

from kitup._privileged.protocol import Request, Response


def test_request_round_trip_defaults_and_validation():
    request = Request("write", {"path": "/tmp/x"}, content_length=3)
    line = request.encode()
    assert line == '{"action":"write","params":{"path":"/tmp/x"},"content_length":3}'
    assert Request.decode(line) == request

    request = Request("stat", {"path": "/tmp/x"})
    assert request.encode() == '{"action":"stat","params":{"path":"/tmp/x"}}'
    assert Request.decode(request.encode()) == request

    request = Request.decode('{"action":"stat"}')
    assert request == Request("stat", {})

    with pytest.raises(ValueError, match="action"):
        Request.decode('{"params": {}}')
    with pytest.raises(ValueError, match="JSON object"):
        Request.decode("[]")
    with pytest.raises(ValueError, match="params"):
        Request("stat", "not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="params"):
        Request.decode('{"action": "stat", "params": []}')

    for value in (-1, True, "3", 1.5):
        with pytest.raises(ValueError, match="content_length"):
            Request("read", {}, content_length=value)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="content_length"):
            Request.decode(f'{{"action": "read", "content_length": {json.dumps(value)}}}')


def test_response_round_trip_empty_content_and_errors():
    assert Response(result={"exists": True}).encode() == '{"result":{"exists":true}}'
    assert Response().encode() == "{}"
    assert Response.decode('{"result": {"exists": true}}') == Response(result={"exists": True})

    empty = Response(result={}, content_length=0, content=b"")
    absent = Response(result={})
    assert empty.encode() == '{"result":{},"content_length":0}'
    assert absent.encode() == '{"result":{}}'
    assert Response.decode(empty.encode()).content_length == 0
    assert Response.decode(absent.encode()).content_length is None

    line = Response(error="ValueError: no").encode()
    assert line == '{"error":"ValueError: no"}'
    assert Response.decode(line).error == "ValueError: no"
    assert Response.decode(line).result is None

    line = Response(error="request too large", fatal=True).encode()
    assert line == '{"error":"request too large","fatal":true}'
    response = Response.decode(line)
    assert response.error == "request too large"
    assert response.fatal is True


def test_response_rejects_invalid_payloads():
    with pytest.raises(ValueError, match="invalid JSON"):
        Request.decode("not json")
    with pytest.raises(ValueError, match="invalid JSON"):
        Response.decode("not json")

    with pytest.raises(ValueError, match="error"):
        Response.decode('{"error": 1}')

    for value in (-1, True, "3", 1.5):
        with pytest.raises(ValueError, match="content_length"):
            Response.decode(f'{{"content_length": {json.dumps(value)}}}')
