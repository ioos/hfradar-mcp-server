"""HTTP client used by the standalone HFRNet totals MCP server."""

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from mcp.server.mcpserver.exceptions import ToolError


DEFAULT_API_URL = "https://cloud.data.ioos.us/hfrnet"


class DatasetAPI:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("HFR_API_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment")
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("HFR_API_BASE_URL", DEFAULT_API_URL))

    def get(self, path: str, params: dict | None = None, *, allow_no_content: bool = False) -> dict:
        # doseq preserves FastAPI's repeated query parameters for regions and bbox.
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
        url = self.base_url + path + ("?" + query if query else "")
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "hfradar-mcp"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status == 204 and allow_no_content:
                    return {}
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    detail = json.load(exc).get("detail", exc.reason)
                except (ValueError, AttributeError):
                    detail = exc.reason
            raise ToolError(f"HFR API HTTP {exc.code} at {self.base_url + path}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ToolError(
                f"Could not reach HFR API at {self.base_url}; check that it is running "
                f"and HFR_API_BASE_URL is correct: {exc}"
            ) from exc
        except ValueError as exc:
            raise ToolError(f"Expected JSON from HFR API at {self.base_url + path}") from exc
        if not isinstance(result, dict):
            raise ToolError(f"Expected a JSON object from HFR API at {self.base_url + path}")
        return result
