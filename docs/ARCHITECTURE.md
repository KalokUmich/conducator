# Conductor Architecture & Extension Guide / 架构与拓展指南

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This document provides a comprehensive guide to the Conductor architecture, design philosophy, and instructions for extending the project.

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [System Overview](#system-overview)
3. [Backend Architecture](#backend-architecture)
4. [Frontend Architecture](#frontend-architecture)
5. [State Management](#state-management)
6. [Code Generation Flow](#code-generation-flow)
7. [Extension Guide](#extension-guide)
8. [API Reference](#api-reference)

---

## Design Philosophy

### Core Principles

1. **Separation of Concerns**: Clear boundaries between frontend (VS Code extension), backend (FastAPI), and communication layers (WebSocket/REST).

2. **State Machine Driven**: The extension uses a finite state machine (FSM) to manage lifecycle states, ensuring predictable behavior and testability.

3. **Pluggable Agent Architecture**: The backend uses a pluggable agent pattern, allowing easy swap between MockAgent (testing) and LLM-based agents (production).

4. **Policy-Based Safety**: Auto-apply feature uses configurable policies to limit blast radius and protect critical paths.

5. **Audit Trail**: All applied changes are logged to DuckDB for compliance and debugging.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           VS Code Extension                              │
│  ┌──────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  StateMachine    │  │  SessionService │  │  PermissionsService     │ │
│  │  (FSM)           │  │  (Room/User)    │  │  (Role-based access)    │ │
│  └────────┬─────────┘  └────────┬────────┘  └────────────┬────────────┘ │
│           │                     │                        │              │
│  ┌────────▼─────────────────────▼────────────────────────▼────────────┐ │
│  │                     ConductorController                             │ │
│  │  - Orchestrates FSM, session, and backend interactions              │ │
│  └────────────────────────────────────┬───────────────────────────────┘ │
│                                       │                                  │
│  ┌────────────────────────────────────▼───────────────────────────────┐ │
│  │                         WebView (chat.html)                         │ │
│  │  - Chat UI, Pending Changes Card, Role Badge                        │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────┬─────────────────────────────────┘
                                        │ HTTP REST / WebSocket
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Backend (FastAPI)                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │   /chat     │  │  /summary   │  │  /agent     │  │    /policy      │ │
│  │ (WebSocket) │  │  (REST)     │  │  (REST)     │  │    (REST)       │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───────┬─────────┘ │
│         │                │                │                  │          │
│  ┌──────▼────────────────▼────────────────▼──────────────────▼────────┐ │
│  │                     Shared Services                                 │ │
│  │  - ConnectionManager (chat rooms)                                   │ │
│  │  - MockAgent / LLM Agent (code generation)                          │ │
│  │  - AutoApplyPolicy (safety evaluation)                              │ │
│  │  - AuditLogService (DuckDB persistence)                             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Backend Architecture

### Module Structure

```
backend/app/
├── main.py              # Application entry, lifespan, routers
├── config.py            # YAML configuration loader
├── ngrok_service.py     # Ngrok tunnel management
│
├── agent/               # Code Generation
│   ├── router.py        # POST /generate-changes
│   ├── schemas.py       # ChangeSet, FileChange, Range models
│   ├── mock_agent.py    # Deterministic test agent
│   └── style_loader.py  # Code style guidelines (future)
│
├── chat/                # Real-time Chat
│   ├── router.py        # WebSocket /ws/chat/{room_id}
│   └── manager.py       # ConnectionManager (rooms, users, history)
│
├── policy/              # Auto-Apply Safety
│   ├── router.py        # POST /policy/evaluate-auto-apply
│   └── auto_apply.py    # PolicyResult, rules evaluation
│
├── summary/             # Chat Summarization
│   ├── router.py        # POST /summary
│   └── schemas.py       # SummaryRequest, SummaryResponse
│
└── audit/               # Change Auditing
    ├── router.py        # POST /audit/log-apply, GET /audit/logs
    ├── schemas.py       # AuditLogEntry, ApplyMode
    └── service.py       # DuckDB storage service
```

### Key Components

#### 1. Agent System (`agent/`)

The agent system generates code changes based on instructions.

**Current Implementation: MockAgent**
- Generates deterministic changes for testing
- Creates `helper.py` and `config.py` in target directory
- Adds import statements to target file

**Future: LLM Agent**
- Use `style_loader.py` to inject code style guidelines
- Call LLM API (OpenAI, Claude, etc.)
- Parse structured output into ChangeSet

```python
# agent/schemas.py - Core Data Structures

class ChangeType(str, Enum):
    REPLACE_RANGE = "replace_range"  # Modify existing file
    CREATE_FILE = "create_file"      # Create new file

class Range(BaseModel):
    start: int  # 1-based, inclusive
    end: int    # 1-based, inclusive

class FileChange(BaseModel):
    id: str                       # UUID for tracking
    file: str                     # Relative path
    type: ChangeType
    range: Optional[Range]        # For replace_range
    content: Optional[str]        # New content
    original_content: Optional[str]

class ChangeSet(BaseModel):
    changes: List[FileChange]     # 1-10 files
    summary: str                  # Human-readable description
```

#### 2. Chat System (`chat/`)

Real-time WebSocket chat with room isolation.

**ConnectionManager Features:**
- Multiple rooms with independent state
- Message history persistence (in-memory)
- User registration with auto-naming (Guest 1, Guest 2...)
- Avatar color assignment
- Broadcast messaging

```python
# chat/manager.py - Key Classes

class UserRole(str, Enum):
    HOST = "host"        # Can end session, use AI features
    ENGINEER = "engineer" # Chat only

class RoomUser(BaseModel):
    userId: str
    displayName: str
    role: UserRole
    avatarColor: str

class ChatMessage(BaseModel):
    id: str              # Auto-generated UUID
    roomId: str
    userId: str
    displayName: str
    role: UserRole
    content: str
    ts: float            # Unix timestamp
```

**WhatsApp-Style Features:**

| Feature | Description |
|---------|-------------|
| 💬 **Typing Indicator** | Shows "User is typing..." when someone is composing |
| ✓ **Message Status** | Checkmarks (✓ sent) for message delivery status |
| 📅 **Date Separators** | "Today", "Yesterday", or date for message grouping |
| 👤 **Message Grouping** | Consecutive messages from same user within 5 min are grouped |
| 📎 **File Sharing** | Images, PDFs, audio files up to 20MB |
| 📥 **File Download** | Click to download files in VS Code or web browser |
| 📱 **Mobile Support** | Web version responsive design for mobile devices |

**WebSocket Message Types:**
| Type | Direction | Description |
|------|-----------|-------------|
| `history` | Server→Client | Initial message with chat history and users |
| `join` | Client→Server | Register user in room |
| `message` | Bidirectional | Chat text message |
| `file` | Server→Client | File upload notification |
| `typing` | Bidirectional | Typing indicator (isTyping: true/false) |
| `user_joined` | Server→Client | User joined notification |
| `user_left` | Server→Client | User left notification |
| `session_ended` | Server→Client | Host ended session |
| `end_session` | Client→Server | Host ends session |

**WebSocket Optimization:**
| Optimization | Description |
|--------------|-------------|
| 🚀 **Concurrent Broadcasting** | Uses `asyncio.gather()` to send messages to all clients concurrently instead of sequentially |
| 💓 **Ping/Pong Heartbeat** | Uvicorn handles ping/pong at protocol level (20s interval, 20s timeout) |
| 🔄 **Auto-Cleanup** | Failed connections are automatically removed during broadcast |
| 📊 **Efficient Serialization** | JSON messages are serialized once and sent to all clients |

**Uvicorn WebSocket Configuration:**
```bash
# Configure via Makefile or command line
uvicorn app.main:app --ws-ping-interval 20.0 --ws-ping-timeout 20.0
```

**Advanced Chat Features:**
| Feature | Description |
|---------|-------------|
| 🔄 **Smart Reconnection** | Exponential backoff (1s base, 30s max) with ±20% jitter to prevent thundering herd |
| 📨 **Message Recovery** | On reconnect, client sends `?since=<timestamp>` to recover missed messages |
| 🔁 **Message Deduplication** | Server uses LRU cache (10,000 messages), client uses Set to prevent duplicates |
| 📄 **Message Pagination** | `GET /chat/{room_id}/history?before=<ts>&limit=50` for lazy loading old messages |
| ✓✓ **Read Receipts** | Intersection Observer detects visible messages (50% threshold), broadcasts `read_receipt` |

**WebSocket Message Types (Extended):**
| Type | Direction | Description |
|------|-----------|-------------|
| `read` | Client→Server | Client sends when message becomes visible |
| `read_receipt` | Server→Client | Server broadcasts with `messageId` and `readBy` array |

**Pagination API:**
```bash
# Get older messages (cursor-based pagination)
GET /chat/{room_id}/history?before=1707321600.123&limit=50

# Response
{
  "messages": [...],
  "hasMore": true
}
```

#### 3. Policy System (`policy/`)

Safety evaluation for auto-apply feature.

**Default Rules:**
| Rule | Limit | Rationale |
|------|-------|-----------|
| `max_files` | ≤ 2 | Limit blast radius |
| `max_lines_changed` | ≤ 50 | Keep changes reviewable |
| `forbidden_paths` | `infra/`, `db/`, `security/` | Protect critical code |

```python
# policy/auto_apply.py

class AutoApplyPolicy:
    def evaluate(self, change_set: ChangeSet) -> PolicyResult:
        reasons = []
        
        # Rule 1: Check file count
        if len(change_set.changes) > self.max_files:
            reasons.append(f"Too many files: {len(change_set.changes)}")
        
        # Rule 2: Check line count
        total_lines = self._count_lines_changed(change_set)
        if total_lines > self.max_lines_changed:
            reasons.append(f"Too many lines: {total_lines}")
        
        # Rule 3: Check forbidden paths
        forbidden = self._find_forbidden_files(change_set)
        if forbidden:
            reasons.append(f"Forbidden paths: {forbidden}")
        
        return PolicyResult(allowed=len(reasons) == 0, reasons=reasons)
```

#### 4. Audit System (`audit/`)

DuckDB-based change logging for compliance.

**Database Schema:**
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY,
    room_id VARCHAR NOT NULL,
    summary_id VARCHAR,
    changeset_hash VARCHAR NOT NULL,  -- SHA-256 (16 chars)
    applied_by VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,            -- 'manual' or 'auto'
    timestamp TIMESTAMP NOT NULL
);
```

---

## Frontend Architecture

### Module Structure

```
extension/src/
├── extension.ts              # Entry point, activation, commands
│
└── services/
    ├── conductorStateMachine.ts  # Pure FSM (no VS Code deps)
    ├── conductorController.ts    # Orchestration layer
    ├── session.ts                # Room/User management
    ├── permissions.ts            # Role-based access control
    ├── diffPreview.ts            # Diff display and apply
    └── backendHealthCheck.ts     # Backend connectivity

extension/media/
├── chat.html           # WebView UI (Tailwind CSS)
├── tailwind.css        # Compiled styles
└── input.css           # Tailwind source
```

### Key Components

#### 1. State Machine (`conductorStateMachine.ts`)

Pure FSM with no external dependencies, fully unit-testable.

**States:**
```
Idle → BackendDisconnected ← (any state on BACKEND_LOST)
  ↓           ↓ (join-only mode)
ReadyToHost ←→ Hosting
  ↓
Joining → Joined
```

**State Diagram:**
```
┌──────┐ BACKEND_CONNECTED ┌─────────────┐
│ Idle │─────────────────▶│ ReadyToHost │
└──────┘                   └──────┬──────┘
    │                             │
    │ BACKEND_LOST        START_HOSTING / JOIN_SESSION
    ▼                             │
┌────────────────────┐            ▼
│ BackendDisconnected│◀───────┌─────────┐
└─────────┬──────────┘        │ Hosting │
          │                   └────┬────┘
          │                        │
    JOIN_SESSION             STOP_HOSTING
    (join-only)                    │
          │                        │
          ▼                        │
    ┌─────────┐                    │
    │ Joining │◀───────────────────┘
    └────┬────┘
         │
  JOIN_SUCCEEDED
         │
         ▼
    ┌────────┐
    │ Joined │
    └────────┘
```

> **Note:** The `BackendDisconnected` state supports "Join Only Mode". Users can still join other people's sessions even without a local backend running. Only the "Start Session" (hosting) feature requires a local backend.

**Event Table:**
| Event | From States | To State |
|-------|-------------|----------|
| BACKEND_CONNECTED | Idle, BackendDisconnected | ReadyToHost |
| BACKEND_LOST | Any (except Idle) | BackendDisconnected |
| START_HOSTING | ReadyToHost | Hosting |
| STOP_HOSTING | Hosting | ReadyToHost |
| JOIN_SESSION | ReadyToHost, **BackendDisconnected** | Joining |
| JOIN_SUCCEEDED | Joining | Joined |
| JOIN_FAILED | Joining | ReadyToHost |
| LEAVE_SESSION | Joined | ReadyToHost |

#### 2. Controller (`conductorController.ts`)

Orchestration layer that connects FSM with side effects.

```typescript
class ConductorController {
    // Dependencies injected for testability
    constructor(
        fsm: ConductorStateMachine,
        healthCheck: HealthCheckFn,      // async (url) => boolean
        urlProvider: UrlProviderFn,      // () => string
        sessionReset: SessionResetFn,    // () => string (new roomId)
    ) {}

    // Public API
    async start(): Promise<ConductorState>      // Health check → Ready/Disconnected
    startHosting(): string                       // → Hosting, returns roomId
    stopHosting(): void                          // → ReadyToHost
    startJoining(inviteUrl: string): ParsedInvite // → Joining
    joinSucceeded(): void                        // → Joined
    joinFailed(): void                           // → ReadyToHost
    leaveSession(): void                         // → ReadyToHost
}
```

#### 3. Session Service (`session.ts`)

Manages room and user identity across reloads.

**Persistence:** Uses VS Code's `globalState` API

**Key Data:**
- `roomId`: UUID for the collaboration room
- `hostId`: Machine ID of the host
- `userId`: UUID for this user
- `liveShareUrl`: VS Code Live Share URL
- `ngrokUrl`: Detected ngrok tunnel URL

#### 4. Permissions Service (`permissions.ts`)

Role-based access control for UI features.

**Permission Matrix:**
| Feature | Lead | Member |
|---------|------|--------|
| chat | ✅ | ✅ |
| createSummary | ✅ | ❌ |
| generateChanges | ✅ | ❌ |
| autoApply | ✅ | ❌ |

---

## State Management

### Frontend State Flow

```
User Action → Controller → FSM → State Change → WebView Update
                 ↓
            Side Effects (Health Check, Live Share, etc.)
```

### Backend State (Per Room)

```
WebSocket Connect → ConnectionManager.connect()
      ↓
Join Message → ConnectionManager.register_user()
      ↓
Chat Message → ConnectionManager.add_message() → broadcast()
      ↓
Disconnect → ConnectionManager.disconnect()
```

---

## Code Generation Flow

```
1. User clicks "Generate Changes"
        ↓
2. Extension sends POST /generate-changes
   {file_path, instruction, file_content}
        ↓
3. Backend calls MockAgent.generate_changes()
        ↓
4. Agent returns ChangeSet with FileChanges
        ↓
5. Extension evaluates policy (POST /policy/evaluate-auto-apply)
        ↓
6. Extension shows diff preview for first change
        ↓
7. User reviews and clicks "Apply" or "Discard"
        ↓
8. DiffPreviewService.applySingleChange()
        ↓
9. Extension logs to audit (POST /audit/log-apply)
        ↓
10. Show next change or complete
```

---

## Extension Guide

### Adding a New Agent

1. **Create Agent Class:**
```python
# backend/app/agent/llm_agent.py
class LLMAgent:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model
    
    def generate_changes(self, request: GenerateChangesRequest) -> GenerateChangesResponse:
        # 1. Load style guidelines
        style = StyleLoader.load_for_file(request.file_path)
        
        # 2. Build prompt
        prompt = self._build_prompt(request, style)
        
        # 3. Call LLM
        response = self.client.chat.completions.create(...)
        
        # 4. Parse response into ChangeSet
        change_set = self._parse_response(response)
        
        return GenerateChangesResponse(success=True, change_set=change_set)
```

2. **Register in Router:**
```python
# backend/app/agent/router.py
from .llm_agent import LLMAgent

agent = LLMAgent(api_key=config.llm.api_key, model=config.llm.model)

@router.post("/generate-changes")
async def generate_changes(request: GenerateChangesRequest):
    return agent.generate_changes(request)
```

### Adding a New Policy Rule

1. **Add Rule to AutoApplyPolicy:**
```python
# backend/app/policy/auto_apply.py

# New constant
ALLOWED_EXTENSIONS = (".py", ".ts", ".js", ".json")

class AutoApplyPolicy:
    def evaluate(self, change_set: ChangeSet) -> PolicyResult:
        reasons = []
        
        # Existing rules...
        
        # New rule: Check file extensions
        disallowed = self._find_disallowed_extensions(change_set)
        if disallowed:
            reasons.append(f"Disallowed file types: {disallowed}")
        
        return PolicyResult(allowed=len(reasons) == 0, reasons=reasons)
    
    def _find_disallowed_extensions(self, change_set: ChangeSet) -> List[str]:
        disallowed = []
        for change in change_set.changes:
            if not change.file.endswith(ALLOWED_EXTENSIONS):
                disallowed.append(change.file)
        return disallowed
```

2. **Add Tests:**
```python
# backend/tests/test_auto_apply_policy.py
def test_disallowed_extension():
    policy = AutoApplyPolicy()
    change_set = ChangeSet(changes=[
        FileChange(file="script.sh", type=ChangeType.CREATE_FILE, content="#!/bin/bash")
    ])
    result = policy.evaluate(change_set)
    assert not result.allowed
    assert "Disallowed file types" in result.reasons[0]
```

### Adding a New FSM State

1. **Add State and Events:**
```typescript
// extension/src/services/conductorStateMachine.ts

export enum ConductorState {
    // Existing states...
    Reviewing = 'Reviewing',  // New state
}

export enum ConductorEvent {
    // Existing events...
    START_REVIEW = 'START_REVIEW',
    FINISH_REVIEW = 'FINISH_REVIEW',
}

const TRANSITION_TABLE = {
    // Existing transitions...
    [`${ConductorState.Hosting}:${ConductorEvent.START_REVIEW}`]: ConductorState.Reviewing,
    [`${ConductorState.Reviewing}:${ConductorEvent.FINISH_REVIEW}`]: ConductorState.Hosting,
};
```

2. **Add Controller Methods:**
```typescript
// extension/src/services/conductorController.ts

startReview(): void {
    this._fsm.transition(ConductorEvent.START_REVIEW);
}

finishReview(): void {
    this._fsm.transition(ConductorEvent.FINISH_REVIEW);
}
```

3. **Update WebView:**
```html
<!-- extension/media/chat.html -->
<div id="review-panel" class="hidden">
    <!-- Review UI -->
</div>

<script>
    function updateUI(state) {
        if (state === 'Reviewing') {
            document.getElementById('review-panel').classList.remove('hidden');
        }
    }
</script>
```

### Adding a New Permission

1. **Update Permission Matrix:**
```typescript
// extension/src/services/permissions.ts

export type Feature = 
    | 'chat'
    | 'createSummary'
    | 'generateChanges'
    | 'autoApply'
    | 'viewAuditLogs';  // New feature

const PERMISSION_MATRIX: Record<Role, Set<Feature>> = {
    lead: new Set([
        'chat', 'createSummary', 'generateChanges', 'autoApply', 'viewAuditLogs'
    ]),
    member: new Set(['chat'])
};
```

2. **Update WebView Interface:**
```typescript
export interface WebViewPermissions {
    role: Role;
    canChat: boolean;
    canCreateSummary: boolean;
    canGenerateChanges: boolean;
    canAutoApply: boolean;
    canViewAuditLogs: boolean;  // New permission
}
```

---

## API Reference

### Swagger Documentation

Start the backend and visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Endpoints Summary

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/public-url` | GET | Get ngrok URL if available |
| `/generate-changes` | POST | Generate code changes |
| `/summary` | POST | Generate chat summary |
| `/policy/evaluate-auto-apply` | POST | Evaluate auto-apply policy |
| `/audit/log-apply` | POST | Log an applied change |
| `/audit/logs` | GET | Get audit logs |
| `/ws/chat/{room_id}` | WebSocket | Real-time chat |

---

<a name="中文"></a>
## 中文

本文档提供 Conductor 项目架构、设计思想和扩展指南的完整说明。

## 目录

1. [设计理念](#设计理念)
2. [系统概览](#系统概览)
3. [后端架构](#后端架构)
4. [前端架构](#前端架构)
5. [状态管理](#状态管理)
6. [代码生成流程](#代码生成流程)
7. [扩展指南](#扩展指南)
8. [API 参考](#api-参考)

---

## 设计理念

### 核心原则

1. **关注点分离**: 前端（VS Code 扩展）、后端（FastAPI）和通信层（WebSocket/REST）之间有清晰的边界。

2. **状态机驱动**: 扩展使用有限状态机（FSM）管理生命周期状态，确保行为可预测且易于测试。

3. **可插拔代理架构**: 后端使用可插拔的代理模式，允许在 MockAgent（测试）和基于 LLM 的代理（生产）之间轻松切换。

4. **基于策略的安全**: 自动应用功能使用可配置的策略来限制影响范围并保护关键路径。

5. **审计追踪**: 所有应用的更改都记录到 DuckDB 以符合合规要求和调试需求。

---

## 系统概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           VS Code 扩展                                   │
│  ┌──────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  状态机          │  │  会话服务       │  │  权限服务               │ │
│  │  (FSM)           │  │  (Room/User)    │  │  (基于角色的访问控制)   │ │
│  └────────┬─────────┘  └────────┬────────┘  └────────────┬────────────┘ │
│           │                     │                        │              │
│  ┌────────▼─────────────────────▼────────────────────────▼────────────┐ │
│  │                     ConductorController                             │ │
│  │  - 协调 FSM、会话和后端交互                                         │ │
│  └────────────────────────────────────┬───────────────────────────────┘ │
│                                       │                                  │
│  ┌────────────────────────────────────▼───────────────────────────────┐ │
│  │                         WebView (chat.html)                         │ │
│  │  - 聊天界面、待处理更改卡片、角色徽章                               │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────┬─────────────────────────────────┘
                                        │ HTTP REST / WebSocket
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           后端 (FastAPI)                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │   /chat     │  │  /summary   │  │  /agent     │  │    /policy      │ │
│  │ (WebSocket) │  │  (REST)     │  │  (REST)     │  │    (REST)       │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───────┬─────────┘ │
│         │                │                │                  │          │
│  ┌──────▼────────────────▼────────────────▼──────────────────▼────────┐ │
│  │                     共享服务                                        │ │
│  │  - ConnectionManager (聊天室管理)                                   │ │
│  │  - MockAgent / LLM Agent (代码生成)                                 │ │
│  │  - AutoApplyPolicy (安全评估)                                       │ │
│  │  - AuditLogService (DuckDB 持久化)                                  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 后端架构

### 模块结构

```
backend/app/
├── main.py              # 应用入口、生命周期、路由注册
├── config.py            # YAML 配置加载器
├── ngrok_service.py     # Ngrok 隧道管理
│
├── agent/               # 代码生成
│   ├── router.py        # POST /generate-changes
│   ├── schemas.py       # ChangeSet, FileChange, Range 模型
│   ├── mock_agent.py    # 确定性测试代理
│   └── style_loader.py  # 代码风格指南（未来）
│
├── chat/                # 实时聊天
│   ├── router.py        # WebSocket /ws/chat/{room_id}
│   └── manager.py       # ConnectionManager (房间、用户、历史)
│
├── policy/              # 自动应用安全
│   ├── router.py        # POST /policy/evaluate-auto-apply
│   └── auto_apply.py    # PolicyResult, 规则评估
│
├── summary/             # 聊天摘要
│   ├── router.py        # POST /summary
│   └── schemas.py       # SummaryRequest, SummaryResponse
│
└── audit/               # 更改审计
    ├── router.py        # POST /audit/log-apply, GET /audit/logs
    ├── schemas.py       # AuditLogEntry, ApplyMode
    └── service.py       # DuckDB 存储服务
```

### 核心组件

#### 1. 代理系统 (`agent/`)

代理系统根据指令生成代码更改。

**当前实现: MockAgent**
- 生成确定性更改用于测试
- 在目标目录创建 `helper.py` 和 `config.py`
- 向目标文件添加 import 语句

**未来: LLM Agent**
- 使用 `style_loader.py` 注入代码风格指南
- 调用 LLM API (OpenAI, Claude 等)
- 将结构化输出解析为 ChangeSet

```python
# agent/schemas.py - 核心数据结构

class ChangeType(str, Enum):
    REPLACE_RANGE = "replace_range"  # 修改现有文件
    CREATE_FILE = "create_file"      # 创建新文件

class Range(BaseModel):
    start: int  # 基于1，包含
    end: int    # 基于1，包含

class FileChange(BaseModel):
    id: str                       # 用于追踪的 UUID
    file: str                     # 相对路径
    type: ChangeType
    range: Optional[Range]        # 用于 replace_range
    content: Optional[str]        # 新内容
    original_content: Optional[str]

class ChangeSet(BaseModel):
    changes: List[FileChange]     # 1-10 个文件
    summary: str                  # 人类可读的描述
```

#### 2. 聊天系统 (`chat/`)

具有房间隔离的实时 WebSocket 聊天。

**ConnectionManager 功能:**
- 多个独立状态的房间
- 消息历史持久化（内存中）
- 自动命名用户注册 (Guest 1, Guest 2...)
- 头像颜色分配
- 广播消息

```python
# chat/manager.py - 关键类

class UserRole(str, Enum):
    HOST = "host"        # 可以结束会话，使用 AI 功能
    ENGINEER = "engineer" # 仅聊天

class RoomUser(BaseModel):
    userId: str
    displayName: str
    role: UserRole
    avatarColor: str

class ChatMessage(BaseModel):
    id: str              # 自动生成的 UUID
    roomId: str
    userId: str
    displayName: str
    role: UserRole
    content: str
    ts: float            # Unix 时间戳
```

**WhatsApp 风格功能:**

| 功能 | 描述 |
|------|------|
| 💬 **输入指示器** | 显示"用户正在输入..."当有人正在输入时 |
| ✓ **消息状态** | 勾号（✓ 已发送）表示消息发送状态 |
| 📅 **日期分隔符** | "今天"、"昨天"或日期用于消息分组 |
| 👤 **消息分组** | 同一用户在 5 分钟内的连续消息会被分组 |
| 📎 **文件共享** | 支持图片、PDF、音频文件，最大 20MB |
| 📥 **文件下载** | 在 VS Code 或 Web 浏览器中点击下载文件 |
| 📱 **移动端支持** | Web 版本响应式设计，支持移动设备 |

**WebSocket 消息类型:**
| 类型 | 方向 | 描述 |
|------|------|------|
| `history` | 服务器→客户端 | 初始消息，包含聊天历史和用户列表 |
| `join` | 客户端→服务器 | 在房间中注册用户 |
| `message` | 双向 | 聊天文本消息 |
| `file` | 服务器→客户端 | 文件上传通知 |
| `typing` | 双向 | 输入指示器 (isTyping: true/false) |
| `user_joined` | 服务器→客户端 | 用户加入通知 |
| `user_left` | 服务器→客户端 | 用户离开通知 |
| `session_ended` | 服务器→客户端 | Host 结束会话 |
| `end_session` | 客户端→服务器 | Host 结束会话 |

**WebSocket 优化:**
| 优化 | 描述 |
|------|------|
| 🚀 **并发广播** | 使用 `asyncio.gather()` 并发发送消息到所有客户端，而非顺序发送 |
| 💓 **Ping/Pong 心跳** | Uvicorn 在协议层处理 ping/pong（20 秒间隔，20 秒超时） |
| 🔄 **自动清理** | 广播时自动移除失败的连接 |
| 📊 **高效序列化** | JSON 消息只序列化一次，发送给所有客户端 |

**Uvicorn WebSocket 配置:**
```bash
# 通过 Makefile 或命令行配置
uvicorn app.main:app --ws-ping-interval 20.0 --ws-ping-timeout 20.0
```

**高级聊天功能:**
| 功能 | 描述 |
|------|------|
| 🔄 **智能重连** | 指数退避（1秒基础，30秒上限）+ ±20% 抖动，防止惊群效应 |
| 📨 **消息恢复** | 重连时客户端发送 `?since=<timestamp>` 恢复错过的消息 |
| 🔁 **消息去重** | 服务器使用 LRU 缓存（10,000 条），客户端使用 Set 防止重复 |
| 📄 **消息分页** | `GET /chat/{room_id}/history?before=<ts>&limit=50` 懒加载历史消息 |
| ✓✓ **读取回执** | Intersection Observer 检测可见消息（50% 阈值），广播 `read_receipt` |

**WebSocket 消息类型（扩展）:**
| 类型 | 方向 | 描述 |
|------|------|------|
| `read` | 客户端→服务器 | 客户端发送当消息可见时 |
| `read_receipt` | 服务器→客户端 | 服务器广播 `messageId` 和 `readBy` 数组 |

**分页 API:**
```bash
# 获取更早的消息（游标分页）
GET /chat/{room_id}/history?before=1707321600.123&limit=50

# 响应
{
  "messages": [...],
  "hasMore": true
}
```

#### 3. 策略系统 (`policy/`)

自动应用功能的安全评估。

**默认规则:**
| 规则 | 限制 | 原因 |
|------|------|------|
| `max_files` | ≤ 2 | 限制影响范围 |
| `max_lines_changed` | ≤ 50 | 保持更改可审查 |
| `forbidden_paths` | `infra/`, `db/`, `security/` | 保护关键代码 |

```python
# policy/auto_apply.py

class AutoApplyPolicy:
    def evaluate(self, change_set: ChangeSet) -> PolicyResult:
        reasons = []

        # 规则 1: 检查文件数量
        if len(change_set.changes) > self.max_files:
            reasons.append(f"文件过多: {len(change_set.changes)}")

        # 规则 2: 检查行数
        total_lines = self._count_lines_changed(change_set)
        if total_lines > self.max_lines_changed:
            reasons.append(f"行数过多: {total_lines}")

        # 规则 3: 检查禁止路径
        forbidden = self._find_forbidden_files(change_set)
        if forbidden:
            reasons.append(f"禁止的路径: {forbidden}")

        return PolicyResult(allowed=len(reasons) == 0, reasons=reasons)
```

#### 4. 审计系统 (`audit/`)

基于 DuckDB 的更改日志记录以符合合规要求。

**数据库 Schema:**
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY,
    room_id VARCHAR NOT NULL,
    summary_id VARCHAR,
    changeset_hash VARCHAR NOT NULL,  -- SHA-256 (16 字符)
    applied_by VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,            -- 'manual' 或 'auto'
    timestamp TIMESTAMP NOT NULL
);
```

---

## 前端架构

### 模块结构

```
extension/src/
├── extension.ts              # 入口点、激活、命令
│
└── services/
    ├── conductorStateMachine.ts  # 纯 FSM (无 VS Code 依赖)
    ├── conductorController.ts    # 编排层
    ├── session.ts                # 房间/用户管理
    ├── permissions.ts            # 基于角色的访问控制
    ├── diffPreview.ts            # Diff 显示和应用
    └── backendHealthCheck.ts     # 后端连接性检查

extension/media/
├── chat.html           # WebView UI (Tailwind CSS)
├── tailwind.css        # 编译后的样式
└── input.css           # Tailwind 源文件
```

### 核心组件

#### 1. 状态机 (`conductorStateMachine.ts`)

无外部依赖的纯 FSM，完全可单元测试。

**状态:**
```
Idle → BackendDisconnected ← (任何状态在 BACKEND_LOST 时)
  ↓           ↓ (仅加入模式)
ReadyToHost ←→ Hosting
  ↓
Joining → Joined
```

**状态图:**
```
┌──────┐ BACKEND_CONNECTED ┌─────────────┐
│ Idle │─────────────────▶│ ReadyToHost │
└──────┘                   └──────┬──────┘
    │                             │
    │ BACKEND_LOST        START_HOSTING / JOIN_SESSION
    ▼                             │
┌────────────────────┐            ▼
│ BackendDisconnected│◀───────┌─────────┐
└─────────┬──────────┘        │ Hosting │
          │                   └────┬────┘
          │                        │
    JOIN_SESSION             STOP_HOSTING
    (仅加入模式)                   │
          │                        │
          ▼                        │
    ┌─────────┐                    │
    │ Joining │◀───────────────────┘
    └────┬────┘
         │
  JOIN_SUCCEEDED
         │
         ▼
    ┌────────┐
    │ Joined │
    └────────┘
```

> **注意:** `BackendDisconnected` 状态支持"仅加入模式"。即使本地后端未运行，用户仍可以加入其他人的会话。只有"启动会话"（托管）功能需要本地后端。

**事件表:**
| 事件 | 源状态 | 目标状态 |
|------|--------|----------|
| BACKEND_CONNECTED | Idle, BackendDisconnected | ReadyToHost |
| BACKEND_LOST | 任何 (除 Idle) | BackendDisconnected |
| START_HOSTING | ReadyToHost | Hosting |
| STOP_HOSTING | Hosting | ReadyToHost |
| JOIN_SESSION | ReadyToHost, **BackendDisconnected** | Joining |
| JOIN_SUCCEEDED | Joining | Joined |
| JOIN_FAILED | Joining | ReadyToHost |
| LEAVE_SESSION | Joined | ReadyToHost |

#### 2. 控制器 (`conductorController.ts`)

连接 FSM 与副作用的编排层。

```typescript
class ConductorController {
    // 注入依赖以便测试
    constructor(
        fsm: ConductorStateMachine,
        healthCheck: HealthCheckFn,      // async (url) => boolean
        urlProvider: UrlProviderFn,      // () => string
        sessionReset: SessionResetFn,    // () => string (新 roomId)
    ) {}

    // 公共 API
    async start(): Promise<ConductorState>      // 健康检查 → Ready/Disconnected
    startHosting(): string                       // → Hosting, 返回 roomId
    stopHosting(): void                          // → ReadyToHost
    startJoining(inviteUrl: string): ParsedInvite // → Joining
    joinSucceeded(): void                        // → Joined
    joinFailed(): void                           // → ReadyToHost
    leaveSession(): void                         // → ReadyToHost
}
```

#### 3. 会话服务 (`session.ts`)

跨重载管理房间和用户身份。

**持久化:** 使用 VS Code 的 `globalState` API

**关键数据:**
- `roomId`: 协作房间的 UUID
- `hostId`: 主机的机器 ID
- `userId`: 此用户的 UUID
- `liveShareUrl`: VS Code Live Share URL
- `ngrokUrl`: 检测到的 ngrok 隧道 URL

#### 4. 权限服务 (`permissions.ts`)

UI 功能的基于角色的访问控制。

**权限矩阵:**
| 功能 | Lead | Member |
|------|------|--------|
| chat | ✅ | ✅ |
| createSummary | ✅ | ❌ |
| generateChanges | ✅ | ❌ |
| autoApply | ✅ | ❌ |

---

## 状态管理

### 前端状态流

```
用户操作 → Controller → FSM → 状态变化 → WebView 更新
                 ↓
            副作用 (健康检查、Live Share 等)
```

### 后端状态 (每个房间)

```
WebSocket 连接 → ConnectionManager.connect()
      ↓
加入消息 → ConnectionManager.register_user()
      ↓
聊天消息 → ConnectionManager.add_message() → broadcast()
      ↓
断开连接 → ConnectionManager.disconnect()
```

---

## 代码生成流程

```
1. 用户点击"生成更改"
        ↓
2. 扩展发送 POST /generate-changes
   {file_path, instruction, file_content}
        ↓
3. 后端调用 MockAgent.generate_changes()
        ↓
4. Agent 返回包含 FileChanges 的 ChangeSet
        ↓
5. 扩展评估策略 (POST /policy/evaluate-auto-apply)
        ↓
6. 扩展显示第一个更改的 diff 预览
        ↓
7. 用户审查并点击"应用"或"放弃"
        ↓
8. DiffPreviewService.applySingleChange()
        ↓
9. 扩展记录到审计 (POST /audit/log-apply)
        ↓
10. 显示下一个更改或完成
```

---

## 扩展指南

### 添加新代理

1. **创建代理类:**
```python
# backend/app/agent/llm_agent.py
class LLMAgent:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate_changes(self, request: GenerateChangesRequest) -> GenerateChangesResponse:
        # 1. 加载代码风格指南
        style = StyleLoader.load_for_file(request.file_path)

        # 2. 构建提示
        prompt = self._build_prompt(request, style)

        # 3. 调用 LLM
        response = self.client.chat.completions.create(...)

        # 4. 将响应解析为 ChangeSet
        change_set = self._parse_response(response)

        return GenerateChangesResponse(success=True, change_set=change_set)
```

2. **在路由中注册:**
```python
# backend/app/agent/router.py
from .llm_agent import LLMAgent

agent = LLMAgent(api_key=config.llm.api_key, model=config.llm.model)

@router.post("/generate-changes")
async def generate_changes(request: GenerateChangesRequest):
    return agent.generate_changes(request)
```

### 添加新策略规则

1. **向 AutoApplyPolicy 添加规则:**
```python
# backend/app/policy/auto_apply.py

# 新常量
ALLOWED_EXTENSIONS = (".py", ".ts", ".js", ".json")

class AutoApplyPolicy:
    def evaluate(self, change_set: ChangeSet) -> PolicyResult:
        reasons = []

        # 现有规则...

        # 新规则: 检查文件扩展名
        disallowed = self._find_disallowed_extensions(change_set)
        if disallowed:
            reasons.append(f"不允许的文件类型: {disallowed}")

        return PolicyResult(allowed=len(reasons) == 0, reasons=reasons)

    def _find_disallowed_extensions(self, change_set: ChangeSet) -> List[str]:
        disallowed = []
        for change in change_set.changes:
            if not change.file.endswith(ALLOWED_EXTENSIONS):
                disallowed.append(change.file)
        return disallowed
```

2. **添加测试:**
```python
# backend/tests/test_auto_apply_policy.py
def test_disallowed_extension():
    policy = AutoApplyPolicy()
    change_set = ChangeSet(changes=[
        FileChange(file="script.sh", type=ChangeType.CREATE_FILE, content="#!/bin/bash")
    ])
    result = policy.evaluate(change_set)
    assert not result.allowed
    assert "不允许的文件类型" in result.reasons[0]
```

### 添加新 FSM 状态

1. **添加状态和事件:**
```typescript
// extension/src/services/conductorStateMachine.ts

export enum ConductorState {
    // 现有状态...
    Reviewing = 'Reviewing',  // 新状态
}

export enum ConductorEvent {
    // 现有事件...
    START_REVIEW = 'START_REVIEW',
    FINISH_REVIEW = 'FINISH_REVIEW',
}

const TRANSITION_TABLE = {
    // 现有转换...
    [`${ConductorState.Hosting}:${ConductorEvent.START_REVIEW}`]: ConductorState.Reviewing,
    [`${ConductorState.Reviewing}:${ConductorEvent.FINISH_REVIEW}`]: ConductorState.Hosting,
};
```

2. **添加控制器方法:**
```typescript
// extension/src/services/conductorController.ts

startReview(): void {
    this._fsm.transition(ConductorEvent.START_REVIEW);
}

finishReview(): void {
    this._fsm.transition(ConductorEvent.FINISH_REVIEW);
}
```

3. **更新 WebView:**
```html
<!-- extension/media/chat.html -->
<div id="review-panel" class="hidden">
    <!-- 审查界面 -->
</div>

<script>
    function updateUI(state) {
        if (state === 'Reviewing') {
            document.getElementById('review-panel').classList.remove('hidden');
        }
    }
</script>
```

### 添加新权限

1. **更新权限矩阵:**
```typescript
// extension/src/services/permissions.ts

export type Feature =
    | 'chat'
    | 'createSummary'
    | 'generateChanges'
    | 'autoApply'
    | 'viewAuditLogs';  // 新功能

const PERMISSION_MATRIX: Record<Role, Set<Feature>> = {
    lead: new Set([
        'chat', 'createSummary', 'generateChanges', 'autoApply', 'viewAuditLogs'
    ]),
    member: new Set(['chat'])
};
```

2. **更新 WebView 接口:**
```typescript
export interface WebViewPermissions {
    role: Role;
    canChat: boolean;
    canCreateSummary: boolean;
    canGenerateChanges: boolean;
    canAutoApply: boolean;
    canViewAuditLogs: boolean;  // 新权限
}
```

---

## API 参考

### Swagger 文档

启动后端并访问:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### 端点摘要

| 端点 | 方法 | 描述 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/public-url` | GET | 获取 ngrok URL（如果可用） |
| `/generate-changes` | POST | 生成代码更改 |
| `/summary` | POST | 生成聊天摘要 |
| `/policy/evaluate-auto-apply` | POST | 评估自动应用策略 |
| `/audit/log-apply` | POST | 记录已应用的更改 |
| `/audit/logs` | GET | 获取审计日志 |
| `/ws/chat/{room_id}` | WebSocket | 实时聊天 |

---

