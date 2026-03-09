/**
 * Async embedding job queue for the Conductor context enricher.
 *
 * Drains symbol-embedding jobs with a configurable concurrency limit
 * (MAX_CONCURRENCY = 5) and persists the resulting vectors to the
 * local ConductorDb.
 *
 * Features
 * --------
 * - Skip items that are already up-to-date (same sha1 + model).
 * - Retry once on transient embed failures before calling onError.
 * - FIFO queue with bounded parallelism.
 *
 * @module services/embeddingQueue
 */

import type { EmbeddingClient } from './embeddingClient';
import type { ConductorDb }     from './conductorDb';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** A single symbol to embed. */
export interface EmbeddingJobItem {
    symbolId: string;
    /** Text representation that will be sent to the embedding API. */
    text:     string;
    /** SHA-1 of the source content used to detect unchanged items. */
    sha1:     string;
}

/** A batch embedding job. */
export interface EmbeddingJob {
    items:      EmbeddingJobItem[];
    /** Embedding model identifier — used as part of the cache key. */
    model:      string;
    /** Expected dimensionality of the returned vectors. */
    dim:        number;
    /** Called with the number of vectors stored after a successful batch. */
    onComplete?: (count: number) => void;
    /** Called when both the initial attempt and the single retry fail. */
    onError?:    (err: Error) => void;
}

// ---------------------------------------------------------------------------
// EmbeddingQueue
// ---------------------------------------------------------------------------

export class EmbeddingQueue {
    /** Maximum number of jobs that may execute concurrently. */
    static readonly MAX_CONCURRENCY = 5;

    private readonly _client:  EmbeddingClient;
    private readonly _db:      ConductorDb;
    private readonly _pending: EmbeddingJob[] = [];
    private _running = 0;

    constructor(client: EmbeddingClient, db: ConductorDb) {
        this._client = client;
        this._db     = db;
    }

    /** Number of jobs waiting to start. */
    get queueLength(): number { return this._pending.length; }

    /** Number of jobs currently executing. */
    get runningCount(): number { return this._running; }

    /**
     * Add a job to the queue and immediately try to start it if a concurrency
     * slot is free.
     */
    enqueue(job: EmbeddingJob): void {
        this._pending.push(job);
        this._drain();
    }

    /**
     * Cancel all pending (not yet started) jobs.
     *
     * Jobs that are already executing will complete normally — they are
     * unaffected. New jobs enqueued after `cancel()` will be processed as
     * usual.
     */
    cancel(): void {
        this._pending.length = 0;
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private _drain(): void {
        while (this._running < EmbeddingQueue.MAX_CONCURRENCY && this._pending.length > 0) {
            const job = this._pending.shift()!;
            this._running++;
            this._runJob(job).finally(() => {
                this._running--;
                this._drain(); // start the next waiting job
            });
        }
    }

    private async _runJob(job: EmbeddingJob): Promise<void> {
        // Filter items that don't need re-embedding.
        const toEmbed = job.items.filter(item =>
            this._db.needsEmbedding(item.symbolId, item.sha1, job.model),
        );

        if (toEmbed.length === 0) {
            // Nothing to do — call onComplete with 0.
            job.onComplete?.(0);
            return;
        }

        // Attempt embedding with one automatic retry.
        let vectors: number[][];
        try {
            vectors = await this._client.embed(toEmbed.map(i => i.text));
        } catch (firstErr) {
            try {
                vectors = await this._client.embed(toEmbed.map(i => i.text));
            } catch (secondErr) {
                const err = secondErr instanceof Error ? secondErr : new Error(String(secondErr));
                job.onError?.(err);
                return;
            }
        }

        // Persist the returned vectors.
        let stored = 0;
        for (let i = 0; i < toEmbed.length; i++) {
            const item = toEmbed[i];
            const vec  = vectors[i];
            const f32  = this._client.toFloat32Array(vec);
            this._db.upsertSymbolVector({
                symbol_id: item.symbolId,
                dim:       job.dim,
                vector:    Buffer.from(f32.buffer),
                model:     job.model,
                sha1:      item.sha1,
            });
            stored++;
        }

        job.onComplete?.(stored);
    }
}

