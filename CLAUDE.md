# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Conductor is a VS Code collaboration extension with a FastAPI backend. The project has two main parts:

1. **`extension/`** - TypeScript VS Code extension
2. **`backend/`** - Python FastAPI server

## Commands

### Backend (Python/FastAPI)
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload          # development server
pytest                             # run all tests
pytest -k "test_workspace"         # run specific tests
pytest --cov=. --cov-report=html   # coverage report
```

### Extension (TypeScript/VS Code)
```bash
cd extension
npm install
npm run compile                    # one-time build
npm run watch                      # watch mode
npm test                           # run extension tests
npm run lint                       # ESLint
vsce package                       # build .vsix
```

## Architecture

### Extension Services

The extension is organized into services under `extension/src/services/`:

| Service | File | Purpose |
|---------|------|---------|
| SessionFSM | `sessionFSM.ts` | Manages WebView session state machine |
| WebSocketService | `webSocketService.ts` | Handles WebSocket connection to backend |
| FileSystemProvider | `fileSystemProvider.ts` | Implements `conductor://` URI scheme for remote files |
| WorkspaceClient | `workspaceClient.ts` | Typed HTTP client for `/workspace/` endpoints |
| FileUploadService | `fileUploadService.ts` | File upload/download with retry logic |

### Model A Architecture (Current)

Model A is the current workspace authentication mode using GIT_ASKPASS token injection:

```
User provides PAT
       ↓
Extension sends token + repo URL to backend
       ↓
Backend creates bare repo clone with GIT_ASKPASS
       ↓
Backend creates worktree at worktrees/{room_id}/
       ↓
FileSystemProvider mounts conductor://{room_id}/ in VS Code
```

**FSM States for Model A workspace creation:**

| State | Description |
|-------|-------------|
| `Idle` | No active session |
| `ReadyToHost` | Host mode selected, no workspace yet |
| `CreatingWorkspace` | WorkspacePanel wizard running, backend provisioning |
| `Hosting` | Workspace ready, FileSystemProvider mounted |
| `Joined` | Joined another host's session |
| `BackendDisconnected` | WebSocket lost, join-only fallback |

### Backend Structure

```
backend/
├── main.py                    # FastAPI app, router registration
├── routers/
│   ├── workspace.py           # /workspace/ endpoints
│   ├── ai.py                  # /ai/ endpoints  
│   ├── files.py               # /files/ endpoints
│   ├── chat.py                # WebSocket + HTTP chat
│   └── changes.py             # /generate-changes, /policy/, /audit/
├── services/
│   ├── workspace_service.py   # Git worktree management
│   ├── auth_service.py        # Token validation
│   └── ai_service.py          # AI provider abstraction
├── models/
│   └── schemas.py             # Pydantic request/response models
└── tests/
    ├── test_workspace.py      # Workspace endpoint tests
    ├── test_ai.py             # AI endpoint tests
    └── test_chat.py           # Chat/WebSocket tests
```

### Extension Structure

```
extension/src/
├── extension.ts               # Entry point, command registration
├── panels/
│   ├── collabPanel.ts         # Main WebView panel
│   └── workspacePanel.ts      # 5-step workspace creation wizard
├── services/
│   ├── sessionFSM.ts          # Session state machine
│   ├── webSocketService.ts    # WebSocket client
│   ├── fileSystemProvider.ts  # conductor:// URI scheme
│   ├── workspaceClient.ts     # /workspace/ HTTP client
│   └── fileUploadService.ts   # Upload/download proxy
└── commands/
    └── index.ts               # VS Code command handlers
```

## Key Patterns

### FSM Pattern
The extension uses a state machine (`SessionFSM`) to manage session lifecycle. States are defined as a TypeScript union type. Transitions are explicit methods that throw if called in an invalid state.

```typescript
// Example: transition to CreatingWorkspace
fsm.startCreatingWorkspace(); // throws if not in ReadyToHost
```

### WorkspaceClient Pattern
All backend calls go through `WorkspaceClient` for type safety:

```typescript
const client = new WorkspaceClient('http://localhost:8000');

// Create workspace
const result = await client.createWorkspace({
  room_id: 'abc123',
  repo_url: 'https://github.com/user/repo',
  token: 'ghp_...',
  base_branch: 'main'
});

// Search code
const results = await client.searchCode(roomId, 'function handleMessage');
// returns: Array<{ file: string, line: number, content: string }>
```

### FileSystemProvider Pattern
Files in the remote worktree are accessed via the `conductor://` URI scheme:

```typescript
// Register provider (done once in extension activation)
vscode.workspace.registerFileSystemProvider('conductor', provider, {
  isCaseSensitive: true
});

// Open a remote file
const uri = vscode.Uri.parse(`conductor://${roomId}/src/main.py`);
await vscode.workspace.openTextDocument(uri);
```

### Backend Workspace Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/workspace/create` | Create bare repo + worktree |
| GET | `/workspace/{room_id}/files` | List files in worktree |
| GET | `/workspace/{room_id}/file` | Read file content |
| PUT | `/workspace/{room_id}/file` | Write file content |
| DELETE | `/workspace/{room_id}/file` | Delete file |
| POST | `/workspace/{room_id}/commit` | Commit + push changes |
| GET | `/workspace/{room_id}/search` | Full-text search in worktree |

## Testing Notes

- Backend tests use `pytest` with `httpx.AsyncClient` for async endpoint testing
- Extension tests use VS Code's built-in test runner with Mocha
- Workspace tests mock Git operations to avoid requiring a real Git remote
- FSM tests verify all valid and invalid state transitions
- FileSystemProvider tests use an in-memory mock backend

## Environment Variables

```bash
# Backend
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
GIT_WORKSPACE_ROOT=/tmp/conductor_workspaces  # where worktrees are stored
GIT_WORKSPACE_ENABLED=true

# Extension (VS Code settings)
conductor.backendUrl=http://localhost:8000
conductor.enableWorkspace=true
```

## Common Issues

### Git worktree creation fails
- Ensure `GIT_WORKSPACE_ENABLED=true` in backend environment
- Check that the PAT has `repo` scope for private repos
- Verify `GIT_WORKSPACE_ROOT` directory is writable

### FileSystemProvider not mounting
- Check extension host logs for `conductor://` registration errors
- Verify `workspaceClient.createWorkspace()` returned `status: "created"`
- FSM must be in `Hosting` state before provider mounts

### WebSocket reconnection loops
- Backend must be running before extension connects
- Check `conductor.backendUrl` setting matches running backend port
- `BackendDisconnected` state is normal when backend is unreachable

## Extension Services Reference

Quick reference for working with extension services:

### SessionFSM
```typescript
import { SessionFSM, SessionState } from './services/sessionFSM';
const fsm = new SessionFSM();
fsm.getState(); // 'Idle'
fsm.setReadyToHost();
fsm.startCreatingWorkspace();
fsm.workspaceReady(); // -> 'Hosting'
```

### WorkspaceClient
```typescript
import { WorkspaceClient } from './services/workspaceClient';
const client = new WorkspaceClient(backendUrl);
await client.createWorkspace({ room_id, repo_url, token, base_branch });
await client.listFiles(roomId);
await client.readFile(roomId, filePath);
await client.writeFile(roomId, filePath, content);
await client.deleteFile(roomId, filePath);
await client.commitChanges(roomId, message);
await client.searchCode(roomId, query);
```

### FileSystemProvider
```typescript
import { ConductorFileSystemProvider } from './services/fileSystemProvider';
const provider = new ConductorFileSystemProvider(client);
vscode.workspace.registerFileSystemProvider('conductor', provider);
// Files are now accessible at conductor://{roomId}/path/to/file
```

## Architecture Decision Notes

- **Why Model A over Model B?** Model A (PAT token) is simpler to implement and test. Model B (delegate auth) requires the extension to act as a Git proxy, adding complexity. Model A is the current focus.
- **Why bare repo + worktree?** Isolates each session without full clones. The bare repo is shared; each session gets its own worktree branch.
- **Why FileSystemProvider over SFTP?** FileSystemProvider integrates natively with VS Code's file explorer, search, and editor without requiring SSH infrastructure.
- **Why WorkspacePanel over WebView wizard?** Native VS Code input boxes feel more integrated and don't require CSP configuration.

## Recent Changes

- Added `WorkspaceClient.searchCode()` for workspace code search
- Added `GET /workspace/{room_id}/search` backend endpoint
- Added `FileSystemProvider` for `conductor://` URI scheme
- Added `WorkspacePanel` 5-step creation wizard
- Updated FSM to include `CreatingWorkspace` state
- Extension services now organized under `src/services/`

## What's Next

See [ROADMAP.md](ROADMAP.md) for planned features. Current focus:
- Model B delegate authentication
- Conflict resolution for concurrent edits
- Workspace search result navigation in VS Code
- Enterprise architecture reference with runtime sequences
