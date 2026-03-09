# Testing Guide

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This guide covers running tests for the backend (Python/pytest) and the extension (TypeScript/VS Code test runner).

## Backend Tests

```bash
cd backend
pytest                             # all tests
pytest -k "test_workspace"         # workspace tests only
pytest -k "test_embedding"         # embedding provider tests only
pytest -k "test_rerank"            # reranking provider tests only
pytest -k "test_repo_graph"        # repo graph tests only
pytest -v --tb=short               # verbose with short tracebacks
pytest --cov=. --cov-report=html   # coverage report
```

### Test Files

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_workspace.py` | Workspace CRUD, Git ops, search | `/workspace/` router |
| `tests/test_ai.py` | AI status, inference, provider selection | `/ai/` router |
| `tests/test_chat.py` | WebSocket, history, typing indicators | `/chat` router |
| `tests/test_files.py` | Upload, download, dedup, retry | `/files/` router |
| `tests/test_changes.py` | MockAgent, policy, audit | `/generate-changes`, `/policy/`, `/audit/` |
| `tests/test_code_search.py` | CodeSearchService, search/index endpoints | `/api/code-search/` router |
| `tests/test_config_new.py` | Config models, secrets, env injection | `config.py` |
| `tests/test_context.py` | Context router, hybrid retrieval, reranking integration | `/api/context/` router |
| `tests/test_embedding_provider.py` | All 5 embedding backends, factory | `embedding_provider.py` |
| `tests/test_rerank_provider.py` | All 4 reranking backends, factory | `rerank_provider.py` |
| `tests/test_repo_graph.py` | Parser, graph, PageRank, RepoMapService | `repo_graph/` module |
| `tests/test_git_workspace.py` | Git workspace lifecycle | `git_workspace/` module |

### Embedding Provider Tests (78 tests)

The `test_embedding_provider.py` file covers all 5 backends:

**LocalEmbeddingProvider:**
- Initialization (default/custom model, lazy loading)
- `embed_texts()` — single/batch/empty, dtype verification
- `embed_query()` — returns 1D vector
- `dimensions` / `health_check()` / `name` properties

**BedrockEmbeddingProvider:**
- Cohere Embed v4 — batch embed, input_type="search_document" vs "search_query"
- Titan V2 — per-text invocation, multiple texts
- Body construction for both model families
- Dimension lookup map

**OpenAIEmbeddingProvider:**
- `text-embedding-3-small` / `text-embedding-3-large` / `ada-002`
- Batch embedding, query embedding
- Dimension lookup

**VoyageEmbeddingProvider:**
- `voyage-code-3` / `voyage-3-lite` dimensions
- `input_type="document"` vs `input_type="query"`

**MistralEmbeddingProvider:**
- `codestral-embed-2505` / `mistral-embed`
- Batch and single embedding

**Factory:**
- `create_embedding_provider()` for all 5 backends
- Custom model names, API keys, credentials
- Unknown backend raises `ValueError`
- ABC contract — cannot instantiate base class

### Reranking Provider Tests (86 tests)

The `test_rerank_provider.py` file covers all 4 backends:

**NoopRerankProvider:**
- Passthrough returns documents in original order
- Monotonically decreasing scores
- `top_n` truncation
- Empty documents → empty list
- Name returns `"none"`

**CohereRerankProvider:**
- Lazy client initialization
- `rerank()` with mocked Cohere API client
- Custom API key passthrough
- `top_n` parameter forwarding
- Empty documents handling
- Score ordering validation

**BedrockRerankProvider:**
- `invoke_model()` with correct body format
- Custom model ID and region
- Explicit AWS credentials passthrough
- Response parsing from Bedrock JSON
- Score sorting (descending)
- Empty documents handling

**CrossEncoderRerankProvider:**
- Lazy model loading
- `predict()` with (query, document) pairs
- Score-based sorting
- Custom model name
- `top_n` truncation

**Factory (`create_rerank_provider`):**
- All 4 backends (`none`, `cohere`, `bedrock`, `cross_encoder`)
- Default settings → NoopRerankProvider
- Custom model names and credentials
- Unknown backend raises `ValueError`
- ABC contract — cannot instantiate `RerankProvider` directly

### RepoMap Tests (72 tests)

The `test_repo_graph.py` file covers:

**Parser (`parser.py`):**
- Language detection for 14 file extensions
- Regex extraction: Python functions, async functions, classes
- JavaScript/TypeScript functions, classes, interfaces
- Multiple definitions in one file
- Reference extraction
- Signature truncation for long lines
- Empty source / unknown language fallback
- `extract_definitions()` with file path and source bytes

**Graph (`graph.py`):**
- Empty workspace → empty graph
- Single file → one node
- Two files with cross-references → edge creation
- Excludes `node_modules/`, `.git/`, `venv/`
- Pre-computed symbols
- No self-edges (self-references filtered)
- Edge weight counts multiple references
- Stats dictionary populated

**PageRank (`rank_files()`):**
- Empty graph returns []
- Uniform ranking for disconnected nodes
- `top_n` limits output
- Personalised PageRank with query files
- Updates `node.pagerank` values

**RepoMapService:**
- Graph building and caching
- Force rebuild
- `generate_repo_map()` text output
- `get_context_files()` — merges vector + graph, preserves order
- `invalidate_cache()` — specific and all
- `get_graph_stats()` — cached and uncached

### Config Tests (42 tests)

The `test_config_new.py` file covers:

- `CodeSearchSettings` — all defaults, all 5 embedding backends, all 4 reranking backends, invalid backend rejection
- `VoyageSecrets` + `MistralSecrets` + `CohereSecrets` — new secret models
- `_inject_embedding_env_vars()` — env var injection for bedrock/openai/voyage/mistral/local (embedding) + cohere/bedrock (reranking)
- `AppSettings` — full model instantiation and serialization
- `load_settings()` — YAML loading, missing files, secrets merging

### Context Router Tests (42 tests)

The `test_context.py` file covers:

- `POST /api/context/context` — vector search + repo map integration
- Repo map included by default, disabled via `include_repo_map=false`
- Repo map service unavailable → graceful fallback
- Repo map generation error → returns null (no 500)
- Validation: missing query/room_id, top_k bounds
- `GET /api/context/context/{room_id}/index-status`
- `GET /api/context/context/{room_id}/graph-stats`
- `GET /api/context/context/{room_id}/rerank-status`
- No workspace → 404
- **Reranking integration tests:**
  - Reranking enabled → fetches more candidates, re-orders chunks
  - Reranking disabled → passes through vector search order
  - Per-request `enable_reranking` override
  - Reranker failure → graceful fallback to vector results
  - Reranked response includes `reranked: true` and per-chunk `rerank_score`
  - Noop provider → no reranking applied
  - No rerank provider configured → reranking skipped

## Extension Tests

```bash
cd extension
npm test                           # all tests (launches VS Code test host)
npm run test:unit                  # unit tests only (no VS Code)
npm run lint                       # ESLint check
```

### Extension Test Files

| File | Tests | Coverage |
|------|-------|----------|
| `src/test/sessionFSM.test.ts` | All FSM state transitions | `SessionFSM` |
| `src/test/workspaceClient.test.ts` | HTTP client methods, error handling | `WorkspaceClient` |
| `src/test/fileSystemProvider.test.ts` | read/write/delete/rename, error cases | `FileSystemProvider` |
| `src/test/workspacePanel.test.ts` | Wizard step progression, validation | `WorkspacePanel` |

**Total extension unit tests: 231**

## CI / GitHub Actions

```yaml
# .github/workflows/test.yml (excerpt)
jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: cd backend && pip install -r requirements.txt && pytest

  extension-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd extension && npm ci && npm test
```

---

<a name="中文"></a>
## 中文

本指南涵盖后端（Python/pytest）和扩展（TypeScript/VS Code 测试运行器）的测试。

## 后端测试

```bash
cd backend
pytest                             # 所有测试
pytest -k "test_embedding"         # 仅 embedding 测试
pytest -k "test_rerank"            # 仅 reranking 测试
pytest -k "test_repo_graph"        # 仅 repo graph 测试
pytest -v --tb=short               # 详细输出
pytest --cov=. --cov-report=html   # 覆盖率报告
```

### 测试文件

| 文件 | 测试数 | 覆盖 |
|------|--------|------|
| `tests/test_embedding_provider.py` | 78 | 5 个 embedding 后端 + 工厂函数 |
| `tests/test_rerank_provider.py` | 86 | 4 个 reranking 后端 + 工厂函数 |
| `tests/test_repo_graph.py` | 72 | 解析器 + 图构建 + PageRank + 服务 |
| `tests/test_config_new.py` | 42 | 配置模型 + 密钥 + 环境变量注入 |
| `tests/test_context.py` | 42 | 上下文路由 + 混合检索 + 重排序 |
| `tests/test_code_search.py` | 52 | 代码搜索服务 + 端点 |
| `tests/test_git_workspace.py` | — | Git 工作区生命周期 |

### Embedding Provider 测试要点

- 所有 5 个后端都有完整的初始化 + 嵌入 + 查询测试
- Bedrock: Cohere v4 批量嵌入 vs Titan V2 逐条调用
- Voyage: `input_type="document"` vs `"query"` 区分
- 工厂函数: 所有后端 + 自定义模型 + API 密钥传递 + 未知后端异常

### Reranking Provider 测试要点

- 所有 4 个后端都有完整的初始化 + 重排序 + 评分测试
- Cohere: 模拟 API 客户端 + `top_n` 转发 + 评分排序
- Bedrock: `invoke_model` 调用 + JSON 响应解析 + AWS 凭证传递
- CrossEncoder: 模型预测 + (query, document) 对构建 + 评分排序
- Noop: 保持原始顺序 + 单调递减评分
- 工厂函数: 所有 4 个后端 + 自定义模型 + 未知后端异常

### RepoMap 测试要点

- 解析器: 14 种文件扩展名语言检测 + 正则回退
- 图构建: 跨文件引用 → 有向边 + 权重
- PageRank: 均匀/个性化排名 + top_n 限制
- 服务: 缓存 + 混合上下文文件合并
