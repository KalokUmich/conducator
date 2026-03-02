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
  routers/          HTTP route handlers
  services/         Business logic (workspace, auth, AI)
  models/           Pydantic schemas
```

### Quick Start

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload

# Extension
cd extension
npm install
npm run compile
# Press F5 in VS Code to launch Extension Development Host
```

### Testing

See [TESTING.md](TESTING.md) for the full test guide.

```bash
# Backend
cd backend && pytest

# Extension
cd extension && npm test
```

---

<a name="中文"></a>
## 中文

Conductor 是一个 VS Code 协作扩展加 FastAPI 后端，支持团队聊天、Git 工作区管理、文件共享和 AI 辅助决策/代码工作流。

### 当前功能

- VS Code WebView 协作面板，FSM 驱动的会话生命周期：
  - `Idle`（空闲）
  - `BackendDisconnected`（仅加入模式）
  - `ReadyToHost`（准备托管）
  - `CreatingWorkspace`（在后端配置 Git worktree）
  - `Hosting`（托管中）
  - `Joining`（加入中）
  - `Joined`（已加入）
- Git 工作区管理（替代 Live Share）：
  - 每个房间独立的裸仓库 + worktree 隔离（每个房间获得独立 Git 分支 `session/{room_id}`）
  - 模式 A：通过 GIT_ASKPASS 进行令牌认证（用户提供个人访问令牌）
  - 模式 B：委托认证（VS Code 扩展代表后端执行 Git 操作）
  - 文件同步广播（带防抖）；从后端提交和推送
  - **FileSystemProvider**（`conductor://` URI 方案）：远程 worktree 文件在 VS Code 资源管理器中显示为本地文件；完整的读/写/删除/重命名支持，由后端 REST API 支持
  - **WorkspacePanel**：5 步原生 VS Code 输入向导（无 WebView）
  - **WorkspaceClient**：所有 `/workspace/` 端点的类型化 HTTP 客户端
- 实时 WebSocket 聊天：
  - 重连恢复（`since`）
  - 输入指示器
  - 已读回执
  - 消息去重
  - 分页历史
- 文件上传/下载（20MB 限制，扩展主机上传代理，重复检测，重试逻辑）
- 代码片段共享 + 编辑器导航
- 变更审查工作流：
  - `POST /generate-changes`（MockAgent）
  - 策略检查（`POST /policy/evaluate-auto-apply`）
  - 每个变更的差异预览
  - 顺序应用/跳过
  - 审计日志（`POST /audit/log-apply`）
- AI 提供者工作流：
  - 提供者健康/状态（`GET /ai/status`）
  - 四步提供者选择 + 确认界面
  - 流式推理（`POST /ai/infer`）
- 工作区代码搜索：
  - `GET /workspace/{room_id}/search?q=...` — 在会话 worktree 的所有文件中进行全文搜索
  - 结果包括文件路径、行号和匹配行内容
  - VS Code 扩展 `WorkspaceClient.searchCode()` 方法
  - 键盘快捷键 `Ctrl+Shift+F` / `Cmd+Shift+F` 在 WebView 中打开内联搜索面板

### 架构

```
extension/          VS Code 扩展（TypeScript）
  src/
    panels/         WebView 面板（CollabPanel、WorkspacePanel）
    services/       FSM、WebSocket、FileSystemProvider、WorkspaceClient
    commands/       VS Code 命令处理器
backend/            FastAPI 服务器（Python）
  routers/          HTTP 路由处理器
  services/         业务逻辑（工作区、认证、AI）
  models/           Pydantic 模式
```

### 快速开始

```bash
# 后端
cd backend
pip install -r requirements.txt
uvicorn main:app --reload

# 扩展
cd extension
npm install
npm run compile
# 在 VS Code 中按 F5 启动扩展开发主机
```

### 测试

查看 [TESTING.md](TESTING.md) 了解完整测试指南。

```bash
# 后端
cd backend && pytest

# 扩展
cd extension && npm test
```
