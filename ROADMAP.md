# Conductor Project Roadmap

Last updated: 2026-03-02

## Current State

Conductor is a VS Code collaboration extension with a FastAPI backend. The project currently has working implementations of:

- Real-time WebSocket chat (with reconnect, typing indicators, read receipts)
- File upload/download (20MB limit, dedup, retry)
- Code snippet sharing + editor navigation
- Change review workflow (MockAgent, policy check, diff preview, audit log)
- AI provider workflow (health check, provider selection, streaming inference)
- **Git Workspace Management (Model A)**:
  - Per-room bare repo + worktree isolation
  - GIT_ASKPASS token authentication
  - FileSystemProvider (`conductor://` URI scheme)
  - WorkspacePanel 5-step creation wizard
  - WorkspaceClient typed HTTP client
  - Workspace code search (`GET /workspace/{room_id}/search`)

## Phase 1: Foundation (COMPLETE)

### 1.1 VS Code Extension Scaffold
- [x] WebView panel with FSM lifecycle
- [x] WebSocket service with reconnect
- [x] Basic chat UI (send/receive messages)
- [x] TypeScript compilation + ESLint
- [x] VS Code command registration

### 1.2 FastAPI Backend Scaffold
- [x] FastAPI app with CORS middleware
- [x] WebSocket endpoint (`/ws/{room_id}`)
- [x] REST chat history endpoint
- [x] Pydantic models for request/response
- [x] pytest test suite

## Phase 2: Collaboration Features (COMPLETE)

### 2.1 Enhanced Chat
- [x] Reconnect with `since` parameter
- [x] Typing indicators (WebSocket broadcast)
- [x] Read receipts
- [x] Message deduplication (client-side UUID)
- [x] Paginated history (`GET /chat/{room_id}/history`)

### 2.2 File Sharing
- [x] File upload endpoint (`POST /files/upload`)
- [x] File download endpoint (`GET /files/{file_id}`)
- [x] 20MB size limit enforcement
- [x] Duplicate detection (SHA-256 hash)
- [x] Extension-host upload proxy
- [x] Retry logic (3 attempts with backoff)

### 2.3 Code Snippet Sharing
- [x] Snippet upload with language metadata
- [x] Editor navigation (open file at line)
- [x] Syntax highlighting in WebView

## Phase 3: AI & Change Workflows (COMPLETE)

### 3.1 Change Review Workflow
- [x] MockAgent for generating changes (`POST /generate-changes`)
- [x] Policy evaluation (`POST /policy/evaluate-auto-apply`)
- [x] Per-change diff preview
- [x] Sequential apply/skip UI
- [x] Audit logging (`POST /audit/log-apply`)

### 3.2 AI Provider Integration
- [x] Provider health/status endpoint (`GET /ai/status`)
- [x] Four-step provider selection UI
- [x] Streaming inference (`POST /ai/infer`)
- [x] Mock provider for testing

## Phase 4: Git Workspace Management (COMPLETE)

### 4.1 Model A: Token Authentication
- [x] Backend: bare repo clone with GIT_ASKPASS
- [x] Backend: worktree creation per room (`session/{room_id}` branch)
- [x] Backend: file CRUD endpoints (`/workspace/{room_id}/file`)
- [x] Backend: commit + push endpoint
- [x] Extension: WorkspaceClient typed HTTP client
- [x] Extension: WorkspacePanel 5-step creation wizard
- [x] Extension: FSM `CreatingWorkspace` state
- [x] Extension: FileSystemProvider (`conductor://` URI scheme)

### 4.2 Workspace Code Search
- [x] Backend: `GET /workspace/{room_id}/search?q=...` full-text search
- [x] Extension: `WorkspaceClient.searchCode()` method
- [x] Extension: inline search panel in WebView (`Ctrl+Shift+F`)
- [x] Tests: search endpoint + client method coverage

## Phase 5: Model B & Advanced Features (PLANNED)

### 5.1 Model B: Delegate Authentication
- [ ] Extension performs Git clone/push via VS Code Git API
- [ ] Backend receives file diffs, not Git credentials
- [ ] No PAT required from user
- [ ] Migration path from Model A sessions

### 5.2 Conflict Resolution
- [ ] Detect concurrent edit conflicts in worktree
- [ ] Show conflict diff in VS Code merge editor
- [ ] Three-way merge with base branch
- [ ] Conflict notification via WebSocket broadcast

### 5.3 Workspace Search Enhancements
- [ ] Search result navigation in VS Code (jump to file:line)
- [ ] Regex search support
- [ ] Search across all active rooms (admin view)
- [ ] Search history and saved queries

### 5.4 Enterprise Features
- [ ] Room access control (invite-only rooms)
- [ ] Audit log export (CSV/JSON)
- [ ] Session recording and replay
- [ ] Admin dashboard (active rooms, user count, file stats)

## Phase 6: Production Hardening (PLANNED)

### 6.1 Performance
- [ ] Worker pool for Git operations (avoid blocking event loop)
- [ ] Worktree cleanup scheduler (remove stale sessions)
- [ ] File diff streaming (chunked transfer for large files)
- [ ] Backend horizontal scaling (shared Redis for WebSocket state)

### 6.2 Security
- [ ] Token rotation (short-lived PATs via OAuth device flow)
- [ ] Rate limiting on all endpoints
- [ ] Path traversal hardening audit
- [ ] Secrets scanning in uploaded files

### 6.3 Observability
- [ ] Structured logging (JSON, correlation IDs)
- [ ] OpenTelemetry tracing
- [ ] Prometheus metrics endpoint
- [ ] Health check improvements (deep checks for Git, AI provider)

## Milestone Summary

| Milestone | Status | Completed |
|-----------|--------|----------|
| Phase 1: Foundation | ✅ Complete | Sprint 1 |
| Phase 2: Collaboration | ✅ Complete | Sprint 2 |
| Phase 3: AI Workflows | ✅ Complete | Sprint 3 |
| Phase 4: Git Workspace (Model A) | ✅ Complete | Sprint 4 |
| Phase 4.2: Workspace Code Search | ✅ Complete | Sprint 4 |
| Phase 5: Model B + Advanced | 🟡 Planned | Sprint 5 |
| Phase 6: Production Hardening | 🟡 Planned | Sprint 6 |

## Architecture Decision Log

### ADR-001: Model A over Model B for initial workspace
**Decision**: Implement Model A (PAT token via GIT_ASKPASS) first.
**Rationale**: Simpler to implement, test, and debug. Model B requires the extension to proxy Git operations, adding significant complexity. Model A validates the core workspace isolation design.
**Status**: Implemented in Phase 4.

### ADR-002: FileSystemProvider over SFTP/SCP
**Decision**: Use VS Code `FileSystemProvider` API with `conductor://` URI scheme.
**Rationale**: Native VS Code integration without SSH infrastructure. Files appear in the file explorer, search, and editor just like local files.
**Status**: Implemented in Phase 4.

### ADR-003: WorkspacePanel over WebView wizard
**Decision**: Use native VS Code `InputBox` / `QuickPick` for workspace creation.
**Rationale**: No CSP configuration needed. Integrates with VS Code themes. Feels native compared to a WebView form.
**Status**: Implemented in Phase 4.

### ADR-004: Per-room worktrees over shared workspace
**Decision**: Each session room gets its own Git branch (`session/{room_id}`) and worktree.
**Rationale**: Isolates concurrent sessions. Allows independent commit history per room. Simplifies conflict detection.
**Status**: Implemented in Phase 4.

### ADR-005: Inline search panel over separate view
**Decision**: Workspace code search opens in an inline WebView panel with `Ctrl+Shift+F`.
**Rationale**: Familiar keyboard shortcut. Keeps search results visible alongside code. No need for a separate VS Code sidebar view.
**Status**: Implemented in Phase 4.2.
