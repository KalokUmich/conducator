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
pytest -k "test_embedding"        # embedding provider tests
pytest -k "test_repo_graph"       # repo graph tests
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

### Backend Structure

```
backend/
├── app/
│   ├── main.py                      # FastAPI app, lifespan, router registration
│   ├── config.py                    # Settings + Secrets from YAML, env injection
│   ├── git_workspace/               # Git workspace management (Model A)
│   │   ├── service.py               # GitWorkspaceService
│   │   ├── delegate_broker.py       # DelegateBroker (Model B prep)
│   │   └── router.py                # /api/git-workspace/ endpoints
│   ├── code_search/                 # CocoIndex semantic code search
│   │   ├── service.py               # CodeSearchService
│   │   ├── embedding_provider.py    # 5 embedding backends (abstract + concrete)
│   │   ├── schemas.py               # Request/response Pydantic models
│   │   └── router.py                # /api/code-search/ endpoints
│   ├── repo_graph/                  # RepoMap graph-based context
│   │   ├── parser.py                # tree-sitter AST + regex fallback
│   │   ├── graph.py                 # networkx dependency graph + PageRank
│   │   └── service.py               # RepoMapService (map generation, caching)
│   └── context/
│       └── router.py                # /api/context/ hybrid retrieval endpoint
├── config/
│   └── conductor.settings.yaml      # Non-secret settings template
├── requirements.txt
└── tests/
    ├── test_embedding_provider.py   # 78 tests — all 5 backends
    ├── test_repo_graph.py           # 72 tests — parser + graph + service
    ├── test_config_new.py           # 42 tests — config + secrets + env vars
    ├── test_context.py              # 28 tests — context router + hybrid retrieval
    ├── test_code_search.py          # 52 tests — code search service + router
    └── test_git_workspace.py        # Git workspace lifecycle
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

### Embedding Provider Architecture

The `embedding_provider.py` module defines an `EmbeddingProvider` ABC with 5 implementations:

| Provider | Default Model | Dimensions | Cost |
|----------|--------------|------------|------|
| `local` | `all-MiniLM-L6-v2` | 384 | Free |
| `bedrock` | `cohere.embed-v4:0` | 1024 | $0.12/1M |
| `openai` | `text-embedding-3-small` | 1536 | $0.02/1M |
| `voyage` | `voyage-code-3` | 1024 | $0.06/1M |
| `mistral` | `codestral-embed-2505` | 1024 | — |

Default: **bedrock** with Cohere Embed v4.

Configuration in `conductor.settings.yaml`:
```yaml
code_search:
  embedding_backend: "bedrock"     # local | bedrock | openai | voyage | mistral
  bedrock_model_id: "cohere.embed-v4:0"
```

Credentials in `conductor.secrets.yaml`:
```yaml
aws:
  access_key_id: "AKIA..."
  secret_access_key: "..."
  region: "us-east-1"
voyage:
  api_key: "pa-..."
mistral:
  api_key: "..."
```

### RepoMap Architecture

The `repo_graph/` module implements Aider-style repository mapping:

1. **Parser** (`parser.py`): Extract symbol definitions and references from source files using tree-sitter AST parsing (with regex fallback)
2. **Graph** (`graph.py`): Build a directed dependency graph (file A → file B means A references symbols defined in B). Uses networkx for storage and PageRank computation
3. **Service** (`service.py`): `RepoMapService` generates text-based repo maps showing top-ranked files and their symbols

**Hybrid retrieval** in `context/router.py`:
- Vector search (CocoIndex) finds semantically similar code
- Graph search (PageRank) finds structurally important files
- PageRank is personalised: biased towards files from vector search

### Model A Architecture (Current)

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

## Key Patterns

### EmbeddingProvider Pattern
```python
from backend.app.code_search.embedding_provider import create_embedding_provider

provider = create_embedding_provider(settings)  # factory
vectors = await provider.embed_texts(["def main(): pass"])  # batch embed
query_vec = await provider.embed_query("search for main")   # query embed
```

### RepoMapService Pattern
```python
from backend.app.repo_graph.service import RepoMapService

svc = RepoMapService(top_n=10)
graph = svc.build_graph("/path/to/workspace")        # build or cache
ranked = svc.get_ranked_files("/path/to/workspace")   # PageRank ranking
repo_map = svc.generate_repo_map("/path/to/workspace")  # text map
files = svc.get_context_files(ws, vector_files)       # hybrid merge
```

### Config Pattern
```python
from backend.app.config import load_settings, _inject_embedding_env_vars

settings = load_settings()                  # loads YAML files
_inject_embedding_env_vars(settings)        # pushes secrets → env vars
```

## Testing Notes

- Backend tests use `pytest` with mocked external dependencies
- All embedding providers are tested with mocked API clients (no real API calls)
- RepoMap tests use real filesystem operations for parser/graph tests
- tree-sitter and networkx are mocked in import stubs
- Config tests verify env var injection for all 5 backends

## Environment Variables

```bash
# Backend
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
GIT_WORKSPACE_ROOT=/tmp/conductor_workspaces

# Embedding (injected by _inject_embedding_env_vars)
AWS_ACCESS_KEY_ID=...            # bedrock backend
AWS_SECRET_ACCESS_KEY=...        # bedrock backend
AWS_DEFAULT_REGION=us-east-1     # bedrock backend
OPENAI_API_KEY=sk-...            # openai backend
VOYAGE_API_KEY=pa-...            # voyage backend
MISTRAL_API_KEY=...              # mistral backend

# Extension (VS Code settings)
conductor.backendUrl=http://localhost:8000
conductor.enableWorkspace=true
```

## Recent Changes

- **P0: Multi-Provider Embeddings** — 5 configurable embedding backends with `EmbeddingProvider` abstraction
- **P1: RepoMap** — tree-sitter + networkx graph + PageRank for graph-based context
- **Hybrid retrieval** — vector search + graph search combined in context router
- Added `VoyageSecrets` + `MistralSecrets` to config
- Updated `_inject_embedding_env_vars()` for all 5 backends
- 220+ new backend test cases

## What's Next

See [ROADMAP.md](ROADMAP.md) for planned features. Current focus:
- Model B delegate authentication
- Conflict resolution for concurrent edits
- Enterprise features (room access control, audit export)
