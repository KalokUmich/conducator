/**
 * ConductorController – orchestrates the VS Code extension lifecycle.
 *
 * Responsibilities
 * ----------------
 * - Subscribe to backend health-check events and drive the FSM accordingly.
 * - Expose VS Code commands that trigger FSM transitions and business logic.
 * - Manage the lifetime of the WorkspacePanel (create / reveal / dispose).
 * - Provide createWorkspace() / destroyWorkspace() helpers that call the
 *   WorkspaceClient and fire the appropriate FSM events.
 *
 * All VS Code API calls are injected via the `vscode` namespace so that the
 * class can be tested with the vscode-test-stub.
 *
 * @module services/conductorController
 */

import * as vscode from 'vscode';
import {
    ConductorStateMachine,
    ConductorState,
    ConductorEvent,
} from './conductorStateMachine';
import { WorkspaceClient, WorkspaceInfo } from './workspaceClient';
import { WorkspacePanel } from './workspacePanel';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ControllerOptions {
    /** Interval in milliseconds between backend health-checks. Default 5000. */
    healthCheckIntervalMs?: number;
    /** Base URL for the backend API. Default 'http://localhost:8000'. */
    backendBaseUrl?: string;
}

// ---------------------------------------------------------------------------
// ConductorController
// ---------------------------------------------------------------------------

export class ConductorController implements vscode.Disposable {
    private readonly _fsm: ConductorStateMachine;
    private readonly _client: WorkspaceClient;
    private readonly _disposables: vscode.Disposable[] = [];
    private _healthCheckTimer: ReturnType<typeof setInterval> | undefined;
    private _panel: WorkspacePanel | undefined;
    private readonly _options: Required<ControllerOptions>;

    constructor(
        private readonly _context: vscode.ExtensionContext,
        options: ControllerOptions = {}
    ) {
        this._options = {
            healthCheckIntervalMs: options.healthCheckIntervalMs ?? 5_000,
            backendBaseUrl: options.backendBaseUrl ?? 'http://localhost:8000',
        };
        this._fsm = new ConductorStateMachine();
        this._client = new WorkspaceClient(this._options.backendBaseUrl);

        this._registerCommands();
        this._startHealthCheck();
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /** Current FSM state (read-only). */
    get state(): ConductorState {
        return this._fsm.state;
    }

    /**
     * Attempt to create a new workspace by:
     * 1. Firing CREATE_WORKSPACE on the FSM.
     * 2. Calling the backend WorkspaceClient.
     * 3. Firing WORKSPACE_READY or WORKSPACE_FAILED depending on the result.
     *
     * @param name        Human-readable workspace name.
     * @param template    Template identifier string.
     * @param gitRepoUrl  Optional Git repository URL to clone into the workspace.
     */
    async createWorkspace(
        name: string,
        template: string,
        gitRepoUrl?: string
    ): Promise<WorkspaceInfo | undefined> {
        try {
            this._fsm.send(ConductorEvent.CREATE_WORKSPACE);
        } catch {
            vscode.window.showErrorMessage(
                `Cannot create workspace in state: ${this._fsm.state}`
            );
            return undefined;
        }

        try {
            const info = await this._client.createWorkspace({
                name,
                template,
                git_repo_url: gitRepoUrl,
            });
            this._fsm.send(ConductorEvent.WORKSPACE_READY);
            return info;
        } catch (err: unknown) {
            this._fsm.send(ConductorEvent.WORKSPACE_FAILED);
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`Workspace creation failed: ${msg}`);
            return undefined;
        }
    }

    /**
     * Attempt to destroy an existing workspace by:
     * 1. Calling the backend WorkspaceClient.
     * 2. Firing DESTROY_WORKSPACE on the FSM (only if currently CreatingWorkspace).
     *
     * If the FSM is not in CreatingWorkspace the FSM is left unchanged – the
     * destroy is still attempted on the backend.
     *
     * @param workspaceId  The workspace identifier returned by createWorkspace.
     */
    async destroyWorkspace(workspaceId: string): Promise<boolean> {
        try {
            await this._client.deleteWorkspace(workspaceId);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`Workspace deletion failed: ${msg}`);
            return false;
        }

        if (this._fsm.state === ConductorState.CreatingWorkspace) {
            try {
                this._fsm.send(ConductorEvent.DESTROY_WORKSPACE);
            } catch {
                // FSM refused transition – not fatal
            }
        }
        return true;
    }

    /** Release all resources held by this controller. */
    dispose(): void {
        this._stopHealthCheck();
        this._panel?.dispose();
        for (const d of this._disposables) {
            d.dispose();
        }
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private _registerCommands(): void {
        const reg = (id: string, fn: () => void) =>
            this._disposables.push(
                vscode.commands.registerCommand(id, fn)
            );

        reg('conductor.startHosting', () => this._onStartHosting());
        reg('conductor.stopHosting', () => this._onStopHosting());
        reg('conductor.joinSession', () => this._onJoinSession());
        reg('conductor.leaveSession', () => this._onLeaveSession());
        reg('conductor.openWorkspacePanel', () => this._onOpenWorkspacePanel());
    }

    private _startHealthCheck(): void {
        this._healthCheckTimer = setInterval(
            () => void this._runHealthCheck(),
            this._options.healthCheckIntervalMs
        );
    }

    private _stopHealthCheck(): void {
        if (this._healthCheckTimer !== undefined) {
            clearInterval(this._healthCheckTimer);
            this._healthCheckTimer = undefined;
        }
    }

    private async _runHealthCheck(): Promise<void> {
        const alive = await this._client.isBackendAlive();
        const current = this._fsm.state;

        if (alive && current === ConductorState.BackendDisconnected) {
            try { this._fsm.send(ConductorEvent.BACKEND_CONNECTED); } catch { /* ignore */ }
        } else if (alive && current === ConductorState.Idle) {
            try { this._fsm.send(ConductorEvent.BACKEND_CONNECTED); } catch { /* ignore */ }
        } else if (!alive && current !== ConductorState.Idle && current !== ConductorState.BackendDisconnected) {
            try { this._fsm.send(ConductorEvent.BACKEND_LOST); } catch { /* ignore */ }
        }
    }

    private _onStartHosting(): void {
        try {
            this._fsm.send(ConductorEvent.START_HOSTING);
        } catch {
            vscode.window.showErrorMessage(
                `Cannot start hosting in state: ${this._fsm.state}`
            );
        }
    }

    private _onStopHosting(): void {
        try {
            this._fsm.send(ConductorEvent.STOP_HOSTING);
        } catch {
            vscode.window.showErrorMessage(
                `Cannot stop hosting in state: ${this._fsm.state}`
            );
        }
    }

    private _onJoinSession(): void {
        try {
            this._fsm.send(ConductorEvent.JOIN_SESSION);
        } catch {
            vscode.window.showErrorMessage(
                `Cannot join session in state: ${this._fsm.state}`
            );
        }
    }

    private _onLeaveSession(): void {
        try {
            this._fsm.send(ConductorEvent.LEAVE_SESSION);
        } catch {
            vscode.window.showErrorMessage(
                `Cannot leave session in state: ${this._fsm.state}`
            );
        }
    }

    private _onOpenWorkspacePanel(): void {
        if (this._panel) {
            this._panel.reveal();
        } else {
            this._panel = new WorkspacePanel(
                this._context.extensionUri,
                async (name, template, gitUrl) => {
                    const info = await this.createWorkspace(name, template, gitUrl);
                    if (info) {
                        this._panel?.dispose();
                        this._panel = undefined;
                    }
                }
            );
            this._panel.onDidDispose(() => {
                this._panel = undefined;
            });
        }
    }
}
