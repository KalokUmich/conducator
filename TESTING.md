# Testing Guide

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

This guide covers running tests for both the backend (Python/pytest) and the extension (TypeScript/VS Code test runner).

## Backend Tests

```bash
cd backend
pytest                             # all tests
pytest -k "test_workspace"         # workspace tests only
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

### Workspace Test Coverage

The workspace tests (`test_workspace.py`) cover:

- `POST /workspace/create` — success, duplicate room, invalid token, missing params
- `GET /workspace/{room_id}/files` — list files, empty worktree, unknown room
- `GET /workspace/{room_id}/file` — read existing, missing path, binary file
- `PUT /workspace/{room_id}/file` — create, overwrite, path traversal rejection
- `DELETE /workspace/{room_id}/file` — delete existing, missing file
- `POST /workspace/{room_id}/commit` — commit with changes, empty commit (no-op)
- `GET /workspace/{room_id}/search` — basic search, no results, multi-file matches

Git operations are mocked via `pytest-mock` to avoid requiring a live Git remote.

## Extension Tests

```bash
cd extension
npm test                           # all tests (launches VS Code test host)
npm run test:unit                  # unit tests only (no VS Code)
npm run lint                       # ESLint check
```

### Test Files

| File | Tests | Coverage |
|------|-------|----------|
| `src/test/sessionFSM.test.ts` | All FSM state transitions | `SessionFSM` |
| `src/test/workspaceClient.test.ts` | HTTP client methods, error handling | `WorkspaceClient` |
| `src/test/fileSystemProvider.test.ts` | read/write/delete/rename, error cases | `FileSystemProvider` |
| `src/test/workspacePanel.test.ts` | Wizard step progression, validation | `WorkspacePanel` |

### Extension Unit Test Details

#### SessionFSM Tests (47 tests)

Covers all valid state transitions and invalid transition errors:

```
Idle -> ReadyToHost (setReadyToHost)
Idle -> BackendDisconnected (backendDisconnected)
ReadyToHost -> CreatingWorkspace (startCreatingWorkspace)
ReadyToHost -> BackendDisconnected (backendDisconnected)
CreatingWorkspace -> Hosting (workspaceReady)
CreatingWorkspace -> ReadyToHost (creationFailed)
Hosting -> Idle (leaveSession)
Joined -> Idle (leaveSession)
BackendDisconnected -> Idle (backendReconnected -> Idle)
```

Invalid transitions throw `InvalidTransitionError`:

```typescript
expect(() => fsm.startCreatingWorkspace()).toThrow(InvalidTransitionError);
// (when FSM is in Idle state)
```

#### WorkspaceClient Tests (68 tests)

Covers all HTTP methods with mocked `fetch`:

- `createWorkspace()` — success 201, conflict 409, server error 500
- `listFiles()` — returns array, empty array, 404 unknown room
- `readFile()` — returns content string, 404 file not found
- `writeFile()` — success 200, path traversal 400
- `deleteFile()` — success 204, 404
- `commitChanges()` — success, no-op (empty diff)
- `searchCode()` — returns matches array, empty results, 404 room

#### FileSystemProvider Tests (83 tests)

Covers VS Code `FileSystemProvider` interface compliance:

- `readFile()` — returns `Uint8Array`, file not found throws `FileNotFound`
- `writeFile()` — creates/overwrites, emits `onDidChangeFile` event
- `delete()` — removes file, emits change event
- `rename()` — moves file, emits change event for both old and new paths
- `readDirectory()` — returns `[name, FileType][]` array
- `stat()` — returns `FileStat` with type, size, mtime
- Error propagation — backend errors map to VS Code `FileSystemError` types

#### WorkspacePanel Tests (33 tests)

Covers the 5-step input wizard:

- Step 1: Repository URL validation (HTTPS, SSH, invalid)
- Step 2: Branch name validation (valid names, reserved names)
- Step 3: Token input (non-empty, masked display)
- Step 4: Confirmation display (shows parsed repo/branch/token preview)
- Step 5: Submit — calls `WorkspaceClient.createWorkspace()`, handles errors
- Cancel at any step — returns to `ReadyToHost` state

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

本指南涵盖后端（Python/pytest）和扩展（TypeScript/VS Code 测试运行器）的测试运行方式。

## 后端测试

```bash
cd backend
pytest                             # 所有测试
pytest -k "test_workspace"         # 仅工作区测试
pytest -v --tb=short               # 详细输出
pytest --cov=. --cov-report=html   # 覆盖率报告
```

### 测试文件

| 文件 | 测试 | 覆盖 |
|------|-------|----------|
| `tests/test_workspace.py` | 工作区 CRUD、Git 操作、搜索 | `/workspace/` 路由 |
| `tests/test_ai.py` | AI 状态、推理、提供者选择 | `/ai/` 路由 |
| `tests/test_chat.py` | WebSocket、历史记录、输入指示器 | `/chat` 路由 |
| `tests/test_files.py` | 上传、下载、重复检测、重试 | `/files/` 路由 |
| `tests/test_changes.py` | MockAgent、策略、审计 | `/generate-changes`、`/policy/`、`/audit/` |

### 工作区测试覆盖

工作区测试（`test_workspace.py`）涵盖：

- `POST /workspace/create` — 成功、重复房间、无效令牌、缺少参数
- `GET /workspace/{room_id}/files` — 列出文件、空 worktree、未知房间
- `GET /workspace/{room_id}/file` — 读取现有文件、缺少路径、二进制文件
- `PUT /workspace/{room_id}/file` — 创建、覆写、路径遍历拒绝
- `DELETE /workspace/{room_id}/file` — 删除现有文件、文件不存在
- `POST /workspace/{room_id}/commit` — 有更改时提交、空提交（无操作）
- `GET /workspace/{room_id}/search` — 基本搜索、无结果、多文件匹配

Git 操作通过 `pytest-mock` 模拟，无需实际 Git 远端。

## 扩展测试

```bash
cd extension
npm test                           # 所有测试（启动 VS Code 测试主机）
npm run test:unit                  # 仅单元测试（无需 VS Code）
npm run lint                       # ESLint 检查
```

### 扩展测试文件

| 文件 | 测试 | 覆盖 |
|------|-------|----------|
| `src/test/sessionFSM.test.ts` | 所有 FSM 状态转换 | `SessionFSM` |
| `src/test/workspaceClient.test.ts` | HTTP 客户端方法、错误处理 | `WorkspaceClient` |
| `src/test/fileSystemProvider.test.ts` | 读/写/删除/重命名、错误情况 | `FileSystemProvider` |
| `src/test/workspacePanel.test.ts` | 向导步骤进展、验证 | `WorkspacePanel` |

### 扩展单元测试详情

#### SessionFSM 测试（47 项）

涵盖所有有效状态转换和无效转换错误。

#### WorkspaceClient 测试（68 项）

涵盖所有 HTTP 方法，使用模拟 `fetch`：包括 `searchCode()` 返回匹配数组、空结果、404 房间。

#### FileSystemProvider 测试（83 项）

涵盖 VS Code `FileSystemProvider` 接口合规性：`readFile()`、`writeFile()`、`delete()`、`rename()`、`readDirectory()`、`stat()`。

#### WorkspacePanel 测试（33 项）

涵盖 5 步输入向导：仓库 URL 验证、分支名验证、令牌输入、确认显示、提交和错误处理。

**扩展单元测试总计：231 项**

## 工作区搜索测试

`GET /workspace/{room_id}/search` 接口测试涵盖：
- 基本字符串搜索：返回 `file_path`、`line`、`content` 字段
- 未配置时确认接口返回 503
