/**
 * HTTP client for the Conductor embedding backend.
 *
 * Sends batches of text strings to the backend `/embeddings/embed` endpoint
 * and returns the resulting vectors as plain number arrays.
 *
 * @module services/embeddingClient
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EmbedResponse {
    vectors: number[][];
    model:   string;
    dim:     number;
}

// ---------------------------------------------------------------------------
// EmbeddingClient
// ---------------------------------------------------------------------------

export class EmbeddingClient {
    private readonly _baseUrl: string;

    constructor(baseUrl: string) {
        // Strip trailing slash for consistent URL construction.
        this._baseUrl = baseUrl.replace(/\/+$/, '');
    }

    /**
     * Embed a batch of texts and return one vector per text.
     *
     * @param texts - Non-empty array of strings to embed.
     * @returns     Array of number arrays, one per input text.
     * @throws      On network error, non-2xx response, or invalid payload shape.
     */
    async embed(texts: string[]): Promise<number[][]> {
        const url = `${this._baseUrl}/embeddings/embed`;

        let response: Response;
        try {
            response = await fetch(url, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ texts }),
            });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            throw new Error(`Network error calling ${url}: ${msg}`);
        }

        if (!response.ok) {
            throw new Error(
                `Embedding request failed with status ${response.status}: ${url}`,
            );
        }

        const data = await response.json() as Partial<EmbedResponse>;

        if (!Array.isArray(data.vectors)) {
            throw new Error(
                `Invalid embedding response: 'vectors' field is missing or not an array`,
            );
        }

        if (data.vectors.length !== texts.length) {
            throw new Error(
                `Embedding count mismatch: sent ${texts.length} texts but received ` +
                `${data.vectors.length} vectors`,
            );
        }

        return data.vectors;
    }

    /**
     * Convert a plain number array to a `Float32Array`.
     *
     * Each call allocates a new `Float32Array` so the returned buffer is
     * independent from any other call.
     *
     * @param v - Source vector values.
     */
    toFloat32Array(v: number[]): Float32Array {
        return new Float32Array(v);
    }
}

