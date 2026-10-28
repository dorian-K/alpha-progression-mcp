"""Read-only access to the user's synced CouchDB database. Only GET requests are made."""

import time
from urllib.parse import quote

import httpx

from .auth import AuthError, load_session
from .config import load_settings

CACHE_SECONDS = 300


class Couch:
    def __init__(self):
        self._docs: list[dict] | None = None
        self._fetched = 0.0

    def _client(self) -> tuple[httpx.Client, str]:
        session = load_session()
        base = load_settings()["COUCH_URL"].rstrip("/")
        client = httpx.Client(auth=(session["name"], session["token"]), timeout=60)
        return client, f"{base}/{session['name']}"

    def _get(self, path: str = "", params: dict | None = None) -> dict:
        client, url = self._client()
        try:
            with client:
                resp = client.get(url + path, params=params)
        except httpx.HTTPError as e:
            raise ConnectionError(f"Could not reach the Alpha Progression sync server: {e}") from e
        if resp.status_code in (401, 403):
            raise AuthError("The saved token was rejected. Run `alpha-progression-mcp login` again.")
        if resp.status_code == 404:
            raise LookupError(f"Not found: {path or 'database'}")
        resp.raise_for_status()
        return resp.json()

    def info(self) -> dict:
        return self._get()

    def all_docs(self, refresh: bool = False) -> list[dict]:
        if refresh or self._docs is None or time.time() - self._fetched > CACHE_SECONDS:
            rows = self._get("/_all_docs", {"include_docs": "true"})["rows"]
            self._docs = [r["doc"] for r in rows if "doc" in r and not r["id"].startswith("_design/")]
            self._fetched = time.time()
        return self._docs

    def get(self, doc_id: str) -> dict:
        return self._get("/" + quote(doc_id, safe=""))
