# Extension CLAUDE.md

## Structure

```
extension/src/
├── extension.ts             # Entry point, command registration, _handleLocalToolRequest,
│                            # _handleAskAI (unified @AI + code explanation via codeContext),
│                            # getOnlineRooms, removeQuitRoom, auto-workspace registration
├── panels/                  # collabPanel.ts, workspacePanel.ts
├── services/
│   ├── conductorStateMachine.ts        # FSM: Idle → ReadyToHost → Hosting → Joined
│   ├── conductorController.ts          # FSM driver
│   ├── workflowPanel.ts                # Workflow visualization WebView (singleton)
│   ├── workspaceClient.ts              # /workspace/ HTTP client
│   ├── conductorFileSystemProvider.ts  # conductor:// URI scheme
│   ├── lspResolver.ts                  # VS Code LSP definition + references
│   ├── relevanceRanker.ts              # Hybrid structural + semantic relevance scoring
│   ├── contextPlanGenerator.ts         # Deduplicated read-file operation planner
│   ├── xmlPromptAssembler.ts           # Structured XML prompt builder for LLM
│   ├── localToolDispatcher.ts          # Three-tier tool dispatch (all native TS)
│   ├── astToolRunner.ts                # 6 AST tools via web-tree-sitter
│   ├── treeSitterService.ts            # web-tree-sitter WASM wrapper (8 languages)
│   ├── complexToolRunner.ts            # 6 complex tools (compressed_view, trace_variable, etc.)
│   ├── fileEditRunner.ts               # file_edit + file_write tools (read-before-write enforcement)
│   ├── ticketProvider.ts               # ITicketProvider interface + JiraTicketProvider (batch status, my tickets)
│   ├── todoScanner.ts                  # Workspace TODO scanner ({jira:TICKET#N|after:M|blocked:OTHER} deps, //+ continuations, 43+ file types)
│   ├── jiraAuthService.ts              # Jira OAuth URI handler + connection state management
│   ├── jiraTokenStore.ts               # Local Jira token persistence (SecretStorage + .conductor/jira.json)
│   └── chatLocalStore.ts               # Local message cache (IndexedDB via VS Code globalState)
└── commands/index.ts

extension/webview-ui/
├── src/
│   ├── components/          # React 18 components (chat, modals, panels, tasks, shared)
│   ├── contexts/            # ChatContext, SessionContext, VSCodeContext
│   ├── hooks/               # useWebSocket, useReadReceipts, useHistoryPagination, useMermaid
│   ├── types/               # commands.ts (postMessage contract), messages.ts (data types)
│   ├── styles/              # design-tokens.css, components.css
│   └── utils/               # format.ts helpers
├── esbuild.mjs              # Bundler config (IIFE, browser target, JSX automatic)
└── tsconfig.json

extension/media/
├── webview.js       # React WebView bundle (esbuild output)
├── webview.css      # React WebView styles (esbuild output)
├── workflow.html    # Workflow visualization — SVG graph + agent detail panel
├── highlight.min.js    # Bundled Highlight.js 11.9.0 (no CDN dependency)
└── github-dark.min.css # Highlight.js GitHub Dark theme

extension/grammars/          # tree-sitter .wasm grammar files (committed)
├── tree-sitter.wasm         # web-tree-sitter runtime
└── tree-sitter-{lang}.wasm  # Python, JS, TS, Java, Go, Rust, C, C++ (8 files)
```

## Local Mode Tool Dispatch

When the agent runs in local workspace mode, tools are proxied via WebSocket to the extension. The extension runs ALL tools natively — zero Python dependency. All tool output schemas are aligned with Python (same field names, same structure) so the LLM sees consistent data regardless of execution path. The TS grep uses `rg --no-ignore --no-messages` with `-E` fallback on system grep to match Python's behavior:

```
RemoteToolExecutor → WebSocket → extension._handleLocalToolRequest
  → localToolDispatcher.ts
    ├── SUBPROCESS (13): grep, read_file, list_files, glob, git_log, git_diff, git_diff_files,
    │                    git_blame, git_show, find_tests, run_test, ast_search, get_repo_graph
    ├── AST (6):         file_outline, find_symbol, find_references, get_callees, get_callers, expand_symbol
    │                    → web-tree-sitter WASM (treeSitterService + astToolRunner)
    └── COMPLEX (6):     compressed_view, trace_variable, detect_patterns, get_dependencies, get_dependents, test_outline
                         → native TypeScript (complexToolRunner)
```

Grammar WASM files in `extension/grammars/` are committed to the repo. **Do not** re-download
grammars independently — the grammar ABI version must match `web-tree-sitter` (pinned at 0.26.7).
Mismatched versions cause silent fallback to regex extraction with degraded accuracy.

## Chat WebView (React)

The WebView is a React 18 SPA built with esbuild (`npm run compile:webview`). Key patterns:

- **Message rendering**: `MessageBubble.tsx` dispatches by `msg.type` (`text`, `code_snippet`, `ai_answer`, `file`, `stack_trace`, `test_failures`, `system`, etc.)
- **Syntax highlighting**: `CodeBlock.tsx` uses bundled Highlight.js (`highlight.min.js` + `github-dark.min.css`)
- **Mermaid diagrams**: `AIContent` renders `.mermaid-source` elements; click opens `DiagramLightbox` (fullscreen zoom). Falls back to raw source on parse error.
- **State management**: `ChatContext` (messages + AI state), `SessionContext` (FSM + permissions + SSO), `VSCodeContext` (postMessage bridge)
- **WebSocket**: `useWebSocket.ts` — full lifecycle (connect → auth → history → join → messages → reconnect)
- **Typed commands**: `commands.ts` defines `IncomingCommand` / `OutgoingCommand` union types for the postMessage contract

## Tool Parity Testing

Python and TypeScript tools must produce equivalent output. `make test-parity` validates this:

1. Checks `contracts/tool_contracts.json` matches Python Pydantic schemas
2. Validates TS tool output shapes against the contract
3. **Validates 11 subprocess tools** by calling the Python CLI (`python -m app.code_tools`) and checking `{success, data}` shape — done inside `extension/tests/validate_contract.js`
4. Runs cross-language parity tests (60+ tests across 13 dual-implementation tools)

```bash
make test-parity          # full validation (contract + shape + output comparison)
make update-contracts     # regenerate contracts after changing Python schemas
```

Contract output: `contracts/tool_contracts.json` (JSON Schema) + `extension/src/services/toolContracts.d.ts` (TypeScript interfaces). Regenerate after any schema change with `make update-contracts`.
