from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "nginx-miniapp.example.conf"


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_miniapp_nginx_csp_allows_telegram_sdk_without_inline_scripts():
    text = _template()
    csp_lines = [line for line in text.splitlines() if "Content-Security-Policy" in line]

    assert csp_lines
    assert all("script-src 'self' https://telegram.org" in line for line in csp_lines)
    assert all("script-src 'self' 'unsafe-inline'" not in line for line in csp_lines)
    assert all("'unsafe-eval'" not in line for line in csp_lines)
    assert all("frame-ancestors https://web.telegram.org https://*.telegram.org" in line for line in csp_lines)


def test_miniapp_nginx_blocks_sensitive_paths_before_spa_fallback():
    text = _template()
    hidden_location = text.index("location ~ ^/(?:\\.env")
    spa_location = text.index("location / {")

    assert hidden_location < spa_location
    for path in (
        "\\.env",
        "\\.env(?:\\..*)?",
        "\\.git(?:/.*)?",
        "config/\\.env",
        "app/\\.env",
        "api/\\.env",
        "application/\\.env",
        "functions/\\.env",
    ):
        assert path in text
    assert ".well-known/acme-challenge" not in text


def test_miniapp_nginx_cache_locations_keep_security_headers():
    text = _template()
    assert 'add_header Cache-Control "public, max-age=31536000, immutable" always;' in text
    assert 'add_header Cache-Control "no-store, no-cache, must-revalidate" always;' in text

    for marker in ("location /assets/ {", "location / {", "location = /favicon.ico {"):
        block = text[text.index(marker):]
        block = block[:block.index("\n    }")]
        assert "add_header X-Content-Type-Options nosniff always;" in block
        assert "add_header Referrer-Policy no-referrer always;" in block
        assert "add_header Content-Security-Policy" in block
