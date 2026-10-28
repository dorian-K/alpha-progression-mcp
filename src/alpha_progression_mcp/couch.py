"""Access to the user's own database, which the app syncs to a CouchDB server.

Reads use GET. The only write is `put`, used by the plan editor.
"""

import time
from urllib.parse import quote

import httpx

from . import config
from .auth import AuthError, load_token

CACHE_SECONDS = 300


class Couch:
    def __init__(self):
        self._docs: list[dict] = []
        self._fetched_at = 0.0

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        token = load_token()
        url = f"{config.settings()['COUCH_URL'].rstrip('/')}/{token['name']}{path}"
        try:
            resp = httpx.request(
                method, url, params=params, json=body, auth=(token["name"], token["token"]), timeout=60
            )
        except httpx.HTTPError as e:
            raise ConnectionError(f"Could not reach the Alpha Progression server: {e}") from e
        if resp.status_code in (401, 403):
            raise AuthError("Your login has expired. Run `alpha-progression-mcp login` again.")
        if resp.status_code == 404:
            raise LookupError(f"Not found: {path}")
        if resp.status_code == 409:
            raise RuntimeError("The plan was changed in the app in the meantime. Load it again and retry.")
        resp.raise_for_status()
        return resp.json()

    def all_docs(self, refresh: bool = False) -> list[dict]:
        """Every document in the database, cached for a few minutes."""
        if refresh or time.time() - self._fetched_at > CACHE_SECONDS:
            rows = self._request("GET", "/_all_docs", {"include_docs": "true"})["rows"]
            self._docs = [r["doc"] for r in rows if not r["id"].startswith("_design/")]
            self._fetched_at = time.time()
        return self._docs

    def get(self, doc_id: str) -> dict:
        return self._request("GET", "/" + quote(doc_id, safe=""))

    def put(self, doc: dict) -> str:
        """Save a changed plan. CouchDB rejects it (409) if `_rev` is no longer the latest version."""
        if doc.get("type") != "plan":
            raise ValueError("Only training plans can be written.")
        rev = self._request("PUT", "/" + quote(doc["_id"], safe=""), body=doc)["rev"]
        self._fetched_at = 0.0  # reload on next read
        return rev
