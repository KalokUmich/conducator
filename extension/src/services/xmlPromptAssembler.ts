/**
 * XML prompt assembler for Conductor.
 *
 * Converts a set of ranked code snippets and a user question into a
 * well-formed XML string ready for submission to an LLM.
 *
 * Output schema
 * -------------
 * ```xml
 * <context>
 *   <file path="src/app.ts" role="current"><![CDATA[...]]></file>
 *   <file path="src/utils.ts" role="definition"><![CDATA[...]]></file>
 *   <file path="src/types.ts" role="related"><![CDATA[...]]></file>
 * </context>
 * <question><![CDATA[...]]></question>
 * ```
 *
 * Constraints
 * -----------
 * - **Max 20 000 tokens (primary) / 80 000 characters (secondary)** — snippets
 *   are trimmed in reverse-priority order (related files first, then definition,
 *   finally current file) until the assembled XML fits within budget.
 * - **Stable deterministic ordering** — current file is always first, then
 *   definition, then related files in the order supplied by the caller.
 * - **CDATA wrapping** — all code content is wrapped in `<![CDATA[…]]>` so
 *   indentation, angle brackets, and special characters are preserved verbatim.
 *   A `]]>` sequence inside content is escaped as `]]]]><![CDATA[>`.
 * - **File path attributes** — every `<file>` element carries a `path` attribute
 *   and a `role` attribute (`current` | `definition` | `related`).
 *
 * @module services/xmlPromptAssembler
 */

// ---------------------------------------------------------------------------
// Public constants
// ---------------------------------------------------------------------------

/** Hard character budget for the entire assembled XML string. */
export const MAX_TOTAL_CHARS = 80_000;

/** Token budget — primary constraint (cl100k_base encoding). */
export const MAX_TOTAL_TOKENS = 20_000;

/** Overhead characters reserved for XML tags, question wrapper, and project section. */
const TAG_OVERHEAD = 1024;

// ---------------------------------------------------------------------------
// Token counting (js-tiktoken with safe fallback)
// ---------------------------------------------------------------------------

import type { Tiktoken } from 'js-tiktoken';

let _encoder: Tiktoken | null = null;
let _encoderFailed = false;

function _getEncoder(): Tiktoken | null {
    if (_encoder) return _encoder;
    if (_encoderFailed) return null;
    try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { getEncoding } = require('js-tiktoken') as typeof import('js-tiktoken');
        _encoder = getEncoding('cl100k_base');
        return _encoder;
    } catch {
        _encoderFailed = true;
        return null;
    }
}

/**
 * Count tokens using cl100k_base encoding, with a character-based fallback
 * if the WASM encoder fails to load.
 */
export function countTokens(text: string): number {
    const enc = _getEncoder();
    if (enc) {
        return enc.encode(text).length;
    }
    // Fallback: ~3.5 chars per token for English/code
    return Math.ceil(text.length / 3.5);
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type SnippetRole = 'current' | 'definition' | 'related';

export interface FileSnippet {
    /** Workspace-relative path shown in the `path` attribute. */
    path: string;
    /** Raw code content — will be CDATA-wrapped. */
    content: string;
    /** Semantic role of this snippet. */
    role: SnippetRole;
}

/** Project-level metadata rendered as a `<project>` element in the XML. */
export interface ProjectMetadataInput {
    name: string;
    languages: string[];
    frameworks: string[];
    structure: string;
}

export interface AssemblerInput {
    /** Snippet from the file containing the cursor (role = "current"). */
    currentFile: FileSnippet;
    /** Snippet from the LSP definition file (role = "definition"; optional). */
    definition?: FileSnippet;
    /** Snippets from related files in ranked order (role = "related"). */
    relatedFiles: FileSnippet[];
    /** The user's question or instruction. */
    question: string;
    /** Optional project-level metadata rendered before file snippets. */
    projectMetadata?: ProjectMetadataInput;
}

export interface AssembleResult {
    /** The assembled XML string ready for the LLM. */
    xml: string;
    /** Whether any snippets were trimmed to fit within budget. */
    wasTrimmed: boolean;
    /** Number of characters in the final XML. */
    charCount: number;
    /** Estimated token count of the final XML. */
    tokenCount: number;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Assemble the XML prompt from ranked context snippets and a user question.
 *
 * Snippets are incorporated in a fixed order:
 *   1. `currentFile`   (always present)
 *   2. `definition`    (if provided)
 *   3. `relatedFiles`  (in supplied order)
 *
 * Budget enforcement uses token count as the primary constraint and character
 * count as a secondary guard. Content is trimmed starting from the
 * lowest-priority items (related files in reverse order, then definition,
 * finally current file).
 *
 * @param input     All context snippets and the user question.
 * @param maxChars  Character budget (default: MAX_TOTAL_CHARS).
 * @param maxTokens Token budget (default: MAX_TOTAL_TOKENS).
 */
export function assembleXmlPrompt(
    input: AssemblerInput,
    maxChars: number = MAX_TOTAL_CHARS,
    maxTokens: number = MAX_TOTAL_TOKENS,
): AssembleResult {
    // ---- Build the ordered list of snippets (stable, deterministic) ----------
    const snippets: FileSnippet[] = [];
    snippets.push(input.currentFile);
    if (input.definition) snippets.push(input.definition);
    for (const r of input.relatedFiles) snippets.push(r);

    // ---- Iteratively trim until the assembled XML fits ----------------------
    let wasTrimmed = false;
    // Keep a mutable copy of content lengths; we never mutate the originals.
    const contents = snippets.map(s => s.content);

    const projectSection = input.projectMetadata
        ? _projectElement(input.projectMetadata)
        : undefined;

    for (let attempt = 0; attempt < snippets.length + 1; attempt++) {
        const xml = _assemble(snippets, contents, input.question, projectSection);
        const tokens = countTokens(xml);

        if (xml.length <= maxChars && tokens <= maxTokens) {
            return { xml, wasTrimmed, charCount: xml.length, tokenCount: tokens };
        }
        wasTrimmed = true;

        // Find the lowest-priority snippet that still has content to trim.
        // Priority (trimming order): related (last → first), definition, current.
        const trimIdx = _nextTrimTarget(contents, snippets);
        if (trimIdx === -1) break; // nothing left to trim

        // Estimate excess in characters. When the token budget is the binding
        // constraint, convert the token excess to characters using the current
        // ratio so we trim approximately the right amount.
        let excessChars: number;
        if (tokens > maxTokens) {
            const charsPerToken = xml.length / Math.max(1, tokens);
            excessChars = Math.ceil((tokens - maxTokens) * charsPerToken) + TAG_OVERHEAD;
        } else {
            excessChars = xml.length - maxChars + TAG_OVERHEAD;
        }
        const trimmed = _trimContent(contents[trimIdx], excessChars);
        contents[trimIdx] = trimmed;
    }

    // Last-resort: return whatever fits in the budget (edge case for huge questions).
    const xml = _assemble(snippets, contents, input.question, projectSection).slice(0, maxChars);
    const tokenCount = countTokens(xml);
    return { xml, wasTrimmed: true, charCount: xml.length, tokenCount };
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/** Escape `]]>` inside CDATA content so the closing delimiter is never present. */
function _escapeCdata(text: string): string {
    return text.replace(/]]>/g, ']]]]><![CDATA[>');
}

/** Wrap text in a CDATA section. */
function _cdata(text: string): string {
    return `<![CDATA[${_escapeCdata(text)}]]>`;
}

/** Escape a string for use in an XML attribute value (double-quoted). */
function _attr(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/** Render a single `<file>` element. */
function _fileElement(snippet: FileSnippet, content: string): string {
    const path = _attr(snippet.path);
    const role = _attr(snippet.role);
    return `  <file path="${path}" role="${role}">${_cdata(content)}</file>`;
}

/** Render a `<project>` element from metadata. */
function _projectElement(meta: ProjectMetadataInput): string {
    const nameAttr = _attr(meta.name);
    const langsAttr = _attr(meta.languages.join(', '));
    const parts: string[] = [];
    parts.push(`  <project name="${nameAttr}" languages="${langsAttr}">`);
    if (meta.frameworks.length > 0) {
        parts.push(`    <frameworks>${meta.frameworks.join(', ')}</frameworks>`);
    }
    if (meta.structure) {
        parts.push(`    <structure>${_cdata('\n' + meta.structure + '\n    ')}</structure>`);
    }
    parts.push('  </project>');
    return parts.join('\n');
}

/** Assemble the full XML string from current content values. */
function _assemble(
    snippets: FileSnippet[],
    contents: string[],
    question: string,
    projectSection?: string,
): string {
    const fileElems = snippets
        .map((s, i) => _fileElement(s, contents[i]))
        .join('\n');

    const contextChildren = projectSection
        ? `${projectSection}\n${fileElems}`
        : fileElems;

    return `<context>\n${contextChildren}\n</context>\n<question>${_cdata(question)}</question>`;
}

/**
 * Return the index of the next snippet to trim, in priority order:
 *   related files last-to-first, then definition, then current.
 * Returns -1 if all contents are already empty or just the truncation marker.
 */
function _nextTrimTarget(contents: string[], snippets: FileSnippet[]): number {
    // Build a trim-priority ordering: related (reversed) → definition → current.
    const order: number[] = [];
    for (let i = snippets.length - 1; i >= 0; i--) {
        if (snippets[i].role === 'related') order.push(i);
    }
    for (let i = 0; i < snippets.length; i++) {
        if (snippets[i].role === 'definition') { order.push(i); break; }
    }
    order.push(0); // current file is always index 0

    for (const idx of order) {
        if (contents[idx].length > 0) return idx;
    }
    return -1;
}

/**
 * Trim `content` by approximately `excess` characters, appending a marker.
 * The result always ends with `\n… [truncated]` to signal truncation.
 */
function _trimContent(content: string, excess: number): string {
    const MARKER = '\n… [truncated]';
    const targetLen = Math.max(0, content.length - excess - MARKER.length);
    if (targetLen === 0) return '';
    // Trim at the last newline before targetLen for a clean cut.
    const slice = content.slice(0, targetLen);
    const lastNl = slice.lastIndexOf('\n');
    const clean = lastNl > 0 ? slice.slice(0, lastNl) : slice;
    return clean + MARKER;
}
