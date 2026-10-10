"""Shared HTTP client policy for corpus builds.

Three things every fetcher needs, kept in one place so they are not
re-litigated per source:

* **Decompress.** The eCFR `/full/` endpoint returns HTTP 406 without an
  `Accept-Encoding` that permits compression, and the exports are large.
* **Identify.** A sysadmin reading their logs should see who is fetching and
  why, not a bare `python-httpx/…` string.
* **Verify safely.** `fam.state.gov` serves an incomplete certificate chain:
  curl and browsers fill in the missing intermediate because the OS has it
  cached, but OpenSSL with a bundled CA list does not, and the request fails
  with CERTIFICATE_VERIFY_FAILED. `truststore` delegates verification to the
  OS, which makes the same request curl makes. Verification is delegated or
  left on — never disabled.
"""

from __future__ import annotations

import ssl

import httpx

HEADERS = {
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "immigration-assistant-corpus-builder/1.0 (offline corpus build)",
}


def _verify() -> ssl.SSLContext | bool:
    try:
        import truststore
    except ImportError:  # pragma: no cover - depends on the environment
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def build_client(**overrides) -> httpx.AsyncClient:
    """An async client with the corpus-build policy applied."""
    options = {"timeout": 120.0, "headers": HEADERS, "follow_redirects": True, "verify": _verify()}
    options.update(overrides)
    return httpx.AsyncClient(**options)
