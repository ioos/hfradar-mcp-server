"""Read-only client for the hfradmin v1 JSON API (https://hfr.gcoos.org/api/v1)."""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_API_URL = "https://hfr.gcoos.org/api/v1"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects to another scheme or host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(urljoin(req.full_url, newurl))
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener(proxy: bool = True) -> Callable[..., Any]:
    handlers: list[Any] = [_SameOriginRedirect]
    if not proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers).open


class HfrApi:
    """GET-only client. Auth, if the API ever needs it, is added in `headers()` only."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        token: str | None = None,
        timeout: float = 30,
        opener: Callable[..., Any] | None = None,
    ):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("HFR_API_URL must be an http(s) URL without a query or fragment")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("HFR_API_URL must not contain credentials; use HFR_API_TOKEN")
        if token and parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("HFR_API_TOKEN is only sent over https")
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._open = opener or make_opener()
        self._cache: dict[str, tuple[float, Any]] = {}

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "gcoos-hfr-mcp"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def url(self, parts: tuple[str, ...], params: dict[str, Any]) -> str:
        path = "/".join(quote(str(part), safe="") for part in parts)
        query = {k: v for k, v in params.items() if v is not None}
        return f"{self.base}/{path}" + (f"?{urlencode(query)}" if query else "")

    def get(self, *parts: str, cache_ttl: float = 0, **params: Any) -> Any:
        """GET /api/v1/<parts...>?<params>. None-valued params are dropped."""
        url = self.url(parts, params)
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < cache_ttl:
            return hit[1]
        request = urllib.request.Request(url, headers=self.headers(), method="GET")
        try:
            with self._open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ToolError(self._error_message(exc)) from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
            raise ToolError(f"Could not reach the hfradmin API at {self.base}: {exc}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise ToolError(f"hfradmin API answer is larger than {MAX_RESPONSE_BYTES // 1024 // 1024} MB; narrow the query")
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ToolError(f"The hfradmin API returned non-JSON for {url}") from exc
        if cache_ttl:
            self._cache[url] = (time.time(), data)
        return data

    @staticmethod
    def _error_message(exc: urllib.error.HTTPError) -> str:
        """Use the API's own error text."""
        try:
            body = json.loads(exc.read(65536))
            # some routes answer {"error": 404, "message": "<text>"}
            message = body["error"] if isinstance(body.get("error"), str) else body.get("message")
        except (ValueError, AttributeError, OSError):
            message = None
        if 300 <= exc.code < 400:
            return "hfradmin API redirected to another origin; refused"
        if exc.code == 429:
            return "hfradmin API rate limit reached (200 requests/minute); wait a minute and retry"
        return f"hfradmin API HTTP {exc.code}: {message or exc.reason}"


def api_from_env() -> HfrApi:
    return HfrApi(
        os.environ.get("HFR_API_URL") or DEFAULT_API_URL,
        token=os.environ.get("HFR_API_TOKEN") or None,
    )
