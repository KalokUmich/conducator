/**
 * Finite State Machine for the Conductor extension lifecycle.
 *
 * Manages transitions between extension states (Idle, BackendDisconnected,
 * ReadyToHost, CreatingWorkspace, Hosting, Joining, Joined) driven by
 * discrete events. Invalid transitions are rejected with an error.
 *
 * The module is intentionally free of VS Code API dependencies so that it
 * can be unit-tested without the extension host.
 *
 * @module services/conductorStateMachine
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/** All possible states of the Conductor extension. */
export enum ConductorState {
    Idle = 'Idle',
    BackendDisconnected = 'BackendDisconnected',
    ReadyToHost = 'ReadyToHost',
    CreatingWorkspace = 'CreatingWorkspace',
    Hosting = 'Hosting',
    Joining = 'Joining',
    Joined = 'Joined',
}

/** Events that drive state transitions. */
export enum ConductorEvent {
    BACKEND_CONNECTED = 'BACKEND_CONNECTED',
    BACKEND_LOST = 'BACKEND_LOST',
    START_HOSTING = 'START_HOSTING',
    STOP_HOSTING = 'STOP_HOSTING',
    JOIN_SESSION = 'JOIN_SESSION',
    JOIN_SUCCEEDED = 'JOIN_SUCCEEDED',
    JOIN_FAILED = 'JOIN_FAILED',
    LEAVE_SESSION = 'LEAVE_SESSION',
    CREATE_WORKSPACE = 'CREATE_WORKSPACE',
    WORKSPACE_READY = 'WORKSPACE_READY',
    WORKSPACE_FAILED = 'WORKSPACE_FAILED',
    DESTROY_WORKSPACE = 'DESTROY_WORKSPACE',
}

// ---------------------------------------------------------------------------
// Transition table
// ---------------------------------------------------------------------------

/**
 * Lookup table that maps (currentState, event) → nextState.
 * Any pair not present in this table is considered an invalid transition.
 */
const TRANSITION_TABLE: Record<string, ConductorState> = {
    // From Idle
    [`${ConductorState.Idle}:${ConductorEvent.BACKEND_CONNECTED}`]: ConductorState.ReadyToHost,

    // From BackendDisconnected
    [`${ConductorState.BackendDisconnected}:${ConductorEvent.BACKEND_CONNECTED}`]: ConductorState.ReadyToHost,

    // From ReadyToHost
    [`${ConductorState.ReadyToHost}:${ConductorEvent.BACKEND_LOST}`]: ConductorState.BackendDisconnected,
    [`${ConductorState.ReadyToHost}:${ConductorEvent.START_HOSTING}`]: ConductorState.Hosting,
    [`${ConductorState.ReadyToHost}:${ConductorEvent.JOIN_SESSION}`]: ConductorState.Joining,
    [`${ConductorState.ReadyToHost}:${ConductorEvent.CREATE_WORKSPACE}`]: ConductorState.CreatingWorkspace,

    // From CreatingWorkspace
    [`${ConductorState.CreatingWorkspace}:${ConductorEvent.WORKSPACE_READY}`]: ConductorState.ReadyToHost,
    [`${ConductorState.CreatingWorkspace}:${ConductorEvent.WORKSPACE_FAILED}`]: ConductorState.ReadyToHost,
    [`${ConductorState.CreatingWorkspace}:${ConductorEvent.DESTROY_WORKSPACE}`]: ConductorState.ReadyToHost,
    [`${ConductorState.CreatingWorkspace}:${ConductorEvent.BACKEND_LOST}`]: ConductorState.BackendDisconnected,

    // From Hosting
    [`${ConductorState.Hosting}:${ConductorEvent.STOP_HOSTING}`]: ConductorState.ReadyToHost,
    [`${ConductorState.Hosting}:${ConductorEvent.BACKEND_LOST}`]: ConductorState.BackendDisconnected,

    // From Joining
    [`${ConductorState.Joining}:${ConductorEvent.JOIN_SUCCEEDED}`]: ConductorState.Joined,
    [`${ConductorState.Joining}:${ConductorEvent.JOIN_FAILED}`]: ConductorState.ReadyToHost,
    [`${ConductorState.Joining}:${ConductorEvent.BACKEND_LOST}`]: ConductorState.BackendDisconnected,

    // From Joined
    [`${ConductorState.Joined}:${ConductorEvent.LEAVE_SESSION}`]: ConductorState.ReadyToHost,
    [`${ConductorState.Joined}:${ConductorEvent.BACKEND_LOST}`]: ConductorState.BackendDisconnected,
};

// ---------------------------------------------------------------------------
// State machine implementation
// ---------------------------------------------------------------------------

/**
 * Immutable snapshot returned by {@link ConductorStateMachine.getSnapshot}.
 */
export interface StateMachineSnapshot {
    readonly state: ConductorState;
    readonly history: ReadonlyArray<{ from: ConductorState; event: ConductorEvent; to: ConductorState }>;
}

/**
 * Lightweight finite-state machine for the Conductor extension.
 *
 * Usage:
 * ```ts
 * const fsm = new ConductorStateMachine();
 * fsm.send(ConductorEvent.BACKEND_CONNECTED); // → ReadyToHost
 * fsm.send(ConductorEvent.CREATE_WORKSPACE);  // → CreatingWorkspace
 * ```
 */
export class ConductorStateMachine {
    private _state: ConductorState = ConductorState.Idle;
    private _history: Array<{ from: ConductorState; event: ConductorEvent; to: ConductorState }> = [];

    /** Current state of the machine. */
    get state(): ConductorState {
        return this._state;
    }

    /**
     * Send an event to the machine and advance to the next state.
     *
     * @param event - The event to process.
     * @throws {Error} When the (currentState, event) pair has no defined transition.
     */
    send(event: ConductorEvent): ConductorState {
        const key = `${this._state}:${event}`;
        const next = TRANSITION_TABLE[key];
        if (next === undefined) {
            throw new Error(
                `Invalid transition: state=${this._state}, event=${event}`
            );
        }
        this._history.push({ from: this._state, event, to: next });
        this._state = next;
        return next;
    }

    /**
     * Returns an immutable snapshot of the current state and transition history.
     */
    getSnapshot(): StateMachineSnapshot {
        return {
            state: this._state,
            history: Object.freeze([...this._history]),
        };
    }

    /**
     * Resets the machine back to the {@link ConductorState.Idle} state and
     * clears the transition history.
     */
    reset(): void {
        this._state = ConductorState.Idle;
        this._history = [];
    }
}
