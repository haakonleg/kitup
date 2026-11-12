import ssl

from kitup._utils import http


def test_ssl_context_is_cached_per_validation_mode(monkeypatch):
    calls = 0

    def fake_create_default_context() -> ssl.SSLContext:
        nonlocal calls
        calls += 1
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(http.ssl, "create_default_context", fake_create_default_context)
    http._ssl_context.cache_clear()
    try:
        first = http._ssl_context(validate_certs=True)
        second = http._ssl_context(validate_certs=True)
        unvalidated = http._ssl_context(validate_certs=False)

        assert first is second
        assert calls == 1
        assert unvalidated is not first
        assert unvalidated.verify_mode == ssl.CERT_NONE
        assert not unvalidated.check_hostname
    finally:
        http._ssl_context.cache_clear()
