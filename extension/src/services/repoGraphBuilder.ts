/**
 * Local repo graph builder for Conductor.
 *
 * Builds a file dependency graph (same structure as the Python backend's
 * `repo_graph` module) using **web-tree-sitter WASM** for AST-based symbol
 * extraction.  Falls back to regex when tree-sitter is not initialized.
 *
 * The graph is serialised to `.conductor/repo_graph.json` and sent to the
 * backend on demand.  The backend uses it to generate repo maps and rank
 * files by importance (PageRank).
 *
 * Freshness: the index stores a `built_at` timestamp.  The caller compares
 * this against the workspace's latest file mtime to decide if a rebuild
 * is needed.
 *
 * @module services/repoGraphBuilder
 */

import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import {
    extractDefinitions as tsExtractDefinitions,
    detectLanguage,
    isInitialized as isTreeSitterReady,
} from './treeSitterService';
import type { FileSymbols } from './treeSitterService';

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
    /** Git HEAD commit hash at build time — cache invalidation key. */
    git_head: string | null;
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

/** Read the current git HEAD commit hash. Returns null if not a git repo. */
function getGitHead(workspaceRoot: string): string | null {
    try {
        return execFileSync('git', ['rev-parse', 'HEAD'], {
            cwd: workspaceRoot,
            encoding: 'utf-8',
            timeout: 5000,
        }).trim();
    } catch {
        return null;
    }
}

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
// Graph building — uses tree-sitter WASM for all symbol extraction
// ---------------------------------------------------------------------------

async function buildRepoGraph(workspaceRoot: string): Promise<RepoGraphData | null> {
    const files: Record<string, FileSymbolsData> = {};
    const sourceFiles = collectSourceFiles(workspaceRoot);

    if (sourceFiles.length === 0) {
        return null;
    }

    const useTreeSitter = isTreeSitterReady();

    for (const relPath of sourceFiles) {
        const absPath = path.join(workspaceRoot, relPath);

        try {
            if (useTreeSitter) {
                // Primary: tree-sitter WASM (8 languages, AST-accurate)
                const source = fs.readFileSync(absPath);
                const result: FileSymbols = await tsExtractDefinitions(relPath, source);
                files[relPath] = {
                    file_path: relPath,
                    definitions: result.definitions.map(d => ({
                        ...d,
                        file_path: relPath,
                    })),
                    references: result.references.map(r => ({
                        ...r,
                        file_path: relPath,
                    })),
                    language: result.language,
                };
            } else {
                // Fallback: regex-based import/reference extraction only
                // (treeSitterService.extractDefinitions also falls back to regex
                //  internally, but we handle the case where init hasn't been called)
                const source = fs.readFileSync(absPath);
                const result = await tsExtractDefinitions(relPath, source);
                files[relPath] = {
                    file_path: relPath,
                    definitions: result.definitions.map(d => ({ ...d, file_path: relPath })),
                    references: result.references.map(r => ({ ...r, file_path: relPath })),
                    language: result.language,
                };
            }
        } catch {
            // Skip files that can't be parsed
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
        `${totalDefs} definitions, ${totalRefs} references (tree-sitter=${useTreeSitter})`,
    );

    return {
        git_head: getGitHead(workspaceRoot),
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

function loadCachedGraph(indexPath: string): RepoGraphData | null {
    try {
        if (!fs.existsSync(indexPath)) { return null; }
        const raw = fs.readFileSync(indexPath, 'utf-8');
        return JSON.parse(raw) as RepoGraphData;
    } catch { return null; }
}

function isExpired(graph: RepoGraphData, workspaceRoot: string): boolean {
    // Primary: git HEAD changed → branch switch or new commit
    const currentHead = getGitHead(workspaceRoot);
    if (currentHead && graph.git_head && currentHead !== graph.git_head) {
        return true;
    }

    // Secondary: time-based expiry
    const age = Date.now() - graph.built_at;
    if (age > MAX_AGE_MS) { return true; }

    // Tertiary: sample file mtime check (catches uncommitted changes)
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
