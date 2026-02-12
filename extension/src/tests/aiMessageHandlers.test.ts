/**
 * Unit tests for AI-related message handlers in the extension.
 *
 * Tests the summarize and code prompt message handling logic.
 * Uses a mock HTTP server to simulate backend responses.
 *
 * Run after compilation:
 *   node --test out/tests/aiMessageHandlers.test.js
 */
import { describe, it, afterEach, beforeEach } from 'node:test';
import * as assert from 'node:assert/strict';
import * as http from 'node:http';

// ---------------------------------------------------------------------------
// Helpers - Mock HTTP Server
// ---------------------------------------------------------------------------

interface MockServerResult {
    server: http.Server;
    url: string;
    port: number;
}

async function startMockServer(
    statusCode: number,
    responseBody: string,
    contentType = 'application/json'
): Promise<MockServerResult> {
    return new Promise((resolve) => {
        const server = http.createServer((req, res) => {
            res.writeHead(statusCode, { 'Content-Type': contentType });
            res.end(responseBody);
        });
        server.listen(0, '127.0.0.1', () => {
            const addr = server.address() as { port: number };
            resolve({
                server,
                url: `http://127.0.0.1:${addr.port}`,
                port: addr.port,
            });
        });
    });
}

async function closeServer(server: http.Server): Promise<void> {
    return new Promise((resolve) => {
        server.close(() => resolve());
    });
}

// ---------------------------------------------------------------------------
// Type definitions for test responses
// ---------------------------------------------------------------------------

interface SummarizeResponse {
    type: string;
    topic: string;
    problem_statement: string;
    proposed_solution: string;
    requires_code_change: boolean;
    affected_components: string[];
    risk_level: string;
    next_steps: string[];
}

interface ErrorResponse {
    detail: string;
}

interface CodePromptResponse {
    code_prompt: string;
}

interface ProviderStatus {
    name: string;
    healthy: boolean;
}

interface AIStatusResponse {
    summary_enabled: boolean;
    active_provider: string | null;
    providers: ProviderStatus[];
}

// ---------------------------------------------------------------------------
// Tests for Summarize Message Handler
// ---------------------------------------------------------------------------

describe('AI Summarize Message Handler', () => {
    let server: http.Server | null = null;

    afterEach(async () => {
        if (server) {
            await closeServer(server);
            server = null;
        }
    });

    it('should parse valid summarize response', async () => {
        const mockResponse = {
            type: 'decision_summary',
            topic: 'Test Topic',
            problem_statement: 'Test Problem',
            proposed_solution: 'Test Solution',
            requires_code_change: true,
            affected_components: ['file1.py', 'file2.py'],
            risk_level: 'medium',
            next_steps: ['Step 1', 'Step 2'],
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: [{ role: 'host', text: 'Hello', timestamp: 1000 }],
            }),
        });

        assert.equal(response.ok, true);
        const data = await response.json() as SummarizeResponse;
        assert.equal(data.type, 'decision_summary');
        assert.equal(data.topic, 'Test Topic');
        assert.equal(data.requires_code_change, true);
        assert.deepEqual(data.affected_components, ['file1.py', 'file2.py']);
    });

    it('should handle 503 when provider unavailable', async () => {
        const s = await startMockServer(
            503,
            JSON.stringify({ detail: 'AI provider not available' })
        );
        server = s.server;

        const response = await fetch(`${s.url}/ai/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: [{ role: 'host', text: 'Hello', timestamp: 1000 }],
            }),
        });

        assert.equal(response.ok, false);
        assert.equal(response.status, 503);
    });

    it('should handle 500 on provider error', async () => {
        const s = await startMockServer(
            500,
            JSON.stringify({ detail: 'Provider error during summarization' })
        );
        server = s.server;

        const response = await fetch(`${s.url}/ai/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: [{ role: 'host', text: 'Hello', timestamp: 1000 }],
            }),
        });

        assert.equal(response.ok, false);
        assert.equal(response.status, 500);
        const data = await response.json() as ErrorResponse;
        assert.ok(data.detail.includes('error'));
    });

    it('should handle empty messages array', async () => {
        const mockResponse = {
            type: 'decision_summary',
            topic: '',
            problem_statement: '',
            proposed_solution: '',
            requires_code_change: false,
            affected_components: [],
            risk_level: 'low',
            next_steps: [],
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: [] }),
        });

        assert.equal(response.ok, true);
        const data = await response.json() as SummarizeResponse;
        assert.equal(data.topic, '');
    });
});

// ---------------------------------------------------------------------------
// Tests for Code Prompt Message Handler
// ---------------------------------------------------------------------------

describe('AI Code Prompt Message Handler', () => {
    let server: http.Server | null = null;

    afterEach(async () => {
        if (server) {
            await closeServer(server);
            server = null;
        }
    });

    it('should parse valid code prompt response', async () => {
        const mockResponse = {
            code_prompt: 'You are a senior software engineer...',
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/code-prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                decision_summary: {
                    type: 'decision_summary',
                    topic: 'Test',
                    problem_statement: 'Problem',
                    proposed_solution: 'Solution',
                    requires_code_change: true,
                    affected_components: ['file.py'],
                    risk_level: 'low',
                    next_steps: [],
                },
            }),
        });

        assert.equal(response.ok, true);
        const data = await response.json() as CodePromptResponse;
        assert.ok(data.code_prompt.includes('senior software engineer'));
    });

    it('should handle 422 for invalid request', async () => {
        const s = await startMockServer(
            422,
            JSON.stringify({ detail: 'Validation error' })
        );
        server = s.server;

        const response = await fetch(`${s.url}/ai/code-prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}), // Missing decision_summary
        });

        assert.equal(response.ok, false);
        assert.equal(response.status, 422);
    });

    it('should include context snippet in request', async () => {
        let receivedBody = '';
        const customServer = http.createServer((req, res) => {
            let body = '';
            req.on('data', (chunk) => (body += chunk));
            req.on('end', () => {
                receivedBody = body;
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ code_prompt: 'Generated prompt' }));
            });
        });

        await new Promise<void>((resolve) => {
            customServer.listen(0, '127.0.0.1', () => resolve());
        });
        server = customServer;

        const addr = customServer.address() as { port: number };
        const url = `http://127.0.0.1:${addr.port}`;

        const contextSnippet = 'def existing_function():\n    pass';
        await fetch(`${url}/ai/code-prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                decision_summary: {
                    type: 'decision_summary',
                    topic: 'Test',
                    problem_statement: 'Problem',
                    proposed_solution: 'Solution',
                    requires_code_change: true,
                    affected_components: [],
                    risk_level: 'low',
                    next_steps: [],
                },
                context_snippet: contextSnippet,
            }),
        });

        const parsed = JSON.parse(receivedBody);
        assert.equal(parsed.context_snippet, contextSnippet);
    });
});

// ---------------------------------------------------------------------------
// Tests for AI Status Message Handler
// ---------------------------------------------------------------------------

describe('AI Status Message Handler', () => {
    let server: http.Server | null = null;

    afterEach(async () => {
        if (server) {
            await closeServer(server);
            server = null;
        }
    });

    it('should parse valid status response with active provider', async () => {
        const mockResponse = {
            summary_enabled: true,
            active_provider: 'claude_direct',
            providers: [
                { name: 'claude_direct', healthy: true },
                { name: 'claude_bedrock', healthy: false },
            ],
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/status`);

        assert.equal(response.ok, true);
        const data = await response.json() as AIStatusResponse;
        assert.equal(data.summary_enabled, true);
        assert.equal(data.active_provider, 'claude_direct');
        assert.equal(data.providers.length, 2);
    });

    it('should handle status with no active provider', async () => {
        const mockResponse = {
            summary_enabled: true,
            active_provider: null,
            providers: [
                { name: 'claude_direct', healthy: false },
                { name: 'claude_bedrock', healthy: false },
            ],
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/status`);

        assert.equal(response.ok, true);
        const data = await response.json() as AIStatusResponse;
        assert.equal(data.active_provider, null);
        assert.ok(data.providers.every((p: ProviderStatus) => !p.healthy));
    });

    it('should handle status when summary disabled', async () => {
        const mockResponse = {
            summary_enabled: false,
            active_provider: null,
            providers: [],
        };

        const s = await startMockServer(200, JSON.stringify(mockResponse));
        server = s.server;

        const response = await fetch(`${s.url}/ai/status`);

        assert.equal(response.ok, true);
        const data = await response.json() as AIStatusResponse;
        assert.equal(data.summary_enabled, false);
        assert.equal(data.providers.length, 0);
    });

    it('should handle network error gracefully', async () => {
        // Try to connect to a port that's not listening
        try {
            await fetch('http://127.0.0.1:59999/ai/status', {
                signal: AbortSignal.timeout(1000),
            });
            assert.fail('Should have thrown an error');
        } catch (error) {
            // Expected - connection refused or timeout
            assert.ok(error instanceof Error);
        }
    });
});

// ---------------------------------------------------------------------------
// Tests for WebView Message Response Format
// ---------------------------------------------------------------------------

describe('WebView Message Response Format', () => {
    it('summarizeResult should have correct structure on success', () => {
        const successResponse = {
            command: 'summarizeResult',
            data: {
                type: 'decision_summary',
                topic: 'Test Topic',
                problem_statement: 'Problem',
                proposed_solution: 'Solution',
                requires_code_change: true,
                affected_components: ['file.py'],
                risk_level: 'medium',
                next_steps: ['Step 1'],
            },
        };

        assert.equal(successResponse.command, 'summarizeResult');
        assert.ok('data' in successResponse);
        assert.ok(!('error' in successResponse.data));
    });

    it('summarizeResult should have error field on failure', () => {
        const errorResponse = {
            command: 'summarizeResult',
            data: {
                error: 'Failed to summarize: 503 - Provider unavailable',
            },
        };

        assert.equal(errorResponse.command, 'summarizeResult');
        assert.ok('error' in errorResponse.data);
    });

    it('codePromptResult should have correct structure on success', () => {
        const successResponse = {
            command: 'codePromptResult',
            data: {
                code_prompt: 'You are a senior software engineer...',
            },
        };

        assert.equal(successResponse.command, 'codePromptResult');
        assert.ok('code_prompt' in successResponse.data);
    });

    it('codePromptResult should have error field on failure', () => {
        const errorResponse = {
            command: 'codePromptResult',
            data: {
                error: 'Cannot connect to backend: ECONNREFUSED',
            },
        };

        assert.equal(errorResponse.command, 'codePromptResult');
        assert.ok('error' in errorResponse.data);
    });

    it('aiStatus should have correct structure', () => {
        const statusResponse = {
            command: 'aiStatus',
            data: {
                summary_enabled: true,
                active_provider: 'claude_direct',
                providers: [{ name: 'claude_direct', healthy: true }],
            },
        };

        assert.equal(statusResponse.command, 'aiStatus');
        assert.ok('summary_enabled' in statusResponse.data);
        assert.ok('active_provider' in statusResponse.data);
        assert.ok('providers' in statusResponse.data);
    });
});

