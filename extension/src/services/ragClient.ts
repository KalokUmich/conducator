/**
 * RagClient — communicates with the backend RAG endpoints for codebase
 * indexing and semantic search.
 *
 * All HTTP calls go through the extension host (not the WebView) to
 * satisfy VS Code's CSP restrictions.
 */

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface RagFileChange {
    path: string;
    content?: string;
    action: 'upsert' | 'delete';
}

export interface RagIndexResponse {
    chunks_added: number;
    chunks_removed: number;
    files_processed: number;
}

export interface RagSearchFilters {
    languages?: string[];
    file_patterns?: string[];
}

export interface RagSearchResultItem {
    file_path: string;
    start_line: number;
    end_line: number;
    symbol_name: string;
    symbol_type: string;
    content: string;
    score: number;
    language: string;
}

export interface RagSearchResponse {
    results: RagSearchResultItem[];
    query: string;
    workspace_id: string;
}

// ---------------------------------------------------------------------------
// RagClient
// ---------------------------------------------------------------------------

export class RagClient {
    private readonly _baseUrl: string;
    private _abortController: AbortController | null = null;

    constructor(backendUrl: string) {
        // Normalise: strip trailing slash
        this._baseUrl = backendUrl.replace(/\/+$/, '');
    }

    /**
     * Cancel all in-flight requests.
     */
    cancel(): void {
        if (this._abortController) {
            this._abortController.abort();
            this._abortController = null;
            console.log('[RagClient] Cancelled in-flight requests');
        }
    }

    /**
     * Incrementally index (upsert/delete) files.
     */
    async index(workspaceId: string, files: RagFileChange[]): Promise<RagIndexResponse> {
        return this._post<RagIndexResponse>('/rag/index', {
            workspace_id: workspaceId,
            files,
        });
    }

    /**
     * Full reindex: clear existing index and rebuild from provided files.
     */
    async reindex(workspaceId: string, files: RagFileChange[]): Promise<RagIndexResponse> {
        return this._post<RagIndexResponse>('/rag/reindex', {
            workspace_id: workspaceId,
            files,
        });
    }

    /**
     * Semantic search over the indexed codebase.
     */
    async search(
        workspaceId: string,
        query: string,
        topK?: number,
        filters?: RagSearchFilters,
    ): Promise<RagSearchResponse> {
        const body: Record<string, unknown> = {
            workspace_id: workspaceId,
            query,
        };
        if (topK !== undefined) body.top_k = topK;
        if (filters) body.filters = filters;

        return this._post<RagSearchResponse>('/rag/search', body);
    }

    // -----------------------------------------------------------------------
    // Internal
    // -----------------------------------------------------------------------

    private async _post<T>(path: string, body: unknown): Promise<T> {
        const url = `${this._baseUrl}${path}`;
        const payload = JSON.stringify(body);
        console.log(`[RagClient] POST ${url} (${(payload.length / 1024).toFixed(0)} KB payload)`);

        // Lazily create a shared AbortController for the current batch of requests.
        if (!this._abortController) {
            this._abortController = new AbortController();
        }

        let response: Response;
        try {
            response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
                signal: this._abortController.signal,
            });
        } catch (err) {
            if (err instanceof Error && err.name === 'AbortError') {
                console.log(`[RagClient] POST ${path} aborted`);
                throw err;
            }
            console.error(`[RagClient] Network error: POST ${path}:`, err);
            throw new Error(`Network error calling ${path}: ${err instanceof Error ? err.message : err}`);
        }

        if (!response.ok) {
            const text = await response.text().catch(() => '');
            console.error(`[RagClient] HTTP ${response.status}: POST ${path}: ${text}`);
            throw new Error(`RAG ${path} failed (${response.status}): ${text}`);
        }

        console.log(`[RagClient] POST ${path} → ${response.status} OK`);
        return response.json() as Promise<T>;
    }
}
