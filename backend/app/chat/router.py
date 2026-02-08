"""Chat router providing WebSocket and HTTP endpoints.

This module provides:
    - GET /chat: Guest chat page (HTML)
    - GET /invite: Unified invite page with Live Share button and chat
    - GET /chat/{room_id}/history: Paginated message history
    - WebSocket /ws/chat/{room_id}: Real-time chat messaging

The WebSocket protocol supports:
    - Message history delivery on connect
    - Message recovery on reconnect (via `since` query parameter)
    - User join/leave notifications
    - Real-time message broadcasting
    - Typing indicators
    - Read receipts
    - Host-initiated session termination
    - Message deduplication

Protocol Message Types:
    - join: User registration
    - message: Chat message
    - file: File upload notification
    - typing: Typing indicator (start/stop)
    - read: Mark message as read
    - end_session: Host ends session
"""
import html
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .manager import (
    ChatMessage,
    ChatMessageInput,
    manager,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)
from app.files.service import FileStorageService

router = APIRouter()

# HTML template directory
TEMPLATES_DIR = Path(__file__).parent / "templates"


@router.get("/chat", response_class=HTMLResponse)
async def guest_chat_page(
    roomId: str = Query(..., description="Room ID to join"),
    role: str = Query("engineer", description="User role (host or engineer)")
) -> HTMLResponse:
    """Serve the standalone guest chat page.

    This page provides a simple chat interface for users who want to
    participate in chat without the full invite page UI.

    Args:
        roomId: The room ID to join (from URL query).
        role: User role, either "host" or "engineer" (default: engineer).

    Returns:
        HTMLResponse with the rendered chat page.

    Example:
        GET /chat?roomId=abc123&role=engineer
    """
    # Validate role (default to engineer if invalid)
    if role not in ("host", "engineer"):
        role = "engineer"

    # Escape user input to prevent XSS attacks
    safe_room_id = html.escape(roomId)

    # Load and render template with simple substitution
    template_path = TEMPLATES_DIR / "guest_chat.html"
    template = template_path.read_text()

    content = template.replace("{{ room_id }}", safe_room_id)
    content = content.replace(
        "{{ room_id[:8] }}",
        safe_room_id[:8] if len(safe_room_id) >= 8 else safe_room_id
    )
    # Role is validated above, so it's safe to use directly
    content = content.replace("{{ role }}", role)
    content = content.replace("{{ role | capitalize }}", role.capitalize())

    return HTMLResponse(content=content)


@router.get("/invite", response_class=HTMLResponse)
async def invite_page(
    roomId: str = Query(..., description="Room ID to join"),
    liveShareUrl: str = Query(..., description="VS Code Live Share URL")
) -> HTMLResponse:
    """Serve the unified invite page with Live Share button and embedded chat.

    This page is the main entry point for guests. It displays:
    - Session info (room ID)
    - "Join Live Share in VS Code" button
    - Embedded chat iframe (2:1 layout ratio)

    Args:
        roomId: The room ID to join.
        liveShareUrl: The VS Code Live Share URL (URL-encoded).

    Returns:
        HTMLResponse with the rendered invite page.

    Example:
        GET /invite?roomId=abc123&liveShareUrl=https%3A%2F%2Fprod.liveshare...
    """
    template_path = TEMPLATES_DIR / "invite.html"
    template = template_path.read_text()

    # Escape user inputs to prevent XSS attacks
    safe_room_id = html.escape(roomId)
    safe_live_share_url = html.escape(liveShareUrl)

    # Simple template substitution (no Jinja2 dependency)
    content = template.replace("{{ room_id }}", safe_room_id)
    content = content.replace(
        "{{ room_id[:8] }}",
        safe_room_id[:8] if len(safe_room_id) >= 8 else safe_room_id
    )
    content = content.replace("{{ live_share_url }}", safe_live_share_url)

    return HTMLResponse(content=content)


@router.get("/chat/{room_id}/history")
async def get_message_history(
    room_id: str,
    before: Optional[float] = Query(None, description="Timestamp cursor (get messages before this time)"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Number of messages to return")
) -> JSONResponse:
    """Get paginated message history for a room.

    This endpoint supports lazy loading of chat history. Clients can fetch
    older messages by passing the `before` timestamp of the oldest message
    they currently have.

    Args:
        room_id: The room ID.
        before: Unix timestamp cursor. Returns messages older than this.
                If not provided, returns the most recent messages.
        limit: Maximum number of messages to return (1-100, default 50).

    Returns:
        JSON with messages array and hasMore boolean.

    Example:
        GET /chat/abc123/history?limit=50
        GET /chat/abc123/history?before=1707321600.123&limit=50
    """
    messages = manager.get_paginated_history(room_id, before, limit)

    # Check if there are more messages before the oldest returned
    has_more = False
    if messages:
        oldest_ts = messages[0].ts
        older_messages = manager.get_paginated_history(room_id, oldest_ts, 1)
        has_more = len(older_messages) > 0

    return JSONResponse({
        "messages": [msg.model_dump() for msg in messages],
        "hasMore": has_more
    })


@router.websocket("/ws/chat/{room_id}")
async def websocket_chat_endpoint(
    websocket: WebSocket,
    room_id: str,
    since: Optional[float] = Query(None, description="Timestamp for message recovery on reconnect")
) -> None:
    """WebSocket endpoint for real-time chat in a room.

    This endpoint handles the complete chat lifecycle for a single client:

    Protocol Flow:
        1. Client connects → Server sends: {type: "history", messages: [], users: []}
           If `since` is provided, only messages newer than that timestamp are sent.
        2. Client sends: {type: "join", userId, displayName, role}
           → Server broadcasts: {type: "user_joined", user: {}, users: []}
        3. Client sends: {userId, displayName, role, content}
           → Server broadcasts: {type: "message", ...fullMessage}
        4. Client sends: {type: "read", messageId, userId}
           → Server broadcasts: {type: "read_receipt", messageId, readBy: [...]}
        5. Client sends: {type: "end_session", userId} (host only)
           → Server broadcasts: {type: "session_ended", message: "..."}
        6. On disconnect → Server broadcasts: {type: "user_left", user: {}, users: []}

    Args:
        websocket: The WebSocket connection.
        room_id: The room ID to join.
        since: Optional timestamp for message recovery on reconnect.
    """
    logger.info(f"[WS] New connection to room: {room_id}, since={since}")
    # Accept connection and get existing message history
    history = await manager.connect(websocket, room_id)
    logger.info(f"[WS] Connection accepted. Room {room_id} now has {manager.get_room_size(room_id)} connections")

    try:
        # If reconnecting with `since`, only send messages newer than that timestamp
        if since is not None:
            history = manager.get_messages_since(room_id, since)
            logger.info(f"[WS] Reconnect recovery: sending {len(history)} messages since {since}")

        # Send message history and user list to the newly connected client
        history_data = [msg.model_dump() for msg in history]
        users_data = [u.model_dump() for u in manager.get_room_users(room_id)]

        await websocket.send_json({
            "type": "history",
            "messages": history_data,
            "users": users_data,
            "isRecovery": since is not None  # Tell client this is a reconnection
        })

        # Main message loop
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            logger.debug(f"[WS] Room {room_id} received: {data}")

            # --- Handle JOIN message (user registration) ---
            if message_type == "join":
                logger.info(f"[WS] JOIN from userId={data.get('userId')}, role={data.get('role')}")
                user = manager.register_user(
                    websocket=websocket,
                    room_id=room_id,
                    user_id=data.get("userId", ""),
                    display_name=data.get("displayName", ""),
                    role=data.get("role", "engineer")
                )

                # Broadcast updated user list to all clients
                users_data = [u.model_dump() for u in manager.get_room_users(room_id)]
                logger.info(f"[WS] Broadcasting user_joined. Total users: {len(users_data)}")
                await manager.broadcast({
                    "type": "user_joined",
                    "user": user.model_dump(),
                    "users": users_data
                }, room_id)
                continue

            # --- Handle END_SESSION message (host only) ---
            if message_type == "end_session":
                user_id = data.get("userId", "")
                user_info = manager.get_user(room_id, user_id)

                # Only the host can end the session
                if user_info and user_info.role == "host":
                    # Delete all files for this room
                    # TODO: CLOUD_BACKUP - Consider backing up files before deletion
                    try:
                        file_service = FileStorageService.get_instance()
                        deleted_count = file_service.delete_room_files(room_id)
                        logger.info(f"[WS] Deleted {deleted_count} files for room {room_id}")
                    except Exception as e:
                        logger.error(f"[WS] Failed to delete files for room {room_id}: {e}")

                    await manager.broadcast({
                        "type": "session_ended",
                        "message": "Host has ended the chat session"
                    }, room_id)

                    # Clear all room data
                    manager.clear_room(room_id)
                continue

            # --- Handle TYPING indicator message ---
            if message_type == "typing":
                user_id = data.get("userId", "")
                user_info = manager.get_user(room_id, user_id)
                if user_info:
                    await manager.broadcast_except(
                        {
                            "type": "typing",
                            "userId": user_id,
                            "displayName": user_info.displayName,
                            "isTyping": data.get("isTyping", True)
                        },
                        room_id,
                        exclude_websocket=websocket
                    )
                continue

            # --- Handle READ receipt message ---
            if message_type == "read":
                message_id = data.get("messageId", "")
                user_id = data.get("userId", "")
                if message_id and user_id:
                    # Mark message as read and get all readers
                    read_by = manager.mark_message_read(room_id, message_id, user_id)
                    # Broadcast read receipt to all clients
                    await manager.broadcast({
                        "type": "read_receipt",
                        "messageId": message_id,
                        "readBy": list(read_by)
                    }, room_id)
                continue

            # --- Handle FILE message (broadcast file upload notification) ---
            if message_type == "file":
                message_id = data.get("id", "")

                # Deduplication check
                if manager.is_duplicate_message(room_id, message_id):
                    logger.debug(f"[WS] Duplicate file message ignored: {message_id}")
                    continue

                user_id = data.get("userId", "")
                user_info = manager.get_user(room_id, user_id)
                display_name = user_info.displayName if user_info else data.get("displayName", "")

                # Broadcast file message to all clients
                file_message = {
                    "type": "file",
                    "id": message_id,
                    "roomId": room_id,
                    "userId": user_id,
                    "displayName": display_name,
                    "role": data.get("role", "engineer"),
                    "fileId": data.get("fileId", ""),
                    "originalFilename": data.get("originalFilename", ""),
                    "fileType": data.get("fileType", "other"),
                    "mimeType": data.get("mimeType", ""),
                    "sizeBytes": data.get("sizeBytes", 0),
                    "downloadUrl": data.get("downloadUrl", ""),
                    "caption": data.get("caption"),
                    "ts": data.get("ts", 0)
                }

                logger.info(f"[WS] Broadcasting file message: {file_message.get('originalFilename')}")
                await manager.broadcast(file_message, room_id)
                continue

            # --- Handle regular CHAT message ---
            logger.info(f"[WS] CHAT message from userId={data.get('userId')}: {data.get('content', '')[:50]}")
            try:
                input_msg = ChatMessageInput(**data)
            except Exception as e:
                # Invalid message format - notify sender only
                logger.error(f"[WS] Invalid message format: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": f"Invalid message format: {str(e)}"
                })
                continue

            # Use registered display name if available
            user_info = manager.get_user(room_id, input_msg.userId)
            display_name = user_info.displayName if user_info else input_msg.displayName

            # Create and store the full message
            full_message = ChatMessage(
                roomId=room_id,
                userId=input_msg.userId,
                displayName=display_name,
                role=input_msg.role,
                content=input_msg.content
            )
            manager.add_message(room_id, full_message)

            # Broadcast to all clients in the room
            logger.info(f"[WS] Broadcasting message to {manager.get_room_size(room_id)} connections")
            await manager.broadcast(
                {"type": "message", **full_message.model_dump()},
                room_id
            )

    except WebSocketDisconnect:
        # Clean up and notify others
        disconnected_user = manager.disconnect(websocket, room_id)

        if disconnected_user:
            users_data = [u.model_dump() for u in manager.get_room_users(room_id)]
            await manager.broadcast({
                "type": "user_left",
                "user": disconnected_user.model_dump(),
                "users": users_data
            }, room_id)

