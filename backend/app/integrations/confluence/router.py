"""Confluence read-only endpoints (service-account Basic auth).

Endpoints:
    GET  /api/integrations/confluence/readonly/whoami
    GET  /api/integrations/confluence/readonly/page?url=<full-page-url>
    GET  /api/integrations/confluence/readonly/page/{page_id}
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from .readonly_client import ConfluenceReadonlyClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/confluence", tags=["confluence"])


def _get_client(request: Request) -> ConfluenceReadonlyClient:
    client = getattr(request.app.state, "confluence_readonly_client", None)
    if client is None or not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Confluence readonly client not configured — set confluence_readonly.{site_url,email,api_token} in secrets.",
        )
    return client


@router.get("/readonly/whoami")
async def whoami(request: Request) -> dict:
    client = _get_client(request)
    try:
        profile = await client.current_user()
        return {
            "ok": True,
            "account_id": profile.get("accountId"),
            "email": profile.get("email"),
            "display_name": profile.get("displayName") or profile.get("publicName"),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text) from e
    except Exception as e:
        logger.error("Confluence readonly whoami failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/readonly/page")
async def get_page_by_url(
    request: Request,
    url: str = Query(..., description="Full Confluence page URL"),
    body_format: str = Query("storage", description="storage | atlas_doc_format | view"),
) -> dict:
    client = _get_client(request)
    try:
        return await client.get_page_by_url(url, body_format=body_format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text) from e
    except Exception as e:
        logger.error("Confluence readonly get_page(url=%s) failed: %s", url, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/readonly/page/{page_id}")
async def get_page_by_id(
    page_id: str,
    request: Request,
    body_format: str = Query("storage"),
) -> dict:
    client = _get_client(request)
    try:
        return await client.get_page(page_id, body_format=body_format)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text) from e
    except Exception as e:
        logger.error("Confluence readonly get_page(id=%s) failed: %s", page_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/readonly/space/{space_key}")
async def get_space_homepage(
    space_key: str,
    request: Request,
    body_format: str = Query("storage"),
) -> dict:
    """Fetch a space's homepage (the page that shows at /wiki/spaces/{key}/overview)."""
    client = _get_client(request)
    try:
        return await client.get_space_homepage(space_key, body_format=body_format)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text) from e
    except Exception as e:
        logger.error("Confluence readonly get_space_homepage(%s) failed: %s", space_key, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
