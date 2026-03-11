# Conductor

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

Conductor is a VS Code collaboration extension + FastAPI backend for team chat, Git workspace sharing, file sharing, and **agentic AI code intelligence**.

### Features

- **Agentic Code Intelligence** — LLM agent that iteratively navigates the codebase using 13 code tools (grep, AST search, call graph, git log, ...) to answer questions. No pre-built vector index needed.
- **Git Workspace Management** — per-room bare repo + worktree isolation. Files appear in VS Code explorer via a `conductor://` URI scheme (FileSystemProvider).
- **Real-time Chat** — WebSocket rooms with typing indicators, read receipts, reconnect recovery, and AI message injection.
- **File Sharing** — multipart upload with SHA-256 deduplication, DuckDB-backed metadata.
- **Audit & Todos** — DuckDB-persisted audit log (AI change apply/skip events) and room-scoped TODO tracker.
- **Multi-Provider AI** — Bedrock Converse, Anthropic Direct, OpenAI. ProviderResolver health-checks all configured providers at startup and picks the fastest.
- **LangExtract Integration** — Claude language model plugin for Google's langextract library (structured information extraction).

### Architecture

```
┌──────────────────────────┐     ┌──────────────────────────────────────────┐
│   VS Code Extension      │     │   FastAPI Backend                        │
│                          │     │                                          │
│  ┌──────────────────┐    │ WS  │  ┌───────────────────────────────────┐  │
│  │ SessionFSM       │    │◄────┼──│ WebSocket Manager (rooms/broadcast)│  │
│  │ WebSocketService  │    │     │  └───────────────────────────────────┘  │
│  │ CollabPanel       │    │     │                                          │
│  └──────────────────┘    │     │  ┌───────────────────────────────────┐  │
│                          │     │  │ Agent Loop Service                 │  │
│  ┌──────────────────┐    │HTTP │  │  LLM ←→ 13 Code Tools            │  │
│  │ WorkspaceClient   │◄──┼─────┼──│  (grep, read_file, find_symbol,  │  │
│  │ WorkspacePanel    │    │     │  │   ast_search, get_callers, ...)   │  │
│  │ FileSystemProvider│    │     │  └───────────────────────────────────┘  │
│  └──────────────────┘    │     │                                          │
│                          │     │  ┌───────────────────────────────────┐  │
│                          │     │  │ AI Provider Layer                  │  │
│                          │     │  │  ProviderResolver → health check  │  │
│                          │     │  │  ├─ ClaudeBedrockProvider         │  │
│                          │     │  │  ├─ ClaudeDirectProvider          │  │
│                          │     │  │  └─ OpenAIProvider                │  │
│                          │     │  └───────────────────────────────────┘  │
│                          │     │                                          │
│                          │     │  ┌───────────────────────────────────┐  │
│                          │     │  │ Git Workspace Service              │  │
│                          │     │  │  bare clone → worktree per room   │  │
│                          │     │  └───────────────────────────────────┘  │
│                          │     │                                          │
│                          │     │  ┌───────────────────────────────────┐  │
│                          │     │  │ DuckDB Storage                    │  │
│                          │     │  │  audit_logs / todos / file meta   │  │
│                          │     │  └───────────────────────────────────┘  │
└──────────────────────────┘     └──────────────────────────────────────────┘
```

### 13 Code Tools

| Tool | Description |
|------|-------------|
| `grep` | Regex search (ripgrep) |
| `read_file` | Read file content with line range |
| `list_files` | Directory tree |
| `find_symbol` | AST-based symbol definition |
| `find_references` | All usages of a symbol |
| `file_outline` | All definitions in a file |
| `get_dependencies` | Files this file imports |
| `get_dependents` | Files that import this file |
| `git_log` | Recent commits |
| `git_diff` | Diff between refs |
| `ast_search` | Structural AST search (ast-grep) |
| `get_callees` | Functions called within a function |
| `get_callers` | Functions that call a given function |

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
pytest                                    # all tests
pytest tests/test_code_tools.py -v        # 13 code tools (52 tests)
pytest tests/test_agent_loop.py -v        # agent loop (21 tests)
pytest tests/test_langextract.py -v       # langextract (21 tests)
pytest tests/test_repo_graph.py -v        # repo graph (72 tests)
pytest tests/test_config_new.py -v        # config (60+ tests)
pytest tests/test_git_workspace.py -v     # git workspace
```

### Documentation

- [Backend Guide](docs/GUIDE.md) — code walkthrough (EN + 中文)
- [Roadmap](ROADMAP.md) — project phases and ADRs
- [Claude](CLAUDE.md) — guide for AI coding assistants

---

<a name="中文"></a>
## 中文

Conductor 是一个 VS Code 协作扩展 + FastAPI 后端，用于团队聊天、Git 工作区共享、文件共享和 **Agentic AI 代码智能分析**。

### 功能特性

- **Agentic 代码智能** — LLM agent 通过迭代调用 13 个代码工具（grep、AST 搜索、调用图、git log 等）主动探索代码库，无需预建向量索引。
- **Git 工作区管理** — 每个房间独立的裸仓库 + worktree 隔离。文件通过 `conductor://` URI 方案（FileSystemProvider）出现在 VS Code 文件管理器中。
- **实时聊天** — WebSocket 房间，支持打字指示、已读回执、断线重连和 AI 消息注入。
- **文件共享** — 多部分上传，SHA-256 去重，DuckDB 元数据存储。
- **审计与任务追踪** — DuckDB 持久化审计日志（AI 变更接受/跳过事件）和房间级 TODO 追踪器。
- **多提供商 AI** — Bedrock Converse、Anthropic Direct、OpenAI。`ProviderResolver` 在启动时对所有已配置的提供商做健康检查，自动选择最快的。
- **LangExtract 集成** — Google langextract 库的 Claude 语言模型插件（结构化信息提取）。

### 架构

架构图见上方英文部分。

### 快速开始

```bash
# 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# 扩展
cd extension
npm install
npm run compile
# 在 VS Code 中按 F5 启动扩展开发主机
```

### 运行测试

```bash
cd backend
pytest                                    # 所有测试
pytest tests/test_code_tools.py -v        # 代码工具 (52 项)
pytest tests/test_agent_loop.py -v        # agent loop (21 项)
pytest tests/test_langextract.py -v       # langextract (21 项)
pytest tests/test_repo_graph.py -v        # 仓库图 (72 项)
```

### 配置

在 `backend/config/conductor.secrets.yaml` 中配置凭证：

```yaml
aws:
  access_key_id: "AKIA..."
  secret_access_key: "..."
  region: "us-east-1"
openai:
  api_key: "sk-..."
anthropic:
  api_key: "sk-ant-..."
```

非敏感配置在 `backend/config/conductor.settings.yaml` 中。
