"""Read-only access to the user's own database. The app syncs it to a CouchDB server; we only send GET requests."""

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

    def _get(self, path: str, params: dict | None = None) -> dict:
        token = load_token()
        url = f"{config.settings()['COUCH_URL'].rstrip('/')}/{token['name']}{path}"
        try:
            resp = httpx.get(url, params=params, auth=(token["name"], token["token"]), timeout=60)
        except httpx.HTTPError as e:
            raise ConnectionError(f"Could not reach the Alpha Progression server: {e}") from e
        if resp.status_code in (401, 403):
            raise AuthError("Your login has expired. Run `alpha-progression-mcp login` again.")
        if resp.status_code == 404:
            raise LookupError(f"Not found: {path}")
        resp.raise_for_status()
        return resp.json()

    def all_docs(self, refresh: bool = False) -> list[dict]:
        """Every document in the database, cached for a few minutes."""
        if refresh or time.time() - self._fetched_at > CACHE_SECONDS:
            rows = self._get("/_all_docs", {"include_docs": "true"})["rows"]
            self._docs = [r["doc"] for r in rows if not r["id"].startswith("_design/")]
            self._fetched_at = time.time()
        return self._docs

    def get(self, doc_id: str) -> dict:
        return self._get("/" + quote(doc_id, safe=""))
