# Architecture Overview

Last updated: 2026-03-09

## System Components

```
┌──────────────────────────┐     ┌─────────────────────────────┐
│   VS Code Extension      │     │   FastAPI Backend            │
│                          │     │                             │
│  ┌──────────────────┐    │     │  ┌───────────────────────┐  │
│  │ SessionFSM       │    │ WS  │  │ WebSocket Manager     │  │
│  │ WebSocketService  │◄──┼─────┼──│ (rooms, broadcast)    │  │
│  │ CollabPanel       │    │     │  └───────────────────────┘  │
│  └──────────────────┘    │     │                             │
│                          │     │  ┌───────────────────────┐  │
│  ┌──────────────────┐    │HTTP │  │ Git Workspace Service │  │
│  │ WorkspaceClient   │◄──┼─────┼──│ (clone, worktree,     │  │
│  │ WorkspacePanel    │    │     │  │  commit, push)        │  │
│  │ FileSystemProvider│    │     │  └───────────────────────┘  │
│  └──────────────────┘    │     │                             │
│                          │     │  ┌───────────────────────┐  │
│                          │     │  │ Code Search Service   │  │
│                          │     │  │ ┌─────────────────┐   │  │
│                          │     │  │ │EmbeddingProvider│   │  │
│                          │     │  │ │ local│bedrock│  │   │  │
│                          │     │  │ │ openai│voyage│  │   │  │
│                          │     │  │ │ mistral        │   │  │
│                          │     │  │ └─────────────────┘   │  │
│                          │     │  │ CocoIndex (sqlite-vec)│  │
│                          │     │  └───────────────────────┘  │
│                          │     │                             │
│                          │     │  ┌───────────────────────┐  │
│                          │     │  │ Rerank Service        │  │
│                          │     │  │ ┌─────────────────┐   │  │
│                          │     │  │ │RerankProvider   │   │  │
│                          │     │  │ │ none│cohere│    │   │  │
│                          │     │  │ │ bedrock│cross_  │   │  │
│                          │     │  │ │ encoder         │   │  │
│                          │     │  │ └─────────────────┘   │  │
│                          │     │  └───────────────────────┘  │
│                          │     │                             │
│                          │     │  ┌───────────────────────┐  │
│                          │     │  │ RepoMap Service       │  │
│                          │     │  │ tree-sitter → graph   │  │
│                          │     │  │ → PageRank ranking    │  │
│                          │     │  └───────────────────────┘  │
│                          │     │                             │
│                          │     │  ┌───────────────────────┐  │
│                          │     │  │ Context Router        │  │
│                          │     │  │ vector → rerank →     │  │
│                          │     │  │ graph hybrid          │  │
│                          │     │  └───────────────────────┘  │
└──────────────────────────┘     └─────────────────────────────┘
```

## Backend Module Architecture

### Configuration (`config.py`)

Settings are loaded from two YAML files:
- `conductor.settings.yaml` — non-secret configuration
- `conductor.secrets.yaml` — API keys, credentials (never committed)

The `AppSettings` Pydantic model contains all sub-settings. The `_inject_embedding_env_vars()` function pushes secrets from our config into environment variables expected by downstream SDKs (embedding and reranking providers).

### Git Workspace (`git_workspace/`)

Manages per-room Git worktrees for file collaboration:
- **Token mode (Model A)**: Backend holds PAT, uses GIT_ASKPASS
- **Delegate mode (Model B)**: Extension proxies Git ops (planned)

### Code Search (`code_search/`)

Semantic code search powered by CocoIndex:

```
Source files → AST chunking → Embedding → sqlite-vec storage
                                  │
                    ┌─────────────┴────────────────┐
                    │     EmbeddingProvider         │
                    │                               │
              ┌─────┴──────┐  ┌────────┐  ┌───────┴──────┐
              │   Local     │  │Bedrock │  │   OpenAI     │
              │SentenceTF   │  │Cohere  │  │ text-emb-3   │
              └────────────┘  │Titan   │  └──────────────┘
                              └────────┘
              ┌────────────┐  ┌────────────┐
              │  Voyage    │  │  Mistral   │
              │voyage-code │  │codestral   │
              └────────────┘  └────────────┘
```

**EmbeddingProvider hierarchy:**
- `EmbeddingProvider` (ABC) — defines `embed_texts()`, `embed_query()`, `dimensions`, `name`
- `LocalEmbeddingProvider` — SentenceTransformers, runs on CPU
- `BedrockEmbeddingProvider` — Cohere Embed v4 (default) or Titan V2
- `OpenAIEmbeddingProvider` — OpenAI API
- `VoyageEmbeddingProvider` — code-specialised models
- `MistralEmbeddingProvider` — Codestral Embed

### Reranking (`code_search/rerank_provider.py`)

Post-retrieval re-scoring for improved search precision:

```
Vector search candidates (K=20)
          │
          ▼
    ┌─────────────────────────────┐
    │      RerankProvider          │
    │                              │
    │  ┌─────┐ ┌──────┐ ┌──────┐ │
    │  │Noop │ │Cohere│ │Bedrk │ │
    │  │     │ │ API  │ │Cohere│ │
    │  └─────┘ └──────┘ └──────┘ │
    │  ┌──────────┐               │
    │  │CrossEnc  │               │
    │  │ms-marco  │               │
    │  └──────────┘               │
    └─────────────────────────────┘
          │
          ▼
    Top-N reranked results (N=5)
```

**RerankProvider hierarchy:**
- `RerankProvider` (ABC) — defines `rerank()`, `name`, `health_check()`
- `NoopRerankProvider` — passthrough (default, no reranking)
- `CohereRerankProvider` — Cohere Rerank 3.5 via direct API
- `BedrockRerankProvider` — Cohere Rerank 3.5 via AWS Bedrock
- `CrossEncoderRerankProvider` — local cross-encoder (ms-marco-MiniLM-L-6-v2)

### RepoMap (`repo_graph/`)

Aider-style repository understanding via dependency graph:

```
Source files → tree-sitter AST → Extract definitions + references
                                          │
                                    Build directed graph
                                    (file A → file B if A
                                     references B's symbols)
                                          │
                                    PageRank ranking
                                    (personalised to query)
                                          │
                                    Generate text repo map
```

**Components:**
- `parser.py` — AST extraction with tree-sitter (regex fallback for CI)
- `graph.py` — networkx DiGraph, weighted edges, PageRank
- `service.py` — caching, map generation, hybrid file selection

### Context Router (`context/router.py`)

Three-stage hybrid retrieval combining vector search, reranking, and graph search:

```
User query
    │
    ├── 1. Vector search (CocoIndex)
    │   → top-K semantically similar code chunks (K=20 if reranking)
    │
    ├── 2. Reranking (optional, RerankProvider)
    │   → re-score candidates with cross-encoder / API reranker
    │   → top-N most relevant chunks (N=5)
    │
    └── 3. Graph search (RepoMap)
        → personalised PageRank (biased to reranked results)
        → top-M structurally important files
        → text-based repo map for AI prompt
```

The `POST /api/context/context` endpoint returns:
- `chunks` — ranked code snippets (with optional `rerank_score`)
- `repo_map` — text showing file structure for AI context
- `reranked` — boolean indicating whether reranking was applied

## Data Flow: Semantic Search

```
1. User types query in chat
2. Extension sends POST /api/context/context {room_id, query}
3. Backend resolves room → workspace path
4. CodeSearchService runs vector search via CocoIndex
5. (Optional) RerankProvider re-scores candidates
6. RepoMapService builds/caches dependency graph
7. PageRank is personalised to files from step 4/5
8. RepoMap text is generated from top-ranked files
9. Response: {chunks, repo_map, reranked} sent to extension
10. Extension feeds chunks + repo_map to AI provider as context
```

## Data Flow: Index Building

```
1. Workspace created (POST /api/git-workspace/workspaces)
2. Files cloned into worktree
3. POST /api/code-search/index {workspace_path}
4. CocoIndex scans files, applies AST-aware chunking
5. EmbeddingProvider embeds each chunk
6. Embeddings stored in sqlite-vec database
7. RepoMapService lazily builds dependency graph on first query
```

## Configuration Reference

### Embedding Backends

| Backend | Model ID | Dimensions | Context | Cost/1M | Credentials |
|---------|----------|------------|---------|---------|-------------|
| `local` | `all-MiniLM-L6-v2` | 384 | — | Free | None |
| `bedrock` | `cohere.embed-v4:0` | 1024 | 128K | $0.12 | AWS keys |
| `bedrock` | `amazon.titan-embed-text-v2:0` | 1024 | 8K | $0.20 | AWS keys |
| `openai` | `text-embedding-3-small` | 1536 | 8K | $0.02 | OpenAI key |
| `voyage` | `voyage-code-3` | 1024 | 16K | $0.06 | Voyage key |
| `mistral` | `codestral-embed-2505` | 1024 | — | — | Mistral key |

### Reranking Backends

| Backend | Model | Cost/1K queries | Latency | Credentials |
|---------|-------|-----------------|---------|-------------|
| `none` | — | Free | 0ms | None |
| `cohere` | `rerank-v3.5` | $2.00 | ~200ms | Cohere API key |
| `bedrock` | `cohere.rerank-v3-5:0` | $2.00 | ~200ms | AWS keys |
| `cross_encoder` | `ms-marco-MiniLM-L-6-v2` | Free | ~100ms | None |

### Settings YAML

```yaml
code_search:
  embedding_backend: "bedrock"           # local|bedrock|openai|voyage|mistral
  bedrock_model_id: "cohere.embed-v4:0"
  bedrock_region: "us-east-1"
  local_model_name: "all-MiniLM-L6-v2"
  openai_model_name: "text-embedding-3-small"
  voyage_model_name: "voyage-code-3"
  mistral_model_name: "codestral-embed-2505"
  repo_map_enabled: true
  repo_map_top_n: 10

  # Reranking
  rerank_backend: "none"                 # none|cohere|bedrock|cross_encoder
  rerank_top_n: 5                        # Return top N after reranking
  rerank_candidates: 20                  # Fetch from vector search before reranking
  cohere_rerank_model: "rerank-v3.5"
  bedrock_rerank_model_id: "cohere.rerank-v3-5:0"
  cross_encoder_model_name: "cross-encoder/ms-marco-MiniLM-L-6-v2"
```

### Secrets YAML

```yaml
aws:
  access_key_id: "AKIA..."
  secret_access_key: "..."
  region: "us-east-1"
openai:
  api_key: "sk-..."
voyage:
  api_key: "pa-..."
mistral:
  api_key: "..."
cohere:
  api_key: "..."                         # For direct Cohere Rerank API
```
