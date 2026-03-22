/**
 * Local filesystem cache for chat messages.
 *
 * Stores per-room message history in JSON files under the extension's
 * globalStorageUri so that:
 *   1. Messages render instantly on room rejoin (before server sync).
 *   2. Incremental sync only fetches messages newer than the local latest.
 *
 * Storage path: {globalStorageUri}/chat_history/{room_id}.json
 * Cap: MAX_MESSAGES_PER_ROOM most recent messages.
 *
 * @module services/chatLocalStore
 */

import * as fs from 'fs';
import * as path from 'path';

const MAX_MESSAGES_PER_ROOM = 2000;

/** Message shape stored locally — preserves all fields needed for rendering. */
export interface ChatMessageLocal {
    id: string;
    type: string;
    roomId: string;
    userId: string;
    displayName: string;
    role: string;
    content: string;
    ts: number;
    aiData?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
    codeSnippet?: Record<string, unknown>;
    identitySource?: string;
    parentMessageId?: string;
    [key: string]: unknown;
}

/** Wrapper stored on disk. */
export interface LocalMessageCache {
    roomId: string;
    lastSyncTs: number;
    messageCount: number;
    messages: ChatMessageLocal[];
}

export class ChatLocalStore {
    private readonly baseDir: string;

    constructor(globalStoragePath: string) {
        this.baseDir = path.join(globalStoragePath, 'chat_history');
        // Ensure directory exists
        if (!fs.existsSync(this.baseDir)) {
            fs.mkdirSync(this.baseDir, { recursive: true });
        }
    }

    private filePath(roomId: string): string {
        // Sanitize roomId for filesystem safety
        const safe = roomId.replace(/[^a-zA-Z0-9_-]/g, '_');
        return path.join(this.baseDir, `${safe}.json`);
    }

    // ------------------------------------------------------------------
    // Write
    // ------------------------------------------------------------------

    /** Overwrite the full cache for a room. */
    async saveMessages(roomId: string, messages: ChatMessageLocal[]): Promise<void> {
        const trimmed = messages.slice(-MAX_MESSAGES_PER_ROOM);
        const cache: LocalMessageCache = {
            roomId,
            lastSyncTs: trimmed.length > 0 ? trimmed[trimmed.length - 1].ts : 0,
            messageCount: trimmed.length,
            messages: trimmed,
        };
        const fp = this.filePath(roomId);
        await fs.promises.writeFile(fp, JSON.stringify(cache), 'utf-8');
    }

    /** Append new messages (dedup by id, cap at MAX). */
    async appendMessages(roomId: string, newMessages: ChatMessageLocal[]): Promise<void> {
        const existing = await this.loadMessages(roomId);
        const existingIds = new Set(existing ? existing.messages.map(m => m.id) : []);
        const merged = [
            ...(existing ? existing.messages : []),
            ...newMessages.filter(m => !existingIds.has(m.id)),
        ];
        await this.saveMessages(roomId, merged);
    }

    // ------------------------------------------------------------------
    // Read
    // ------------------------------------------------------------------

    /** Load cached messages. Returns null if no cache or corrupt file. */
    async loadMessages(roomId: string): Promise<LocalMessageCache | null> {
        const fp = this.filePath(roomId);
        try {
            if (!fs.existsSync(fp)) { return null; }
            const raw = await fs.promises.readFile(fp, 'utf-8');
            const cache: LocalMessageCache = JSON.parse(raw);
            if (!cache || !Array.isArray(cache.messages)) { return null; }
            return cache;
        } catch {
            return null;
        }
    }

    /** Get the timestamp of the newest locally cached message. */
    async getLatestTimestamp(roomId: string): Promise<number> {
        const cache = await this.loadMessages(roomId);
        return cache ? cache.lastSyncTs : 0;
    }

    /** Get the UUID of the last locally cached message (for incremental sync). */
    async getLastMessageId(roomId: string): Promise<string | null> {
        const cache = await this.loadMessages(roomId);
        if (!cache || cache.messages.length === 0) { return null; }
        return cache.messages[cache.messages.length - 1].id;
    }

    // ------------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------------

    /** Delete local cache for a room. */
    async clearRoom(roomId: string): Promise<void> {
        const fp = this.filePath(roomId);
        try {
            if (fs.existsSync(fp)) {
                await fs.promises.unlink(fp);
            }
        } catch {
            // ignore
        }
    }

    /** List room IDs that have local caches. */
    async listRooms(): Promise<string[]> {
        try {
            const files = await fs.promises.readdir(this.baseDir);
            return files
                .filter(f => f.endsWith('.json'))
                .map(f => f.replace('.json', ''));
        } catch {
            return [];
        }
    }
}
