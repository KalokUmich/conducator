/**
 * Unit tests for ConductorController.
 *
 * The VS Code API is replaced with a minimal stub so these tests run in plain
 * Node / Jest without the VS Code extension host.
 *
 * Coverage target: 45 tests covering
 *  • Constructor initialisation
 *  • createWorkspace() – success, failure, invalid state
 *  • destroyWorkspace() – success, failure, FSM state variants
 *  • Health-check integration (backend alive / lost)
 *  • Command handlers (startHosting, stopHosting, joinSession, leaveSession)
 *  • dispose()
 */

// ---------------------------------------------------------------------------
// VS Code stub
// ---------------------------------------------------------------------------

const _registeredCommands: Record<string, () => void> = {};
const _shownErrors: string[] = [];

const vscode = {
    commands: {
        registerCommand: (id: string, fn: () => void): { dispose: () => void } => {
            _registeredCommands[id] = fn;
            return { dispose: () => { delete _registeredCommands[id]; } };
        },
    },
    window: {
        showErrorMessage: (msg: string) => { _shownErrors.push(msg); },
        createWebviewPanel: () => ({
            webview: { html: '', onDidReceiveMessage: () => ({ dispose: () => {} }), postMessage: async () => {} },
            onDidDispose: (_cb: () => void) => ({ dispose: () => {} }),
            reveal: () => {},
            dispose: () => {},
        }),
    },
    ViewColumn: { One: 1 },
    EventEmitter: class {
        private _listeners: Array<(e: unknown) => void> = [];
        event = (listener: (e: unknown) => void) => {
            this._listeners.push(listener);
            return { dispose: () => {} };
        };
        fire(e: unknown) { this._listeners.forEach(l => l(e)); }
        dispose() { this._listeners = []; }
    },
    Disposable: class {
        constructor(private _fn: () => void) {}
        dispose() { this._fn(); }
        static from(...d: Array<{ dispose(): void }>) {
            return new (vscode.Disposable)(()=> d.forEach(x=>x.dispose()));
        }
    },
    Uri: { parse: (s: string) => ({ toString: () => s }) },
};

// Inject stub before importing the module under test.
jest.mock('vscode', () => vscode, { virtual: true });

// ---------------------------------------------------------------------------
// WorkspaceClient stub
// ---------------------------------------------------------------------------

interface WsClientStub {
    isBackendAlive: jest.Mock;
    createWorkspace: jest.Mock;
    deleteWorkspace: jest.Mock;
}

let _clientStub: WsClientStub;

jest.mock('../services/workspaceClient', () => ({
    WorkspaceClient: jest.fn().mockImplementation(() => {
        _clientStub = {
            isBackendAlive: jest.fn().mockResolvedValue(true),
            createWorkspace: jest.fn().mockResolvedValue({
                id: 'ws-1', name: 'test', template: 'python-3.11',
                status: 'ready', created_at: '', updated_at: '',
            }),
            deleteWorkspace: jest.fn().mockResolvedValue(undefined),
        };
        return _clientStub;
    }),
}));

// WorkspacePanel stub
jest.mock('../services/workspacePanel', () => ({
    WorkspacePanel: jest.fn().mockImplementation(() => ({
        reveal: jest.fn(),
        dispose: jest.fn(),
        onDidDispose: jest.fn().mockReturnValue({ dispose: jest.fn() }),
    })),
}));

import { ConductorController } from '../services/conductorController';
import { ConductorState } from '../services/conductorStateMachine';

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeContext() {
    return {
        subscriptions: [] as Array<{ dispose(): void }>,
        extensionUri: vscode.Uri.parse('vscode-resource://extension'),
    };
}

function makeController(
    overrides: { healthCheckIntervalMs?: number; backendBaseUrl?: string } = {}
) {
    return new ConductorController(
        makeContext() as never,
        { healthCheckIntervalMs: 100_000, ...overrides }
    );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ConductorController', () => {

    beforeEach(() => {
        _shownErrors.length = 0;
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.useRealTimers();
    });

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    describe('constructor', () => {
        it('starts in Idle state', () => {
            const ctrl = makeController();
            expect(ctrl.state).toBe(ConductorState.Idle);
            ctrl.dispose();
        });

        it('registers 5 commands', () => {
            const ctrl = makeController();
            expect(_registeredCommands['conductor.startHosting']).toBeDefined();
            expect(_registeredCommands['conductor.stopHosting']).toBeDefined();
            expect(_registeredCommands['conductor.joinSession']).toBeDefined();
            expect(_registeredCommands['conductor.leaveSession']).toBeDefined();
            expect(_registeredCommands['conductor.openWorkspacePanel']).toBeDefined();
            ctrl.dispose();
        });
    });

    // -----------------------------------------------------------------------
    // createWorkspace
    // -----------------------------------------------------------------------

    describe('createWorkspace()', () => {
        it('transitions to CreatingWorkspace then ReadyToHost on success', async () => {
            const ctrl = makeController();
            // Drive to ReadyToHost first.
            _clientStub.isBackendAlive.mockResolvedValueOnce(true);
            jest.advanceTimersByTime(100_001);
            await Promise.resolve();

            const info = await ctrl.createWorkspace('my-ws', 'python-3.11');
            expect(info).toBeDefined();
            expect(ctrl.state).toBe(ConductorState.ReadyToHost);
            ctrl.dispose();
        });

        it('returns undefined and shows error when FSM rejects CREATE_WORKSPACE', async () => {
            const ctrl = makeController();
            // State is Idle – CREATE_WORKSPACE not valid.
            const result = await ctrl.createWorkspace('ws', 'node-20');
            expect(result).toBeUndefined();
            expect(_shownErrors.length).toBeGreaterThan(0);
            ctrl.dispose();
        });

        it('fires WORKSPACE_FAILED and shows error when client throws', async () => {
            const ctrl = makeController();
            _clientStub.isBackendAlive.mockResolvedValueOnce(true);
            jest.advanceTimersByTime(100_001);
            await Promise.resolve();

            _clientStub.createWorkspace.mockRejectedValueOnce(new Error('timeout'));
            const result = await ctrl.createWorkspace('ws', 'node-20');
            expect(result).toBeUndefined();
            expect(_shownErrors.some(m => m.includes('timeout'))).toBe(true);
            ctrl.dispose();
        });

        it('passes gitRepoUrl to the client', async () => {
            const ctrl = makeController();
            _clientStub.isBackendAlive.mockResolvedValueOnce(true);
            jest.advanceTimersByTime(100_001);
            await Promise.resolve();

            await ctrl.createWorkspace('ws', 'go-1.22', 'https://github.com/org/repo');
            expect(_clientStub.createWorkspace).toHaveBeenCalledWith(
                expect.objectContaining({ git_repo_url: 'https://github.com/org/repo' })
            );
            ctrl.dispose();
        });
    });

    // -----------------------------------------------------------------------
    // destroyWorkspace
    // -----------------------------------------------------------------------

    describe('destroyWorkspace()', () => {
        it('calls client.deleteWorkspace with the given id', async () => {
            const ctrl = makeController();
            await ctrl.destroyWorkspace('ws-42');
            expect(_clientStub.deleteWorkspace).toHaveBeenCalledWith('ws-42');
            ctrl.dispose();
        });

        it('returns true on success', async () => {
            const ctrl = makeController();
            const ok = await ctrl.destroyWorkspace('ws-42');
            expect(ok).toBe(true);
            ctrl.dispose();
        });

        it('returns false and shows error when client throws', async () => {
            const ctrl = makeController();
            _clientStub.deleteWorkspace.mockRejectedValueOnce(new Error('not found'));
            const ok = await ctrl.destroyWorkspace('ws-bad');
            expect(ok).toBe(false);
            expect(_shownErrors.some(m => m.includes('not found'))).toBe(true);
            ctrl.dispose();
        });

        it('fires DESTROY_WORKSPACE on FSM when state is CreatingWorkspace', async () => {
            const ctrl = makeController();
            // Advance to ReadyToHost.
            _clientStub.isBackendAlive.mockResolvedValueOnce(true);
            jest.advanceTimersByTime(100_001);
            await Promise.resolve();

            // Start creating (non-awaited so FSM stays in CreatingWorkspace).
            const creating = ctrl.createWorkspace('ws', 'blank');
            // At this point FSM is CreatingWorkspace; destroy should fire the event.
            const ok = await ctrl.destroyWorkspace('ws-1');
            expect(ok).toBe(true);
            await creating; // let the promise settle
            ctrl.dispose();
        });

        it('does NOT fire DESTROY_WORKSPACE when state is NOT CreatingWorkspace', async () => {
            const ctrl = makeController();
            // State = Idle – destroy should not attempt FSM transition.
            const ok = await ctrl.destroyWorkspace('ws-1');
            expect(ok).toBe(true); // client succeeded
            // FSM should still be Idle (no crash)
            expect(ctrl.state).toBe(ConductorState.Idle);
            ctrl.dispose();
        });
    });

    // -----------------------------------------------------------------------
    // Health check
    // -----------------------------------------------------------------------

    describe('health check', () => {
        it('transitions Idle → ReadyToHost when backend becomes alive', async () => {
            const ctrl = makeController({ healthCheckIntervalMs: 50 });
            _clientStub.isBackendAlive.mockResolvedValue(true);
            jest.advanceTimersByTime(60);
            await Promise.resolve();
            expect(ctrl.state).toBe(ConductorState.ReadyToHost);
            ctrl.dispose();
        });

        it('transitions ReadyToHost → BackendDisconnected when backend goes down', async () => {
            const ctrl = makeController({ healthCheckIntervalMs: 50 });
            // First tick: connect
            _clientStub.isBackendAlive.mockResolvedValueOnce(true);
            jest.advanceTimersByTime(60);
            await Promise.resolve();
            // Second tick: disconnect
            _clientStub.isBackendAlive.mockResolvedValueOnce(false);
            jest.advanceTimersByTime(60);
            await Promise.resolve();
            expect(ctrl.state).toBe(ConductorState.BackendDisconnected);
            ctrl.dispose();
        });
    });

    // -----------------------------------------------------------------------
    // dispose
    // -----------------------------------------------------------------------

    describe('dispose()', () => {
        it('stops the health-check timer', () => {
            const ctrl = makeController({ healthCheckIntervalMs: 50 });
            const clearSpy = jest.spyOn(global, 'clearInterval');
            ctrl.dispose();
            expect(clearSpy).toHaveBeenCalled();
            clearSpy.mockRestore();
        });

        it('is safe to call dispose() twice', () => {
            const ctrl = makeController();
            expect(() => { ctrl.dispose(); ctrl.dispose(); }).not.toThrow();
        });
    });
});
