# Backend Code Walkthrough

**A Learning Journey Through the Conductor Backend**

This guide walks junior engineers through the Conductor backend codebase, explaining not just *what* the code does but *why* it's structured this way. We cover every router, service, and design pattern — with worked examples you can run yourself.

---

## Table of Contents

1. [Project Layout](#1-project-layout)
2. [Entry Point: main.py](#2-entry-point-mainpy)
3. [Chat System](#3-chat-system)
4. [File Sharing](#4-file-sharing)
5. [Change Review Workflow](#5-change-review-workflow)
6. [AI Provider Integration](#6-ai-provider-integration)
7. [Git Workspace Management](#7-git-workspace-management)
8. [Workspace Code Search](#8-workspace-code-search)
9. [Authentication Patterns](#9-authentication-patterns)
10. [Testing Patterns](#10-testing-patterns)
11. [Deployment Notes](#11-deployment-notes)
12. [Contributing](#12-contributing)

---

## 1. Project Layout

```
backend/
├── main.py                     # App factory, router registration
├── requirements.txt
├── routers/
│   ├── chat.py                 # WebSocket + HTTP chat endpoints
│   ├── workspace.py            # Git workspace CRUD + search
│   ├── files.py                # File upload/download
│   ├── ai.py                   # AI provider status + inference
│   └── changes.py              # Change generation, policy, audit
├── services/
│   ├── workspace_service.py    # Git worktree management
│   ├── auth_service.py         # Token validation helpers
│   └── ai_service.py           # AI provider abstraction
├── models/
│   └── schemas.py              # Pydantic request/response models
└── tests/
    ├── test_workspace.py
    ├── test_ai.py
    ├── test_chat.py
    ├── test_files.py
    └── test_changes.py
```

**Why this layout?**

FastAPI encourages separating route handlers (routers) from business logic (services). Routers handle HTTP concerns — parsing request bodies, returning status codes, validation errors. Services handle domain logic — talking to Git, calling AI APIs, managing state.

This separation means:
- You can test services without spinning up an HTTP server
- Routers stay thin and readable
- Business logic is reusable across routers

---

## 2. Entry Point: main.py

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import chat, workspace, files, ai, changes

app = FastAPI(title="Conductor Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(workspace.router, prefix="/workspace")
app.include_router(files.router, prefix="/files")
app.include_router(ai.router, prefix="/ai")
app.include_router(changes.router)
```

**What's happening here?**

1. `FastAPI()` creates the ASGI application. Think of it as the top-level object that handles every incoming request.
2. `CORSMiddleware` lets the VS Code WebView (which runs on a `vscode-webview://` origin) call the backend. Without this, browsers block cross-origin requests.
3. `include_router` registers all the routes defined in each router file. The `prefix` means routes in `workspace.py` don't have to repeat `/workspace` on every decorator.

**Why `allow_origins=["*"]` in development?**

During development, the WebView origin changes each time VS Code restarts. Wildcarding is fine locally. In production, you'd lock this to the specific extension host origin.

---

## 3. Chat System

### 3.1 The Room Model

Each collaboration session is a "room" identified by a `room_id` string. Rooms are created implicitly — the first WebSocket connection to a `room_id` creates it.

```python
# Simplified in-memory room store
rooms: dict[str, Room] = {}

class Room:
    def __init__(self):
        self.connections: list[WebSocket] = []
        self.messages: list[Message] = []
        self.typing: dict[str, datetime] = {}  # user_id -> last_typed_at
```

**Why in-memory?**

For the current scope — a development tool used by a team — an in-memory store is appropriate. Messages persist for the session lifetime. Adding persistence (Redis, SQLite) is a future concern tracked in the roadmap.

### 3.2 WebSocket Endpoint

```python
@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()
    room = get_or_create_room(room_id)
    room.connections.append(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            await handle_message(room, websocket, data)
    except WebSocketDisconnect:
        room.connections.remove(websocket)
        await broadcast(room, {"type": "user_left"})
```

**The connection lifecycle:**
1. `accept()` — completes the WebSocket handshake
2. Add to room's connection list
3. Receive messages in a loop
4. On disconnect (client closes tab, network drop), remove from list and notify others

**Why `receive_json()`?**

All messages are JSON objects with a `type` field. This is the classic discriminated union pattern — the `type` tells the handler what shape the rest of the message has.

### 3.3 Message Types

```python
async def handle_message(room, websocket, data):
    msg_type = data.get("type")
    
    if msg_type == "chat":
        await handle_chat(room, data)
    elif msg_type == "typing":
        await handle_typing(room, data)
    elif msg_type == "read_receipt":
        await handle_read_receipt(room, data)
    elif msg_type == "history_request":
        await send_history(websocket, room, data)
```

Each handler is a pure async function. New message types are added by adding a new `elif` branch — no framework magic needed.

### 3.4 Reconnect Recovery

One of the trickier requirements: when a client reconnects after a network drop, they need messages they missed.

```python
@router.get("/chat/{room_id}/history")
async def get_history(
    room_id: str,
    since: Optional[str] = None,
    limit: int = 50
):
    room = rooms.get(room_id)
    if not room:
        raise HTTPException(404)
    
    messages = room.messages
    if since:
        since_dt = datetime.fromisoformat(since)
        messages = [m for m in messages if m.timestamp > since_dt]
    
    return messages[-limit:]
```

**How the client uses this:**
1. Client disconnects at time T
2. Client reconnects, sends `GET /chat/{room_id}/history?since=T`
3. Receives all messages since T
4. Merges with local message store (dedup by message UUID)

The dedup step matters — if the client was briefly disconnected and some messages arrived via WebSocket before the reconnect HTTP call returns, they'd see duplicates. UUID dedup prevents that.

### 3.5 Typing Indicators

```python
async def handle_typing(room, data):
    user_id = data["user_id"]
    room.typing[user_id] = datetime.now()
    
    await broadcast(room, {
        "type": "typing",
        "user_id": user_id
    })
```

The `room.typing` dict records *when* each user last sent a typing event. The frontend uses this to show/hide the "User is typing..." indicator with a timeout — if no typing event arrives for 3 seconds, the indicator disappears.

**Why not just broadcast and let the frontend manage state?**

We store it server-side for the history endpoint: a client joining mid-session can see who's currently typing.

---

## 4. File Sharing

### 4.1 Upload Endpoint

```python
@router.post("/files/upload")
async def upload_file(
    file: UploadFile,
    room_id: str = Form(...)
):
    content = await file.read()
    
    # Size check
    if len(content) > 20 * 1024 * 1024:  # 20MB
        raise HTTPException(413, "File too large")
    
    # Dedup check
    sha256 = hashlib.sha256(content).hexdigest()
    if sha256 in file_store:
        existing = file_store[sha256]
        return {"file_id": existing.file_id, "deduplicated": True}
    
    # Store
    file_id = str(uuid.uuid4())
    file_store[sha256] = StoredFile(
        file_id=file_id,
        filename=file.filename,
        content=content,
        content_type=file.content_type,
        sha256=sha256,
        room_id=room_id
    )
    
    return {"file_id": file_id, "deduplicated": False}
```

**Three things to notice:**

1. **Size check** — 413 is "Payload Too Large". The VS Code extension shows a user-friendly error message when it gets a 413.

2. **Dedup by SHA-256** — If two team members upload the same file, it's stored once. The second upload gets the same `file_id` as the first. This is invisible to users (they both see "upload succeeded") but saves storage.

3. **UUID file IDs** — File IDs are random UUIDs, not sequential integers. This prevents enumeration attacks: you can't guess `file_id=2` by knowing `file_id=1` exists.

### 4.2 Extension-Host Upload Proxy

VS Code extensions run in two contexts:
- **Extension host** (Node.js) — can make HTTP requests
- **WebView** (sandboxed iframe) — cannot make direct HTTP requests to arbitrary URLs

The WebView posts a message to the extension host, which proxies the upload:

```typescript
// In WebView (browser context)
function uploadFile(file: File) {
    // Can't fetch() directly — WebView is sandboxed
    vscode.postMessage({
        type: 'upload_file',
        filename: file.name,
        content: arrayBufferToBase64(await file.arrayBuffer())
    });
}

// In extension host (Node.js context)
panel.webview.onDidReceiveMessage(async (msg) => {
    if (msg.type === 'upload_file') {
        const buffer = Buffer.from(msg.content, 'base64');
        const response = await fetch(`${backendUrl}/files/upload`, {
            method: 'POST',
            body: createFormData(msg.filename, buffer)
        });
        const result = await response.json();
        panel.webview.postMessage({ type: 'upload_result', ...result });
    }
});
```

This pattern — WebView → postMessage → extension host → fetch → backend — is the standard way to make HTTP requests from a WebView.

### 4.3 Retry Logic

```typescript
async function uploadWithRetry(file: File, maxAttempts = 3): Promise<UploadResult> {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            return await uploadFile(file);
        } catch (error) {
            if (attempt === maxAttempts) throw error;
            // Exponential backoff: 1s, 2s, 4s
            await sleep(Math.pow(2, attempt - 1) * 1000);
        }
    }
}
```

Exponential backoff is the standard retry strategy for network requests: wait longer between each attempt. This prevents hammering a temporarily overloaded server.

---

## 5. Change Review Workflow

This is a more complex workflow that demonstrates how to orchestrate multiple API calls from the VS Code extension.

### 5.1 The Workflow

```
1. User clicks "Generate Changes"
2. Extension calls POST /generate-changes
3. Backend returns a list of proposed file changes
4. Extension calls POST /policy/evaluate-auto-apply
5. Policy returns: apply_all, review_required, or reject_all
6. For each change:
   a. Show diff preview
   b. User clicks Apply or Skip
   c. Extension applies change to local file
   d. Extension calls POST /audit/log-apply
7. Summary shown to user
```

### 5.2 MockAgent

The backend uses a MockAgent for generating changes during development:

```python
@router.post("/generate-changes")
async def generate_changes(req: GenerateChangesRequest):
    # MockAgent: returns hardcoded changes based on request context
    # In production, this would call an LLM
    changes = MockAgent.generate(req.context, req.files)
    return {"changes": changes, "agent": "mock-v1"}
```

**Why a MockAgent?**

Building the workflow first (UI, policy check, audit log) and mocking the AI part is good engineering practice. It lets you test the entire workflow without depending on an external AI service. Swapping MockAgent for a real LLM client is a one-line change in `generate_changes`.

### 5.3 Policy Evaluation

```python
@router.post("/policy/evaluate-auto-apply")
async def evaluate_policy(req: PolicyEvalRequest):
    """
    Evaluates whether changes can be auto-applied without review.
    
    Returns:
        apply_all: All changes are low-risk, apply automatically
        review_required: Some changes need human review
        reject_all: Changes violate policy (e.g., deleting critical files)
    """
    risk_score = calculate_risk(req.changes)
    
    if risk_score < 0.3:
        return {"decision": "apply_all"}
    elif risk_score < 0.7:
        return {"decision": "review_required"}
    else:
        return {"decision": "reject_all"}
```

The risk calculator looks at things like: are any changes to configuration files? Are any changes deletions? Do the changes touch files not in the original context?

### 5.4 Audit Logging

```python
@router.post("/audit/log-apply")
async def log_apply(req: AuditLogRequest):
    entry = AuditEntry(
        timestamp=datetime.now(),
        user_id=req.user_id,
        room_id=req.room_id,
        change_id=req.change_id,
        action=req.action,  # "applied" or "skipped"
        file_path=req.file_path
    )
    audit_log.append(entry)
    return {"logged": True}
```

Every apply/skip action is logged. This creates an audit trail for code review — you can reconstruct exactly what changes the AI suggested and which ones the developer accepted.

---

## 6. AI Provider Integration

### 6.1 Provider Abstraction

The AI service abstracts over multiple providers:

```python
class AIService:
    def __init__(self):
        self.providers = {
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "mock": MockProvider(),
        }
        self.active_provider = "mock"
    
    async def infer(self, prompt: str) -> AsyncIterator[str]:
        provider = self.providers[self.active_provider]
        async for token in provider.stream(prompt):
            yield token
```

**Why an abstraction layer?**

Different team members may have different API key access. Some deployments might use a local model. The abstraction lets you swap providers without changing the router or the extension.

### 6.2 Streaming Inference

```python
@router.post("/ai/infer")
async def infer(req: InferRequest):
    async def generate():
        async for token in ai_service.infer(req.prompt):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

This is Server-Sent Events (SSE) — a simple streaming protocol where the server sends `data: ...\n\n` chunks. The extension reads these chunks as they arrive and appends tokens to the output buffer.

**Why SSE over WebSocket for inference?**

SSE is unidirectional (server → client) and works over standard HTTP/1.1. It's simpler than WebSocket for this use case because inference is one-shot: send a prompt, receive a stream of tokens.

### 6.3 Provider Selection UI

The four-step provider selection flow:

```
Step 1: GET /ai/status
        <- { providers: [{name, available, latency_ms}] }
Step 2: Show provider list to user
Step 3: User selects provider
Step 4: POST /ai/select { provider_name }
        <- { active_provider: "openai" }
```

The status endpoint returns latency measurements (from a periodic health-check ping), so users can see which provider is fastest right now.

---

## 7. Git Workspace Management

This is the most complex part of the backend. Take your time here.

### 7.1 The Core Idea

Instead of using VS Code Live Share (which requires a Microsoft account and has licensing limitations), Conductor implements its own workspace sharing via Git:

1. Host provides a Git repo URL + Personal Access Token
2. Backend clones the repo as a **bare repository** (no working tree)
3. Backend creates a **Git worktree** for this session (an isolated working directory linked to the bare repo)
4. Each file edit is staged in the worktree
5. On commit: `git add -A && git commit && git push`

**Why bare repo?**

A bare repo is just the `.git` directory contents, without a working tree. It's the right choice for a server-side repo that exists purely to receive pushes and create worktrees — you never need to check out files directly into it.

**Why worktrees?**

Git worktrees let you have multiple working directories linked to the same repository. Each session room gets its own worktree on its own branch (`session/{room_id}`). Changes in room A don't affect room B, even if they're clones of the same upstream repo.

### 7.2 WorkspaceService

```python
class WorkspaceService:
    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.rooms: dict[str, WorkspaceInfo] = {}
    
    def repo_path(self, room_id: str) -> Path:
        return Path(self.workspace_root) / "repos" / f"{room_id}.git"
    
    def worktree_path(self, room_id: str) -> Path:
        return Path(self.workspace_root) / "worktrees" / room_id
```

Each room has:
- `repos/{room_id}.git` — the bare clone
- `worktrees/{room_id}/` — the working directory for this session

### 7.3 Creating a Workspace

```python
def create_workspace(
    self,
    room_id: str,
    repo_url: str,
    token: str,
    base_branch: str = "main"
) -> WorkspaceInfo:
    
    repo_path = self.repo_path(room_id)
    worktree_path = self.worktree_path(room_id)
    
    # Step 1: Clone bare repo using GIT_ASKPASS for auth
    askpass_script = self._create_askpass_script(token)
    subprocess.run(
        ["git", "clone", "--bare", repo_url, str(repo_path)],
        env={**os.environ, "GIT_ASKPASS": askpass_script},
        check=True
    )
    
    # Step 2: Create worktree on a new session branch
    session_branch = f"session/{room_id}"
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add",
         str(worktree_path), "-b", session_branch, base_branch],
        check=True
    )
    
    # Step 3: Store mapping
    info = WorkspaceInfo(
        room_id=room_id,
        repo_path=repo_path,
        worktree_path=worktree_path,
        session_branch=session_branch
    )
    self.rooms[room_id] = info
    return info
```

**The GIT_ASKPASS mechanism:**

Git calls the `GIT_ASKPASS` script when it needs credentials. The script receives the prompt ("Username for https://...") via argument and prints the answer to stdout. We create a tiny shell script that always prints the PAT:

```python
def _create_askpass_script(self, token: str) -> str:
    script_path = Path(tempfile.mktemp(suffix='.sh'))
    script_path.write_text(f'#!/bin/sh\necho "{token}"\n')
    script_path.chmod(0o700)
    return str(script_path)
```

This is the standard way to pass credentials to Git non-interactively. The script is temporary and cleaned up after the clone.

### 7.4 File Operations

```python
def read_file(self, room_id: str, file_path: str) -> str:
    worktree = self._get_worktree(room_id)  # raises if not found
    full_path = self._safe_path(worktree, file_path)  # raises if traversal
    return full_path.read_text()

def _safe_path(self, worktree: Path, file_path: str) -> Path:
    """Prevent path traversal attacks."""
    resolved = (worktree / file_path).resolve()
    if not str(resolved).startswith(str(worktree.resolve())):
        raise HTTPException(400, "Invalid path")
    return resolved
```

**Path traversal protection is critical.** Without it, a malicious client could request `file_path=../../etc/passwd` and read arbitrary files from the server. The `resolve()` call expands `..` components, and the `startswith` check ensures the result is still inside the worktree.

### 7.5 Commit and Push

```python
def commit_and_push(self, room_id: str, message: str) -> CommitResult:
    info = self.rooms[room_id]
    worktree = info.worktree_path
    
    # Stage all changes
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
    
    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        return CommitResult(committed=False, reason="no changes")
    
    # Commit
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", message],
        check=True
    )
    
    # Push to origin
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin",
         info.session_branch],
        env={**os.environ, "GIT_ASKPASS": info.askpass_script},
        check=True
    )
    
    return CommitResult(committed=True)
```

**Why check for empty diff before committing?**

`git commit` fails with exit code 1 if there's nothing to commit. We handle this gracefully by checking `git status --porcelain` first (porcelain output is stable and machine-readable) and returning a `committed=False` result instead of raising an exception.

---

## 8. Workspace Code Search

### 8.1 The Search Endpoint

```python
@router.get("/workspace/{room_id}/search")
async def search_workspace(
    room_id: str,
    q: str,
    max_results: int = 50
) -> List[SearchResult]:
    """
    Full-text search across all files in a session worktree.
    
    Returns matches with file path, line number, and matched content.
    """
    return workspace_service.search_files(room_id, q, max_results)
```

### 8.2 The Search Implementation

```python
def search_files(
    self,
    room_id: str,
    query: str,
    max_results: int = 50
) -> List[SearchResult]:
    worktree = self.worktree_path(room_id)
    
    if not worktree.exists():
        raise HTTPException(404, f"Room {room_id} not found")
    
    results = []
    
    for file_path in worktree.rglob("*"):
        if not file_path.is_file():
            continue
        if _is_binary(file_path):
            continue
        
        try:
            lines = file_path.read_text(errors='replace').splitlines()
        except OSError:
            continue
        
        rel_path = str(file_path.relative_to(worktree))
        
        for line_num, line in enumerate(lines, start=1):
            if query.lower() in line.lower():
                results.append(SearchResult(
                    file_path=rel_path,
                    line=line_num,
                    content=line.strip()
                ))
                if len(results) >= max_results:
                    return results
    
    return results
```

**Design decisions:**

1. **Case-insensitive by default** — `query.lower() in line.lower()`. Most code searches are case-insensitive.

2. **Skip binary files** — Binary files (images, compiled artifacts) produce garbage when searched as text. `_is_binary()` checks for null bytes in the first 8KB.

3. **`errors='replace'`** — Source files are usually UTF-8, but sometimes contain Latin-1 encoded strings (especially in older codebases). `replace` mode substitutes undecodable bytes with `�` instead of crashing.

4. **Early exit at `max_results`** — Without this, searching a large repo could take seconds. 50 results is enough to be useful.

5. **Relative paths in results** — We strip the worktree prefix from results so the extension doesn't see server filesystem paths.

### 8.3 Binary File Detection

```python
def _is_binary(path: Path) -> bool:
    """Heuristic: files containing null bytes are binary."""
    try:
        chunk = path.read_bytes()[:8192]
        return b'\x00' in chunk
    except OSError:
        return True
```

This is the same heuristic Git uses. It's not perfect (some binary formats don't contain null bytes) but catches the common cases (PNG, JPEG, compiled Python .pyc files, etc.).

### 8.4 Extension Integration

The `WorkspaceClient` method:

```typescript
async searchCode(
    roomId: string,
    query: string,
    maxResults = 50
): Promise<SearchResult[]> {
    const url = new URL(
        `/workspace/${roomId}/search`,
        this.baseUrl
    );
    url.searchParams.set('q', query);
    url.searchParams.set('max_results', String(maxResults));
    
    const response = await fetch(url.toString());
    if (!response.ok) {
        throw new Error(`Search failed: ${response.status}`);
    }
    return response.json();
}
```

The WebView search panel:

```typescript
// In WebView HTML
function handleSearchInput(query: string) {
    if (query.length < 2) return;  // don't search for single chars
    
    vscode.postMessage({
        type: 'search_code',
        query,
        roomId: currentRoomId
    });
}

// In extension host
if (msg.type === 'search_code') {
    const results = await workspaceClient.searchCode(msg.roomId, msg.query);
    panel.webview.postMessage({
        type: 'search_results',
        results
    });
}
```

The keyboard shortcut `Ctrl+Shift+F` (Mac: `Cmd+Shift+F`) is registered as a VS Code command that focuses the search input in the WebView.

---

## 9. Authentication Patterns

### 9.1 Model A: Token via GIT_ASKPASS

As described in section 7.3, Model A passes a Personal Access Token to Git via the `GIT_ASKPASS` mechanism. The token is:

- Sent by the user in the `POST /workspace/create` request body
- Held in memory in the `WorkspaceInfo` object
- Used for Git operations (clone, push)
- Never written to disk (except temporarily in the ASKPASS script)
- Never returned to the client after creation

**Security properties:**
- The token is only in memory on the server — not in the database, not in logs
- The ASKPASS script is created in a temp directory with mode `0700` (owner-only execute)
- The script is cleaned up after the clone operation

### 9.2 Model B (Planned)

Model B is the more secure but complex alternative:

```
Instead of:
  User → token → backend → git clone

Model B:
  User's extension → git clone (using VS Code Git API)
  Extension → file diffs → backend (no credentials transferred)
```

This means credentials never leave the user's machine. The backend receives file contents, not Git credentials.

Model B is tracked in Phase 5 of the roadmap.

---

## 10. Testing Patterns

### 10.1 Backend Test Structure

All backend tests use `pytest` with `httpx.AsyncClient`:

```python
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_create_workspace():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/workspace/create", json={
            "room_id": "test-room-1",
            "repo_url": "https://github.com/example/repo",
            "token": "ghp_test_token",
            "base_branch": "main"
        })
    assert response.status_code == 201
    assert response.json()["status"] == "created"
```

**Why `httpx.AsyncClient` over `TestClient`?**

FastAPI's `TestClient` is synchronous, but our endpoints are async. `httpx.AsyncClient` lets you test async code naturally with `await`.

### 10.2 Mocking Git Operations

Git operations are slow and require network access. Tests mock them:

```python
@pytest.mark.asyncio
async def test_create_workspace_mocked(mocker):
    mock_run = mocker.patch("services.workspace_service.subprocess.run")
    mock_run.return_value = MagicMock(returncode=0)
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/workspace/create", json={
            "room_id": "test-room-mock",
            "repo_url": "https://github.com/example/repo",
            "token": "ghp_test",
            "base_branch": "main"
        })
    
    assert response.status_code == 201
    # Verify git was called with correct arguments
    assert mock_run.call_count == 2  # clone + worktree add
    clone_call = mock_run.call_args_list[0]
    assert "--bare" in clone_call.args[0]
```

### 10.3 Extension Test Patterns

Extension unit tests use Mocha with sinon for mocking:

```typescript
import * as sinon from 'sinon';
import { WorkspaceClient } from '../services/workspaceClient';

suite('WorkspaceClient', () => {
    let fetchStub: sinon.SinonStub;
    let client: WorkspaceClient;
    
    setup(() => {
        fetchStub = sinon.stub(global, 'fetch');
        client = new WorkspaceClient('http://localhost:8000');
    });
    
    teardown(() => {
        fetchStub.restore();
    });
    
    test('searchCode returns results', async () => {
        fetchStub.resolves(new Response(
            JSON.stringify([
                { file_path: 'src/main.py', line: 42, content: 'def handle_message' }
            ]),
            { status: 200 }
        ));
        
        const results = await client.searchCode('room-1', 'handle_message');
        
        assert.equal(results.length, 1);
        assert.equal(results[0].line, 42);
        assert.ok(fetchStub.calledWithMatch(
            sinon.match(/\/workspace\/room-1\/search/)
        ));
    });
});
```

### 10.4 FSM Testing

FSM tests verify every valid and invalid transition:

```typescript
suite('SessionFSM', () => {
    test('valid: Idle -> ReadyToHost', () => {
        const fsm = new SessionFSM();
        fsm.setReadyToHost();
        assert.equal(fsm.getState(), 'ReadyToHost');
    });
    
    test('invalid: Idle -> CreatingWorkspace throws', () => {
        const fsm = new SessionFSM();
        assert.throws(
            () => fsm.startCreatingWorkspace(),
            InvalidTransitionError
        );
    });
    
    test('full Model A flow', () => {
        const fsm = new SessionFSM();
        fsm.setReadyToHost();
        fsm.startCreatingWorkspace();
        fsm.workspaceReady();
        assert.equal(fsm.getState(), 'Hosting');
        fsm.leaveSession();
        assert.equal(fsm.getState(), 'Idle');
    });
});
```

---

## 11. Deployment Notes

### 11.1 Environment Variables

```bash
# Required
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000

# Git workspace
GIT_WORKSPACE_ROOT=/var/conductor/workspaces
GIT_WORKSPACE_ENABLED=true

# Optional: AI providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### 11.2 File System Requirements

```
/var/conductor/workspaces/
├── repos/          # bare clones (one per room)
└── worktrees/      # working directories (one per room)
```

Both directories must be writable by the process user. Disk space depends on repo sizes and number of active sessions. A reasonable estimate: 2-3x the repo size per active session.

### 11.3 Git Requirements

- Git 2.15+ (for `git worktree` support)
- The process user must have Git installed and `git` in PATH
- For GitHub repos: PAT needs `repo` scope (read/write)

### 11.4 Running in Production

```bash
# With gunicorn + uvicorn workers (recommended)
cd backend
gunicorn main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000

# Or directly with uvicorn (single process)
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Note**: With multiple Gunicorn workers, the in-memory room store won't be shared between processes. For production multi-worker deployments, you'd need to extract the room state to Redis or a database. This is tracked in Phase 6 (horizontal scaling).

### 11.5 Docker

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV GIT_WORKSPACE_ROOT=/var/conductor/workspaces
ENV GIT_WORKSPACE_ENABLED=true

RUN mkdir -p /var/conductor/workspaces/repos /var/conductor/workspaces/worktrees

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 12. Contributing

### Code Style

**Python:**
- Black formatting (line length 100)
- Type hints on all public functions
- Docstrings for all public classes and non-trivial functions
- `ruff` for linting

**TypeScript:**
- ESLint with the VS Code extension recommended ruleset
- Strict mode enabled
- All functions typed (no `any`)

### Adding a New Endpoint

1. Add the Pydantic model to `models/schemas.py`
2. Add the route handler to the appropriate router in `routers/`
3. Add business logic to the appropriate service in `services/`
4. Add tests to `tests/`
5. Update `CLAUDE.md` if the endpoint is part of a new feature

### Adding a New FSM State

1. Add the state to the `SessionState` union type in `sessionFSM.ts`
2. Add transition methods
3. Handle the new state in `CollabPanel.ts` (update the UI accordingly)
4. Add tests in `sessionFSM.test.ts` covering all transitions to/from the new state
5. Update `docs/ARCHITECTURE.md` FSM state table

### Pull Request Checklist

- [ ] Tests pass (`pytest` + `npm test`)
- [ ] New code has test coverage
- [ ] `CLAUDE.md` updated if new patterns introduced
- [ ] `ROADMAP.md` updated if completing a planned item
- [ ] No hardcoded secrets
- [ ] TypeScript: no `any` types introduced

### Getting Help

- **Architecture questions**: Read `docs/ARCHITECTURE.md` first
- **API reference**: FastAPI auto-docs at `http://localhost:8000/docs`
- **Bug reports**: Open a GitHub issue with reproduction steps
- **Feature requests**: Open a GitHub issue with use case and rationale

Happy coding! 🚀
