/**
 * Local repo graph builder for Conductor.
 *
 * Builds a file dependency graph (same structure as the Python backend's
 * `repo_graph` module) using:
 *   - VS Code LSP Document Symbols when available (highest accuracy)
 *   - `symbolExtractor.ts` as fallback (regex/TS compiler, no VS Code dependency)
 *
 * The graph is serialised to `.conductor/repo_graph.json` and sent to the
 * backend on demand.  The backend uses it to generate repo maps and rank
 * files by importance (PageRank) — without needing tree-sitter.
 *
 * Freshness: the index stores a `built_at` timestamp.  The caller compares
 * this against the workspace's latest file mtime to decide if a rebuild
 * is needed.
 *
 * @module services/repoGraphBuilder
 */

import * as fs from 'fs';
import * as path from 'path';
import { extractSymbols } from './symbolExtractor';

// Type-only import so this module can be tested without VS Code.
import type * as vscodeT from 'vscode';

// ---------------------------------------------------------------------------
// Public types — must match Python backend's parser.py / graph.py
// ---------------------------------------------------------------------------

export interface SymbolDef {
    name: string;
    kind: string;       // "function" | "class" | "method" | "module"
    file_path: string;  // workspace-relative
    start_line: number;
    end_line: number;
    signature: string;
}

export interface SymbolRef {
    name: string;
    file_path: string;
    line: number;
}

export interface FileSymbolsData {
    file_path: string;
    definitions: SymbolDef[];
    references: SymbolRef[];
    language: string | null;
}

export interface RepoGraphData {
    /** Timestamp when this index was built (ms since epoch). */
    built_at: number;
    /** Workspace root path. */
    workspace: string;
    /** Per-file symbol data, keyed by relative path. */
    files: Record<string, FileSymbolsData>;
    stats: {
        total_files: number;
        total_definitions: number;
        total_references: number;
    };
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const INDEX_DIR = '.conductor';
const INDEX_FILE = 'repo_graph.json';

const EXCLUDE_DIRS = new Set([
    'node_modules', '.git', 'venv', '.venv', '__pycache__',
    'dist', 'build', '.mypy_cache', '.pytest_cache', '.tox',
    'coverage', '.next', '.nuxt', 'target', 'out',
]);

const SUPPORTED_EXTS = new Set([
    '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs',
    '.java', '.go', '.rs', '.rb', '.cs', '.cpp', '.cc', '.c', '.h',
]);

/** Max staleness before the index is considered expired (ms). */
const MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Get the repo graph for a workspace, building it if needed.
 *
 * @param workspaceRoot  Absolute path to workspace root.
 * @param forceRebuild   If true, always rebuild (ignore cache).
 * @returns The graph data, or null if the workspace has no source files.
 */
export async function getOrBuildRepoGraph(
    workspaceRoot: string,
    forceRebuild: boolean = false,
): Promise<RepoGraphData | null> {
    const indexPath = path.join(workspaceRoot, INDEX_DIR, INDEX_FILE);

    if (!forceRebuild) {
        const cached = loadCachedGraph(indexPath);
        if (cached && !isExpired(cached, workspaceRoot)) {
            return cached;
        }
    }

    // Build fresh
    const graph = await buildRepoGraph(workspaceRoot);
    if (graph) {
        saveGraph(indexPath, graph);
    }
    return graph;
}

/**
 * Clear the cached repo graph (called by "Rebuild Index").
 */
export function clearRepoGraph(workspaceRoot: string): void {
    const indexPath = path.join(workspaceRoot, INDEX_DIR, INDEX_FILE);
    try {
        if (fs.existsSync(indexPath)) {
            fs.unlinkSync(indexPath);
        }
    } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Graph building
// ---------------------------------------------------------------------------

async function buildRepoGraph(workspaceRoot: string): Promise<RepoGraphData | null> {
    const files: Record<string, FileSymbolsData> = {};
    const sourceFiles = collectSourceFiles(workspaceRoot);

    if (sourceFiles.length === 0) {
        return null;
    }

    // Try VS Code LSP first (if available)
    let usedLsp = false;
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const vscode = require('vscode') as typeof vscodeT;

        for (const relPath of sourceFiles) {
            const absPath = path.join(workspaceRoot, relPath);
            const uri = vscode.Uri.file(absPath);

            try {
                const symbols = await vscode.commands.executeCommand<vscodeT.DocumentSymbol[]>(
                    'vscode.executeDocumentSymbolProvider', uri,
                );

                if (symbols && symbols.length > 0) {
                    usedLsp = true;
                    const defs = flattenToDefinitions(symbols, relPath);
                    const refs = extractReferencesFromFile(absPath, relPath);
                    files[relPath] = {
                        file_path: relPath,
                        definitions: defs,
                        references: refs,
                        language: detectLanguage(relPath),
                    };
                    continue;
                }
            } catch { /* LSP not available for this file */ }

            // Fallback to symbolExtractor
            const extracted = extractSymbols(absPath);
            files[relPath] = symbolExtractorToFileSymbols(extracted, relPath);
        }
    } catch {
        // VS Code not available (running in test), use symbolExtractor for all
        for (const relPath of sourceFiles) {
            const absPath = path.join(workspaceRoot, relPath);
            const extracted = extractSymbols(absPath);
            files[relPath] = symbolExtractorToFileSymbols(extracted, relPath);
        }
    }

    let totalDefs = 0;
    let totalRefs = 0;
    for (const f of Object.values(files)) {
        totalDefs += f.definitions.length;
        totalRefs += f.references.length;
    }

    console.log(
        `[RepoGraph] Built index: ${sourceFiles.length} files, ` +
        `${totalDefs} definitions, ${totalRefs} references (LSP=${usedLsp})`,
    );

    return {
        built_at: Date.now(),
        workspace: workspaceRoot,
        files,
        stats: {
            total_files: sourceFiles.length,
            total_definitions: totalDefs,
            total_references: totalRefs,
        },
    };
}

// ---------------------------------------------------------------------------
// LSP helpers
// ---------------------------------------------------------------------------

const LSP_STRUCTURAL_KINDS: Set<number> | null = (() => {
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const vscode = require('vscode') as typeof vscodeT;
        return new Set([
            vscode.SymbolKind.Class, vscode.SymbolKind.Function,
            vscode.SymbolKind.Method, vscode.SymbolKind.Constructor,
            vscode.SymbolKind.Interface, vscode.SymbolKind.Enum,
            vscode.SymbolKind.Struct, vscode.SymbolKind.Module,
            vscode.SymbolKind.Namespace,
        ]);
    } catch { return null; }
})();

function flattenToDefinitions(
    symbols: vscodeT.DocumentSymbol[],
    relPath: string,
    parent?: string,
    depth: number = 0,
): SymbolDef[] {
    const result: SymbolDef[] = [];
    for (const s of symbols) {
        if (LSP_STRUCTURAL_KINDS && LSP_STRUCTURAL_KINDS.has(s.kind) && depth <= 1) {
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const vscode = require('vscode') as typeof vscodeT;
            const kindName = vscode.SymbolKind[s.kind]?.toLowerCase() || 'unknown';
            result.push({
                name: s.name,
                kind: kindName,
                file_path: relPath,
                start_line: s.range.start.line + 1,
                end_line: s.range.end.line + 1,
                signature: s.detail || s.name,
            });
        }
        // Recurse into class/module children only
        if (s.children && s.children.length > 0 && LSP_STRUCTURAL_KINDS) {
            // eslint-disable-next-line @typescript-eslint/no-require-imports
            const vscode = require('vscode') as typeof vscodeT;
            if (s.kind === vscode.SymbolKind.Class ||
                s.kind === vscode.SymbolKind.Module ||
                s.kind === vscode.SymbolKind.Namespace ||
                s.kind === vscode.SymbolKind.Enum ||
                s.kind === vscode.SymbolKind.Interface) {
                result.push(...flattenToDefinitions(s.children, relPath, s.name, depth + 1));
            }
        }
    }
    return result;
}

// ---------------------------------------------------------------------------
// Reference extraction (import/require parsing)
// ---------------------------------------------------------------------------

function extractReferencesFromFile(absPath: string, relPath: string): SymbolRef[] {
    const refs: SymbolRef[] = [];
    try {
        const content = fs.readFileSync(absPath, 'utf-8');
        const lines = content.split('\n');

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();
            // Python: from X import Y  /  import X
            let m = line.match(/^from\s+([\w.]+)\s+import\s+(.+)/);
            if (m) {
                for (const name of m[2].split(',')) {
                    const n = name.trim().split(/\s+as\s+/)[0].trim();
                    if (n && !n.startsWith('(')) {
                        refs.push({ name: n, file_path: relPath, line: i + 1 });
                    }
                }
                continue;
            }
            m = line.match(/^import\s+([\w.]+)/);
            if (m) {
                refs.push({ name: m[1], file_path: relPath, line: i + 1 });
                continue;
            }
            // JS/TS: import { X, Y } from 'module'
            m = line.match(/import\s+\{([^}]+)\}\s+from/);
            if (m) {
                for (const name of m[1].split(',')) {
                    const n = name.trim().split(/\s+as\s+/)[0].trim();
                    if (n) { refs.push({ name: n, file_path: relPath, line: i + 1 }); }
                }
                continue;
            }
            // import DefaultExport from 'module'
            m = line.match(/import\s+(\w+)\s+from/);
            if (m) {
                refs.push({ name: m[1], file_path: relPath, line: i + 1 });
                continue;
            }
            // require('module')
            m = line.match(/require\s*\(\s*['"]([^'"]+)['"]\s*\)/);
            if (m) {
                const mod = m[1].split('/').pop() || m[1];
                refs.push({ name: mod, file_path: relPath, line: i + 1 });
            }
        }
    } catch { /* ignore read errors */ }
    return refs;
}

// ---------------------------------------------------------------------------
// symbolExtractor fallback
// ---------------------------------------------------------------------------

function symbolExtractorToFileSymbols(
    extracted: { imports: string[]; symbols: Array<{ name: string; kind: string; signature: string; range: { start: { line: number }; end: { line: number } } }> },
    relPath: string,
): FileSymbolsData {
    const definitions: SymbolDef[] = extracted.symbols.map(s => ({
        name: s.name,
        kind: s.kind,
        file_path: relPath,
        start_line: s.range.start.line + 1,
        end_line: s.range.end.line + 1,
        signature: s.signature,
    }));

    // Parse import strings into references
    const references: SymbolRef[] = [];
    for (const imp of extracted.imports) {
        const m = imp.match(/(?:from\s+[\w.]+\s+import\s+|import\s+\{?\s*)(\w+)/);
        if (m) {
            references.push({ name: m[1], file_path: relPath, line: 0 });
        }
    }

    return {
        file_path: relPath,
        definitions,
        references,
        language: detectLanguage(relPath),
    };
}

// ---------------------------------------------------------------------------
// File system helpers
// ---------------------------------------------------------------------------

function collectSourceFiles(workspaceRoot: string): string[] {
    const result: string[] = [];
    const walk = (dir: string, rel: string) => {
        let entries: fs.Dirent[];
        try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
        } catch { return; }
        for (const e of entries) {
            if (e.isDirectory()) {
                if (!EXCLUDE_DIRS.has(e.name) && !e.name.startsWith('.')) {
                    walk(path.join(dir, e.name), path.join(rel, e.name));
                }
            } else if (e.isFile()) {
                const ext = path.extname(e.name).toLowerCase();
                if (SUPPORTED_EXTS.has(ext)) {
                    result.push(path.join(rel, e.name));
                }
            }
        }
    };
    walk(workspaceRoot, '');
    return result;
}

function detectLanguage(relPath: string): string | null {
    const ext = path.extname(relPath).toLowerCase();
    const map: Record<string, string> = {
        '.py': 'python', '.js': 'javascript', '.jsx': 'javascript',
        '.ts': 'typescript', '.tsx': 'typescript', '.mjs': 'javascript',
        '.java': 'java', '.go': 'go', '.rs': 'rust', '.rb': 'ruby',
        '.cs': 'csharp', '.cpp': 'cpp', '.cc': 'cpp', '.c': 'c',
    };
    return map[ext] || null;
}

function loadCachedGraph(indexPath: string): RepoGraphData | null {
    try {
        if (!fs.existsSync(indexPath)) { return null; }
        const raw = fs.readFileSync(indexPath, 'utf-8');
        return JSON.parse(raw) as RepoGraphData;
    } catch { return null; }
}

function isExpired(graph: RepoGraphData, workspaceRoot: string): boolean {
    const age = Date.now() - graph.built_at;
    if (age > MAX_AGE_MS) { return true; }

    // Quick check: sample a few source files and compare mtime
    const sampleFiles = Object.keys(graph.files).slice(0, 20);
    for (const rel of sampleFiles) {
        try {
            const stat = fs.statSync(path.join(workspaceRoot, rel));
            if (stat.mtimeMs > graph.built_at) {
                return true;
            }
        } catch { /* file deleted — stale */ return true; }
    }
    return false;
}

function saveGraph(indexPath: string, graph: RepoGraphData): void {
    try {
        const dir = path.dirname(indexPath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(indexPath, JSON.stringify(graph));
    } catch (e) {
        console.error('[RepoGraph] Failed to save index:', e);
    }
}
