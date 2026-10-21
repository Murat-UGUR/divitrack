"""Küçük HTTP yardımcıları (stdlib `urllib`). Testlerde `Transport` sahte bir fonksiyonla değiştirilir."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# (url, method, headers, body) → yanıt gövdesi
Transport = Callable[[str, str, dict[str, str], bytes | None], bytes]


def urllib_transport(timeout: float = 20) -> Transport:
    def send(url: str, method: str, headers: dict[str, str], body: bytes | None) -> bytes:
        request = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **headers})
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — sabit https adresleri
            return bytes(response.read())

    return send


def get_json(transport: Transport, url: str, headers: dict[str, str] | None = None) -> Any:
    return json.loads(transport(url, "GET", {"Accept": "application/json", **(headers or {})}, None))


def post_json(transport: Transport, url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
    return json.loads(transport(url, "POST", headers, body))
