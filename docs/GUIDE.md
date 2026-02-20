# Backend Code Walkthrough

**A Learning Journey Through the Conductor Backend**

This guide walks junior engineers through the Conductor backend codebase as a structured learning journey. It assumes basic Python and FastAPI knowledge and builds up to understanding the full architecture, patterns, and data flows.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Getting Started](#2-getting-started)
3. [Project Layout](#3-project-layout)
4. [Entry Point (main.py)](#4-entry-point-mainpy)
5. [Configuration (config.py)](#5-configuration-configpy)
6. [Chat System (chat/)](#6-chat-system-chat)
7. [AI Provider System (ai_provider/)](#7-ai-provider-system-ai_provider)
8. [Code Generation Agent (agent/)](#8-code-generation-agent-agent)
9. [Authentication (auth/)](#9-authentication-auth)
10. [Policy Evaluation (policy/)](#10-policy-evaluation-policy)
11. [Audit Logging (audit/)](#11-audit-logging-audit)
12. [File Sharing (files/)](#12-file-sharing-files)
13. [Key Data Flows](#13-key-data-flows)
14. [Patterns and Conventions](#14-patterns-and-conventions)
15. [How to Add a New Module](#15-how-to-add-a-new-module)
16. [Testing Guide](#16-testing-guide)

---

## 1. Introduction

### What is Conductor?

Conductor is a VS Code extension that combines:
- **Live Share** for real-time code collaboration
- **WebSocket-based chat** for team communication
- **AI-powered summarization** for decision tracking
- **Code generation** for implementing agreed-upon changes

The backend is a FastAPI service that handles chat, AI integration, file sharing, and audit logging.

### What Does the Backend Do?

The backend provides:
- **Real-time chat rooms** via WebSocket (room-scoped message history, user management)
- **AI summarization pipeline** (4-stage: classification → targeted summary → code relevance scoring → item extraction)
- **Code prompt generation** with `PromptBuilder` (language inference, doc-only detection, configurable output modes)
- **File upload/download** with room-scoped storage
- **Audit logging** for applied code changes
- **SSO authentication** (AWS IAM Identity Center, Google OAuth)
- **Policy evaluation** for auto-apply safety checks

### Prerequisites

Before diving in, you should understand:
- **Python 3.12+** basics (async/await, type hints, dataclasses)
- **FastAPI** fundamentals (routers, dependency injection, WebSockets)
- **Pydantic** for data validation
- **REST APIs** and **WebSocket** protocols
- **Basic SQL** (we use DuckDB for audit logs and file metadata)

---

## 2. Getting Started

### Setup Commands

```bash
# First-time setup (creates .venv, installs dependencies)
make setup

# Run backend in dev mode (auto-reload on code changes, port 8000)
make run-backend

# Run all tests
make test

# Run backend tests only
make test-backend

# Run a single test module
cd backend && ../.venv/bin/pytest tests/test_chat.py -v

# Run a single test by name
cd backend && ../.venv/bin/pytest tests/test_chat.py -v -k "test_name"
```

### Health Check

Once the backend is running:

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### API Documentation

FastAPI auto-generates interactive API docs:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Running Tests

```bash
# All backend tests (368 tests as of this writing)
make test-backend

# With verbose output
cd backend && ../.venv/bin/pytest -v

# With coverage report
cd backend && ../.venv/bin/pytest --cov=app --cov-report=html
```

---

## 3. Project Layout

Here's the annotated file tree of `backend/app/`:

```
backend/app/
├── __init__.py                 # Empty package marker
├── main.py                     # FastAPI app entry point, lifespan, router registration
├── config.py                   # YAML config loading, Pydantic models, singleton pattern
├── ngrok_service.py            # Ngrok tunnel lifecycle (start/stop/get_public_url)
│
├── chat/                       # Real-time chat via WebSocket
│   ├── __init__.py
│   ├── manager.py              # ConnectionManager (WebSocket state, message history, user management)
│   ├── router.py               # WebSocket endpoint, message handling, broadcast logic
│   ├── settings_router.py     # Room settings API (code style overrides)
│   └── templates/              # HTML templates for guest chat UI
│
├── ai_provider/                # AI provider abstraction and resolution
│   ├── __init__.py
│   ├── base.py                 # Abstract AIProvider interface
│   ├── claude_direct.py        # Anthropic direct API implementation
│   ├── claude_bedrock.py       # AWS Bedrock implementation
│   ├── openai_provider.py      # OpenAI API implementation
│   ├── resolver.py             # Provider resolution with health checks and model selection
│   ├── pipeline.py             # 4-stage summarization pipeline (classify → summarize → score → extract)
│   ├── prompt_builder.py       # PromptBuilder fluent class (language inference, doc-only detection, output modes)
│   ├── prompts.py              # Prompt templates for classification and targeted summaries
│   ├── wrapper.py              # Code prompt generation with style loading
│   └── router.py               # AI endpoints (/ai/summarize, /ai/code-prompt, /ai/code-prompt/selective)
│
├── agent/                      # Code generation agent
│   ├── __init__.py
│   ├── mock_agent.py           # Deterministic mock agent for testing (no LLM calls)
│   ├── schemas.py              # ChangeSet, FileChange, Range Pydantic models
│   ├── style_loader.py         # Code style loading (custom .ai/code-style.md or built-in)
│   ├── router.py               # Agent endpoints (/agent/generate-changes)
│   └── styles/                 # Built-in Google-derived style guides
│       ├── universal.md        # Language-agnostic guidelines
│       ├── python.md
│       ├── java.md
│       ├── javascript.md
│       ├── go.md
│       └── json.md
│
├── auth/                       # SSO authentication
│   ├── __init__.py
│   ├── service.py              # AWS SSO OIDC device authorization flow
│   ├── google_service.py       # Google OAuth device authorization flow
│   └── router.py               # Auth endpoints (/auth/aws/start, /auth/google/start, etc.)
│
├── policy/                     # Auto-apply policy evaluation
│   ├── __init__.py
│   ├── auto_apply.py           # Safety checks (max files, max lines, forbidden paths)
│   └── router.py               # Policy endpoints (/policy/evaluate)
│
├── audit/                      # DuckDB-based audit logging
│   ├── __init__.py
│   ├── service.py              # AuditLogService singleton (DuckDB connection, log storage)
│   ├── schemas.py              # AuditLogEntry, AuditLogCreate Pydantic models
│   └── router.py               # Audit endpoints (/audit/logs)
│
└── files/                      # File upload/download
    ├── __init__.py
    ├── service.py              # FileStorageService singleton (disk + DuckDB metadata)
    ├── schemas.py              # FileMetadata, FileUploadResponse, FileMessage models
    └── router.py               # File endpoints (/files/upload, /files/download/{file_id})
```

---

## 4. Entry Point (main.py)

### Overview

`main.py` is the FastAPI application entry point. It:
1. Configures logging
2. Defines the lifespan context manager (startup/shutdown)
3. Creates the FastAPI app instance
4. Registers all routers
5. Defines health check endpoints

### Lifespan Context Manager

The `lifespan` function is an async context manager that runs on app startup and shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = get_config()  # Load YAML config (singleton)
    
    # Start ngrok tunnel if enabled
    if config.ngrok_settings.enabled:
        public_url = start_ngrok(...)
    
    # Initialize AI provider resolver if AI features enabled
    if config.summary.enabled:
        resolver = ProviderResolver(config)
        active = resolver.resolve()  # Health check all providers
        set_resolver(resolver)  # Store in global singleton
    
    yield  # Application runs here
    
    # Shutdown
    stop_ngrok()
```

**Key Points:**
- **Ngrok**: Optional tunnel for exposing localhost to the internet (useful for testing with remote clients)
- **AI Resolver**: Checks health of all configured AI providers (Anthropic, AWS Bedrock, OpenAI) and selects the default model
- **Singleton Pattern**: `set_resolver()` stores the resolver in a module-level global for access by routers

### Router Registration

All feature routers are registered with the app:

```python
app.include_router(chat_router)          # /chat/* endpoints
app.include_router(agent_router)         # /agent/* endpoints
app.include_router(policy_router)        # /policy/* endpoints
app.include_router(audit_router)         # /audit/* endpoints
app.include_router(files_router)         # /files/* endpoints
app.include_router(ai_router)            # /ai/* endpoints
app.include_router(auth_router)          # /auth/* endpoints
app.include_router(room_settings_router) # /room-settings/* endpoints
```

**Convention**: Each module has a `router.py` that exports a `router` object (FastAPI `APIRouter` instance).

### Health Check Endpoints

```python
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

@app.get("/public-url")
async def public_url() -> dict:
    return {"public_url": get_public_url()}
```

**Usage**: The extension calls `/health` to verify backend connectivity before joining a session.

---

## 5. Configuration (config.py)

### Overview

Configuration is split into **two YAML files**:
1. **`conductor.secrets.yaml`** (gitignored) — API keys, tokens, credentials
2. **`conductor.settings.yaml`** (commitable) — All other settings

This separation allows teams to commit settings while keeping secrets out of version control.

### File Search Order

For each file type, the loader searches in priority order:
1. `./config/conductor.{secrets,settings}.yaml` (current directory)
2. `./conductor.{secrets,settings}.yaml` (current directory)
3. `../config/conductor.{secrets,settings}.yaml` (parent directory)
4. `~/.conductor/conductor.{secrets,settings}.yaml` (user home)

**Rationale**: Supports both project-local config (for teams) and user-global config (for personal setups).

### Pydantic Models

All config is validated with Pydantic models. Key models:

- **`SecretsConfig`**: Root model for `conductor.secrets.yaml`
  - `ai_providers`: API keys for Anthropic, AWS Bedrock, OpenAI
  - `ngrok`: Ngrok authtoken
  - `google_sso`: Google OAuth client ID/secret

- **`SettingsConfig`**: Root model for `conductor.settings.yaml`
  - `server`: Host, port, public_url
  - `ngrok`: Region, enabled flag
  - `change_limits`: Max files, max lines, auto-apply limits
  - `session`: Timeout, max participants
  - `logging`: Level, audit enabled, audit path
  - `summary`: Enabled flag, default model
  - `ai_provider_settings`: Enable flags for each provider
  - `ai_models`: List of model configurations

- **`ConductorConfig`**: Merged config from both files
  - Combines all fields from `SecretsConfig` and `SettingsConfig`
  - Used throughout the application

### Key Mapping

The loader maps YAML keys to `ConductorConfig` field names:

```python
# Settings YAML keys → ConductorConfig fields
settings_key_map = {
    "server": "server",
    "ngrok": "ngrok_settings",  # Note: renamed to ngrok_settings
    "change_limits": "change_limits",
    "session": "session",
    "logging": "logging",
    "summary": "summary",
    "ai_provider_settings": "ai_provider_settings",
    "ai_models": "ai_models",
    "sso": "sso",
    "google_sso": "google_sso",
}

# Secrets YAML keys → ConductorConfig fields
secrets_key_map = {
    "ai_providers": "ai_providers",
    "ngrok": "ngrok_secrets",  # Note: renamed to ngrok_secrets
    "google_sso": "google_sso_secrets",
}
```

**Why the renaming?** To avoid field name collisions in `ConductorConfig` (e.g., `ngrok_settings` vs `ngrok_secrets`).

### Singleton Pattern

Config is loaded once and cached in a module-level global:

```python
_config: ConductorConfig | None = None

def get_config() -> ConductorConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config
```

**Usage**: Import `get_config()` anywhere in the codebase to access configuration.

### Example: Reading a Setting

```python
from app.config import get_config

config = get_config()
print(config.server.port)  # 8000
print(config.summary.enabled)  # True/False
print(config.ai_providers.anthropic.api_key)  # "sk-ant-..."
```

---

## 6. Chat System (chat/)

### Overview

The chat system provides real-time WebSocket-based chat rooms with:
- **Multiple rooms** with isolated state
- **Message history** (in-memory, per room)
- **User management** (auto-generated guest names, avatar colors)
- **Broadcasting** (concurrent message delivery with `asyncio.gather()`)
- **Message deduplication** (LRU cache to prevent duplicate messages)
- **Read receipts** (track which users have read each message)
- **Lead management** (transfer lead role, permission checks)

### Data Models

Key Pydantic models in `chat/manager.py`:

- **`UserRole`**: Enum for user roles (`HOST`, `GUEST`, `ENGINEER`, `AI`)
- **`IdentitySource`**: Enum for identity establishment (`SSO`, `NAMED`, `ANONYMOUS`)
- **`MessageType`**: Enum for message types (`MESSAGE`, `CODE_SNIPPET`, `FILE`, `AI_SUMMARY`, `AI_CODE_PROMPT`)
- **`RoomUser`**: User info stored in a room (userId, displayName, role, avatarColor, identitySource)
- **`ChatMessage`**: Complete message with metadata (id, type, roomId, userId, displayName, role, content, ts, aiData)
- **`ChatMessageInput`**: Lightweight input schema from client (userId, displayName, role, content)

### ConnectionManager

The `ConnectionManager` class is the heart of the chat system. It's a **singleton-style global instance** that maintains:

```python
class ConnectionManager:
    def __init__(self):
        # room_id -> list of active WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

        # room_id -> list of messages (append-only history)
        self.message_history: Dict[str, List[ChatMessage]] = {}

        # room_id -> {userId -> RoomUser}
        self.room_users: Dict[str, Dict[str, RoomUser]] = {}

        # room_id -> guest counter (for "Guest 1", "Guest 2" naming)
        self.guest_counters: Dict[str, int] = {}

        # websocket -> (room_id, userId) for disconnect handling
        self.websocket_to_user: Dict[WebSocket, Tuple[str, str]] = {}

        # Message deduplication: room_id -> OrderedDict of message IDs (LRU cache)
        self.seen_message_ids: Dict[str, OrderedDict] = {}

        # Read receipts: room_id -> {message_id -> set of user_ids who read it}
        self.message_read_by: Dict[str, Dict[str, Set[str]]] = {}

        # SECURITY: room_id -> host_user_id (first user to join becomes host)
        self.room_hosts: Dict[str, str] = {}

        # Lead tracking: room_id -> lead_user_id (initially the host)
        self.room_leads: Dict[str, str] = {}

        # Room settings: room_id -> settings dict (e.g., {"code_style": "..."})
        self.room_settings: Dict[str, dict] = {}
```

**Key Point**: All state is **in-memory**. When the backend restarts, all rooms are cleared.

### Security Model

The backend enforces security at the connection level:

1. **Backend assigns userId**: Never trust client-provided IDs. The backend generates a UUID on connection.
2. **Backend determines role**: First user in a room = `host`, others = `guest`.
3. **Backend validates permissions**: Only the host can end sessions. Only the lead can use AI features.

```python
async def connect(self, websocket: WebSocket, room_id: str) -> Tuple[str, str, List[ChatMessage]]:
    await websocket.accept()

    # SECURITY: Generate userId on backend (never trust client-provided IDs)
    user_id = str(uuid.uuid4())

    # SECURITY: First user to connect becomes host and initial lead
    if room_id not in self.room_hosts:
        self.room_hosts[room_id] = user_id
        self.room_leads[room_id] = user_id
        role = "host"
    else:
        role = "guest"

    self.active_connections[room_id].append(websocket)
    return (user_id, role, self.message_history[room_id])
```

### WebSocket Protocol

The WebSocket endpoint (`/chat/{room_id}`) handles these message types:

1. **`join`**: Register user in room (assigns display name, avatar color)
2. **`message`**: Send a chat message (broadcast to all users)
3. **`typing`**: Typing indicator (broadcast to all except sender)
4. **`read_receipt`**: Mark message as read
5. **`end_session`**: Host-only, clears room and disconnects all users

**Example `join` message**:
```json
{
  "type": "join",
  "userId": "abc-123",
  "displayName": "Alice",
  "role": "host",
  "identitySource": "sso"
}
```

**Response**:
```json
{
  "type": "user_joined",
  "user": {
    "userId": "abc-123",
    "displayName": "Alice",
    "role": "host",
    "avatarColor": "amber",
    "identitySource": "sso"
  },
  "users": [...]
}
```

### Broadcasting

The `broadcast()` method uses `asyncio.gather()` for concurrent message delivery:

```python
async def broadcast(self, message: dict, room_id: str) -> None:
    connections = self.active_connections[room_id].copy()

    # Send to all connections concurrently
    results = await asyncio.gather(
        *[self._safe_send(conn, message) for conn in connections],
        return_exceptions=True
    )

    # Remove failed connections
    failed_connections = [
        conn for conn, success in zip(connections, results)
        if success is False
    ]
    self._cleanup_connections(room_id, failed_connections)
```

**Why concurrent?** Sequential iteration is slow for rooms with many connections. `asyncio.gather()` sends all messages in parallel.

### Message Deduplication

The `is_duplicate_message()` method uses an **OrderedDict as an LRU cache**:

```python
def is_duplicate_message(self, room_id: str, message_id: str) -> bool:
    if room_id not in self.seen_message_ids:
        self.seen_message_ids[room_id] = OrderedDict()

    cache = self.seen_message_ids[room_id]

    if message_id in cache:
        cache.move_to_end(message_id)  # Mark as recently used
        return True

    cache[message_id] = True

    # Evict oldest if cache is full
    while len(cache) > MESSAGE_DEDUP_CACHE_SIZE:
        cache.popitem(last=False)

    return False
```

**Why deduplication?** WebSocket reconnections can cause duplicate messages. The LRU cache prevents showing the same message twice.

### Pagination

The `get_paginated_history()` method supports lazy loading of message history:

```python
def get_paginated_history(
    self,
    room_id: str,
    before_ts: Optional[float] = None,
    limit: int = DEFAULT_PAGE_SIZE
) -> List[ChatMessage]:
    limit = min(limit, MAX_PAGE_SIZE)  # Prevent abuse
    messages = self.message_history.get(room_id, [])

    if before_ts is not None:
        messages = [msg for msg in messages if msg.ts < before_ts]

    return messages[-limit:]  # Return last N messages
```

**Usage**: Client sends `before_ts` (timestamp of oldest message it has) to fetch older messages.

### Lead Management

The lead role can be transferred between users:

```python
def transfer_lead(self, room_id: str, new_lead_id: str) -> bool:
    if new_lead_id not in self.room_users.get(room_id, {}):
        return False
    self.room_leads[room_id] = new_lead_id
    return True
```

**Permissions**:
- **Lead**: Can use AI features (summarization, code generation)
- **Host**: Can end sessions, configure settings, and has all lead permissions
- **Guest**: Can chat and view AI outputs

If the lead disconnects (and is not the host), lead automatically reverts to the host.

---

## 7. AI Provider System (ai_provider/)

### Overview

The AI provider system provides:
- **Abstract interface** (`AIProvider`) for multiple AI backends
- **Three implementations**: Anthropic direct, AWS Bedrock, OpenAI
- **Provider resolution** with health checks and priority-based fallback
- **4-stage summarization pipeline**: classification → targeted summary → code relevance scoring → item extraction
- **Code prompt generation** with `PromptBuilder` (language inference from components, doc-only detection, configurable output modes)

### Abstract Interface (base.py)

All AI providers implement the `AIProvider` abstract base class:

```python
class AIProvider(ABC):
    @abstractmethod
    def call_model(self, prompt: str) -> str:
        """Call the AI model with a prompt and return the response."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is healthy and accessible."""
        pass
```

**Why abstract?** Allows swapping providers without changing calling code. The resolver picks the best available provider at runtime.

### Implementations

1. **`ClaudeDirectProvider`** (claude_direct.py): Calls Anthropic API directly
2. **`ClaudeBedrockProvider`** (claude_bedrock.py): Calls Claude via AWS Bedrock
3. **`OpenAIProvider`** (openai_provider.py): Calls OpenAI API

Each implementation:
- Handles API authentication
- Formats requests for the specific provider
- Parses responses into a common format
- Implements health checks

### Provider Resolver (resolver.py)

The `ProviderResolver` class manages provider selection:

```python
class ProviderResolver:
    def __init__(self, config: ConductorConfig):
        self.config = config
        self._providers: Dict[str, AIProvider] = {}
        self._provider_health: Dict[str, bool] = {}
        self.active_model_id: Optional[str] = None
        self.active_provider_type: Optional[str] = None

    def resolve(self) -> Optional[AIProvider]:
        """Resolve providers and set the active model based on config."""
        # Check all provider types
        for provider_type in [ANTHROPIC, AWS_BEDROCK, OPENAI]:
            if self._is_provider_enabled(provider_type):
                if self._is_provider_configured(provider_type):
                    healthy = self._check_provider_health(provider_type)
                    self._provider_health[provider_type] = healthy

        # Set active model based on default_model config
        default_model = self._find_model(self.config.summary.default_model)
        if default_model and self._provider_health.get(default_model.provider):
            self.active_model_id = default_model.id
            self.active_provider_type = default_model.provider
            return self._providers.get(default_model.provider)

        # Fallback: find first available model
        for model in self.config.ai_models:
            if model.enabled and self._provider_health.get(model.provider):
                self.active_model_id = model.id
                self.active_provider_type = model.provider
                return self._providers.get(model.provider)

        return None
```

**Resolution Algorithm**:
1. Check all enabled providers for configuration (API keys present)
2. Health check each configured provider
3. Try to use the default model (from config)
4. Fallback to first available model if default is unavailable
5. Return `None` if no healthy provider found

**Singleton Pattern**: The resolver is stored in a module-level global (`_resolver`) and accessed via `get_resolver()`.

### 4-Stage Summarization Pipeline (pipeline.py)

The pipeline processes chat messages in four stages:

#### Stage 1: Classification

Classify the discussion type using a specialized prompt:

```python
def classify_discussion(messages: List[ChatMessage], provider: AIProvider) -> ClassificationResult:
    prompt = get_classification_prompt(messages)
    response_text = provider.call_model(prompt)
    data = json.loads(response_text)

    discussion_type = data.get("discussion_type", "general")
    confidence = float(data.get("confidence", 0.0))

    return ClassificationResult(discussion_type=discussion_type, confidence=confidence)
```

**Discussion Types**:
- `api_design`: Designing API endpoints, request/response formats
- `product_flow`: User flows, state transitions, UX decisions
- `code_change`: Specific code modifications, refactoring
- `architecture`: System design, component structure
- `innovation`: Brainstorming, new features, experiments
- `debugging`: Bug investigation, root cause analysis
- `general`: Everything else

#### Stage 2: Targeted Summary

Generate a specialized summary based on the discussion type:

```python
def generate_targeted_summary(
    messages: List[ChatMessage],
    provider: AIProvider,
    discussion_type: DiscussionType
) -> PipelineSummary:
    prompt = get_targeted_summary_prompt(messages, discussion_type)
    response_text = provider.call_model(prompt)
    data = json.loads(response_text)

    return PipelineSummary(
        type="decision_summary",
        topic=data.get("topic", ""),
        core_problem=data.get("core_problem", ""),
        proposed_solution=data.get("proposed_solution", ""),
        requires_code_change=_infer_requires_code_change(data, discussion_type),
        impact_scope=data.get("impact_scope", "local"),
        affected_components=data.get("affected_components", []),
        risk_level=data.get("risk_level", "low"),
        next_steps=data.get("next_steps", []),
        discussion_type=discussion_type,
    )
```

**Why targeted?** Different discussion types need different summary structures. For example:
- `api_design` summaries focus on endpoints, request/response schemas
- `debugging` summaries focus on symptoms, root cause, fix
- `architecture` summaries focus on components, interactions, trade-offs

#### Stage 3: Code Relevance Scoring

Determine which discussion types should trigger code prompt generation:

```python
def compute_code_relevant_types(
    discussion_type: str,
    requires_code_change: bool,
    proposed_solution: str = "",
) -> List[str]:
    code_relevant = []

    if discussion_type == "code_change":
        code_relevant.append("code_change")
    elif discussion_type == "architecture":
        if requires_code_change:
            code_relevant.append("architecture")
            code_relevant.append("code_change")
    # ... more rules for other types

    return code_relevant
```

**Rules**:
- `code_change` is always code-relevant
- `architecture` is code-relevant if implementation is required
- `api_design` is code-relevant if backend logic is affected
- `product_flow` is code-relevant if backend state changes are required
- `debugging` is code-relevant if a fix is needed
- `innovation` is excluded unless it requires code
- `general` is excluded unless it explicitly mentions implementation

**Why?** Prevents generating code prompts for pure discussion (e.g., brainstorming, planning).

#### Stage 4: Item Extraction

Extract actionable `CodeRelevantItem` objects from the summary:

```python
def extract_code_relevant_items(
    summary: PipelineSummary,
    provider: AIProvider,
) -> List[CodeRelevantItem]:
    prompt = get_item_extraction_prompt(summary)
    response_text = provider.call_model(prompt)
    items = json.loads(response_text)
    return [CodeRelevantItem(**item) for item in items]
```

**`CodeRelevantItem`** represents a concrete action extracted from the discussion:
- `title`: Short description of the action
- `description`: Detailed explanation
- `affected_components`: Files/modules involved
- `type`: Type of change (`code_change`, `api_design`, etc.)
- `priority`: `high`, `medium`, or `low`

**Why a 4th stage?** The summary gives a high-level picture; the item extraction produces a structured, machine-readable list of discrete tasks. This list drives which code-generation prompts to offer the user.

### Prompt Templates (prompts.py)

The module provides prompt templates for each discussion type:

```python
def get_classification_prompt(messages: List[ChatMessage]) -> str:
    """Generate a prompt for classifying the discussion type."""
    # Returns a prompt that asks the AI to classify the discussion
    # into one of 7 types with a confidence score

def get_targeted_summary_prompt(messages: List[ChatMessage], discussion_type: str) -> str:
    """Generate a targeted summary prompt based on discussion type."""
    # Returns a type-specific prompt that asks for structured output
    # (topic, core_problem, proposed_solution, etc.)
```

**Design**: Prompts are engineered to return **JSON** for easy parsing. The AI is instructed to return structured data, not prose.

### Code Prompt Generation (prompt_builder.py)

The `PromptBuilder` class uses a **fluent builder pattern** to assemble task-focused code generation prompts. It replaces the old `generate_code_prompt()` function with a more intelligent approach.

```python
from app.ai_provider.prompt_builder import PromptBuilder

prompt = (
    PromptBuilder(
        problem_statement=summary.core_problem,
        proposed_solution=summary.proposed_solution,
        affected_components=summary.affected_components,
        risk_level=summary.risk_level,
    )
    .with_room_code_style(room_settings.get("code_style"))
    .with_detected_languages(detected_languages)
    .with_output_mode(room_settings.get("output_mode", "unified_diff"))
    .with_context_snippets(context_snippets)  # Optional per-file code snippets
    .with_policy_constraints(policy_str)
    .build()
)
```

**Language inference** (from `prompt_builder.py`):
- `PromptBuilder` infers languages from `affected_components` file extensions first (e.g., `.py` → `Language.PYTHON`)
- Falls back to workspace-detected languages if no components have known extensions
- Falls back to `CodeStyleLoader` for the universal style

**Style Loading Priority**:
1. **Room-level override**: If the room has a custom `code_style` setting, use it verbatim
2. **Inferred from components**: File extensions in `affected_components` determine which built-in styles to load
3. **Workspace-detected languages**: Extension-provided language list used if component inference yields nothing
4. **Fallback**: Universal style via `CodeStyleLoader`

**Doc-only detection** (`is_documentation_only()`):
- If all affected components are `.md`, `.rst`, `.txt`, or in `docs/` paths, the builder skips "add tests" and "add error handling" requirements
- If no components provided, it checks the `proposed_solution` text for doc/code keywords

**Output modes** (configurable via room settings or `conductor.settings.yaml`):

| Mode | Description |
|------|-------------|
| `unified_diff` | Output as `git apply`-compatible unified diff patches (default) |
| `direct_repo_edits` | Output complete updated file contents |
| `plan_then_diff` | Short implementation plan, then unified diff |

**Selective prompt** (`build_selective_prompt()`):
For multi-type summaries (e.g., architecture + code_change), `build_selective_prompt()` collects `affected_components` from all summaries for language inference, then produces a structured JSON implementation plan.

### Error Hierarchy

The module defines custom exceptions for error handling:

```python
class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    pass

class ProviderNotAvailableError(AIProviderError):
    """Raised when no healthy provider is available."""
    pass

class ModelNotFoundError(AIProviderError):
    """Raised when a requested model is not found."""
    pass
```

**Usage**: Routers catch these exceptions and return appropriate HTTP error responses.

---

## 8. Code Generation Agent (agent/)

### Overview

The agent module handles code generation. Currently, it uses a **MockAgent** for deterministic testing. Future versions will integrate an LLM-based agent.

### ChangeSet Schema (schemas.py)

The `ChangeSet` is the core data structure for code changes:

```python
class Range(BaseModel):
    """A range of lines in a file (1-based, inclusive)."""
    start: int = Field(..., ge=1)
    end: int = Field(..., ge=1)

class ChangeType(str, Enum):
    """Type of file change."""
    REPLACE_RANGE = "replace_range"  # Replace lines in existing file
    CREATE_FILE = "create_file"      # Create new file

class FileChange(BaseModel):
    """A single file change."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file: str  # Path to file
    type: ChangeType
    range: Optional[Range] = None  # Required for replace_range
    content: str  # New content
    original_content: Optional[str] = None  # For reference

class ChangeSet(BaseModel):
    """A set of file changes to apply."""
    changes: List[FileChange] = Field(..., min_items=1, max_items=10)
    summary: str = ""
```

**Design Notes**:
- **1-based line numbers**: Matches editor conventions (most editors use 1-based line numbers)
- **Inclusive ranges**: `Range(start=1, end=3)` means lines 1, 2, and 3
- **Two change types**: `replace_range` for modifications, `create_file` for new files
- **Max 10 changes**: Prevents overwhelming the user with too many changes at once

**Shared Schema**: The schema is defined in `shared/changeset.schema.json` and used by both backend (Pydantic) and extension (TypeScript).

### MockAgent (mock_agent.py)

The `MockAgent` generates deterministic changes for testing:

```python
class MockAgent:
    def generate_changes(self, request: GenerateChangesRequest) -> GenerateChangesResponse:
        file_changes = self._generate_realistic_changes(request)

        change_set = ChangeSet(
            changes=file_changes,
            summary="Create helper and config modules"
        )

        return GenerateChangesResponse(
            success=True,
            change_set=change_set,
            message=f"Generated {len(file_changes)} changes"
        )

    def _generate_realistic_changes(self, request: GenerateChangesRequest) -> List[FileChange]:
        changes = []

        # 1. Create helper.py
        changes.append(FileChange(
            file="helper.py",
            type=ChangeType.CREATE_FILE,
            content='"""Helper utilities module."""\n\ndef format_output(data: str) -> str:\n    return f"[OUTPUT] {data}"\n'
        ))

        # 2. Create config.py
        changes.append(FileChange(
            file="config.py",
            type=ChangeType.CREATE_FILE,
            content='"""Configuration module."""\n\nDEBUG = True\nVERSION = "1.0.0"\n'
        ))

        # 3. Modify original file (if provided)
        if request.file_path:
            changes.append(FileChange(
                file=request.file_path,
                type=ChangeType.REPLACE_RANGE,
                range=Range(start=1, end=1),
                content='from helper import format_output\nfrom config import DEBUG, VERSION\n\n'
            ))

        return changes
```

**Why MockAgent?** Allows testing the full change review flow without LLM API calls. Useful for:
- Extension development (predictable changes)
- Integration tests (no API costs)
- Demo mode (works offline)

### StyleLoader (style_loader.py)

The `CodeStyleLoader` loads code style guidelines:

```python
class CodeStyleLoader:
    DEFAULT_STYLE_PATH = ".ai/code-style.md"

    def get_style(self, language: Optional[Language] = None) -> tuple[str, StyleSource]:
        # 1. Try custom style first
        custom_style = self._read_custom_style()
        if custom_style is not None:
            return (custom_style, StyleSource.CUSTOM)

        # 2. Fallback to built-in style
        if language is not None:
            return (_read_builtin_style(language), StyleSource.BUILTIN)

        # 3. Return universal style when no language specified
        return (_read_universal_style(), StyleSource.UNIVERSAL)
```

**Priority Chain**:
1. **Custom style**: `.ai/code-style.md` in workspace root (if exists and non-empty)
2. **Built-in language-specific**: `agent/styles/{language}.md` (e.g., `python.md`)
3. **Universal**: `agent/styles/universal.md` (language-agnostic guidelines)

**Built-in Styles**: Derived from Google style guides:
- `python.md`: Google Python Style Guide
- `java.md`: Google Java Style Guide
- `javascript.md`: Google JavaScript Style Guide
- `go.md`: Effective Go
- `json.md`: JSON formatting conventions
- `universal.md`: Language-agnostic best practices

### Future LLM Integration

The MockAgent will be replaced with an LLM-based agent that:
1. Receives the code generation prompt (from `wrapper.py`)
2. Calls an LLM to generate a `ChangeSet`
3. Validates the `ChangeSet` against the schema
4. Returns the `ChangeSet` to the extension

**Design Considerations**:
- **Streaming**: For large changes, stream the response to show progress
- **Validation**: Ensure the LLM output conforms to the `ChangeSet` schema
- **Error Handling**: Retry on malformed JSON, fallback to MockAgent on failure
- **Cost Control**: Limit prompt size, use cheaper models for simple changes

---

## 9. Authentication (auth/)

### Overview

The auth module provides SSO authentication via device authorization flows:
- **AWS SSO** (IAM Identity Center)
- **Google OAuth 2.0**

Both use the same pattern: register client → start device auth → poll for token → resolve identity.

### AWS SSO Service (service.py)

The `SSOService` class implements the AWS SSO OIDC device authorization flow:

```python
class SSOService:
    def __init__(self, start_url: str, region: str = "us-east-1"):
        self.start_url = start_url
        self.region = region
        self._oidc_client = boto3.client("sso-oidc", region_name=region)

    def register_and_start(self) -> dict:
        # Step 1: Register a public OIDC client
        reg = self._oidc_client.register_client(
            clientName="conductor-vscode",
            clientType="public",
        )

        # Step 2: Start device authorization
        auth = self._oidc_client.start_device_authorization(
            clientId=reg["clientId"],
            clientSecret=reg["clientSecret"],
            startUrl=self.start_url,
        )

        return {
            "verification_uri_complete": auth["verificationUriComplete"],
            "user_code": auth["userCode"],
            "device_code": auth["deviceCode"],
            "client_id": reg["clientId"],
            "client_secret": reg["clientSecret"],
            "expires_in": auth.get("expiresIn", 600),
            "interval": auth.get("interval", 5),
        }

    def poll_for_token(self, client_id: str, client_secret: str, device_code: str) -> str | None:
        try:
            token_resp = self._oidc_client.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=device_code,
            )
            return token_resp["accessToken"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("AuthorizationPendingException", "SlowDownException"):
                return None  # Still pending
            raise

    def get_identity(self, access_token: str) -> dict:
        # 4-step AWS API chain to resolve identity:
        # 1. ListAccounts - Get accounts user has access to
        # 2. ListAccountRoles - Get roles in first account
        # 3. GetRoleCredentials - Get temp credentials for first role
        # 4. STS GetCallerIdentity - Get user ARN and email

        sso_client = boto3.client("sso", region_name=self.region)

        # Step 1: List accounts
        accounts_resp = sso_client.list_accounts(accessToken=access_token)
        accounts = accounts_resp.get("accountList", [])
        first_account = accounts[0]
        account_id = first_account["accountId"]

        # Step 2: List roles in first account
        roles_resp = sso_client.list_account_roles(
            accessToken=access_token,
            accountId=account_id,
        )
        roles = roles_resp.get("roleList", [])
        first_role = roles[0]
        role_name = first_role["roleName"]

        # Step 3: Get temp credentials for first role
        creds_resp = sso_client.get_role_credentials(
            accessToken=access_token,
            accountId=account_id,
            roleName=role_name,
        )
        role_creds = creds_resp["roleCredentials"]

        # Step 4: Call STS to get caller identity
        sts_client = boto3.client(
            "sts",
            aws_access_key_id=role_creds["accessKeyId"],
            aws_secret_access_key=role_creds["secretAccessKey"],
            aws_session_token=role_creds["sessionToken"],
            region_name=self.region,
        )
        identity = sts_client.get_caller_identity()

        email = self._extract_email_from_arn(identity.get("Arn", ""))

        return {
            "email": email,
            "arn": identity.get("Arn", ""),
            "user_id": identity.get("UserId", ""),
            "account_id": account_id,
            "account_name": first_account.get("accountName", ""),
            "role_name": role_name,
            "accounts": [...],
            "roles": [...],
        }
```

**Why 4 steps?** AWS SSO doesn't provide a direct "get user info" API. We must:
1. List accounts to find one the user has access to
2. List roles in that account to find one the user can assume
3. Get temporary credentials for that role
4. Use those credentials to call STS and get the user's ARN (which contains their email)

**Why first account/role?** For simplicity, we use the first account and first role. In production, you might want to let the user choose.

### Google OAuth Service (google_service.py)

The `GoogleSSOService` class implements the Google OAuth device authorization flow:

```python
class GoogleSSOService:
    def start_device_authorization(self) -> dict:
        # Call Google's device authorization endpoint
        response = requests.post(
            "https://oauth2.googleapis.com/device/code",
            data={
                "client_id": self.client_id,
                "scope": "openid email profile",
            }
        )
        data = response.json()

        return {
            "verification_uri_complete": data["verification_url"],
            "user_code": data["user_code"],
            "device_code": data["device_code"],
            "expires_in": data.get("expires_in", 600),
            "interval": data.get("interval", 5),
        }

    def poll_for_token(self, device_code: str) -> str | None:
        # Poll Google's token endpoint
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        )
        data = response.json()

        if "error" in data:
            if data["error"] in ("authorization_pending", "slow_down"):
                return None  # Still pending
            raise Exception(f"Token error: {data['error']}")

        return data["access_token"]

    def get_user_info(self, access_token: str) -> dict:
        # Call Google's userinfo endpoint
        response = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        data = response.json()

        return {
            "email": data.get("email", ""),
            "name": data.get("name", ""),
            "picture": data.get("picture", ""),
            "verified_email": data.get("verified_email", False),
        }
```

**Simpler than AWS**: Google provides a direct userinfo endpoint, so we don't need the multi-step chain.

### Shared Poll Helper

Both services use a shared `_poll_for_identity()` helper in the router:

```python
async def _poll_for_identity(
    poll_func: Callable,
    resolve_func: Callable,
    interval: int,
    timeout: int
) -> dict:
    """Shared polling logic for device authorization flows."""
    start_time = time.time()

    while time.time() - start_time < timeout:
        token = poll_func()
        if token:
            identity = resolve_func(token)
            return identity

        await asyncio.sleep(interval)

    raise TimeoutError("Device authorization timed out")
```

**Why shared?** Both AWS and Google use the same poll-then-resolve pattern. This helper reduces code duplication.

---

## 10. Policy Evaluation (policy/)

### Overview

The policy module implements safety checks for the **auto-apply** feature. Auto-apply allows small, low-risk changes to be applied automatically without explicit user confirmation.

### Auto-Apply Rules (auto_apply.py)

The `AutoApplyPolicy` class evaluates three rules:

```python
class AutoApplyPolicy:
    def __init__(
        self,
        max_files: int = 2,
        max_lines_changed: int = 50,
        forbidden_paths: tuple = ("infra/", "db/", "security/"),
    ):
        self.max_files = max_files
        self.max_lines_changed = max_lines_changed
        self.forbidden_paths = forbidden_paths

    def evaluate(self, change_set: ChangeSet) -> PolicyResult:
        reasons: List[str] = []

        # Rule 1: Check max files
        if len(change_set.changes) > self.max_files:
            reasons.append(f"Too many files: {len(change_set.changes)} > {self.max_files}")

        # Rule 2: Check max lines changed
        total_lines = self._count_lines_changed(change_set)
        if total_lines > self.max_lines_changed:
            reasons.append(f"Too many lines changed: {total_lines} > {self.max_lines_changed}")

        # Rule 3: Check forbidden paths
        forbidden_files = self._find_forbidden_files(change_set)
        if forbidden_files:
            reasons.append(f"Forbidden paths: {', '.join(forbidden_files)}")

        return PolicyResult(allowed=len(reasons) == 0, reasons=reasons)
```

**Line Counting**:
- For `replace_range`: Count lines in the range (`end - start + 1`)
- For `create_file`: Count lines in the content (`content.count('\n') + 1`)

**Forbidden Paths**: Blocks changes to critical infrastructure:
- `infra/`: Infrastructure as code (Terraform, CloudFormation)
- `db/`: Database migrations, schemas
- `security/`: Security-sensitive code (auth, encryption)

**Security Rationale**: Auto-apply trades convenience for safety. By limiting scope and excluding critical paths, we reduce the risk of accidental damage.

### Configuration-Driven Limits

The policy can be configured via `conductor.settings.yaml`:

```yaml
change_limits:
  max_files_per_request: 10
  max_lines_per_file: 500
  max_total_lines: 2000
  auto_apply:
    enabled: false
    max_lines: 50
```

The `evaluate_auto_apply()` function reads limits from config:

```python
def evaluate_auto_apply(
    change_set: ChangeSet,
    config: ConductorConfig | None = None,
) -> PolicyResult:
    if config is not None:
        limits = config.change_limits
        policy = AutoApplyPolicy(
            max_files=limits.max_files_per_request,
            max_lines_changed=limits.auto_apply.max_lines,
        )
        return policy.evaluate(change_set)
    return _default_policy.evaluate(change_set)
```

---

## 11. Audit Logging (audit/)

### Overview

The audit module provides persistent storage for applied code changes using **DuckDB**, a fast embedded analytical database.

### Database Schema

The `audit_logs` table stores:

```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY,
    room_id VARCHAR NOT NULL,
    summary_id VARCHAR,
    changeset_hash VARCHAR NOT NULL,
    applied_by VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,  -- 'manual' or 'auto'
    timestamp TIMESTAMP NOT NULL
)
```

**Changeset Hash**: SHA-256 hash of the applied changeset (for integrity verification).

### AuditLogService (service.py)

The `AuditLogService` class is a **singleton** that manages the DuckDB connection:

```python
class AuditLogService:
    _instance: Optional["AuditLogService"] = None
    _db_path: str = "audit_logs.duckdb"

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "AuditLogService":
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    def log_apply(self, entry: AuditLogCreate) -> AuditLogEntry:
        timestamp = datetime.utcnow()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO audit_logs (room_id, summary_id, changeset_hash, applied_by, mode, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [entry.room_id, entry.summary_id, entry.changeset_hash, entry.applied_by, entry.mode.value, timestamp]
        )
        return AuditLogEntry(...)

    def get_logs(self, room_id: Optional[str] = None, limit: int = 100) -> List[AuditLogEntry]:
        conn = self._get_connection()
        if room_id:
            result = conn.execute(
                "SELECT ... FROM audit_logs WHERE room_id = ? ORDER BY timestamp DESC LIMIT ?",
                [room_id, limit]
            ).fetchall()
        else:
            result = conn.execute(
                "SELECT ... FROM audit_logs ORDER BY timestamp DESC LIMIT ?",
                [limit]
            ).fetchall()
        return [AuditLogEntry(...) for row in result]
```

**Singleton Pattern**: Ensures only one database connection exists per process.

**Thread Safety**: DuckDB connections are NOT thread-safe. In production with multiple workers, each process will have its own connection to the same file. DuckDB handles concurrent file access internally.

### Changeset Hashing

The `compute_changeset_hash()` function creates a deterministic hash:

```python
def compute_changeset_hash(changeset: dict) -> str:
    # Sort keys for deterministic hashing
    changeset_str = json.dumps(changeset, sort_keys=True)
    return hashlib.sha256(changeset_str.encode()).hexdigest()[:16]
```

**Why hash?** Provides a compact, unique identifier for each changeset. Useful for:
- Deduplication (detect if the same changeset was applied twice)
- Integrity verification (detect if a changeset was tampered with)
- Audit trail (link applied changes back to the original changeset)

---

## 12. File Sharing (files/)

### Overview

The files module provides file upload/download with:
- **Room-scoped storage**: Files are stored in `uploads/{room_id}/`
- **DuckDB metadata tracking**: File metadata (filename, size, uploader, etc.)
- **20MB size limit**: Prevents abuse
- **Lifecycle management**: Files are deleted when the session ends

### FileStorageService (service.py)

The `FileStorageService` class is a **singleton** that manages file storage:

```python
class FileStorageService:
    _instance: Optional["FileStorageService"] = None
    _upload_dir: str = "uploads"
    _db_path: str = "file_metadata.duckdb"

    @classmethod
    def get_instance(cls, upload_dir: Optional[str] = None, db_path: Optional[str] = None) -> "FileStorageService":
        if cls._instance is None:
            cls._instance = cls(upload_dir, db_path)
        return cls._instance

    async def save_file(
        self,
        room_id: str,
        user_id: str,
        display_name: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> FileMetadata:
        # Check file size
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File size exceeds limit")

        # Generate unique filename
        file_id = str(uuid.uuid4())
        ext = Path(filename).suffix.lower()
        stored_filename = f"{file_id}{ext}"

        # Save to disk
        room_dir = self._get_room_dir(room_id)
        room_dir.mkdir(parents=True, exist_ok=True)
        file_path = room_dir / stored_filename
        file_path.write_bytes(content)

        # Save metadata to DuckDB
        metadata = FileMetadata(...)
        conn.execute("INSERT INTO file_metadata (...) VALUES (...)", [...])

        return metadata

    def get_file(self, file_id: str) -> Optional[FileMetadata]:
        # Query DuckDB for metadata
        result = conn.execute("SELECT ... FROM file_metadata WHERE id = ?", [file_id]).fetchone()
        return FileMetadata(...) if result else None

    def get_file_path(self, file_id: str) -> Optional[Path]:
        # Get metadata, then construct file path
        metadata = self.get_file(file_id)
        if not metadata:
            return None
        file_path = self._get_room_dir(metadata.room_id) / metadata.stored_filename
        return file_path if file_path.exists() else None

    def get_room_files(self, room_id: str) -> List[FileMetadata]:
        # Query DuckDB for all files in room
        results = conn.execute("SELECT ... FROM file_metadata WHERE room_id = ? ORDER BY uploaded_at ASC", [room_id]).fetchall()
        return [FileMetadata(...) for r in results]

    def delete_room_files(self, room_id: str) -> int:
        # Delete files from disk
        room_dir = self._get_room_dir(room_id)
        if room_dir.exists():
            shutil.rmtree(room_dir)

        # Delete metadata from DuckDB
        conn.execute("DELETE FROM file_metadata WHERE room_id = ?", [room_id])

        return file_count
```

**Room-Scoped Storage**: Files are organized by room ID:
```
uploads/
├── room-abc-123/
│   ├── uuid1.png
│   ├── uuid2.pdf
│   └── uuid3.mp3
└── room-def-456/
    ├── uuid4.jpg
    └── uuid5.wav
```

**Lifecycle**: When a session ends (host calls `end_session`), all files for that room are deleted.

**TODO: Cloud Backup**: Before deleting files, consider backing up to cloud storage (S3, GCS, Azure Blob) for compliance or recovery purposes.

### File Type Detection (schemas.py)

The `get_file_type()` function categorizes files by MIME type:

```python
def get_file_type(mime_type: str) -> FileType:
    for file_type, mime_types in ALLOWED_MIME_TYPES.items():
        if mime_type in mime_types:
            return file_type
    return FileType.OTHER
```

**Supported Types**:
- **IMAGE**: JPEG, PNG, GIF, WebP, SVG
- **PDF**: PDF documents
- **AUDIO**: MP3, WAV, OGG, M4A, FLAC
- **OTHER**: Everything else

---

## 13. Key Data Flows

This section traces end-to-end data flows through the system.

### Flow 1: Host Starts a Session

1. **Extension**: User clicks "Start Session" in VS Code
2. **Extension**: Calls `POST /chat/create-room` (not implemented, room ID is generated client-side)
3. **Extension**: Opens WebSocket connection to `/chat/{room_id}`
4. **Backend**: `ConnectionManager.connect()` assigns userId and role (`host`)
5. **Backend**: Returns `(userId, role, message_history)` to extension
6. **Extension**: Sends `join` message with display name
7. **Backend**: `ConnectionManager.register_user()` assigns avatar color
8. **Backend**: Broadcasts `user_joined` to all connections (just the host at this point)
9. **Extension**: Displays chat UI with host as the only user

### Flow 2: Guest Joins a Session

1. **Extension**: User receives room ID from host (via Live Share or manual sharing)
2. **Extension**: Calls `GET /health` to verify backend connectivity
3. **Extension**: Opens WebSocket connection to `/chat/{room_id}`
4. **Backend**: `ConnectionManager.connect()` assigns userId and role (`guest`)
5. **Backend**: Returns `(userId, role, message_history)` to extension
6. **Extension**: Sends `join` message with display name (or empty for auto-generated)
7. **Backend**: `ConnectionManager.register_user()` assigns display name (`Guest 1`) and avatar color
8. **Backend**: Broadcasts `user_joined` to all connections (host + guest)
9. **Extension**: Both host and guest see the updated user list

### Flow 3: AI Summarization

1. **Extension**: Lead user clicks "Summarize Discussion" in chat UI
2. **Extension**: Sends `POST /ai/summarize` with chat messages and room ID
3. **Backend**: Router calls `run_summary_pipeline(messages, provider)`
4. **Backend**: **Stage 1** - `classify_discussion()` calls AI to classify discussion type (7 types)
5. **Backend**: **Stage 2** - `generate_targeted_summary()` calls AI with type-specific prompt
6. **Backend**: **Stage 3** - `compute_code_relevant_types()` determines code relevance
7. **Backend**: **Stage 4** - `extract_code_relevant_items()` produces `CodeRelevantItem` list
8. **Backend**: Returns `PipelineSummary` with classification metadata, code relevance, and extracted items
9. **Extension**: Displays summary in chat as an AI message
10. **Extension**: If `code_relevant_types` is non-empty, shows "Generate Code Prompt" button

### Flow 4: Code Prompt Generation

1. **Extension**: Lead user clicks "Generate Code Prompt" after summary
2. **Extension**: Detects workspace languages (Python, Java, JavaScript, Go)
3. **Extension**: Sends `POST /ai/code-prompt` with decision summary, room ID, and detected languages
4. **Backend**: Router calls `generate_code_prompt()`
5. **Backend**: Checks for room-level code style override in `ConnectionManager.room_settings`
6. **Backend**: If no override, loads style guidelines based on detected languages:
   - Universal style (`universal.md`)
   - Language-specific styles (e.g., `python.md`, `java.md`)
7. **Backend**: Constructs code generation prompt template with problem, solution, components, risk, policy, and style
8. **Backend**: Returns prompt as string
9. **Extension**: Displays prompt in chat as an AI message
10. **Extension**: Shows "Send to Agent" button (future: will call LLM agent to generate code)

### Flow 5: Change Review and Application

1. **Extension**: User receives a `ChangeSet` (from MockAgent or future LLM agent)
2. **Extension**: Calls `POST /policy/evaluate` to check auto-apply eligibility
3. **Backend**: `AutoApplyPolicy.evaluate()` checks max files, max lines, forbidden paths
4. **Backend**: Returns `PolicyResult` with `allowed` flag and reasons
5. **Extension**: If auto-apply is allowed and enabled, applies changes automatically
6. **Extension**: Otherwise, shows sequential diff preview for each `FileChange`
7. **Extension**: User reviews each change and clicks "Apply" or "Discard"
8. **Extension**: For applied changes, calls `POST /audit/log-apply` to record in audit log
9. **Backend**: `AuditLogService.log_apply()` computes changeset hash and stores in DuckDB
10. **Backend**: Returns `AuditLogEntry` with timestamp

---

## 14. Patterns and Conventions

This section documents common patterns used throughout the backend codebase.

### Module Convention

Each feature module follows a consistent structure:

```
module_name/
├── __init__.py          # Empty or exports key classes
├── router.py            # FastAPI router with endpoints
├── service.py           # Business logic (optional)
├── schemas.py           # Pydantic models (optional)
└── [domain files]       # Additional domain-specific files
```

**Example**: The `chat/` module has:
- `router.py`: WebSocket endpoint and message handling
- `manager.py`: ConnectionManager business logic
- `settings_router.py`: Room settings API

**Why this structure?** Separates concerns:
- **Router**: HTTP/WebSocket protocol handling
- **Service**: Business logic (can be tested without FastAPI)
- **Schemas**: Data validation and serialization

### Singleton Pattern

Several services use the singleton pattern to ensure only one instance exists:

```python
class MyService:
    _instance: Optional["MyService"] = None

    @classmethod
    def get_instance(cls, *args, **kwargs) -> "MyService":
        if cls._instance is None:
            cls._instance = cls(*args, **kwargs)
        return cls._instance
```

**Services using singletons**:
- `ConnectionManager` (chat/manager.py)
- `AuditLogService` (audit/service.py)
- `FileStorageService` (files/service.py)
- `ProviderResolver` (ai_provider/resolver.py)
- `get_config()` (config.py)

**Why singletons?** These services manage shared state (database connections, WebSocket connections, configuration) that should be initialized once and reused.

**Thread Safety**: Python's GIL provides basic thread safety for the singleton check. For production with multiple workers, each process will have its own singleton instance.

### Pydantic Usage

All data validation uses Pydantic models:

```python
from pydantic import BaseModel, Field

class MyModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., min_length=1, max_length=100)
    count: int = Field(..., ge=0, le=1000)
    optional_field: Optional[str] = None
```

**Key Features**:
- **Type validation**: Automatic type checking and coercion
- **Field constraints**: `min_length`, `max_length`, `ge` (>=), `le` (<=)
- **Default factories**: Use `default_factory` for mutable defaults (lists, dicts, UUIDs)
- **Optional fields**: Use `Optional[T]` with `= None`

**FastAPI Integration**: Pydantic models are automatically validated in request bodies and serialized in responses.

### Security Annotations

Security-critical code is marked with `# SECURITY:` comments:

```python
# SECURITY: Backend assigns userId (never trust client-provided IDs)
user_id = str(uuid.uuid4())

# SECURITY: First user to connect becomes host and initial lead
if room_id not in self.room_hosts:
    self.room_hosts[room_id] = user_id
    role = "host"
```

**Why annotate?** Makes security decisions explicit and easier to audit. Helps reviewers understand the security model.

### Docstring Style

The codebase uses Google-style docstrings:

```python
def my_function(arg1: str, arg2: int) -> bool:
    """Short one-line summary.

    Longer description if needed. Can span multiple lines.
    Explains the purpose, behavior, and any important details.

    Args:
        arg1: Description of arg1
        arg2: Description of arg2

    Returns:
        Description of return value

    Raises:
        ValueError: When arg2 is negative
        KeyError: When arg1 is not found
    """
    pass
```

**Sections**:
- **Summary**: One-line description (required)
- **Description**: Longer explanation (optional)
- **Args**: Parameter descriptions (if any)
- **Returns**: Return value description (if not None)
- **Raises**: Exceptions that may be raised (if any)

### Error Handling

The codebase uses custom exception hierarchies:

```python
class MyModuleError(Exception):
    """Base exception for my_module."""
    pass

class SpecificError(MyModuleError):
    """Raised when a specific condition occurs."""
    pass
```

**Pattern**: Define a base exception per module, then specific exceptions that inherit from it. This allows catching all module errors with a single `except MyModuleError:` clause.

**FastAPI Error Handling**: Routers catch exceptions and return appropriate HTTP status codes:

```python
@router.post("/endpoint")
async def my_endpoint():
    try:
        result = do_something()
        return result
    except SpecificError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except MyModuleError as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Testing Patterns

Tests follow these conventions:

1. **One test file per module**: `tests/test_chat.py` tests `app/chat/`
2. **Shared fixtures in conftest.py**: Common setup (test client, mock config, etc.)
3. **Descriptive test names**: `test_connect_assigns_host_role_to_first_user()`
4. **Arrange-Act-Assert**: Structure tests in three sections
5. **Async tests**: Use `@pytest.mark.asyncio` for async functions

**Example**:

```python
@pytest.mark.asyncio
async def test_connect_assigns_host_role_to_first_user():
    # Arrange
    manager = ConnectionManager()
    websocket = MockWebSocket()
    room_id = "test-room"

    # Act
    user_id, role, history = await manager.connect(websocket, room_id)

    # Assert
    assert role == "host"
    assert manager.room_hosts[room_id] == user_id
```

---

## 15. How to Add a New Module

This section provides a step-by-step guide for adding a new feature module.

### Step 1: Create Module Directory

```bash
mkdir backend/app/my_feature
touch backend/app/my_feature/__init__.py
touch backend/app/my_feature/router.py
touch backend/app/my_feature/schemas.py
```

### Step 2: Define Pydantic Schemas

In `schemas.py`, define request/response models:

```python
from pydantic import BaseModel, Field
from typing import Optional

class MyFeatureRequest(BaseModel):
    """Request model for my feature."""
    name: str = Field(..., min_length=1, max_length=100)
    value: int = Field(..., ge=0)

class MyFeatureResponse(BaseModel):
    """Response model for my feature."""
    id: str
    name: str
    value: int
    created_at: str
```

### Step 3: Create Router

In `router.py`, define endpoints:

```python
from fastapi import APIRouter, HTTPException
from .schemas import MyFeatureRequest, MyFeatureResponse

router = APIRouter(prefix="/my-feature", tags=["my-feature"])

@router.post("/create", response_model=MyFeatureResponse)
async def create_item(request: MyFeatureRequest) -> MyFeatureResponse:
    """Create a new item."""
    try:
        # Business logic here
        return MyFeatureResponse(
            id=str(uuid.uuid4()),
            name=request.name,
            value=request.value,
            created_at=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{item_id}", response_model=MyFeatureResponse)
async def get_item(item_id: str) -> MyFeatureResponse:
    """Get an item by ID."""
    # Business logic here
    pass
```

### Step 4: Register Router in main.py

In `backend/app/main.py`, import and register the router:

```python
from app.my_feature.router import router as my_feature_router

# ... existing code ...

app.include_router(my_feature_router)
```

### Step 5: Add Tests

Create `backend/tests/test_my_feature.py`:

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_create_item():
    response = client.post(
        "/my-feature/create",
        json={"name": "test", "value": 42}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test"
    assert data["value"] == 42
    assert "id" in data

def test_get_item():
    # Create item first
    create_resp = client.post(
        "/my-feature/create",
        json={"name": "test", "value": 42}
    )
    item_id = create_resp.json()["id"]

    # Get item
    get_resp = client.get(f"/my-feature/{item_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == item_id
```

### Step 6: Run Tests

```bash
make test-backend
# or
cd backend && ../.venv/bin/pytest tests/test_my_feature.py -v
```

### Step 7: Update API Documentation

FastAPI auto-generates docs, but you can add descriptions:

```python
@router.post(
    "/create",
    response_model=MyFeatureResponse,
    summary="Create a new item",
    description="Creates a new item with the given name and value.",
    responses={
        200: {"description": "Item created successfully"},
        400: {"description": "Invalid request"},
        500: {"description": "Internal server error"},
    }
)
async def create_item(request: MyFeatureRequest) -> MyFeatureResponse:
    ...
```

### Optional: Add Service Layer

If business logic is complex, create `service.py`:

```python
class MyFeatureService:
    _instance: Optional["MyFeatureService"] = None

    @classmethod
    def get_instance(cls) -> "MyFeatureService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create_item(self, name: str, value: int) -> dict:
        # Complex business logic here
        pass
```

Then use it in the router:

```python
from .service import MyFeatureService

@router.post("/create")
async def create_item(request: MyFeatureRequest):
    service = MyFeatureService.get_instance()
    result = service.create_item(request.name, request.value)
    return result
```

---

## 16. Testing Guide

This section explains how to write and run tests for the backend.

### Test Structure

Tests are organized in `backend/tests/`:

```
backend/tests/
├── conftest.py              # Shared fixtures
├── test_chat.py             # Chat system tests
├── test_ai_provider.py      # AI provider tests
├── test_agent.py            # Agent tests
├── test_auth.py             # Auth tests
├── test_policy.py           # Policy tests
├── test_audit.py            # Audit tests
└── test_files.py            # File sharing tests
```

**Convention**: One test file per module, named `test_{module_name}.py`.

### Shared Fixtures (conftest.py)

The `conftest.py` file provides shared fixtures used across tests:

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import ConductorConfig

@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)

@pytest.fixture
def mock_config():
    """Mock configuration for testing."""
    return ConductorConfig(
        server=ServerConfig(host="localhost", port=8000),
        summary=SummaryConfig(enabled=True, default_model="claude-sonnet-4-anthropic"),
        # ... other config fields
    )

@pytest.fixture
def mock_websocket():
    """Mock WebSocket for testing."""
    class MockWebSocket:
        def __init__(self):
            self.messages = []
            self.closed = False

        async def accept(self):
            pass

        async def send_json(self, data):
            self.messages.append(data)

        async def close(self):
            self.closed = True

    return MockWebSocket()
```

**Usage**: Import fixtures in test files by adding them as function parameters:

```python
def test_something(client, mock_config):
    # client and mock_config are automatically injected
    response = client.get("/health")
    assert response.status_code == 200
```

### Running Tests

```bash
# All backend tests
make test-backend

# With verbose output
cd backend && ../.venv/bin/pytest -v

# Single test file
cd backend && ../.venv/bin/pytest tests/test_chat.py -v

# Single test by name
cd backend && ../.venv/bin/pytest tests/test_chat.py -v -k "test_connect"

# With coverage report
cd backend && ../.venv/bin/pytest --cov=app --cov-report=html

# Stop on first failure
cd backend && ../.venv/bin/pytest -x

# Show print statements
cd backend && ../.venv/bin/pytest -s
```

### Test Patterns

#### Pattern 1: Testing REST Endpoints

```python
def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_create_endpoint(client):
    response = client.post(
        "/my-feature/create",
        json={"name": "test", "value": 42}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test"
    assert data["value"] == 42
```

#### Pattern 2: Testing WebSocket Endpoints

```python
@pytest.mark.asyncio
async def test_websocket_connection():
    manager = ConnectionManager()
    websocket = MockWebSocket()
    room_id = "test-room"

    user_id, role, history = await manager.connect(websocket, room_id)

    assert role == "host"
    assert user_id in manager.room_users[room_id]
```

#### Pattern 3: Testing Async Functions

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await my_async_function()
    assert result == expected_value
```

**Note**: Use `@pytest.mark.asyncio` decorator for async tests.

#### Pattern 4: Testing with Mocks

```python
from unittest.mock import Mock, patch

def test_with_mock():
    mock_provider = Mock()
    mock_provider.call_model.return_value = '{"result": "success"}'

    result = my_function(mock_provider)

    assert result == "success"
    mock_provider.call_model.assert_called_once()

@patch('app.my_module.external_api_call')
def test_with_patch(mock_api):
    mock_api.return_value = {"data": "test"}

    result = my_function()

    assert result == "test"
    mock_api.assert_called_once()
```

#### Pattern 5: Testing Exceptions

```python
import pytest

def test_raises_exception():
    with pytest.raises(ValueError, match="Invalid input"):
        my_function(invalid_input)

def test_http_exception(client):
    response = client.post("/endpoint", json={"invalid": "data"})
    assert response.status_code == 400
    assert "error" in response.json()["detail"].lower()
```

#### Pattern 6: Parametrized Tests

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("", ""),
])
def test_uppercase(input, expected):
    assert my_uppercase_function(input) == expected
```

### Testing Database Code

For DuckDB-based services (audit, files), use temporary databases:

```python
import tempfile
from pathlib import Path

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.duckdb"
        yield str(db_path)

def test_audit_service(temp_db):
    service = AuditLogService.get_instance(db_path=temp_db)

    entry = service.log_apply(AuditLogCreate(
        room_id="test-room",
        changeset_hash="abc123",
        applied_by="user1",
        mode=ApplyMode.MANUAL,
    ))

    assert entry.room_id == "test-room"

    logs = service.get_logs(room_id="test-room")
    assert len(logs) == 1
```

### Testing AI Provider Code

Mock AI provider responses to avoid API calls:

```python
@pytest.fixture
def mock_provider():
    provider = Mock(spec=AIProvider)
    provider.call_model.return_value = '{"discussion_type": "code_change", "confidence": 0.9}'
    provider.health_check.return_value = True
    return provider

def test_classification(mock_provider):
    messages = [ChatMessage(...), ChatMessage(...)]
    result = classify_discussion(messages, mock_provider)

    assert result.discussion_type == "code_change"
    assert result.confidence == 0.9
    mock_provider.call_model.assert_called_once()
```

### Test Coverage

Check test coverage to find untested code:

```bash
cd backend && ../.venv/bin/pytest --cov=app --cov-report=html
# Open htmlcov/index.html in browser
```

**Target**: Aim for >80% coverage for critical modules (chat, ai_provider, agent, policy, audit).

### Continuous Integration

Tests should run on every commit. Example GitHub Actions workflow:

```yaml
name: Backend Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: make setup-backend
      - name: Run tests
        run: make test-backend
```

---

## Conclusion

You've now completed the learning journey through the Conductor backend! You should understand:

- **Architecture**: FastAPI app with modular routers, WebSocket chat, AI integration
- **Configuration**: Two-file YAML system with Pydantic validation
- **Chat System**: ConnectionManager with room-scoped state, broadcasting, deduplication
- **AI Provider**: Abstract interface, provider resolution, 4-stage summarization pipeline
- **Agent**: ChangeSet schema, MockAgent, style loading
- **Auth**: AWS SSO and Google OAuth device authorization flows
- **Policy**: Auto-apply safety checks
- **Audit**: DuckDB-based logging with changeset hashing
- **Files**: Room-scoped storage with metadata tracking
- **Patterns**: Singleton, Pydantic, error handling, testing
- **Testing**: Fixtures, mocks, async tests, coverage

### Next Steps

1. **Explore the code**: Read through the modules in the order presented in this guide
2. **Run the backend**: `make run-backend` and explore the API docs at http://localhost:8000/docs
3. **Write a test**: Pick a module and add a new test case
4. **Add a feature**: Follow the "How to Add a New Module" guide to implement a small feature
5. **Read ROADMAP.md**: Understand the future direction of the project

### Getting Help

- **Code questions**: Ask in team chat or open a GitHub discussion
- **Bugs**: Open a GitHub issue with reproduction steps
- **Feature requests**: Open a GitHub issue with use case and rationale

Happy coding! 🚀

