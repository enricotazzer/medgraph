"""Polite, verified downloads for the knowledge store.

Every file is written atomically (to a temporary name, then renamed) and checked against its
pinned SHA-256 when one is given, so a changed document is refused, never silently replaced.
Requests identify medgraph, wait between calls and retry transient failures.

What leaves the machine: public document URLs and drug codes (RxCUIs), one per request, never
linked to a patient.
"""

import hashlib
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "medgraph/0.1 (research prototype; knowledge-store fetch)"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """A download failed or did not match its pinned digest."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Fetcher:
    def __init__(
        self,
        *,
        delay_s: float = 0.25,
        retries: int = 3,
        backoff_s: float = 1.0,
        timeout_s: float = 120,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._delay_s = delay_s
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._last = 0.0

    def close(self) -> None:
        self._http.close()

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(self._retries + 1):
            wait = self._delay_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                error: str = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUS:
                    if response.status_code != 200:
                        raise FetchError(f"{url}: HTTP {response.status_code}")
                    return response
                error = f"HTTP {response.status_code}"
            if attempt < self._retries:
                self._sleep(self._backoff_s * 2**attempt)
        raise FetchError(f"{url}: {error} after {self._retries + 1} attempts")

    def json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(url, params).json()

    def download(self, url: str, dest: Path, sha256: str | None = None) -> str:
        """Fetch ``url`` into ``dest``; refuse it if it does not match ``sha256``."""
        data = self.get(url).content
        digest = sha256_bytes(data)
        if sha256 is not None and digest != sha256:
            raise FetchError(
                f"{url}: SHA-256 {digest} does not match the pinned {sha256}; the document "
                "changed, so it was not stored"
            )
        write_atomic(dest, data)
        return digest
