# Conductor

[English](#english) | [中文](#中文)

---

## English

### What is Conductor?

Conductor is an AI-powered coding assistant backend. It provides:

- **Git Workspace Management** — clone, list, update, delete, and diff git repositories in isolated workspace directories
- **Code Search** — semantic vector search over indexed repositories using [CocoIndex](https://github.com/cocoindex-io/cocoindex) and local embedding models
- **Context Enrichment** — given a query, retrieve the most relevant code snippets from indexed workspaces
- **DuckDB Integration** — durable storage for conversation history
- **Claude API Integration** — route prompts to Anthropic's Claude models
- **Configurable Prompts & Personas** — YAML-driven system prompts and multi-turn conversation management

### Architecture Overview

```
backend/
├── main.py                  # FastAPI app entry point
├── config.py                # Configuration models (Pydantic)
├── git_workspace.py         # Git workspace manager
├── code_search.py           # CocoIndex-based semantic code search
├── routers/
│   ├── git_workspace.py     # REST API: /api/git-workspace/*
│   ├── code_search.py       # REST API: /api/code-search/*
│   └── context.py           # REST API: /api/context
└── tests/
    ├── test_git_workspace.py
    ├── test_code_search.py
    ├── test_context.py
    └── test_config_new.py
```

### Quick Start

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Run the server
uvicorn main:app --reload

# 3. Run tests
pytest tests/ -v
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WORKSPACE_BASE_DIR` | `./workspaces` | Root directory for git workspaces |
| `CODE_SEARCH_DB` | `./code_search.db` | SQLite-vec database path |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local sentence-transformer model |
| `ANTHROPIC_API_KEY` | _(required)_ | Anthropic API key for Claude |

### Running Tests

```bash
pytest backend/tests/ -v --tb=short
```

See [TESTING.md](TESTING.md) for full test documentation.

---

## 中文

### Conductor 是什么？

Conductor 是一个 AI 驱动的编程助手后端，提供：

- **Git 工作区管理** — 克隆、列出、更新、删除和对比 Git 仓库
- **代码搜索** — 使用 [CocoIndex](https://github.com/cocoindex-io/cocoindex) 和本地嵌入模型进行语义向量搜索
- **上下文丰富** — 根据查询，从已索引的工作区检索最相关的代码片段
- **DuckDB 集成** — 持久化存储对话历史
- **Claude API 集成** — 将提示词路由到 Anthropic 的 Claude 模型
- **可配置提示词与角色** — 基于 YAML 的系统提示词和多轮对话管理

### 架构概览

```
backend/
├── main.py                  # FastAPI 应用入口
├── config.py                # 配置模型 (Pydantic)
├── git_workspace.py         # Git 工作区管理器
├── code_search.py           # 基于 CocoIndex 的语义代码搜索
├── routers/
│   ├── git_workspace.py     # REST API: /api/git-workspace/*
│   ├── code_search.py       # REST API: /api/code-search/*
│   └── context.py           # REST API: /api/context
└── tests/
    ├── test_git_workspace.py
    ├── test_code_search.py
    ├── test_context.py
    └── test_config_new.py
```

### 快速开始

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 启动服务器
uvicorn main:app --reload

# 3. 运行测试
pytest tests/ -v
```

### 环境变量

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `WORKSPACE_BASE_DIR` | `./workspaces` | Git 工作区根目录 |
| `CODE_SEARCH_DB` | `./code_search.db` | SQLite-vec 数据库路径 |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | 本地 sentence-transformer 模型 |
| `ANTHROPIC_API_KEY` | _(必填)_ | Anthropic API 密钥 |

### 运行测试

```bash
pytest backend/tests/ -v --tb=short
```

详见 [TESTING.md](TESTING.md)。
