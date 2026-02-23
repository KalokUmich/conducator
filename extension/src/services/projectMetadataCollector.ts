/**
 * Project metadata collector for the Explain pipeline.
 *
 * Detects the project name, languages, frameworks, and top-level directory
 * structure from workspace folders.  Results are cached at module level;
 * subsequent calls return in <1 ms.
 *
 * Design constraints:
 * - Takes a generic folder array (not `vscode.WorkspaceFolder`) for testability.
 * - Framework detection is rule-based (manifest key matching), not AST-based.
 * - Directory tree is depth-limited (2 levels) and excludes build artefacts.
 *
 * @module services/projectMetadataCollector
 */

import * as fs from 'fs/promises';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface ProjectMetadata {
    /** Human-readable project name (from package.json, go.mod, or dir name). */
    name: string;
    /** Detected workspace languages (e.g. ["python", "typescript"]). */
    languages: string[];
    /** Detected frameworks / libraries (e.g. ["FastAPI", "React"]). */
    frameworks: string[];
    /** Top-level directory tree (depth <= 2, directories only). */
    structure: string;
}

/** Minimal folder shape accepted by the collector (avoids VS Code dependency). */
export interface WorkspaceFolder {
    uri: { fsPath: string };
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Directories excluded from the structure tree (mirrors workspaceScanner.ts). */
const IGNORED_DIRS = new Set([
    '.git', '.conductor', 'node_modules', 'dist', 'build', 'out', 'target',
    '.venv', 'venv', 'env', '.env', '__pycache__', '.mypy_cache',
    '.pytest_cache', '.tox', 'site-packages', '.coverage', 'htmlcov',
    '.cache', '.idea', '.vscode',
]);

/** npm dependency keys that map to a framework display name. */
const JS_FRAMEWORK_MAP: Record<string, string> = {
    'react':           'React',
    'next':            'Next.js',
    'express':         'Express',
    '@angular/core':   'Angular',
    'vue':             'Vue',
    'typescript':      'TypeScript',
    'tailwindcss':     'Tailwind CSS',
    'vite':            'Vite',
};

/** Python package substrings that map to a framework display name. */
const PY_FRAMEWORK_MAP: Record<string, string> = {
    'fastapi':     'FastAPI',
    'django':      'Django',
    'flask':       'Flask',
    'sqlalchemy':  'SQLAlchemy',
    'pydantic':    'Pydantic',
    'boto3':       'boto3',
    'celery':      'Celery',
};

/** Java groupId/artifactId substrings that map to a framework display name. */
const JAVA_FRAMEWORK_MAP: Record<string, string> = {
    'spring-boot': 'Spring Boot',
    'spring-web':  'Spring Web',
    'hibernate':   'Hibernate',
};

// ---------------------------------------------------------------------------
// Module-level cache
// ---------------------------------------------------------------------------

let _cache: ProjectMetadata | null | undefined;   // undefined = not yet computed

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Collect project metadata from the first workspace folder.
 *
 * Returns `null` when the workspace has no folders.  On cache hit the result
 * is returned synchronously (wrapped in a resolved promise).
 */
export async function collectProjectMetadata(
    workspaceFolders: WorkspaceFolder[],
): Promise<ProjectMetadata | null> {
    if (_cache !== undefined) return _cache;

    if (workspaceFolders.length === 0) {
        _cache = null;
        return null;
    }

    const root = workspaceFolders[0].uri.fsPath;

    // Run all manifest reads + directory scan in parallel.
    const [pkgJson, reqTxt, pyproject, pomXml, goMod, structure] =
        await Promise.all([
            _readFile(path.join(root, 'package.json')),
            _readFile(path.join(root, 'requirements.txt')),
            _readFile(path.join(root, 'pyproject.toml')),
            _readFile(path.join(root, 'pom.xml')),
            _readFile(path.join(root, 'go.mod')),
            _buildStructure(root),
        ]);

    // --- Project name ---
    let name = path.basename(root);
    if (pkgJson) {
        try {
            const parsed = JSON.parse(pkgJson);
            if (typeof parsed.name === 'string' && parsed.name) {
                name = parsed.name;
            }
        } catch { /* invalid JSON — ignore */ }
    }
    if (goMod) {
        const moduleMatch = /^module\s+(\S+)/m.exec(goMod);
        if (moduleMatch && name === path.basename(root)) {
            name = moduleMatch[1];
        }
    }

    // --- Languages ---
    const languages = _detectLanguages(pkgJson, reqTxt, pyproject, pomXml, goMod);

    // --- Frameworks ---
    const frameworks = _detectFrameworks(pkgJson, reqTxt, pyproject, pomXml);

    _cache = { name, languages, frameworks, structure };
    return _cache;
}

/** Clear the cache so the next call re-scans.  Call on workspace folder change. */
export function clearProjectMetadataCache(): void {
    _cache = undefined;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/** Best-effort file read; returns null on any error. */
async function _readFile(filePath: string): Promise<string | null> {
    try {
        return await fs.readFile(filePath, 'utf-8');
    } catch {
        return null;
    }
}

/** Detect languages from manifest file presence. */
function _detectLanguages(
    pkgJson: string | null,
    reqTxt: string | null,
    pyproject: string | null,
    pomXml: string | null,
    goMod: string | null,
): string[] {
    const langs: string[] = [];
    if (pkgJson) {
        try {
            const parsed = JSON.parse(pkgJson);
            const allDeps = {
                ...(parsed.dependencies ?? {}),
                ...(parsed.devDependencies ?? {}),
            };
            if ('typescript' in allDeps) {
                langs.push('typescript');
            } else {
                langs.push('javascript');
            }
        } catch {
            langs.push('javascript');
        }
    }
    if (reqTxt || pyproject) langs.push('python');
    if (pomXml) langs.push('java');
    if (goMod) langs.push('go');
    return langs;
}

/** Detect frameworks from manifest content. */
function _detectFrameworks(
    pkgJson: string | null,
    reqTxt: string | null,
    pyproject: string | null,
    pomXml: string | null,
): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    const add = (name: string) => {
        if (!seen.has(name)) { seen.add(name); result.push(name); }
    };

    // --- package.json ---
    if (pkgJson) {
        try {
            const parsed = JSON.parse(pkgJson);
            const allDeps = {
                ...(parsed.dependencies ?? {}),
                ...(parsed.devDependencies ?? {}),
            };
            for (const [key, displayName] of Object.entries(JS_FRAMEWORK_MAP)) {
                if (key in allDeps) add(displayName);
            }
        } catch { /* ignore */ }
    }

    // --- requirements.txt ---
    if (reqTxt) {
        _matchPythonFrameworks(reqTxt, add);
    }

    // --- pyproject.toml ---
    if (pyproject) {
        _matchPythonFrameworks(pyproject, add);
    }

    // --- pom.xml ---
    if (pomXml) {
        for (const [key, displayName] of Object.entries(JAVA_FRAMEWORK_MAP)) {
            if (pomXml.includes(key)) add(displayName);
        }
    }

    return result;
}

/** Match Python package names in a text block (requirements.txt or pyproject.toml). */
function _matchPythonFrameworks(text: string, add: (name: string) => void): void {
    const lower = text.toLowerCase();
    for (const [key, displayName] of Object.entries(PY_FRAMEWORK_MAP)) {
        if (lower.includes(key)) add(displayName);
    }
}

/**
 * Build a directory-only tree string from `root`, depth <= 2.
 * Hidden directories (starting with `.`) and IGNORED_DIRS are excluded.
 */
async function _buildStructure(root: string, maxDepth = 2): Promise<string> {
    const lines: string[] = [];

    async function walk(dir: string, prefix: string, depth: number): Promise<void> {
        if (depth > maxDepth) return;
        let entries;
        try {
            entries = await fs.readdir(dir, { withFileTypes: true });
        } catch {
            return;
        }

        const dirs = entries
            .filter(e => e.isDirectory() && !e.name.startsWith('.') && !IGNORED_DIRS.has(e.name))
            .sort((a, b) => a.name.localeCompare(b.name));

        for (const d of dirs) {
            lines.push(`${prefix}${d.name}/`);
            await walk(path.join(dir, d.name), prefix + '  ', depth + 1);
        }
    }

    await walk(root, '', 1);
    return lines.join('\n');
}
