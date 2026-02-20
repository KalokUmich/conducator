/**
 * Stack trace parser for the Conductor extension.
 *
 * Parses raw stack trace text (Python, JavaScript/TypeScript, Java, Go) into
 * structured objects and resolves file paths to workspace-relative paths so
 * chat participants can click through to the exact line in their editor.
 *
 * Mirrors the logic in backend/app/chat/stack_trace_parser.py so both sides
 * produce identical structured data.
 */
import * as path from 'path';
import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StackTraceLanguage = 'python' | 'javascript' | 'java' | 'go' | 'unknown';

export interface StackFrame {
    /** Original text of the frame line. */
    raw: string;
    /** Raw file path from the trace (may be absolute). */
    filePath?: string;
    /** Workspace-relative path (resolved by the extension). */
    relativePath?: string;
    lineNumber?: number;
    columnNumber?: number;
    functionName?: string;
    /** True for stdlib / node_modules / etc. */
    isInternal: boolean;
}

export interface ParsedStackTrace {
    language: StackTraceLanguage;
    errorType: string;
    errorMessage: string;
    frames: StackFrame[];
    rawText: string;
}

// ---------------------------------------------------------------------------
// Regex patterns (mirrors backend)
// ---------------------------------------------------------------------------

// Python:  File "path/to/file.py", line 42, in my_func
const PY_FRAME = /^\s*File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+(.+)/;

// JS named:     at MyFunc (/abs/path/file.ts:42:10)
const JS_NAMED = /^\s*at\s+(.+?)\s+\((.+?):(\d+):(\d+)\)/;
// JS named without column:  at MyFunc (/abs/path/file.ts:42)
const JS_NAMED_NC = /^\s*at\s+(.+?)\s+\((.+?):(\d+)\)/;
// JS anonymous:     at /abs/path/file.ts:42:10
const JS_ANON = /^\s*at\s+((?:\/|[A-Za-z]:\\|\.\.?\/|\w).+?):(\d+):(\d+)$/;
// JS anonymous without column:  at /abs/path/file.ts:42
const JS_ANON_NC = /^\s*at\s+((?:\/|[A-Za-z]:\\|\.\.?\/|\w).+?):(\d+)$/;

// Java:     at com.example.App.method(FileName.java:42)
const JAVA_FRAME = /^\s*at\s+([\w.$]+)\.([\w$<>[\]]+)\((.+?\.java):(\d+)\)/;

// Go frame:   \t/path/to/file.go:42 +0x68
const GO_FILE = /^\t(.+\.go):(\d+)(?:\s+\+0x[0-9a-f]+)?$/;
const GO_FUNC = /^(\S+)\(/;

// ---------------------------------------------------------------------------
// Internal path detection
// ---------------------------------------------------------------------------

const INTERNAL_INDICATORS = [
    'node_modules',
    '/usr/lib/python',
    '/usr/local/lib/python',
    '<frozen ',
    '<string>',
    '/internal/',
    'site-packages',
    '/usr/local/go/src/',
    '/pkg/mod/',
];

function isInternal(filePath: string): boolean {
    return INTERNAL_INDICATORS.some(ind => filePath.includes(ind));
}

// ---------------------------------------------------------------------------
// Language detection
// ---------------------------------------------------------------------------

function detectLanguage(text: string): StackTraceLanguage {
    if (text.includes('Traceback (most recent call last)') ||
        /File ".+", line \d+/.test(text)) {
        return 'python';
    }
    if (text.includes('goroutine') && /\t.+\.go:\d+/.test(text)) {
        return 'go';
    }
    if (/\tat [\w.$]+\([\w]+\.java:\d+\)/.test(text)) {
        return 'java';
    }
    if (/\n?\s*at\s+.+?[:(]\d+/.test(text)) {
        return 'javascript';
    }
    return 'unknown';
}

// ---------------------------------------------------------------------------
// Per-language parsers
// ---------------------------------------------------------------------------

function parsePython(lines: string[], result: ParsedStackTrace): void {
    for (const line of lines) {
        const m = PY_FRAME.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[1],
                lineNumber: parseInt(m[2], 10),
                functionName: m[3].trim(),
                isInternal: isInternal(m[1]),
            });
        }
    }
    // Error is last non-blank, non-frame line
    for (let i = lines.length - 1; i >= 0; i--) {
        const s = lines[i].trim();
        if (s && !s.startsWith('File ') && !s.startsWith('Traceback') &&
            !s.startsWith('During handling')) {
            const colon = s.indexOf(':');
            if (colon !== -1) {
                result.errorType = s.slice(0, colon).trim();
                result.errorMessage = s.slice(colon + 1).trim();
            } else {
                result.errorMessage = s;
            }
            break;
        }
    }
}

function parseJavaScript(lines: string[], result: ParsedStackTrace): void {
    for (const line of lines) {
        let m: RegExpExecArray | null;

        m = JS_NAMED.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[2],
                lineNumber: parseInt(m[3], 10),
                columnNumber: parseInt(m[4], 10),
                functionName: m[1].trim(),
                isInternal: isInternal(m[2]),
            });
            continue;
        }

        m = JS_NAMED_NC.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[2],
                lineNumber: parseInt(m[3], 10),
                functionName: m[1].trim(),
                isInternal: isInternal(m[2]),
            });
            continue;
        }

        m = JS_ANON.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[1],
                lineNumber: parseInt(m[2], 10),
                columnNumber: parseInt(m[3], 10),
                isInternal: isInternal(m[1]),
            });
            continue;
        }

        m = JS_ANON_NC.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[1],
                lineNumber: parseInt(m[2], 10),
                isInternal: isInternal(m[1]),
            });
        }
    }
    // Error type = first non-"at" line
    for (const line of lines) {
        const s = line.trim();
        if (s && !s.startsWith('at ')) {
            const colon = s.indexOf(':');
            if (colon !== -1) {
                result.errorType = s.slice(0, colon).trim();
                result.errorMessage = s.slice(colon + 1).trim();
            }
            break;
        }
    }
}

function parseJava(lines: string[], result: ParsedStackTrace): void {
    for (const line of lines) {
        const m = JAVA_FRAME.exec(line);
        if (m) {
            const classPath = m[1];
            const method = m[2];
            const lineNo = parseInt(m[4], 10);
            result.frames.push({
                raw: line.trimEnd(),
                filePath: classPath.replace(/\./g, '/') + '.java',
                lineNumber: lineNo,
                functionName: `${classPath.split('.').pop()}.${method}`,
                isInternal: isInternal(classPath),
            });
        }
    }
    for (const line of lines) {
        const s = line.trim();
        if ((s.includes('Exception') || s.includes('Error')) && !s.startsWith('at ')) {
            const colon = s.indexOf(':');
            if (colon !== -1) {
                const rawType = s.slice(0, colon).trim().split(/\s+/).pop() || '';
                result.errorType = rawType.split('.').pop() || rawType;
                result.errorMessage = s.slice(colon + 1).trim();
            } else {
                const rawType = s.split(/\s+/).pop() || '';
                result.errorType = rawType.split('.').pop() || rawType;
            }
            break;
        }
    }
}

function parseGo(lines: string[], result: ParsedStackTrace): void {
    let pendingFunc: string | undefined;

    for (const line of lines) {
        const m = GO_FILE.exec(line);
        if (m) {
            result.frames.push({
                raw: line.trimEnd(),
                filePath: m[1],
                lineNumber: parseInt(m[2], 10),
                functionName: pendingFunc,
                isInternal: isInternal(m[1]),
            });
            pendingFunc = undefined;
            continue;
        }
        const fm = GO_FUNC.exec(line.trim());
        if (fm && !line.startsWith('goroutine')) {
            pendingFunc = fm[1];
        }
    }
    for (const line of lines) {
        const s = line.trim();
        if (s.startsWith('panic:')) {
            result.errorType = 'panic';
            result.errorMessage = s.slice(6).trim();
            break;
        }
        if (s.startsWith('runtime error:')) {
            result.errorType = 'runtime error';
            result.errorMessage = s.slice(14).trim();
            break;
        }
    }
}

// ---------------------------------------------------------------------------
// Synchronous parse (no I/O)
// ---------------------------------------------------------------------------

/**
 * Parse a raw stack trace string into a structured {@link ParsedStackTrace}.
 * File paths are NOT resolved at this stage — call {@link resolveFramePaths}
 * afterwards to get workspace-relative paths.
 */
export function parseStackTrace(text: string): ParsedStackTrace {
    const result: ParsedStackTrace = {
        language: detectLanguage(text),
        errorType: '',
        errorMessage: '',
        frames: [],
        rawText: text,
    };

    const lines = text.split('\n');

    switch (result.language) {
        case 'python':      parsePython(lines, result);     break;
        case 'javascript':  parseJavaScript(lines, result); break;
        case 'java':        parseJava(lines, result);        break;
        case 'go':          parseGo(lines, result);          break;
        default:
            // Try all, keep first successful
            parsePython(lines, result);
            if (result.frames.length === 0) parseJavaScript(lines, result);
            if (result.frames.length === 0) parseJava(lines, result);
            if (result.frames.length === 0) parseGo(lines, result);
    }

    return result;
}

// ---------------------------------------------------------------------------
// Async path resolution
// ---------------------------------------------------------------------------

/**
 * Attempt to resolve an absolute or ambiguous path from a stack trace to a
 * workspace-relative path.
 *
 * Resolution strategy:
 *  1. If path is absolute and falls inside a workspace folder → strip prefix.
 *  2. Try as a relative path from each workspace root.
 *  3. Search the workspace by filename (last resort, picks best match).
 *
 * Returns `undefined` if the file cannot be found in the workspace.
 */
async function resolveToRelative(
    rawPath: string,
): Promise<string | undefined> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
        return undefined;
    }

    // Normalise separators
    const normalised = rawPath.replace(/\\/g, '/');

    // 1. Absolute path inside workspace
    for (const folder of folders) {
        const folderPath = folder.uri.fsPath.replace(/\\/g, '/');
        if (normalised.startsWith(folderPath)) {
            const rel = normalised.slice(folderPath.length).replace(/^\//, '');
            // Verify file exists
            try {
                await vscode.workspace.fs.stat(vscode.Uri.joinPath(folder.uri, rel));
                return rel;
            } catch { /* not found here */ }
        }
    }

    // 2. Try as relative path from workspace root
    for (const folder of folders) {
        const candidate = vscode.Uri.joinPath(folder.uri, normalised);
        try {
            await vscode.workspace.fs.stat(candidate);
            return vscode.workspace.asRelativePath(candidate);
        } catch { /* not found */ }
    }

    // 3. Find by filename
    const filename = path.posix.basename(normalised);
    if (filename) {
        const found = await vscode.workspace.findFiles(
            `**/${filename}`, '**/node_modules/**', 10,
        );
        if (found.length > 0) {
            // Prefer the file whose path best matches the raw path
            const best = found.sort((a, b) => {
                const aMatch = a.fsPath.replace(/\\/g, '/').includes(normalised) ? 1 : 0;
                const bMatch = b.fsPath.replace(/\\/g, '/').includes(normalised) ? 1 : 0;
                return bMatch - aMatch;
            })[0];
            return vscode.workspace.asRelativePath(best);
        }
    }

    return undefined;
}

/**
 * Resolve file paths for all frames in a parsed stack trace.
 * Mutates each frame in-place by setting `relativePath`.
 *
 * Non-internal frames without a match are left with `relativePath = undefined`
 * so the UI can render them as non-clickable.
 */
export async function resolveFramePaths(parsed: ParsedStackTrace): Promise<void> {
    await Promise.all(
        parsed.frames.map(async (frame) => {
            if (!frame.filePath || frame.isInternal) return;
            frame.relativePath = await resolveToRelative(frame.filePath);
        }),
    );
}
