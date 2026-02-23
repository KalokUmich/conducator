# Conductor Architecture

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This document describes the architecture that is currently implemented in the repository.

### 1. System Boundary

Conductor has two runtime parts:

1. VS Code extension (TypeScript)
- WebView UI (`extension/media/chat.html`)
- session FSM and orchestration
- Live Share integration
- diff preview/apply in workspace

2. FastAPI backend (Python)
- WebSocket chat + REST APIs
- AI provider resolution and summary pipeline
- policy checks
- audit logs
- file storage

```text
WebView <-> Extension Host <-> FastAPI
              |                |
              |                +-> DuckDB + local file storage
              +-> Live Share
```

### 2. Main Module Map

```text
extension/
  src/extension.ts
    ├─ SessionService
    ├─ PermissionsService
    ├─ ConductorStateMachine + ConductorController
    ├─ DiffPreviewService
    └─ ExplainWithContextPipeline (8 stages)
  media/chat.html

backend/app/
  main.py
    ├─ chat.router
    ├─ ai_provider.router
    ├─ agent.router
    ├─ auth.router
    ├─ policy.router
    ├─ audit.router
    ├─ files.router
    ├─ context.router
    ├─ todos.router
    ├─ embeddings.router
    └─ rag.router
```

### 3. Backend Architecture

#### Entry and Lifecycle

`backend/app/main.py`:
- registers routers
- exposes `/health` and `/public-url`
- optionally starts ngrok from config
- initializes AI provider resolver when `summary.enabled=true`
- stops ngrok on shutdown

#### Router Responsibilities

- `chat`:
  - `GET /invite`
  - `GET /chat`
  - `GET /chat/{room_id}/history`
  - `POST /chat/{room_id}/ai-message`
  - `WS /ws/chat/{room_id}`
- `ai_provider`:
  - `GET /ai/status`
  - `POST /ai/summarize`
  - `POST /ai/code-prompt`
  - `POST /ai/code-prompt/selective`
- `agent`:
  - `POST /generate-changes` (MockAgent)
- `policy`:
  - `POST /policy/evaluate-auto-apply`
- `audit`:
  - `POST /audit/log-apply`
  - `GET /audit/logs`
- `auth`:
  - `POST /auth/sso/start`
  - `POST /auth/sso/poll`
  - `POST /auth/google/start`
  - `POST /auth/google/poll`
  - `GET /auth/providers`
- `files`:
  - `POST /files/upload/{room_id}`
  - `GET /files/download/{file_id}`
  - `DELETE /files/room/{room_id}`
- `context`:
  - `POST /context/explain` — enriches request with `ContextEnricher` then calls LLM
  - `POST /context/explain-rich` — accepts pre-assembled XML prompt, optionally injects RAG context, calls LLM directly
- `todos`:
  - `GET /todos/{room_id}` — list room tasks
  - `POST /todos/{room_id}` — create task
  - `PUT /todos/{room_id}/{todo_id}` — update task
  - `DELETE /todos/{room_id}/{todo_id}` — delete task
- `embeddings`:
  - `GET /embeddings/config` — active model ID + dim + provider
  - `POST /embeddings` — batch embed 1–32 texts → `[[float]]`
- `rag`:
  - `POST /rag/index` — incremental upsert/delete files in workspace index
  - `POST /rag/reindex` — full workspace rebuild
  - `POST /rag/search` — semantic query → ranked `SearchResult[]`

#### AI Summary Pipeline

Implemented in `backend/app/ai_provider/pipeline.py`:

1. Classification stage
- classify discussion into one type:
  - `api_design`, `product_flow`, `code_change`, `architecture`, `innovation`, `debugging`, `general`

2. Targeted summary stage
- generate structured summary based on classified type
- includes `topic`, `core_problem`, `proposed_solution`, `impact_scope`, `risk_level`, etc.

3. Code relevance stage
- compute `code_relevant_types` for selective code prompt generation

4. Item extraction stage
- extract actionable `CodeRelevantItem` list (title, description, affected_components, type, priority)
- drives which code-generation prompts are offered in the extension UI

Provider resolution:
- configured in `summary` section of `conductor.settings.yaml` and provider keys in `conductor.secrets.yaml`
- priority order: `anthropic` -> `aws_bedrock` -> `openai`
- first healthy provider with a valid API key becomes active

#### Chat Core: ConnectionManager

Current responsibilities:
- room-scoped active WebSocket connections
- backend-assigned user identity (`host` first, then `guest`)
- in-memory message history
- read receipt tracking
- message dedup (LRU)
- paginated history
- broadcast cleanup for dead sockets

### 4. Extension Architecture

#### FSM and Controller

Files:
- `extension/src/services/conductorStateMachine.ts`
- `extension/src/services/conductorController.ts`

States:
- `Idle`
- `BackendDisconnected`
- `ReadyToHost`
- `Hosting`
- `Joining`
- `Joined`

Key behavior:
- join-only mode supported via `BackendDisconnected -> JOIN_SESSION -> Joining`
- start hosting checks backend health and Live Share conflict first

#### Session and Permissions

- `SessionService` (`extension/src/services/session.ts`):
  - globalState persistence for room/session IDs
  - backend URL resolution (including ngrok detection)
  - guest override from invite URL
- `PermissionsService` (`extension/src/services/permissions.ts`):
  - local role model (`lead` / `member`)

#### WebView and Host Message Bridge

WebView: `extension/media/chat.html`
Host: `extension/src/extension.ts`

Implemented host commands include:
- Session: `startSession`, `stopSession`, `retryConnection`, `joinSession`, `leaveSession`, `copyInviteLink`
- Files/snippets: `uploadFile`, `downloadFile`, `getCodeSnippet`, `navigateToCode`
- Review flow: `generateChanges`, `applyChanges`, `viewDiff`, `setAutoApply`
- AI flow: `getAiStatus`, `summarize`, `generateCodePrompt`, `generateCodePromptAndPost`

### 5. Data Contract

Shared schema:
- `shared/changeset.schema.json`

Core concepts:
- `ChangeSet.changes[]`
- `FileChange.type` in `create_file | replace_range`
- `replace_range` requires `range` and `content`

### 6. Runtime Sequences (Implemented)

Host start:
1. user clicks `Start Session`
2. extension checks backend health and Live Share conflicts
3. FSM enters `Hosting`
4. extension starts Live Share and generates invite URL
5. WebView connects to room WebSocket

Guest join:
1. guest pastes invite URL
2. controller parses `roomId` / `backendUrl` / `liveShareUrl`
3. session service switches to guest room/backend
4. FSM transitions to `Joined`
5. optional Live Share join prompt runs in background

AI summary and prompt:
1. WebView requests `/ai/status` for provider state
2. WebView sends selected messages to extension
3. extension calls `/ai/summarize`
4. extension posts AI summary via `/chat/{room_id}/ai-message`
5. user can request `/ai/code-prompt`
6. prompt can be posted as `ai_code_prompt` message

Change review:
1. extension calls `/generate-changes`
2. extension calls `/policy/evaluate-auto-apply`
3. changes enter sequential review queue
4. each change diff-previewed and applied/skipped
5. applied changes logged to `/audit/log-apply`

### 7. Persistence

- audit DB: `audit_logs.duckdb`
- file metadata DB: `file_metadata.duckdb`
- TODO DB: `todos.duckdb`
- file binaries: `uploads/`
- FAISS indices: `data/rag/{workspace_id}/index.faiss` + `metadata.json`
- chat room state: in-memory per process
- extension symbol/vector cache: `~/.conductor/workspace.db` (SQLite, per workspace)

### 8. Implemented vs Limited

Implemented:
- chat/file/snippet/session/review workflow
- AI provider status + summary + code prompt workflow in extension UI
- TODO tracking (room-scoped, DuckDB, full CRUD)
- Embeddings service (Bedrock Cohere, 1024-dim)
- FAISS code RAG: AST chunking, workspace-scoped index, incremental updates, semantic search
- Explain with Context 8-stage pipeline (LSP + dependency resolution + ranking + XML assembly + `/context/explain-rich`)

Limited:
- `/generate-changes` is still MockAgent-based
- extension currently uses `/ai/code-prompt` (not selective endpoint)
- git commit retrieval not yet implemented (planned in former Phase 2.4)

---

<a name="中文"></a>
## 中文

本文档描述仓库中当前已经实现的架构。

### 1. 系统边界

Conductor 由两部分运行时组成：

1. VS Code 扩展（TypeScript）
- WebView UI（`extension/media/chat.html`）
- 会话状态机与编排
- Live Share 集成
- 工作区 Diff 预览与应用

2. FastAPI 后端（Python）
- WebSocket 聊天 + REST API
- AI Provider 解析与摘要流水线
- 策略评估
- 审计日志
- 文件存储

### 2. 后端路由职责（当前实现）

- `chat`：`/invite`、`/chat`、`/chat/{room_id}/history`、`/chat/{room_id}/ai-message`、`/ws/chat/{room_id}`
- `ai_provider`：`/ai/status`、`/ai/summarize`、`/ai/code-prompt`、`/ai/code-prompt/selective`
- `agent`：`/generate-changes`（MockAgent）
- `policy`：`/policy/evaluate-auto-apply`
- `audit`：`/audit/log-apply`、`/audit/logs`
- `auth`：`/auth/sso/start`、`/auth/sso/poll`、`/auth/google/start`、`/auth/google/poll`、`/auth/providers`
- `files`：上传/下载/房间清理
- `context`：`/context/explain`（后端填充上下文）、`/context/explain-rich`（扩展预组装 XML 提示词直接转发至 LLM，可选 RAG 增强）
- `todos`：`/todos/{room_id}` CRUD（GET/POST/PUT/DELETE）
- `embeddings`：`/embeddings/config`（模型配置）、`/embeddings`（批量向量化，1–32 条文本）
- `rag`：`/rag/index`（增量索引）、`/rag/reindex`（全量重建）、`/rag/search`（语义检索）

### 3. AI 摘要流水线

`backend/app/ai_provider/pipeline.py` 当前实现四阶段：

1. 对话分类（7 类讨论类型）
2. 按分类生成定向结构化摘要
3. 计算 `code_relevant_types`（用于 selective code prompt）
4. 条目提取——生成 `CodeRelevantItem` 列表（标题、描述、影响组件、类型、优先级）

Provider 选择由 `conductor.settings.yaml` 的 `summary` 配置和 `conductor.secrets.yaml` 的 provider 密钥驱动，优先级 `anthropic -> aws_bedrock -> openai`。

### 4. 扩展侧关键模块

- 状态机/控制器：`conductorStateMachine.ts`、`conductorController.ts`
- 会话服务：`session.ts`（globalState、ngrok 检测、guest 覆盖）
- 权限服务：`permissions.ts`（`lead/member`）
- WebView + Host 消息桥：`chat.html` + `extension.ts`

已接入的 AI 命令：
- `getAiStatus`
- `summarize`
- `generateCodePrompt`
- `generateCodePromptAndPost`

### 5. 关键运行时序

Host 发起：
1. 点击 `Start Session`
2. 扩展检查后端健康与 Live Share 冲突
3. FSM 进入 `Hosting`
4. 启动 Live Share 并生成邀请链接
5. WebView 建立房间 WebSocket

Guest 加入：
1. 粘贴邀请链接
2. 解析 `roomId/backendUrl/liveShareUrl`
3. SessionService 切换到 guest 房间与后端
4. FSM 转到 `Joined`
5. Live Share 加入提示异步执行（可只聊天）

AI 摘要与代码提示词：
1. WebView 拉取 `/ai/status`
2. WebView 发送消息集合给 extension
3. extension 调 `/ai/summarize`
4. extension 通过 `/chat/{room_id}/ai-message` 写回 AI 摘要
5. 用户可继续请求 `/ai/code-prompt` 并回写聊天

### 6. 持久化

- 审计库：`audit_logs.duckdb`
- 文件元数据库：`file_metadata.duckdb`
- TODO 库：`todos.duckdb`
- 文件目录：`uploads/`
- FAISS 索引：`data/rag/{workspace_id}/index.faiss` + `metadata.json`
- 聊天房间状态：进程内内存
- 扩展符号/向量缓存：`~/.conductor/workspace.db`（SQLite，每工作区一份）

### 7. 已实现与限制

已实现：
- 聊天、文件、片段、会话、审查流程
- 扩展 UI 内 AI 状态/摘要/代码提示词流程
- TODO 任务管理（DuckDB 持久化，房间隔离）
- 向量嵌入服务（Bedrock Cohere，1024 维）
- FAISS 代码 RAG：AST 分块、工作区级索引、增量更新、语义检索
- 8 阶段代码解释流水线（LSP + 依赖解析 + 排名 + XML 组装 + `/context/explain-rich`）

限制：
- `/generate-changes` 仍为 MockAgent
- 扩展目前调用 `/ai/code-prompt`，未走 selective 接口
- Git 提交语义检索尚未实现
