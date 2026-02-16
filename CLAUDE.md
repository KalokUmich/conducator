# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Conductor is a VS Code collaboration extension with a FastAPI backend. It enables team chat via WebSocket, Live Share session management, file sharing, and AI-assisted summarization/code workflows.

## Common Commands

```bash
# Setup (first time)
make setup                  # Creates .venv, installs backend + extension deps

# Run backend (dev mode with auto-reload, port 8000)
make run-backend

# Compile extension (TypeScript + Tailwind CSS)
make compile

# Lint extension
cd extension && npm run lint

# Run all tests
make test

# Run backend tests only
make test-backend

# Run a single backend test module
cd backend && ../.venv/bin/pytest tests/test_chat.py -v

# Run a single backend test by name
cd backend && ../.venv/bin/pytest tests/test_chat.py -v -k "test_name"

# Run extension tests (must compile first)
cd extension && npm run compile
cd extension && npm run test              # Runs all out/tests/*.test.js

# Run a single extension test file
node --test extension/out/tests/conductorStateMachine.test.js

# Partial setup
make setup-backend              # Backend only (venv + pip install)
make setup-extension            # Extension only (npm install)

# Individual compile steps
make compile-ts                 # TypeScript only
make compile-css                # Tailwind CSS only

# Clean all generated files (venv, out/, node_modules, __pycache__)
make clean

# Package extension
cd extension && npx @vscode/vsce package
```

## Architecture

Two runtime components communicate over REST + WebSocket:

```
VS Code Extension (TypeScript)  <-->  FastAPI Backend (Python, port 8000)
       |                                       |
       +-> Live Share                          +-> DuckDB (audit, file metadata)
                                               +-> Local filesystem (uploads/)
```

### Backend (`backend/app/`)

FastAPI application in `main.py`. Each feature is a separate module with its own router. Modules follow a `module/{__init__.py, router.py, service.py or domain files}` convention:

- **chat/**: WebSocket real-time chat with `ConnectionManager` (room-scoped connections, in-memory message history, read receipts, message dedup). Room state is in-memory only.
- **ai_provider/**: AI summarization pipeline (`pipeline.py`) with 3 stages: classification (7 discussion types) -> targeted summary -> code relevance scoring. Provider resolution (`resolver.py`) with priority: `claude_bedrock` -> `claude_direct`. Supports Anthropic direct, AWS Bedrock, and OpenAI.
- **agent/**: `MockAgent` for deterministic change generation (not LLM-based yet).
- **auth/**: SSO login via device authorization flows — AWS IAM Identity Center and Google OAuth 2.0.
- **policy/**: Auto-apply safety policy evaluation for code changes.
- **audit/**: DuckDB-based audit logging for applied changes.
- **files/**: File upload/download with room-scoped storage.
- **summary/**: Legacy keyword extraction (deprecated, router unregistered; use `ai_provider` instead).
- **config.py**: Pydantic-validated YAML config loading. Split into `conductor.secrets.yaml` (gitignored, API keys) and `conductor.settings.yaml` (commitable settings). Search order: `./config/` -> `./` -> `../config/` -> `~/.conductor/`.
- **ngrok_service.py**: Ngrok tunnel lifecycle (`start_ngrok`, `stop_ngrok`, `get_public_url`). Started/stopped in `main.py` lifespan.

### Extension (`extension/src/`)

Entry point: `extension.ts` which registers commands and sets up the WebView message bridge.

- **services/conductorStateMachine.ts**: 6-state FSM (Idle, BackendDisconnected, ReadyToHost, Hosting, Joining, Joined). Join-only mode works via `BackendDisconnected -> Joining`.
- **services/conductorController.ts**: Orchestrates FSM transitions, backend health checks, session lifecycle.
- **services/session.ts**: `globalState` persistence for room/session IDs, backend URL resolution including ngrok detection.
- **services/permissions.ts**: Role-based access (`lead` vs `member` via `aiCollab.role` VS Code setting).
- **services/diffPreview.ts**: Sequential diff preview and code change application.
- **services/backendHealthCheck.ts**: Stateless async health check against `GET /health`, no VS Code API dependency.
- **services/ssoIdentityCache.ts**: SSO identity storage with 24h expiry, provider tagging (`aws`/`google`), globalState persistence.
- **media/chat.html**: Single-file WebView UI that communicates with the extension host via `postMessage`.

### Shared Contract

`shared/changeset.schema.json` defines the `ChangeSet` format used between backend and extension. `FileChange.type` is either `create_file` or `replace_range`.

## Configuration

Two YAML files in `config/`:
- `conductor.secrets.yaml` (gitignored; see `.example` files) — sections: `ai_providers` (anthropic, aws_bedrock, openai), `google_sso` (client_id, client_secret), `ngrok` (authtoken)
- `conductor.settings.yaml` (commitable) — sections: `server`, `ngrok`, `sso`, `google_sso`, `summary`, `ai_provider_settings`, `ai_models`, `session`, `change_limits`, `logging`

Key VS Code extension settings: `aiCollab.role` (lead/member), `aiCollab.backendUrl`, `aiCollab.autoStartLiveShare`.

## Testing

- Backend: pytest (243 tests). Tests are in `backend/tests/`, one file per module.
- Extension: Node test runner (5 test files in `extension/src/tests/`). Run all with `cd extension && npm run test` or individually with `node --test`.
- Extension tests cover service logic, not VS Code UI automation. Some tests start local HTTP servers and may fail in sandboxed environments.
