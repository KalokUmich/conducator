import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useVSCode, useCommand } from "../../contexts/VSCodeContext";
import { useSession } from "../../contexts/SessionContext";
import { WorkspaceTodoEditModal } from "../modals/WorkspaceTodoEditModal";
import type { Todo, WorkspaceItem } from "../../types/messages";

// ============================================================
// TasksTab — Task Board with Backlog + AI Working Space
// ============================================================

type TodoSection = "linked" | "code" | "jira";

/** Ticket status from extension scan enrichment */
interface TicketStatus {
  key: string;
  summary: string;
  status: string;
  isDone: boolean;
  browseUrl?: string;
  priority?: string;
}

/** Dependency graph entry for a backlog item */
export interface DepEntry {
  unresolvedAfter: { changeNumber: number; ticketKey: string }[];
  unresolvedBlocked: { ticketKey: string }[];
}

// ── Dependency graph builder ─────────────────────────────

export function buildDependencyGraph(todos: Todo[]): Record<string, DepEntry> {
  const graph: Record<string, DepEntry> = {};

  // Index: "TICKET#N" -> item id
  const changeIndex: Record<string, string> = {};
  // Index: "TICKET" -> [item ids]
  const ticketIndex: Record<string, string[]> = {};

  for (const todo of todos) {
    const itemId = "todo:" + todo.id;
    if (todo.jiraKey) {
      if (!ticketIndex[todo.jiraKey]) ticketIndex[todo.jiraKey] = [];
      ticketIndex[todo.jiraKey].push(itemId);
      if (todo.changeNumber) {
        changeIndex[todo.jiraKey + "#" + todo.changeNumber] = itemId;
      }
    }
  }

  for (const todo of todos) {
    const itemId = "todo:" + todo.id;
    const unresolvedAfter: DepEntry["unresolvedAfter"] = [];
    const unresolvedBlocked: DepEntry["unresolvedBlocked"] = [];

    // Intra-ticket: after:N
    if (todo.afterDeps && todo.jiraKey) {
      for (const n of todo.afterDeps) {
        const depKey = todo.jiraKey + "#" + n;
        if (changeIndex[depKey]) {
          unresolvedAfter.push({ changeNumber: n, ticketKey: todo.jiraKey });
        }
      }
    }

    // Cross-ticket: blocked:OTHER
    if (todo.blockedBy) {
      for (const otherTicket of todo.blockedBy) {
        if (ticketIndex[otherTicket] && ticketIndex[otherTicket].length > 0) {
          unresolvedBlocked.push({ ticketKey: otherTicket });
        }
      }
    }

    if (unresolvedAfter.length > 0 || unresolvedBlocked.length > 0) {
      graph[itemId] = { unresolvedAfter, unresolvedBlocked };
    }
  }

  return graph;
}

// ── Convert Todo to WorkspaceItem ────────────────────────

export function todoToWorkspaceItem(
  todo: Todo,
  ticketStatuses: Record<string, TicketStatus>
): WorkspaceItem {
  const ts = todo.jiraKey ? ticketStatuses[todo.jiraKey] : undefined;
  return {
    id: "todo:" + todo.id,
    source: "code",
    title: todo.title,
    description: todo.description,
    ticketKey: todo.jiraKey,
    ticketStatus: ts,
    filePath: todo.filePath,
    relativePath: todo.relativePath,
    lineNumber: todo.lineNumber,
    commentPrefix: todo.commentPrefix,
    descriptionLine: todo.descriptionLine,
    rawTag: todo.rawTag,
    blockEndLine: todo.blockEndLine,
    changeNumber: todo.changeNumber,
    afterDeps: todo.afterDeps,
    blockedBy: todo.blockedBy,
    parentTicket: todo.parentTicket,
    browseUrl: ts?.browseUrl,
    isDone: ts?.isDone,
  };
}

export function jiraTicketToWorkspaceItem(ticket: Todo): WorkspaceItem {
  return {
    id: "ticket:" + ticket.id,
    source: "ticket",
    title: ticket.title,
    ticketKey: ticket.jiraKey || ticket.id,
    ticketStatus: {
      key: ticket.jiraKey || ticket.id,
      summary: ticket.title,
      status: ticket.ticketStatus || "To Do",
      isDone: ticket.isDone || false,
      browseUrl: ticket.browseUrl,
    },
    browseUrl: ticket.browseUrl,
    priority: ticket.priority,
    isDone: ticket.isDone,
  };
}

// ============================================================
// Main TasksTab component
// ============================================================

export function TasksTab() {
  const [workspaceTodos, setWorkspaceTodos] = useState<Todo[]>([]);
  const [jiraTickets, setJiraTickets] = useState<Todo[]>([]);
  const [ticketStatuses, setTicketStatuses] = useState<Record<string, TicketStatus>>({});
  const [aiWorkspaceItems, setAiWorkspaceItems] = useState<WorkspaceItem[]>([]);
  const [expandedSections, setExpandedSections] = useState<Set<TodoSection>>(
    new Set(["linked", "code", "jira"])
  );
  const [scanning, setScanning] = useState(false);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [editingTodo, setEditingTodo] = useState<WorkspaceItem | null>(null);
  const [jiraAuthNeeded, setJiraAuthNeeded] = useState(false);
  const { send, onAny } = useVSCode();
  const { state: sessionState } = useSession();

  const roomId = sessionState.session?.roomId;

  // Load todos on mount
  useEffect(() => {
    if (roomId) {
      send({ command: "loadTodos", roomId });
    }
  }, [send, roomId]);

  // Listen for room todo updates
  useCommand("todosLoaded", (msg) => {
    if (msg.command !== "todosLoaded") return;
    setTodos(msg.todos);
  });

  useCommand("todoCreated", (msg) => {
    if (msg.command !== "todoCreated") return;
    setTodos((prev) => [...prev, msg.todo]);
  });

  useCommand("todoUpdated", (msg) => {
    if (msg.command !== "todoUpdated") return;
    setTodos((prev) => prev.map((t) => (t.id === msg.todo.id ? msg.todo : t)));
  });

  useCommand("todoDeleted", (msg) => {
    if (msg.command !== "todoDeleted") return;
    setTodos((prev) => prev.filter((t) => t.id !== msg.todoId));
  });

  // Listen for workspace scan results (with full WorkspaceTodo fields)
  useCommand("workspaceTodosScanned", (msg) => {
    if (msg.command !== "workspaceTodosScanned") return;
    setScanning(false);
    const raw = msg as unknown as {
      todos: Todo[];
      ticketStatuses?: Record<string, TicketStatus>;
    };
    setWorkspaceTodos(raw.todos || []);
    if (raw.ticketStatuses) setTicketStatuses(raw.ticketStatuses);

    // Auto-refresh Jira tickets if scan found linked ticket keys
    const hasLinkedKeys = (raw.todos || []).some((t) => t.jiraKey);
    if (hasLinkedKeys) {
      send({ command: "loadJiraTickets" });
    }
  });

  // Listen for Jira tickets
  useEffect(() => {
    return onAny((msg) => {
      const cmd = (msg as unknown as { command: string }).command;
      if (cmd === "jiraTicketsLoaded") {
        const data = msg as unknown as {
          tickets?: Array<Record<string, unknown>>;
          error?: string;
          authNeeded?: boolean;
        };
        console.log("[TasksTab] jiraTicketsLoaded:", data.tickets?.length ?? 0, "tickets, error:", data.error, "authNeeded:", data.authNeeded);
        if (data.authNeeded) {
          setJiraAuthNeeded(true);
        } else {
          setJiraAuthNeeded(false);
        }
        if (data.error) {
          console.warn("[TasksTab] Jira load error:", data.error);
        }
        if (data.tickets && data.tickets.length > 0) {
          const mapped: Todo[] = data.tickets.map((t) => ({
            id: (t.key as string) || `jira-${Date.now()}`,
            title: (t.summary as string) || (t.key as string) || "Jira ticket",
            status: (t.status as string) === "Done" ? ("done" as const) : ("pending" as const),
            jiraKey: t.key as string,
            epicKey: t.epicKey as string,
            epicName: t.epicName as string,
            epicColor: t.epicColor as string,
            source: "jira" as const,
            browseUrl: t.browseUrl as string,
            priority: t.priority as string,
            assignee: t.assignee as string,
            isDone: (t.status as string) === "Done" || (t.isDone as boolean),
          }));
          setJiraTickets(mapped);
        } else {
          setJiraTickets([]);
        }
      }
    });
  }, [onAny]);

  // Build dependency graph
  const depGraph = useMemo(() => buildDependencyGraph(workspaceTodos), [workspaceTodos]);

  // Categorize backlog items (excluding items already in workspace)
  const wsItemIds = useMemo(() => new Set(aiWorkspaceItems.map((i) => i.id)), [aiWorkspaceItems]);
  const todoTicketKeys = useMemo(
    () => new Set(workspaceTodos.filter((t) => t.jiraKey).map((t) => t.jiraKey)),
    [workspaceTodos]
  );

  const { linkedItems, codeItems, jiraItems } = useMemo(() => {
    const linked: Todo[] = [];
    const code: Todo[] = [];
    for (const todo of workspaceTodos) {
      const itemId = "todo:" + todo.id;
      if (wsItemIds.has(itemId)) continue;
      if (todo.jiraKey) {
        linked.push(todo);
      } else {
        code.push(todo);
      }
    }
    const jira: Todo[] = jiraTickets.filter((t) => {
      if (t.isDone) return false;
      const itemId = "ticket:" + t.id;
      if (wsItemIds.has(itemId)) return false;
      if (todoTicketKeys.has(t.jiraKey)) return false;
      return true;
    });
    return { linkedItems: linked, codeItems: code, jiraItems: jira };
  }, [workspaceTodos, jiraTickets, wsItemIds, todoTicketKeys]);

  // Room TODOs go directly to workspace
  const roomTodoItems = useMemo(
    () =>
      todos
        .filter((t) => t.source === "linked" || t.source === "code")
        .filter((t) => !wsItemIds.has("room:" + t.id)),
    [todos, wsItemIds]
  );

  const handleScan = useCallback(() => {
    setScanning(true);
    send({ command: "scanWorkspaceTodos" });
  }, [send]);

  const handleLoadJira = useCallback(() => {
    send({ command: "loadJiraTickets" });
  }, [send]);

  const toggleSection = useCallback((section: TodoSection) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }, []);

  // Drag-and-drop handlers
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const json = e.dataTransfer.getData("text/plain");
      if (!json) return;
      try {
        const item: WorkspaceItem = JSON.parse(json);
        // Avoid duplicates
        if (aiWorkspaceItems.some((i) => i.id === item.id)) return;
        // Check dependency blocking
        if (depGraph[item.id]) {
          const parts: string[] = [];
          for (const a of depGraph[item.id].unresolvedAfter) {
            parts.push("{jira:" + a.ticketKey + "#" + a.changeNumber + "}");
          }
          for (const b of depGraph[item.id].unresolvedBlocked) {
            parts.push("{jira:" + b.ticketKey + "}");
          }
          // Could show toast here — for now, silently reject
          return;
        }
        setAiWorkspaceItems((prev) => [...prev, item]);
      } catch {
        /* invalid data */
      }
    },
    [aiWorkspaceItems, depGraph]
  );

  const handleRemoveFromWorkspace = useCallback((id: string) => {
    setAiWorkspaceItems((prev) => prev.filter((i) => i.id !== id));
  }, []);

  const handleInvestigate = useCallback(
    (item: WorkspaceItem) => {
      if (!roomId) return;
      const ticketCtx = item.ticketKey ? `Jira ticket ${item.ticketKey}: "${item.title}".` : "";
      const codeCtx = item.relativePath ? `Code location: ${item.relativePath}:${item.lineNumber}.` : "";
      const isJiraOnly = item.source === "ticket" && !item.relativePath;
      const repoCheck = isJiraOnly
        ? `CRITICAL: Before using any code tools, use ask_user: "Does this ticket relate to the code in your current workspace?" ` +
          `with options ["Yes, investigate here", "No, different repo"]. ` +
          `If no, ask which repo and say "Please open that workspace and investigate again." `
        : "";
      const query =
        `[jira] Investigate this task: "${item.title}". ${ticketCtx} ${codeCtx} ${repoCheck}` +
        `Read the relevant code, analyze what needs to be done, and provide a detailed plan. ` +
        `If this is a Jira ticket, update the description with your findings. ` +
        `If this is a code TODO, update the TODO_DESC with key findings.`;

      send({ command: "askAI", roomId, query, planMode: true });
    },
    [send, roomId]
  );

  const totalCount = linkedItems.length + codeItems.length + jiraItems.length;

  return (
    <div className="tasks-tab">
      {/* Header */}
      <div className="tasks-header">
        <div className="tasks-title-row">
          <span className="tasks-icon">📋</span>
          <span className="tasks-title">Task Board</span>
          <span className="todo-count">{totalCount}</span>
        </div>
        <div className="tasks-actions">
          <button className="action-btn action-brand" onClick={handleLoadJira}>
            Load Jira
          </button>
          <button className="action-btn action-brand" onClick={handleScan} disabled={scanning}>
            {scanning ? "Scanning..." : "Scan TODOs"}
          </button>
        </div>
      </div>

      {/* Quick add */}
      <QuickAddTodo />

      {/* AI Working Space (drop zone — shown above backlog) */}
      <WorkspaceSection
        items={aiWorkspaceItems}
        depGraph={depGraph}
        roomId={roomId}
        onDrop={handleDrop}
        onRemove={handleRemoveFromWorkspace}
        onInvestigate={handleInvestigate}
      />

      {/* Backlog sections */}
      <BacklogSection
        title="Linked TODOs"
        section="linked"
        count={linkedItems.length}
        expanded={expandedSections.has("linked")}
        onToggle={() => toggleSection("linked")}
        todos={linkedItems}
        depGraph={depGraph}
        ticketStatuses={ticketStatuses}
        onEdit={(todo) => setEditingTodo(todoToWorkspaceItem(todo, ticketStatuses))}
      />

      <BacklogSection
        title="Code TODOs"
        section="code"
        count={codeItems.length}
        expanded={expandedSections.has("code")}
        onToggle={() => toggleSection("code")}
        todos={codeItems}
        depGraph={depGraph}
        ticketStatuses={ticketStatuses}
        onEdit={(todo) => setEditingTodo(todoToWorkspaceItem(todo, ticketStatuses))}
      />

      <JiraSectionComp
        count={jiraItems.length}
        expanded={expandedSections.has("jira")}
        onToggle={() => toggleSection("jira")}
        tickets={jiraItems}
        authNeeded={jiraAuthNeeded}
      />

      {/* Workspace TODO Edit Modal */}
      <WorkspaceTodoEditModal
        open={!!editingTodo}
        onClose={() => setEditingTodo(null)}
        todo={editingTodo}
        onSaved={(newTitle, newDesc) => {
          if (editingTodo) {
            // Optimistically update local state
            setWorkspaceTodos((prev) =>
              prev.map((t) =>
                "todo:" + t.id === editingTodo.id
                  ? { ...t, title: newTitle, description: newDesc || undefined }
                  : t
              )
            );
          }
        }}
      />
    </div>
  );
}

// ── AI Working Space Section ─────────────────────────────

function WorkspaceSection({
  items,
  depGraph,
  roomId,
  onDrop,
  onRemove,
  onInvestigate,
}: {
  items: WorkspaceItem[];
  depGraph: Record<string, DepEntry>;
  roomId?: string;
  onDrop: (e: React.DragEvent) => void;
  onRemove: (id: string) => void;
  onInvestigate: (item: WorkspaceItem) => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      setDragOver(false);
      onDrop(e);
    },
    [onDrop]
  );

  return (
    <div className="workspace-section">
      <div className="workspace-header">
        <span className="workspace-title">AI Working Space</span>
        {items.length > 0 && <span className="todo-count">{items.length}</span>}
      </div>
      <div
        className={`workspace-dropzone ${dragOver ? "drag-over" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {items.length === 0 ? (
          <div className="workspace-empty">Drag linked items here for AI investigation</div>
        ) : (
          <div className="workspace-list">
            {items.map((item, idx) => (
              <WorkspaceCard
                key={item.id}
                item={item}
                depGraph={depGraph}
                roomId={roomId}
                onRemove={onRemove}
                onInvestigate={onInvestigate}
                delay={idx * 30}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Workspace Card ───────────────────────────────────────

const WorkspaceCard = memo(function WorkspaceCard({
  item,
  depGraph,
  roomId,
  onRemove,
  onInvestigate,
  delay,
}: {
  item: WorkspaceItem;
  depGraph: Record<string, DepEntry>;
  roomId?: string;
  onRemove: (id: string) => void;
  onInvestigate: (item: WorkspaceItem) => void;
  delay: number;
}) {
  const [investigating, setInvestigating] = useState(false);
  const deps = depGraph[item.id];
  const isBlocked = !!deps;
  const invDisabled = item.isDone || isBlocked || !roomId || investigating;

  let blockTitle = "";
  if (deps) {
    const parts: string[] = [];
    for (const a of deps.unresolvedAfter) parts.push("#" + a.changeNumber);
    for (const b of deps.unresolvedBlocked) parts.push(b.ticketKey);
    blockTitle = "Blocked by: " + parts.join(", ");
  }

  const sourceBadge = item.isDone
    ? "Completed"
    : item.source === "code"
      ? "TODO"
      : "Jira";

  const badgeClass = item.isDone ? "badge-done" : "badge-todo";

  const handleInvestigate = useCallback(() => {
    setInvestigating(true);
    onInvestigate(item);
  }, [item, onInvestigate]);

  return (
    <div
      className={`workspace-card${item.isDone ? " ws-done" : ""}`}
      style={{ animationDelay: delay + "ms" }}
    >
      <div className="ws-card-top">
        <div className="ws-card-info">
          <span className={`badge ${badgeClass}`}>{sourceBadge}</span>
          <span className="ws-card-title">{item.title}</span>
        </div>
        <button
          className="ws-remove-btn"
          onClick={() => onRemove(item.id)}
          title="Remove from workspace"
          disabled={item.isDone}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
            <path
              fillRule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      </div>
      {item.ticketKey && <span className="ws-card-ticket">{item.ticketKey}</span>}
      <div className="ws-actions">
        <button
          className={`ws-action-btn btn-investigate${invDisabled ? " disabled" : ""}`}
          onClick={handleInvestigate}
          disabled={invDisabled}
          title={blockTitle || undefined}
        >
          {investigating ? (
            <>
              <svg className="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              Investigating...
            </>
          ) : (
            <>
              <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
                <path
                  fillRule="evenodd"
                  d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z"
                  clipRule="evenodd"
                />
              </svg>
              Investigate
            </>
          )}
        </button>
        <button className="ws-action-btn btn-implement" disabled title="Coming soon">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
            <path
              fillRule="evenodd"
              d="M12.316 3.051a1 1 0 01.633 1.265l-4 12a1 1 0 11-1.898-.632l4-12a1 1 0 011.265-.633zM5.707 6.293a1 1 0 010 1.414L3.414 10l2.293 2.293a1 1 0 11-1.414 1.414l-3-3a1 1 0 010-1.414l3-3a1 1 0 011.414 0zm8.586 0a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 11-1.414-1.414L16.586 10l-2.293-2.293a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
          Implement
        </button>
      </div>
    </div>
  );
});

// ── Backlog Section (Linked / Code TODOs) ────────────────

function BacklogSection({
  title,
  section,
  count,
  expanded,
  onToggle,
  todos,
  depGraph,
  ticketStatuses,
  onEdit,
}: {
  title: string;
  section: TodoSection;
  count: number;
  expanded: boolean;
  onToggle: () => void;
  todos: Todo[];
  depGraph: Record<string, DepEntry>;
  ticketStatuses: Record<string, TicketStatus>;
  onEdit?: (todo: Todo) => void;
}) {
  if (count === 0) return null;

  return (
    <div className="todo-section">
      <button className="todo-section-header" onClick={onToggle}>
        <span className={`todo-chevron ${expanded ? "chevron-open" : ""}`}>›</span>
        <span className="todo-section-title">{title}</span>
        <span className="todo-count">{count}</span>
      </button>
      {expanded && (
        <div className="todo-list stagger-children">
          {todos.map((todo, i) => (
            <BacklogCard
              key={todo.id}
              todo={todo}
              depGraph={depGraph}
              ticketStatuses={ticketStatuses}
              isLinked={section === "linked"}
              delay={i * 30}
              onEdit={onEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Draggable Backlog Card ───────────────────────────────

const BacklogCard = memo(function BacklogCard({
  todo,
  depGraph,
  ticketStatuses,
  isLinked,
  delay,
  onEdit,
}: {
  todo: Todo;
  depGraph: Record<string, DepEntry>;
  ticketStatuses: Record<string, TicketStatus>;
  isLinked: boolean;
  delay: number;
  onEdit?: (todo: Todo) => void;
}) {
  const { send } = useVSCode();
  const itemId = "todo:" + todo.id;
  const deps = depGraph[itemId];
  const ts = todo.jiraKey ? ticketStatuses[todo.jiraKey] : undefined;
  const isDone = ts?.isDone || todo.isDone || todo.status === "done";
  const isBlocked = !!deps;
  const canDrag = isLinked && !isDone && !isBlocked;

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      const item = todoToWorkspaceItem(todo, ticketStatuses);
      e.dataTransfer.setData("text/plain", JSON.stringify(item));
      e.dataTransfer.effectAllowed = "move";
    },
    [todo, ticketStatuses]
  );

  const handleNavigate = useCallback(() => {
    if (todo.filePath || todo.relativePath) {
      send({
        command: "navigateToCode",
        relativePath: todo.relativePath || todo.filePath || "",
        startLine: todo.lineNumber || 1,
      });
    }
  }, [send, todo.filePath, todo.relativePath, todo.lineNumber]);

  const cardClass = isDone ? "card-done" : isBlocked ? "card-blocked" : isLinked ? "card-linked" : "";

  let blockTitle = "";
  if (isBlocked && deps) {
    const parts: string[] = [];
    for (const a of deps.unresolvedAfter) parts.push("#" + a.changeNumber + " (" + a.ticketKey + ")");
    for (const b of deps.unresolvedBlocked) parts.push(b.ticketKey);
    blockTitle = "Blocked by: " + parts.join(", ");
  }

  const statusBadgeClass = ts
    ? ["done", "closed", "resolved"].includes((ts.status || "").toLowerCase())
      ? "badge-done"
      : ["in progress", "in review", "in development"].includes((ts.status || "").toLowerCase())
        ? "badge-todo"
        : ""
    : "";

  return (
    <div
      className={`backlog-card ${cardClass}`}
      draggable={canDrag}
      onDragStart={canDrag ? handleDragStart : undefined}
      title={blockTitle || undefined}
      style={{ animationDelay: delay + "ms" }}
    >
      <div className="backlog-card-top">
        <div className="backlog-card-info">
          <span className={`badge ${isDone ? "badge-done" : "badge-todo"}`}>
            {isDone ? "Done" : "TODO"}
          </span>
          <span className="backlog-card-title" onClick={handleNavigate}>
            {todo.title}
          </span>
        </div>
        {onEdit && (
          <button
            className="backlog-edit-btn"
            onClick={(e) => { e.stopPropagation(); onEdit(todo); }}
            title="Edit TODO"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 20 20" fill="currentColor">
              <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
            </svg>
          </button>
        )}
        {canDrag && <span className="drag-handle" title="Drag to Working Space">⠿</span>}
      </div>
      <div className="backlog-card-meta">
        {todo.relativePath && (
          <span className="backlog-card-location" onClick={handleNavigate}>
            {todo.relativePath}:{todo.lineNumber}
          </span>
        )}
        {ts && (
          <a
            className={`badge badge-status ${statusBadgeClass}`}
            href={ts.browseUrl || "#"}
            title={ts.summary}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (ts.browseUrl) send({ command: "openExternal", url: ts.browseUrl });
            }}
          >
            {todo.jiraKey} · {ts.status}
          </a>
        )}
        {isBlocked && <span className="badge badge-blocked">Blocked</span>}
      </div>
    </div>
  );
});

// ── Jira Section with Epic Grouping ──────────────────────

function JiraSectionComp({
  count,
  expanded,
  onToggle,
  tickets,
  authNeeded,
}: {
  count: number;
  expanded: boolean;
  onToggle: () => void;
  tickets: Todo[];
  authNeeded?: boolean;
}) {
  const { send } = useVSCode();

  // Group by epic
  const epicGroups = new Map<string, Todo[]>();
  const noEpic: Todo[] = [];
  tickets.forEach((t) => {
    if (t.epicKey) {
      if (!epicGroups.has(t.epicKey)) epicGroups.set(t.epicKey, []);
      epicGroups.get(t.epicKey)!.push(t);
    } else {
      noEpic.push(t);
    }
  });

  return (
    <div className="todo-section">
      <button className="todo-section-header" onClick={onToggle}>
        <span className={`todo-chevron ${expanded ? "chevron-open" : ""}`}>›</span>
        <span className="todo-section-title">Jira Tickets</span>
        <span className="todo-count">{count}</span>
      </button>
      {expanded && (
        <div className="jira-ticket-list">
          {tickets.length === 0 ? (
            authNeeded ? (
              <div className="jira-auth-hint-panel">
                <p className="todo-empty">Connect Jira to load tickets</p>
                <button
                  className="action-btn action-brand"
                  onClick={() => send({ command: "jiraConnect" })}
                >
                  Connect Jira
                </button>
              </div>
            ) : (
              <div className="todo-empty">No tickets loaded</div>
            )
          ) : (
            <>
              {Array.from(epicGroups.entries()).map(([epicKey, items]) => {
                const epicName = items[0]?.epicName || epicKey;
                const epicColor = items[0]?.epicColor || "var(--c-accent-400)";
                return (
                  <div key={epicKey} className="epic-group">
                    <div className="epic-header" style={{ borderLeftColor: epicColor }}>
                      <span className="epic-name">{epicName}</span>
                      <span className="epic-count">{items.length}</span>
                    </div>
                    {items.map((t) => (
                      <JiraTicketCard key={t.id} ticket={t} />
                    ))}
                  </div>
                );
              })}
              {noEpic.length > 0 && (
                <div className="epic-group">
                  {epicGroups.size > 0 && (
                    <div
                      className="epic-header"
                      style={{ borderLeftColor: "var(--c-text-muted)" }}
                    >
                      <span className="epic-name" style={{ color: "var(--c-text-muted)" }}>
                        No Epic
                      </span>
                      <span className="epic-count">{noEpic.length}</span>
                    </div>
                  )}
                  {noEpic.map((t) => (
                    <JiraTicketCard key={t.id} ticket={t} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Jira Ticket Card (draggable to workspace) ────────────

const JiraTicketCard = memo(function JiraTicketCard({ ticket }: { ticket: Todo }) {
  const { send } = useVSCode();
  const [showPopup, setShowPopup] = useState(false);

  const handleDragStart = useCallback(
    (e: React.DragEvent) => {
      const item = jiraTicketToWorkspaceItem(ticket);
      e.dataTransfer.setData("text/plain", JSON.stringify(item));
      e.dataTransfer.effectAllowed = "move";
    },
    [ticket]
  );

  return (
    <>
      <div
        className="backlog-card card-jira"
        draggable
        onDragStart={handleDragStart}
        onClick={() => setShowPopup(true)}
      >
        <div className="backlog-card-top">
          <div className="backlog-card-info">
            <span className="badge badge-jira">{ticket.jiraKey || ticket.id}</span>
            <span className="backlog-card-title">{ticket.title}</span>
          </div>
          <span className="drag-handle" title="Drag to Working Space">⠿</span>
        </div>
        {ticket.priority && (
          <div className="backlog-card-meta">
            <span className="backlog-card-priority">{ticket.priority}</span>
          </div>
        )}
      </div>

      {/* Jira Ticket Popup */}
      {showPopup && (
        <JiraTicketPopup ticket={ticket} onClose={() => setShowPopup(false)} />
      )}
    </>
  );
});

// ── Jira Ticket Popup ────────────────────────────────────

function JiraTicketPopup({
  ticket,
  onClose,
}: {
  ticket: Todo;
  onClose: () => void;
}) {
  const { send } = useVSCode();
  const popupRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  const handleBackdrop = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose]
  );

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="jira-popup-backdrop" onClick={handleBackdrop}>
      <div className="jira-popup" ref={popupRef}>
        <div className="jira-popup-header">
          <span className="badge badge-jira">{ticket.jiraKey || ticket.id}</span>
          <span className="jira-popup-status">{ticket.ticketStatus || ticket.status}</span>
          <button className="jira-popup-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="jira-popup-title">{ticket.title}</div>
        {ticket.priority && (
          <div className="jira-popup-meta">Priority: {ticket.priority}</div>
        )}
        {ticket.epicName && (
          <div className="jira-popup-meta">
            Epic: <span style={{ color: ticket.epicColor }}>{ticket.epicName}</span>
          </div>
        )}
        <div className="jira-popup-actions">
          {ticket.browseUrl && (
            <button
              className="action-btn action-brand"
              onClick={() => {
                send({ command: "openExternal", url: ticket.browseUrl! });
                onClose();
              }}
            >
              Open in Jira
            </button>
          )}
          <button className="action-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Quick Add Todo ───────────────────────────────────────

function QuickAddTodo() {
  const [value, setValue] = useState("");
  const { send } = useVSCode();
  const { state: sessionState } = useSession();

  const handleAdd = useCallback(() => {
    const title = value.trim();
    if (!title || !sessionState.session?.roomId) return;
    send({ command: "createTodo", title, roomId: sessionState.session.roomId });
    setValue("");
  }, [value, send, sessionState.session?.roomId]);

  return (
    <div className="quick-add">
      <input
        type="text"
        className="text-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleAdd()}
        placeholder="Add a task..."
      />
      <button className="btn-primary btn-sm" onClick={handleAdd} disabled={!value.trim()}>
        +
      </button>
    </div>
  );
}
