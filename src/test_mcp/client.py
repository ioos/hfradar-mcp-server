"""Read-only client for the hfradmin v1 JSON API (https://hfr.gcoos.org/api/v1)."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_API_URL = "https://hfr.gcoos.org/api/v1"


class HfrApi:
    """GET-only client. Auth, if the API ever needs it, is added in `headers()` only."""

    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        token: str | None = None,
        timeout: float = 30,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("HFR_API_URL must be an http(s) URL without a query or fragment")
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._open = opener
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
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise ToolError(self._error_message(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ToolError(f"Could not reach the hfradmin API at {self.base}: {exc}") from exc
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ToolError(f"The hfradmin API returned non-JSON for {url}") from exc
        if cache_ttl:
            self._cache[url] = (time.time(), data)
        return data

    @staticmethod
    def _error_message(exc: urllib.error.HTTPError) -> str:
        """Surface the API's own `{"error": "..."}` text (it says what to do)."""
        try:
            body = json.loads(exc.read())
            # Usually {"error": "<text>"}; some routes answer {"error": 404, "message": "<text>"}.
            message = body["error"] if isinstance(body.get("error"), str) else body.get("message")
        except (ValueError, AttributeError, OSError):
            message = None
        if exc.code == 429:
            return "hfradmin API rate limit reached (200 requests/minute); wait a minute and retry"
        return f"hfradmin API HTTP {exc.code}: {message or exc.reason}"


def api_from_env() -> HfrApi:
    return HfrApi(
        os.environ.get("HFR_API_URL") or DEFAULT_API_URL,
        token=os.environ.get("HFR_API_TOKEN") or None,
    )
