"""Confluence read-only client using service-account API token + HTTP Basic auth.

Uses the Confluence v2 REST API:
  ``{site_url}/wiki/api/v2/pages/{id}``
  ``{site_url}/wiki/api/v2/pages?title=...&space-id=...``

Falls back to v1 only where v2 has no equivalent (search by CQL).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx

logger = logging.getLogger(__name__)

_PAGE_ID_IN_PATH = re.compile(r"/(?:pages|display)/(\d+)")
_VIEWPAGE_QUERY_ID = re.compile(r"pageId=(\d+)")
_SPACE_KEY_IN_PATH = re.compile(r"/spaces/([A-Za-z0-9_-]+)(?:/|$)")


def extract_page_id(url: str) -> Optional[str]:
    """Pull the numeric page id out of a Confluence page URL.

    Handles the common shapes:
      - .../wiki/spaces/DEV/pages/123456/Title
      - .../wiki/display/DEV/Title?pageId=123456
    Returns None if no numeric id is present (e.g. /display/Space/Title without pageId).
    """
    if not url:
        return None
    parsed = urlparse(unquote(url))
    combined = f"{parsed.path}?{parsed.query}"
    m = _PAGE_ID_IN_PATH.search(combined) or _VIEWPAGE_QUERY_ID.search(combined)
    return m.group(1) if m else None


def extract_space_key(url: str) -> Optional[str]:
    """Pull the space key from a Confluence URL like .../wiki/spaces/HWBAA/..."""
    if not url:
        return None
    parsed = urlparse(unquote(url))
    m = _SPACE_KEY_IN_PATH.search(parsed.path)
    return m.group(1) if m else None


class ConfluenceReadonlyClient:
    def __init__(self, site_url: str, email: str, api_token: str, timeout: float = 15.0):
        self._site_url = site_url.rstrip("/")
        self._auth = httpx.BasicAuth(email, api_token)
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._site_url and self._auth)

    async def get_page(self, page_id: str, body_format: str = "storage") -> dict[str, Any]:
        """Fetch a page by numeric id, including its body.

        body_format: "storage" (Confluence XHTML), "atlas_doc_format", or "view".
        """
        url = f"{self._site_url}/wiki/api/v2/pages/{page_id}"
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            r = await c.get(url, params={"body-format": body_format})
            r.raise_for_status()
            return r.json()

    async def get_page_by_url(self, page_url: str, body_format: str = "storage") -> dict[str, Any]:
        """Fetch a page by Confluence URL.

        Accepts any of:
          .../wiki/spaces/KEY/pages/{id}/Title   → direct page
          .../wiki/display/KEY/Title?pageId={id}  → direct page
          .../wiki/spaces/KEY/overview            → space homepage
          .../wiki/spaces/KEY                     → space homepage
        """
        page_id = extract_page_id(page_url)
        if page_id:
            return await self.get_page(page_id, body_format=body_format)
        space_key = extract_space_key(page_url)
        if space_key:
            return await self.get_space_homepage(space_key, body_format=body_format)
        raise ValueError(f"Could not extract page id or space key from URL: {page_url}")

    async def get_space(self, space_key: str) -> dict[str, Any]:
        url = f"{self._site_url}/wiki/api/v2/spaces"
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            r = await c.get(url, params={"keys": space_key})
            r.raise_for_status()
            results = r.json().get("results") or []
            if not results:
                raise ValueError(f"No space found with key: {space_key}")
            return results[0]

    async def get_space_homepage(self, space_key: str, body_format: str = "storage") -> dict[str, Any]:
        space = await self.get_space(space_key)
        homepage_id = space.get("homepageId")
        if not homepage_id:
            raise ValueError(f"Space {space_key} has no homepageId")
        page = await self.get_page(str(homepage_id), body_format=body_format)
        page["_space"] = {"key": space_key, "id": space.get("id"), "name": space.get("name")}
        return page

    async def current_user(self) -> dict[str, Any]:
        """Verify credentials — returns the service account's Confluence profile."""
        url = f"{self._site_url}/wiki/rest/api/user/current"
        async with httpx.AsyncClient(auth=self._auth, timeout=self._timeout) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()
