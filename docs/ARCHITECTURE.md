# Conductor Architecture

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This document describes the architecture of the Conductor system: a VS Code extension frontend and a FastAPI backend.

## System Overview

```
┌───────────────────────┐  WebSocket  ┌───────────────────────┐
│   VS Code Extension      ├───────────┤   FastAPI Backend        │
│                         │  REST/HTTP │                         │
│  CollabPanel (WebView)  ├───────────┤  Routers:               │
│  WorkspacePanel         │           │  - chat                  │
│  SessionFSM             │           │  - workspace             │
│  FileSystemProvider     │           │  - files                 │
│  WorkspaceClient        │           │  - ai                    │
│  WebSocketService       │           │  - changes               │
└───────────────────────┘           └───────────────────────┘
                                                  │
                                         ┌───────┴───────┐
                                         │  Git Worktrees  │
                                         │  worktrees/     │
                                         │  {room_id}/     │
                                         └───────────────┘
```

## Extension Architecture

### Session FSM

The extension manages session lifecycle through a Finite State Machine (FSM). All state transitions are explicit and validated.

```
                    ┌──────────┐
                    │   Idle   │
                    └────┬───┘
                         │ setReadyToHost()
                         ▼
                    ┌──────────────┐
                    │ ReadyToHost  │
                    └─────┬─────┘
                          │ startCreatingWorkspace()
                          ▼
                    ┌───────────────────┐
                    │  CreatingWorkspace  │
                    └──────┬─────────┘
                           │ workspaceReady() or creationFailed()
               ┌─────────┼─────────┐
               ▼                    ▼
         ┌─────────┐      ┌────────────┐
         │ Hosting │      │ ReadyToHost  │ (retry)
         └────┬───┘      └────────────┘
              │ leaveSession()
              ▼
         ┌─────────┐
         │   Idle  │
         └─────────┘
```

**Model A FSM States:**

| State | Entry Condition | Exit Transitions |
|-------|----------------|------------------|
| `Idle` | Initial / after leave | `setReadyToHost()` → `ReadyToHost` |
| `ReadyToHost` | Host mode selected | `startCreatingWorkspace()` → `CreatingWorkspace` |
| `CreatingWorkspace` | Wizard launched | `workspaceReady()` → `Hosting`; `creationFailed()` → `ReadyToHost` |
| `Hosting` | Workspace provisioned | `leaveSession()` → `Idle` |
| `Joined` | Joined another host | `leaveSession()` → `Idle` |
| `BackendDisconnected` | WebSocket lost | `backendReconnected()` → `Idle` |

### FileSystemProvider Design

The `FileSystemProvider` implements VS Code's `vscode.FileSystemProvider` interface to expose remote worktree files under the `conductor://` URI scheme.

```
VS Code Explorer                 FileSystemProvider          Backend REST
     │                                 │                        │
     │  readFile(uri)                  │                        │
     ├────────────────────────────►│                        │
     │                                 │  GET /workspace/{id}/  │
     │                                 ├──────────────────────►│
     │                                 │   file?path=src/main   │
     │                                 │◄──────────────────────┤
     │  returns Uint8Array              │   200 { content: ... } │
     ◄────────────────────────────┤
```

**URI Mapping:**
```
conductor://{room_id}/src/main.py
         └────────┘└───────────┘
           room_id      file path in worktree
```

**Methods implemented:**

| Method | VS Code API | Backend Call |
|--------|------------|-------------|
| `readFile` | `FileSystemProvider.readFile` | `GET /workspace/{id}/file` |
| `writeFile` | `FileSystemProvider.writeFile` | `PUT /workspace/{id}/file` |
| `delete` | `FileSystemProvider.delete` | `DELETE /workspace/{id}/file` |
| `rename` | `FileSystemProvider.rename` | `DELETE` old + `PUT` new |
| `readDirectory` | `FileSystemProvider.readDirectory` | `GET /workspace/{id}/files` |
| `stat` | `FileSystemProvider.stat` | Derived from file listing |

### WorkspacePanel Flow

The 5-step workspace creation wizard using native VS Code `InputBox`:

```
Step 1: Enter repository URL
        ↓ (validate HTTPS or SSH format)
Step 2: Enter branch name
        ↓ (validate branch name format)
Step 3: Enter Personal Access Token
        ↓ (non-empty, masked input)
Step 4: Confirm (show repo/branch/token preview)
        ↓ (user confirms or cancels)
Step 5: POST /workspace/create
        ↓ (success or error message)
     FSM.workspaceReady() / creationFailed()
```

## Backend Architecture

### Workspace Service

The `workspace_service.py` manages Git operations:

```python
class WorkspaceService:
    def create_workspace(room_id, repo_url, token, base_branch):
        # 1. Clone bare repo to repos/{room_id}.git
        # 2. Create worktree at worktrees/{room_id}/
        # 3. Checkout new branch session/{room_id}
        # 4. Store mapping: room_id -> worktree path
        
    def read_file(room_id, file_path):
        # Reads file from worktrees/{room_id}/{file_path}
        
    def write_file(room_id, file_path, content):
        # Writes to worktrees/{room_id}/{file_path}
        
    def commit_and_push(room_id, message):
        # git add -A && git commit -m message && git push
        
    def search_files(room_id, query):
        # grep -r query worktrees/{room_id}/
        # returns: List[{file_path, line, content}]
```

### Router Layer

```python
# routers/workspace.py

@router.post("/workspace/create")
async def create_workspace(req: CreateWorkspaceRequest):
    # Delegates to workspace_service.create_workspace()
    
@router.get("/workspace/{room_id}/search")
async def search_workspace(room_id: str, q: str):
    # Delegates to workspace_service.search_files()
    # Returns: List[SearchResult]
    
@router.get("/workspace/{room_id}/file")
async def read_file(room_id: str, path: str):
    # Returns file content as text
```

### Data Flow: Workspace Creation

```
Extension (WorkspacePanel)
    │
    │ POST /workspace/create
    │ { room_id, repo_url, token, base_branch }
    ▼
FastAPI (workspace router)
    │
    │ workspace_service.create_workspace()
    ▼
Git Operations
    │
    ├─ git clone --bare {repo_url} repos/{room_id}.git
    ├─ git worktree add worktrees/{room_id} -b session/{room_id}
    └─ store mapping
    │
    ▼
Response { status: "created", worktree_path: "..." }
    │
    ▼
Extension FSM.workspaceReady()
    │
    ▼
FileSystemProvider registered at conductor://{room_id}/
```

### Data Flow: Code Search

```
Extension (WebView Ctrl+Shift+F)
    │
    │ WorkspaceClient.searchCode(roomId, query)
    │ GET /workspace/{room_id}/search?q={query}
    ▼
FastAPI (workspace router)
    │
    │ workspace_service.search_files(room_id, query)
    ▼
Grep in worktrees/{room_id}/
    │
    ▼
Response: [{ file_path, line, content }, ...]
    │
    ▼
Extension: display results in inline search panel
```

## Dependency Map

```
extension/src/extension.ts
    ├── panels/collabPanel.ts
    │       ├── services/sessionFSM.ts
    │       ├── services/webSocketService.ts
    │       └── services/fileUploadService.ts
    ├── panels/workspacePanel.ts
    │       └── services/workspaceClient.ts
    └── services/fileSystemProvider.ts
            └── services/workspaceClient.ts

backend/main.py
    ├── routers/chat.py
    ├── routers/workspace.py
    │       └── services/workspace_service.py
    ├── routers/files.py
    ├── routers/ai.py
    │       └── services/ai_service.py
    └── routers/changes.py
```

## Configuration

### Backend Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_HOST` | `0.0.0.0` | Bind address |
| `BACKEND_PORT` | `8000` | Port |
| `GIT_WORKSPACE_ROOT` | `/tmp/conductor_workspaces` | Worktree storage root |
| `GIT_WORKSPACE_ENABLED` | `false` | Enable Git workspace feature |

### Extension VS Code Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `conductor.backendUrl` | `http://localhost:8000` | Backend base URL |
| `conductor.enableWorkspace` | `true` | Enable workspace features |

## Security Considerations

- **Path traversal**: All file paths are validated against the worktree root before access. Paths containing `..` are rejected with HTTP 400.
- **Token storage**: PATs are held in memory only during the session; not persisted to disk by the extension.
- **Git isolation**: Each room's worktree is on a dedicated branch (`session/{room_id}`). Rooms cannot access each other's files.
- **File size limits**: File read/write operations enforce a 10MB per-file limit on the backend.

---

<a name="中文"></a>
## 中文

本文档描述 Conductor 系统架构：VS Code 扩展前端和 FastAPI 后端。

## 系统概览

同上方系统概览图。

## 扩展架构

### 会话 FSM

扩展通过有限状态机（FSM）管理会话生命周期，所有状态转换都是显式且经过验证的。

**Model A FSM 状态表:**

| 状态 | 进入条件 | 退出转换 |
|-------|----------------|------------------|
| `Idle` | 初始 / 离开后 | `setReadyToHost()` → `ReadyToHost` |
| `ReadyToHost` | 选择主机模式 | `startCreatingWorkspace()` → `CreatingWorkspace` |
| `CreatingWorkspace` | 向导已启动 | `workspaceReady()` → `Hosting`；`creationFailed()` → `ReadyToHost` |
| `Hosting` | 工作区已配置 | `leaveSession()` → `Idle` |
| `Joined` | 已加入其他主机 | `leaveSession()` → `Idle` |
| `BackendDisconnected` | WebSocket 断开 | `backendReconnected()` → `Idle` |

### FileSystemProvider 设计

`FileSystemProvider` 实现 VS Code `vscode.FileSystemProvider` 接口，将远程 worktree 文件暴露在 `conductor://` URI 方案下。

**已实现方法：**

| 方法 | VS Code API | 后端调用 |
|--------|------------|-------------|
| `readFile` | `FileSystemProvider.readFile` | `GET /workspace/{id}/file` |
| `writeFile` | `FileSystemProvider.writeFile` | `PUT /workspace/{id}/file` |
| `delete` | `FileSystemProvider.delete` | `DELETE /workspace/{id}/file` |
| `rename` | `FileSystemProvider.rename` | `DELETE` 旧路径 + `PUT` 新路径 |
| `readDirectory` | `FileSystemProvider.readDirectory` | `GET /workspace/{id}/files` |
| `stat` | `FileSystemProvider.stat` | 由文件列表推导 |

## 后端架构

### 工作区服务

`workspace_service.py` 管理 Git 操作：创建工作区、读写文件、提交推送、搜索文件。

### 配置

- Git 工作区默认未启用（`git_workspace.enabled: false`）
