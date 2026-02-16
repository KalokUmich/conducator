/**
 * Unit tests for SSO Identity Cache — validation, expiry, and wrapping logic.
 *
 * Run after compilation:
 *   node --test out/tests/ssoIdentityCache.test.js
 */
import { describe, it } from 'node:test';
import * as assert from 'node:assert/strict';

import {
    SSO_EXPIRY_MS,
    wrapIdentity,
    getValidIdentity,
    isStale,
} from '../services/ssoIdentityCache';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SAMPLE_IDENTITY: Record<string, unknown> = {
    email: 'alice@example.com',
    arn: 'arn:aws:sts::123456789012:assumed-role/ViewOnlyAccess/alice@example.com',
    user_id: 'AROA123456789:alice@example.com',
    account_id: '123456789012',
    account_name: 'my-account',
    role_name: 'ViewOnlyAccess',
    accounts: [{ accountId: '123456789012', accountName: 'my-account' }],
    roles: [{ roleName: 'ViewOnlyAccess', accountId: '123456789012' }],
};

// ---------------------------------------------------------------------------
// wrapIdentity
// ---------------------------------------------------------------------------

describe('wrapIdentity', () => {
    it('wraps identity with provided timestamp', () => {
        const now = 1700000000000;
        const result = wrapIdentity(SAMPLE_IDENTITY, now);

        assert.deepEqual(result.identity, SAMPLE_IDENTITY);
        assert.equal(result.storedAt, now);
    });

    it('uses Date.now() by default', () => {
        const before = Date.now();
        const result = wrapIdentity(SAMPLE_IDENTITY);
        const after = Date.now();

        assert.deepEqual(result.identity, SAMPLE_IDENTITY);
        assert.ok(result.storedAt >= before);
        assert.ok(result.storedAt <= after);
    });
});

// ---------------------------------------------------------------------------
// getValidIdentity
// ---------------------------------------------------------------------------

describe('getValidIdentity', () => {
    const now = 1700000000000;

    // --- Valid (non-expired) ---

    it('returns identity when stored just now', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now);
        const result = getValidIdentity(stored, now);

        assert.deepEqual(result, SAMPLE_IDENTITY);
    });

    it('returns identity when stored 23h59m ago', () => {
        const almostExpired = now - SSO_EXPIRY_MS + 60_000; // 1 minute before expiry
        const stored = wrapIdentity(SAMPLE_IDENTITY, almostExpired);
        const result = getValidIdentity(stored, now);

        assert.deepEqual(result, SAMPLE_IDENTITY);
    });

    it('returns identity when stored 1 second ago', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now - 1000);
        const result = getValidIdentity(stored, now);

        assert.deepEqual(result, SAMPLE_IDENTITY);
    });

    // --- Expired ---

    it('returns null when stored exactly 24h ago', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now - SSO_EXPIRY_MS);
        const result = getValidIdentity(stored, now);

        assert.equal(result, null);
    });

    it('returns null when stored 25h ago', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now - SSO_EXPIRY_MS - 3600_000);
        const result = getValidIdentity(stored, now);

        assert.equal(result, null);
    });

    it('returns null when stored 7 days ago', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now - 7 * 24 * 60 * 60 * 1000);
        const result = getValidIdentity(stored, now);

        assert.equal(result, null);
    });

    // --- Old format (no storedAt) ---

    it('returns null for old format (raw identity without storedAt)', () => {
        const result = getValidIdentity(SAMPLE_IDENTITY, now);

        assert.equal(result, null);
    });

    it('returns null for object with identity but no storedAt', () => {
        const result = getValidIdentity({ identity: SAMPLE_IDENTITY }, now);

        assert.equal(result, null);
    });

    it('returns null for object with storedAt but no identity', () => {
        const result = getValidIdentity({ storedAt: now }, now);

        assert.equal(result, null);
    });

    // --- Missing / falsy ---

    it('returns null for undefined', () => {
        assert.equal(getValidIdentity(undefined, now), null);
    });

    it('returns null for null', () => {
        assert.equal(getValidIdentity(null, now), null);
    });

    it('returns null for empty string', () => {
        assert.equal(getValidIdentity('', now), null);
    });

    it('returns null for number', () => {
        assert.equal(getValidIdentity(42, now), null);
    });

    it('returns null for empty object', () => {
        assert.equal(getValidIdentity({}, now), null);
    });

    // --- storedAt type edge cases ---

    it('returns null when storedAt is a string', () => {
        const stored = { identity: SAMPLE_IDENTITY, storedAt: '1700000000000' };
        const result = getValidIdentity(stored, now);

        assert.equal(result, null);
    });
});

// ---------------------------------------------------------------------------
// isStale
// ---------------------------------------------------------------------------

describe('isStale', () => {
    const now = 1700000000000;

    it('returns false for undefined (nothing to clear)', () => {
        assert.equal(isStale(undefined, now), false);
    });

    it('returns false for null', () => {
        assert.equal(isStale(null, now), false);
    });

    it('returns false for valid (non-expired) wrapper', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now);
        assert.equal(isStale(stored, now), false);
    });

    it('returns true for expired wrapper', () => {
        const stored = wrapIdentity(SAMPLE_IDENTITY, now - SSO_EXPIRY_MS - 1000);
        assert.equal(isStale(stored, now), true);
    });

    it('returns true for old format (raw identity object)', () => {
        assert.equal(isStale(SAMPLE_IDENTITY, now), true);
    });

    it('returns true for empty object', () => {
        assert.equal(isStale({}, now), true);
    });
});

// ---------------------------------------------------------------------------
// Round-trip: wrapIdentity → getValidIdentity
// ---------------------------------------------------------------------------

describe('round-trip', () => {
    it('wrap then validate immediately returns the original identity', () => {
        const now = Date.now();
        const wrapped = wrapIdentity(SAMPLE_IDENTITY, now);
        const result = getValidIdentity(wrapped, now);

        assert.deepEqual(result, SAMPLE_IDENTITY);
    });

    it('wrap then validate after 24h returns null', () => {
        const storeTime = Date.now();
        const wrapped = wrapIdentity(SAMPLE_IDENTITY, storeTime);
        const result = getValidIdentity(wrapped, storeTime + SSO_EXPIRY_MS);

        assert.equal(result, null);
    });

    it('identity object is not mutated by wrap/validate', () => {
        const original = { ...SAMPLE_IDENTITY };
        const now = Date.now();
        const wrapped = wrapIdentity(original, now);
        getValidIdentity(wrapped, now);

        assert.deepEqual(original, SAMPLE_IDENTITY);
    });
});
