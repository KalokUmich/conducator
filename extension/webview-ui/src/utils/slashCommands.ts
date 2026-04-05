// ============================================================
// Slash command parsing — extracted for testability
// ============================================================

export interface SlashCommand {
  name: string;
  description: string;
  hint: string;
  transform: (args: string) => string;
  isAI: boolean;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: "/ask", description: "Ask AI a question", hint: "Type your question...", transform: (args) => args, isAI: true },
  { name: "/pr", description: "Request a code review", hint: "Describe the PR or paste a link...", transform: (args) => `[query_type:code_review] ${args}`, isAI: true },
  { name: "/jira", description: "Create or search Jira issues", hint: "Describe the task or search query...", transform: (args) => `[query_type:issue_tracking] ${args}`, isAI: true },
];

/** Match slash commands based on current input. Returns matching commands. */
export function matchSlashCommands(input: string): SlashCommand[] {
  if (!input.startsWith("/")) return [];
  const prefix = input.toLowerCase().split(" ")[0];
  // Only match if user hasn't completed the command (no space yet)
  if (input.includes(" ")) return [];
  return SLASH_COMMANDS.filter((c) => c.name.startsWith(prefix));
}

/** Compute ghost hint text (the autocomplete preview). */
export function computeGhostHint(input: string, matches: SlashCommand[]): string {
  if (matches.length === 0) return "";
  const prefix = input.toLowerCase().split(" ")[0];
  if (matches[0].name.startsWith(prefix)) {
    return matches[0].name.slice(prefix.length);
  }
  return "";
}

/** Parse a message and determine if it's an AI query, and transform it. */
export function parseMessageForAI(text: string): { query: string; isAI: boolean } {
  for (const cmd of SLASH_COMMANDS) {
    if (text.startsWith(cmd.name + " ") || text === cmd.name) {
      const args = text.slice(cmd.name.length).trim();
      return { query: cmd.transform(args), isAI: !!cmd.isAI };
    }
  }
  if (text.startsWith("@AI ") || text.startsWith("@ai ")) {
    return { query: text.slice(4), isAI: true };
  }
  return { query: text, isAI: false };
}
