/**
 * Workspace language detection for CGP style guidelines.
 *
 * Scans the workspace for file extensions and maps them to backend Language
 * enum values. Results are cached and cleared when workspace folders change.
 *
 * @module languageDetector
 */
import * as vscode from 'vscode';

/** Maps glob patterns to backend Language enum string values. */
const LANGUAGE_GLOBS: { glob: string; language: string }[] = [
    { glob: '**/*.py', language: 'python' },
    { glob: '**/*.java', language: 'java' },
    { glob: '**/*.{js,jsx,ts,tsx,mjs,cjs}', language: 'javascript' },
    { glob: '**/*.go', language: 'go' },
];

/** Exclude patterns for node_modules, .venv, build artifacts, etc. */
const EXCLUDE_PATTERN = '{**/node_modules/**,**/.venv/**,**/out/**,**/dist/**,**/build/**,**/__pycache__/**,**/.git/**}';

/** Module-level cache for detected languages. */
let cachedLanguages: string[] | null = null;

/**
 * Detect programming languages present in the workspace.
 *
 * Uses `vscode.workspace.findFiles` with `maxResults=1` per language glob
 * to efficiently check for the presence of each language. All globs run
 * in parallel via `Promise.all`.
 *
 * Results are cached in a module-level variable. Call `clearLanguageCache()`
 * to invalidate (e.g., when workspace folders change).
 *
 * @returns Array of backend Language enum string values (e.g., ["python", "javascript"])
 */
export async function detectWorkspaceLanguages(): Promise<string[]> {
    if (cachedLanguages !== null) {
        return cachedLanguages;
    }

    const results = await Promise.all(
        LANGUAGE_GLOBS.map(async ({ glob, language }) => {
            const files = await vscode.workspace.findFiles(glob, EXCLUDE_PATTERN, 1);
            return files.length > 0 ? language : null;
        })
    );

    cachedLanguages = results.filter((lang): lang is string => lang !== null);
    return cachedLanguages;
}

/**
 * Clear the cached language detection results.
 *
 * Should be called when workspace folders change so that the next call
 * to `detectWorkspaceLanguages()` re-scans the workspace.
 */
export function clearLanguageCache(): void {
    cachedLanguages = null;
}
