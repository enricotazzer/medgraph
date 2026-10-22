"""Who may use the local app (ADR 0013): the person who started it.

The server binds to 127.0.0.1, but every account on this machine can reach that address. So
the app has a secret, created on first start at ``Settings.app_token_path`` with mode 0600, in
the home folder (the exFAT data volume has no file permissions). ``make app`` prints a link
that carries it; the app answers that link with an HttpOnly, SameSite=Strict cookie and a
redirect to the same page without the secret. A request without the cookie gets a short page
that says to open the link.

A form is accepted only with the cookie, from the app's own origin (the ``Origin`` header, which
browsers send with every POST), and with a form token derived from the secret
(:func:`form_token`, checked by the routes). SameSite=Strict keeps the cookie off requests that
other sites start, and the origin check refuses any that get through.
"""

import hashlib
import hmac
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "medgraph"
TOKEN_PARAM = "token"
SAFE_METHODS = frozenset({"GET", "HEAD"})


def load_secret(path: Path) -> str:
    """The app's secret, created (mode 0600) if there is none; a readable-by-others file is
    made private again."""
    if path.exists():
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(0o600)
        secret = path.read_text(encoding="utf-8").strip()
        if len(secret) >= 32:
            return secret
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret + "\n")
    return secret


def form_token(secret: str) -> str:
    """The token every form carries, derived from the secret."""
    return hmac.new(secret.encode(), b"medgraph form", hashlib.sha256).hexdigest()


def same(a: str | None, b: str) -> bool:
    return a is not None and hmac.compare_digest(a.encode(), b.encode())


class Access:
    """ASGI middleware: the cookie on every request, the app's origin on every POST.
    ``locked(lang)`` is the page shown without the cookie."""

    def __init__(self, app: ASGIApp, *, secret: str, locked: Callable[[str], str]) -> None:
        self.app = app
        self.secret = secret
        self.locked = locked

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"))
        lang = next((v for k, v in query if k == "lang" and v in ("en", "it")), "en")
        response: Response | None = None
        given = next((v for k, v in query if k == TOKEN_PARAM), None)
        if given is not None:
            if same(given, self.secret):
                rest = urlencode([(k, v) for k, v in query if k != TOKEN_PARAM])
                response = RedirectResponse(scope["path"] + (f"?{rest}" if rest else ""), 303)
                response.set_cookie(COOKIE, self.secret, httponly=True, samesite="strict", path="/")
            else:
                response = HTMLResponse(self.locked(lang), status_code=401)
        elif not same(cookie_parser(headers.get("cookie", "")).get(COOKIE), self.secret):
            response = HTMLResponse(self.locked(lang), status_code=401)
        elif scope["method"] not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is None or origin != f"http://{headers.get('host', '')}":
                response = Response("This form did not come from the app.", 403)
        if response is not None:
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
