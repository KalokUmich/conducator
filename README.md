# Conductor

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

Conductor is a VS Code collaboration extension plus a FastAPI backend for team chat, Git workspace management, file sharing, and AI-assisted decision/code workflows.

### Current Capabilities

- VS Code WebView collaboration panel with FSM-driven session lifecycle:
  - `Idle`
  - `BackendDisconnected` (join-only mode)
  - `ReadyToHost`
  - `CreatingWorkspace` (provisioning a Git worktree on the backend)
  - `Hosting`
  - `Joining`
  - `Joined`
- Git Workspace management replacing Live Share:
  - Per-room bare repo + worktree isolation (each room gets its own Git branch `session/{room_id}`)
  - Mode A: token authentication via GIT_ASKPASS (user provides a Personal Access Token)
  - Mode B: delegate authentication (VS Code extension performs Git operations on behalf of the backend)
  - File-sync broadcast with debouncing; commit and push from backend
  - **FileSystemProvider** (`conductor://` URI scheme): remote worktree files appear in VS Code explorer as if local; full read/write/delete/rename support backed by the backend REST API
  - **WorkspacePanel**: 5-step native VS Code input wizard for workspace creation (no WebView)
  - **WorkspaceClient**: typed HTTP client for all `/workspace/` endpoints
- Real-time WebSocket chat with:
  - reconnect recovery (`since`)
  - typing indicators
  - read receipts
  - message deduplication
  - paginated history
- File upload/download (20MB limit, extension-host upload proxy, duplicate detection, retry logic)
- Code snippet sharing + editor navigation
- Change review workflow:
  - `POST /generate-changes` (MockAgent)
  - policy check (`POST /policy/evaluate-auto-apply`)
  - per-change diff preview
  - sequential apply/skip
  - audit logging (`POST /audit/log-apply`)
- AI provider workflow:
  - provider health/status (`GET /ai/status`)
  - four-step provider selection + confirmation UI
  - streaming inference (`POST /ai/infer`)
- **Semantic Code Search (CocoIndex)**:
  - AST-aware code chunking + embedding + sqlite-vec vector storage
  - 5 configurable embedding backends: local (SentenceTransformers), bedrock (Cohere Embed v4), openai, voyage, mistral
  - Default: Cohere Embed v4 via AWS Bedrock ($0.12/1M tokens, 128K context)
  - Per-workspace index management
- **RepoMap (Graph-Based Context)**:
  - tree-sitter AST parsing for symbol extraction (regex fallback)
  - File dependency graph (networkx) with PageRank ranking
  - Hybrid retrieval: vector search + graph-based repo map
  - Personalised PageRank biased towards query-relevant files
- Workspace code search:
  - `GET /workspace/{room_id}/search?q=...` — full-text search across all files in a session worktree
  - results include file path, line number, and matched line content
  - VS Code extension `WorkspaceClient.searchCode()` method
  - keyboard shortcut `Ctrl+Shift+F` / `Cmd+Shift+F` opens inline search panel in WebView

### Architecture

```
extension/          VS Code extension (TypeScript)
  src/
    panels/         WebView panels (CollabPanel, WorkspacePanel)
    services/       FSM, WebSocket, FileSystemProvider, WorkspaceClient
    commands/       VS Code command handlers
backend/            FastAPI server (Python)
  app/
    git_workspace/  Git worktree management (Model A/B)
    code_search/    CocoIndex + EmbeddingProvider abstraction
    repo_graph/     tree-sitter + networkx + PageRank
    context/        Hybrid retrieval (vector + graph)
    config.py       Settings + Secrets from YAML
    main.py         App factory + lifespan
  config/           YAML config templates
  tests/            pytest test suite (220+ new tests)
```

### Embedding Backend Options

| Backend | Model | Dimensions | Cost/1M | Context |
|---------|-------|------------|---------|---------|
| `local` | all-MiniLM-L6-v2 | 384 | Free | — |
| `bedrock` | cohere.embed-v4:0 | 1024 | $0.12 | 128K |
| `openai` | text-embedding-3-small | 1536 | $0.02 | 8K |
| `voyage` | voyage-code-3 | 1024 | $0.06 | 16K |
| `mistral` | codestral-embed-2505 | 1024 | — | — |

Switch backends in `conductor.settings.yaml`:
```yaml
code_search:
  embedding_backend: "bedrock"  # local | bedrock | openai | voyage | mistral
```

### Quick Start

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Extension
cd extension
npm install
npm run compile
# Press F5 in VS Code to launch Extension Development Host
```

### Running Tests

```bash
cd backend
pytest                             # all tests
pytest tests/test_embedding_provider.py -v  # embedding tests (78)
pytest tests/test_repo_graph.py -v          # repo graph tests (72)
pytest tests/test_config_new.py -v          # config tests (42)
```

### Documentation

- [Architecture](docs/ARCHITECTURE.md) — system components and data flow
- [Backend Guide](docs/GUIDE.md) — code walkthrough for junior engineers
- [Guide Addendum](docs/GUIDE_ADDENDUM.md) — embedding providers and RepoMap
- [Testing](TESTING.md) — comprehensive test guide (EN + 中文)
- [Roadmap](ROADMAP.md) — project phases and ADRs
- [Claude](CLAUDE.md) — guide for AI coding assistants

---

<a name="中文"></a>
## 中文

Conductor 是一个 VS Code 协作扩展 + FastAPI 后端，用于团队聊天、Git 工作区管理、文件共享和 AI 辅助代码工作流。

### 当前功能

- **语义代码搜索**: CocoIndex AST 感知分块 + 5 种可配置 embedding 后端
  - 默认: Cohere Embed v4 (AWS Bedrock, $0.12/百万 token, 128K 上下文)
  - 本地: SentenceTransformers (免费, 无需 API 密钥)
  - 还支持: OpenAI, Voyage AI, Mistral
- **RepoMap 图上下文**: tree-sitter AST 解析 + networkx 依赖图 + PageRank 排名
- **混合检索**: 向量搜索 + 图搜索组合, 个性化 PageRank
- Git 工作区管理 (替代 Live Share)
- 实时 WebSocket 聊天
- 文件上传/下载
- AI 提供者集成
- 变更审查工作流

### Embedding 后端

在 `conductor.settings.yaml` 中切换:
```yaml
code_search:
  embedding_backend: "bedrock"  # local | bedrock | openai | voyage | mistral
```

密钥在 `conductor.secrets.yaml` 中配置:
```yaml
aws:
  access_key_id: "AKIA..."
  secret_access_key: "..."
voyage:
  api_key: "pa-..."
mistral:
  api_key: "..."
```

### 测试

```bash
cd backend
pytest                                        # 所有测试
pytest tests/test_embedding_provider.py -v    # embedding 测试 (78 项)
pytest tests/test_repo_graph.py -v            # 图测试 (72 项)
```
