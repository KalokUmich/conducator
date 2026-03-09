# Guide Addendum: Embedding Providers, RepoMap & Reranking

**This is an addendum to the main [Backend Code Walkthrough](GUIDE.md).**

Sections 8.5, 8.6, and 8.7 extend the Workspace Code Search chapter.

---

## 8.5 Multi-Provider Embeddings

### 8.5.1 The Problem

Different teams have different cloud provider preferences and API access:
- Some can use AWS Bedrock (existing infra)
- Some prefer OpenAI (already have API keys)
- Some want local/offline mode for development
- Some want code-specialised models (Voyage, Mistral)

We need a pluggable system that lets you switch embedding backends without changing service code.

### 8.5.2 The EmbeddingProvider Abstraction

```python
# backend/app/code_search/embedding_provider.py

class EmbeddingProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @property
    @abc.abstractmethod
    def dimensions(self) -> int: ...

    @abc.abstractmethod
    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...

    async def embed_query(self, query: str) -> np.ndarray:
        # Default: delegates to embed_texts
        # Override for models that distinguish document vs query input
        result = await self.embed_texts([query])
        return result[0]
```

**Why two methods (`embed_texts` vs `embed_query`)?**

Some embedding models use different input prefixes for documents vs queries. For example:
- Cohere uses `input_type="search_document"` vs `input_type="search_query"`
- Voyage uses `input_type="document"` vs `input_type="query"`

The default `embed_query` just calls `embed_texts`, but Bedrock and Voyage override it to pass the correct type.

### 8.5.3 The Five Backends

**1. Local (SentenceTransformers)**

```python
class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        self._model = None  # lazy-loaded

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self._model_name)
```

Free, runs on CPU, no API key needed. Good for development and CI.

**2. Bedrock (Cohere Embed v4)**

```python
class BedrockEmbeddingProvider(EmbeddingProvider):
    async def embed_texts(self, texts):
        body = json.dumps({
            "texts": list(texts),
            "input_type": "search_document",
            "truncate": "END",
        })
        response = await self._invoke(body)
        return np.array(response["embeddings"], dtype=np.float32)
```

Default backend. Cohere Embed v4 has 128K context window and costs $0.12/1M tokens. Also supports Titan V2 (but Titan only handles one text at a time and has only 8K context).

**3. OpenAI**

```python
class OpenAIEmbeddingProvider(EmbeddingProvider):
    async def embed_texts(self, texts):
        response = await loop.run_in_executor(
            None,
            lambda: self._client.embeddings.create(
                model=self._model_name, input=list(texts)
            ),
        )
        return np.array([d.embedding for d in response.data], dtype=np.float32)
```

Standard OpenAI API. `text-embedding-3-small` at $0.02/1M tokens.

**4. Voyage AI**

Code-specialised models. `voyage-code-3` is trained on code and returns 1024-d vectors.

**5. Mistral**

`codestral-embed-2505` is Mistral's code embedding model.

### 8.5.4 The Factory

```python
def create_embedding_provider(settings) -> EmbeddingProvider:
    backend = settings.embedding_backend

    if backend == "local":
        return LocalEmbeddingProvider(model_name=settings.local_model_name)
    if backend == "bedrock":
        return BedrockEmbeddingProvider(
            model_id=settings.bedrock_model_id,
            region=settings.bedrock_region,
        )
    # ... openai, voyage, mistral
    raise ValueError(f"Unknown backend: {backend}")
```

The factory reads settings from `CodeSearchSettings` and instantiates the correct provider.

### 8.5.5 Credential Management

Credentials flow from `conductor.secrets.yaml` through our config to environment variables:

```
conductor.secrets.yaml → Secrets model → _inject_embedding_env_vars() → os.environ
```

This means the embedding SDK libraries (boto3, openai, voyageai, mistralai) can use their standard credential discovery without knowing about our config system.

```python
def _inject_embedding_env_vars(settings):
    if backend == "bedrock":
        os.environ["AWS_ACCESS_KEY_ID"] = secrets.aws.access_key_id
        os.environ["AWS_SECRET_ACCESS_KEY"] = secrets.aws.secret_access_key
    elif backend == "voyage":
        os.environ["VOYAGE_API_KEY"] = secrets.voyage.api_key
    # ...
```

**Why not pass credentials directly?**

Some libraries (especially boto3) have complex credential chains (env vars → instance profiles → config files). Injecting into env vars keeps our code simple and lets the SDK do its normal credential resolution.

---

## 8.6 RepoMap: Graph-Based Context

### 8.6.1 The Problem with Vector Search Alone

Vector search finds code that's semantically similar to the query. But it misses structural context:

- A query about "how the config is loaded" finds `config.py` but not `main.py` (which calls `load_settings()`)
- A query about "the search endpoint" finds `router.py` but not `service.py` or `schemas.py` that it depends on

We need a way to identify structurally important files — files that are central to the architecture, heavily imported, or closely connected to the query results.

### 8.6.2 The Aider Approach

[Aider](https://aider.chat) pioneered the "repo map" concept:

1. Parse every source file to extract definitions (functions, classes) and references (identifiers)
2. Build a directed graph: file A → file B if A uses symbols defined in B
3. Run PageRank to find the most important files
4. Generate a compact text map of those files and their symbols
5. Include this map in the AI prompt so it understands the repo structure

### 8.6.3 The Parser

```python
# backend/app/repo_graph/parser.py

@dataclass
class SymbolDef:
    name: str        # "MyService"
    kind: str        # "class"
    file_path: str   # "service.py"
    start_line: int  # 15
    end_line: int    # 42
    signature: str   # "class MyService(BaseService):"

@dataclass
class SymbolRef:
    name: str        # "MyService"
    file_path: str   # "main.py"
    line: int        # 23
```

The parser has two modes:

**tree-sitter (preferred)**: Parses the full AST, walks for `function_definition`, `class_definition`, etc. Supports Python, JavaScript, TypeScript, Java, Go, Rust, C, C++.

**regex fallback**: For CI environments without tree-sitter grammars installed. Pattern-matches `def foo()`, `class Foo:`, `function bar()`, etc. Less accurate but works everywhere.

### 8.6.4 The Dependency Graph

```python
# backend/app/repo_graph/graph.py

def build_dependency_graph(workspace_path, file_symbols=None):
    # 1. Extract symbols from all files
    # 2. Build symbol → file lookup
    # 3. For each reference in file A, if it matches a definition in file B:
    #    Add edge A → B with weight = number of references
```

Example: if `main.py` calls `load_settings()` (defined in `config.py`) 3 times, the edge `main.py → config.py` has weight 3.

Self-references (same file) are filtered out.

### 8.6.5 PageRank

```python
def rank_files(dep_graph, query_files=None, top_n=10):
    # Personalised PageRank:
    # - query_files get higher teleportation probability
    # - Connected files get score via graph traversal
    scores = nx.pagerank(G, alpha=0.85, personalization=personalization)
```

**Personalised PageRank** is key: by biasing the teleportation probability towards files found by vector search, we get files that are structurally connected to the query results. A file that imports and is imported by the vector search hits will rank high, even if it has no semantic similarity to the query text.

### 8.6.6 The Repo Map

```python
svc = RepoMapService(top_n=10)
print(svc.generate_repo_map("/path/to/workspace"))
```

Output:
```
## Repository Map (top 5 files by importance)

backend/app/config.py
    ├── class AppSettings
    ├── class CodeSearchSettings
    ├── function def load_settings():
    └── function def _inject_embedding_env_vars():

backend/app/main.py
    ├── function async def lifespan():
    └── function def create_app():
```

### 8.6.7 Hybrid Retrieval

The context router combines both signals:

```python
@router.post("/context", response_model=ContextResponse)
async def get_context(req: ContextRequest):
    # 1. Vector search → chunks
    search_result = await code_search.search(query, workspace_path, top_k)

    # 2. Graph search → repo map
    vector_files = [c.file_path for c in chunks]
    repo_map_text = repo_map_svc.generate_repo_map(
        workspace_path, query_files=vector_files
    )

    return ContextResponse(chunks=chunks, repo_map=repo_map_text)
```

The AI receives both:
- **chunks**: Specific code snippets directly relevant to the query
- **repo_map**: Overall file structure showing how the codebase is organised

This gives the AI both "zoom-in" (specific lines) and "zoom-out" (architectural overview) context.

---

## 8.7 Reranking: Post-Retrieval Precision Boost

### 8.7.1 The Problem with Vector Search Ranking

Embedding models compress entire text chunks into a single vector. When you compare query and document vectors via cosine similarity, you get a rough semantic score — but it's lossy. The embedding captures the "gist" but may miss nuances like:

- The query mentions a specific function name, but the embedding model treats it as a generic word
- Two chunks have similar topics but different relevance to the exact question
- The top-5 from vector search might not be the best top-5 a more careful model would pick

### 8.7.2 How Reranking Works

Reranking is a **two-stage retrieval** pattern used in production search systems:

```
Stage 1: Vector search (fast, approximate)
    Query → embedding → cosine similarity → top-K candidates (e.g. 20)

Stage 2: Reranking (slow, precise)
    For each candidate: score(query, document) → re-sort → top-N results (e.g. 5)
```

The reranker sees the full query and full document text together (not compressed into vectors), so it can make more nuanced relevance judgments. This is called a **cross-encoder** approach — both inputs go through the model simultaneously, producing a single relevance score.

### 8.7.3 The RerankProvider Abstraction

```python
# backend/app/code_search/rerank_provider.py

@dataclass
class RerankResult:
    index: int      # Original position in the input list
    score: float    # Reranker relevance score (higher = more relevant)
    text: str       # The document text

class RerankProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: Optional[int] = None,
    ) -> List[RerankResult]: ...

    def health_check(self) -> Dict[str, Any]:
        return {"provider": self.name, "status": "ok"}
```

The interface is simple: give it a query and a list of candidate documents, get back a sorted list of results with relevance scores.

### 8.7.4 The Four Backends

**1. Noop (Passthrough)**

```python
class NoopRerankProvider(RerankProvider):
    async def rerank(self, query, documents, top_n=None):
        # Returns documents in original order with monotonically decreasing scores
        return [RerankResult(index=i, score=1.0 - (i * 0.001), text=doc)
                for i, doc in enumerate(documents[:n])]
```

Default backend — reranking disabled. Documents keep their vector search ranking.

**2. Cohere Rerank (Direct API)**

```python
class CohereRerankProvider(RerankProvider):
    async def rerank(self, query, documents, top_n=None):
        response = await loop.run_in_executor(
            None,
            lambda: self._client.rerank(
                model=self._model,
                query=query,
                documents=doc_list,
                top_n=n,
            ),
        )
        # Map response to RerankResult objects
```

Uses the Cohere Rerank API directly. Model: `rerank-v3.5`. Cost: ~$2/1K queries. Each query can rerank up to 100 documents.

**3. Bedrock Rerank (Cohere on AWS)**

```python
class BedrockRerankProvider(RerankProvider):
    async def rerank(self, query, documents, top_n=None):
        body = json.dumps({"query": query, "documents": doc_list, "top_n": n})
        response = await loop.run_in_executor(
            None,
            lambda: self._client.invoke_model(
                modelId=self._model_id, body=body, ...
            ),
        )
```

Same Cohere Rerank 3.5 model, but accessed through AWS Bedrock. Reuses existing AWS credentials from `conductor.secrets.yaml`. Model ID: `cohere.rerank-v3-5:0`.

**4. Cross-Encoder (Local)**

```python
class CrossEncoderRerankProvider(RerankProvider):
    async def rerank(self, query, documents, top_n=None):
        pairs = [(query, doc) for doc in doc_list]
        scores = await loop.run_in_executor(
            None,
            lambda: self._model.predict(pairs),
        )
        # Sort by score descending
```

Uses `sentence-transformers` `CrossEncoder` class. Default model: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80 MB). Free, runs on CPU. Good for development and testing.

### 8.7.5 The Factory

```python
def create_rerank_provider(settings) -> RerankProvider:
    backend = getattr(settings, "rerank_backend", "none")
    if backend == "none":
        return NoopRerankProvider()
    if backend == "cohere":
        return CohereRerankProvider(model=settings.cohere_rerank_model)
    if backend == "bedrock":
        return BedrockRerankProvider(model_id=settings.bedrock_rerank_model_id, ...)
    if backend == "cross_encoder":
        return CrossEncoderRerankProvider(model_name=settings.cross_encoder_model_name)
    raise ValueError(f"Unknown rerank backend: {backend!r}")
```

### 8.7.6 Integration with the Context Router

The context router now has a 3-stage pipeline:

```python
@router.post("/context", response_model=ContextResponse)
async def get_context(req: ContextRequest):
    # 1. Vector search — fetch a larger candidate set when reranking
    if should_rerank:
        fetch_k = settings.code_search.rerank_candidates  # e.g. 20
    else:
        fetch_k = req.top_k  # e.g. 5

    search_result = await code_search.search(query, workspace_path, fetch_k)

    # 2. Rerank — re-score candidates and take top-N
    if should_rerank and chunks:
        rerank_results = await rerank_provider.rerank(
            query=req.query,
            documents=[c.content for c in chunks],
            top_n=req.top_k,
        )
        # Rebuild chunks in reranked order with rerank_score

    # 3. Graph search — personalised PageRank biased to (reranked) results
    repo_map_text = repo_map_svc.generate_repo_map(
        workspace_path, query_files=vector_files
    )
```

Key points:
- Reranking is optional and controlled by `rerank_backend` in settings or per-request via `enable_reranking`
- When reranking is enabled, vector search fetches more candidates (default: 20 → rerank → top 5)
- Graph search receives the reranked file list, so PageRank is biased towards the best results
- The `ContextResponse` includes `reranked: bool` and per-chunk `rerank_score: Optional[float]`
- If reranking fails (API error), the system falls back gracefully to vector search results

### 8.7.7 Configuration

```yaml
# conductor.settings.yaml
code_search:
  rerank_backend: "none"     # none | cohere | bedrock | cross_encoder
  rerank_top_n: 5            # Return top N after reranking
  rerank_candidates: 20      # Fetch this many from vector search before reranking
  cohere_rerank_model: "rerank-v3.5"
  bedrock_rerank_model_id: "cohere.rerank-v3-5:0"
  cross_encoder_model_name: "cross-encoder/ms-marco-MiniLM-L-6-v2"
```

```yaml
# conductor.secrets.yaml (for direct Cohere API)
cohere:
  api_key: "..."
```

### 8.7.8 Choosing a Backend

| Backend | Model | Cost | Latency | Best For |
|---------|-------|------|---------|----------|
| `none` | — | Free | 0ms | Development, cost-sensitive |
| `cohere` | rerank-v3.5 | $2/1K queries | ~200ms | Best quality, direct API |
| `bedrock` | cohere.rerank-v3-5:0 | $2/1K queries | ~200ms | AWS infrastructure, credential reuse |
| `cross_encoder` | ms-marco-MiniLM-L-6-v2 | Free | ~100ms | Local development, offline, CI |

For production, we recommend `bedrock` (reuses AWS credentials) or `cohere` (direct API). For development, use `cross_encoder` (free, no API key) or `none` (skip entirely).

---

## Testing the New Modules

### Running Tests

```bash
# Embedding providers (78 tests)
pytest tests/test_embedding_provider.py -v

# Reranking providers (86 tests)
pytest tests/test_rerank_provider.py -v

# Repo graph (72 tests)
pytest tests/test_repo_graph.py -v

# Config (42 tests)
pytest tests/test_config_new.py -v

# Context router (42 tests)
pytest tests/test_context.py -v
```

### Writing New Embedding Provider Tests

All providers follow the same pattern:

```python
@pytest.fixture()
def provider():
    p = SomeEmbeddingProvider()
    mock_client = MagicMock()
    p._client = mock_client
    return p, mock_client

@pytest.mark.asyncio
async def test_embed_texts(provider):
    p, mock_client = provider
    mock_client.some_method.return_value = ...
    result = await p.embed_texts(["hello", "world"])
    assert result.shape == (2, expected_dims)
```

External API calls are always mocked. No real API calls in tests.

### Writing New Reranking Provider Tests

Reranking tests follow a similar pattern to embedding tests:

```python
@pytest.fixture()
def provider():
    p = SomeCohereRerankProvider(model="rerank-v3.5")
    mock_client = MagicMock()
    p._client = mock_client
    return p, mock_client

@pytest.mark.asyncio
async def test_rerank(provider):
    p, mock_client = provider
    mock_client.rerank.return_value = MockResponse(results=[...])
    result = await p.rerank("query", ["doc1", "doc2"], top_n=2)
    assert len(result) == 2
    assert result[0].score >= result[1].score  # Sorted descending
```

Key testing patterns:
- All external APIs (Cohere, Bedrock, CrossEncoder) are fully mocked
- Empty document list → returns `[]`
- `top_n` truncation is verified
- Score ordering (descending) is asserted
- Factory function tested for all 4 backends + unknown backend error
- ABC contract: cannot instantiate `RerankProvider` directly

### Writing New RepoMap Tests

```python
def test_with_real_files(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    helper()\n")
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")
    svc = RepoMapService()
    graph = svc.build_graph(str(tmp_path))
    assert graph.stats["total_files"] == 2
```

Parser and graph tests use real filesystem operations with `tmp_path`. Service tests use the full pipeline.
