"""Microsoft Teams bot integration — Phase 1 (connectivity proof).

Architecture — where "bot logic" lives
--------------------------------------

A Teams app has no client-side logic. The manifest.json we ship in the Teams
app package is just a pointer: it tells Microsoft the bot's name, icon, and
the Azure Bot Service resource to route to. Every decision about *what the
bot does* happens here, in the backend.

End-to-end flow when a user types "@Conductor summarize" in a channel::

    Teams client
       │   (user message + @mention)
       ▼
    Microsoft Bot Framework Connector (cloud service)
       │   looks up botId in manifest → Azure Bot Service resource
       │   the Bot Service's "Messaging endpoint" points at our backend
       ▼
    POST https://<our-host>/api/integrations/teams/bot/messages
       │   body: JSON "Activity" (type=message, text, from, conversation, ...)
       │   auth: Bearer JWT signed by Bot Framework (Phase 2 validates this)
       ▼
    bot_messages() in this file  ← 100% of the logic lives here
       │   Phase 1: log the payload and return 200.
       │   Phase 2: validate JWT → parse command → call summarizer →
       │            post Adaptive Card back by POSTing to activity.serviceUrl.

Teams forwards messages to us only when:
  * the user @mentions the bot in a channel or group chat, OR
  * the user DMs the bot 1:1 (all messages in that DM are forwarded).

Reading arbitrary channel history requires the Graph API ``ChatMessage.Read.All``
permission (already granted) and an app-only token acquired with the client
secret — that is Phase 2 work.

Endpoints
---------
    GET  /api/integrations/teams/health        liveness probe for DevOps smoke tests
    POST /api/integrations/teams/bot/messages  Bot Framework messaging endpoint
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/teams", tags=["teams"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe. Also reports whether credentials are configured, so DevOps
    can see via the tunnel whether env vars landed — without leaking secret values."""
    cfg = get_config()
    return {
        "status": "ok",
        "phase": 1,
        "description": "Teams bot endpoint — logs activities, no replies",
        "configured": {
            "enabled": cfg.teams.enabled,
            "app_id_present": bool(cfg.teams_secrets.app_id),
            "app_password_present": bool(cfg.teams_secrets.app_password),
            "app_tenant_id_present": bool(cfg.teams_secrets.app_tenant_id),
            "app_type": cfg.teams_secrets.app_type,
        },
    }


@router.post("/bot/messages")
async def bot_messages(request: Request) -> JSONResponse:
    """Receive a Teams Activity, log it, return 200.

    Teams (Bot Framework Connector) retries on non-2xx responses and on timeouts
    beyond ~15s. We must respond fast; parsing + logging should stay well under
    a second. A Phase 1 implementation intentionally accepts everything — malformed
    bodies, missing JWT, wrong content-type — so we can observe the full spectrum
    of what Microsoft actually sends during initial wiring. JWT validation and
    strict schema enforcement are Phase 2 work.
    """
    raw_bytes = await request.body()

    body: object
    try:
        body = json.loads(raw_bytes) if raw_bytes else {}
    except json.JSONDecodeError:
        body = {"_raw_non_json": raw_bytes.decode("utf-8", errors="replace")}

    redacted_headers = {k: v for k, v in request.headers.items() if k.lower() != "authorization"}
    auth_present = "authorization" in {k.lower() for k in request.headers}

    activity_type = body.get("type") if isinstance(body, dict) else None
    text = body.get("text") if isinstance(body, dict) else None
    from_name = (body.get("from") or {}).get("name") if isinstance(body, dict) else None
    conversation_id = (body.get("conversation") or {}).get("id") if isinstance(body, dict) else None

    logger.info(
        "[teams] Activity received type=%s from=%r conversation=%s text=%r auth_header=%s",
        activity_type,
        from_name,
        conversation_id,
        text,
        auth_present,
    )
    logger.debug(
        "[teams] full payload\n  headers: %s\n  body: %s",
        json.dumps(redacted_headers, indent=2, default=str),
        json.dumps(body, indent=2, default=str) if isinstance(body, dict) else body,
    )

    return JSONResponse(status_code=200, content={"ok": True})
