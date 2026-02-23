/**
 * Tests for projectMetadataCollector.
 *
 * Uses temp directories to simulate workspace layouts.
 * No VS Code dependency.
 *
 * Run after compilation:
 *   node --test out/tests/projectMetadataCollector.test.js
 */
import { describe, it, beforeEach, afterEach } from 'node:test';
import * as assert from 'node:assert/strict';
import * as fs from 'fs/promises';
import * as path from 'path';
import * as os from 'os';

import {
    collectProjectMetadata,
    clearProjectMetadataCache,
    WorkspaceFolder,
} from '../services/projectMetadataCollector';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let tmpDir: string;

async function makeTmpDir(): Promise<string> {
    return fs.mkdtemp(path.join(os.tmpdir(), 'conductor-test-'));
}

function folder(fsPath: string): WorkspaceFolder {
    return { uri: { fsPath } };
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
    clearProjectMetadataCache();
    tmpDir = await makeTmpDir();
});

afterEach(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('collectProjectMetadata', () => {
    it('returns null for empty workspace folders', async () => {
        const result = await collectProjectMetadata([]);
        assert.equal(result, null);
    });

    it('detects project name from package.json', async () => {
        await fs.writeFile(
            path.join(tmpDir, 'package.json'),
            JSON.stringify({ name: 'my-cool-project', dependencies: {} }),
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.equal(result.name, 'my-cool-project');
    });

    it('falls back to directory basename when no manifest has a name', async () => {
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.equal(result.name, path.basename(tmpDir));
    });

    it('detects JS frameworks from package.json dependencies', async () => {
        await fs.writeFile(
            path.join(tmpDir, 'package.json'),
            JSON.stringify({
                name: 'test',
                dependencies: { react: '^18', express: '^4' },
                devDependencies: { typescript: '^5', tailwindcss: '^3' },
            }),
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.ok(result.frameworks.includes('React'));
        assert.ok(result.frameworks.includes('Express'));
        assert.ok(result.frameworks.includes('TypeScript'));
        assert.ok(result.frameworks.includes('Tailwind CSS'));
    });

    it('detects Python frameworks from requirements.txt', async () => {
        clearProjectMetadataCache();
        await fs.writeFile(
            path.join(tmpDir, 'requirements.txt'),
            'fastapi>=0.100\npydantic>=2.0\nboto3\n',
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.ok(result.frameworks.includes('FastAPI'));
        assert.ok(result.frameworks.includes('Pydantic'));
        assert.ok(result.frameworks.includes('boto3'));
        assert.ok(result.languages.includes('python'));
    });

    it('detects Python frameworks from pyproject.toml', async () => {
        clearProjectMetadataCache();
        await fs.writeFile(
            path.join(tmpDir, 'pyproject.toml'),
            '[project]\ndependencies = ["django>=4.0", "celery>=5"]\n',
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.ok(result.frameworks.includes('Django'));
        assert.ok(result.frameworks.includes('Celery'));
    });

    it('builds directory structure at depth 2, skips ignored dirs', async () => {
        clearProjectMetadataCache();
        // Create dirs: src/components/, src/utils/, node_modules/, .git/
        await fs.mkdir(path.join(tmpDir, 'src', 'components'), { recursive: true });
        await fs.mkdir(path.join(tmpDir, 'src', 'utils'), { recursive: true });
        await fs.mkdir(path.join(tmpDir, 'node_modules', 'foo'), { recursive: true });
        await fs.mkdir(path.join(tmpDir, '.git', 'objects'), { recursive: true });
        await fs.mkdir(path.join(tmpDir, 'docs'), { recursive: true });

        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.ok(result.structure.includes('src/'), 'should contain src/');
        assert.ok(result.structure.includes('components/'), 'should contain components/');
        assert.ok(result.structure.includes('docs/'), 'should contain docs/');
        assert.ok(!result.structure.includes('node_modules'), 'should skip node_modules');
        assert.ok(!result.structure.includes('.git'), 'should skip .git');
    });

    it('caches on repeated calls and clearProjectMetadataCache forces refresh', async () => {
        await fs.writeFile(
            path.join(tmpDir, 'package.json'),
            JSON.stringify({ name: 'v1' }),
        );
        const r1 = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(r1);
        assert.equal(r1.name, 'v1');

        // Overwrite the file — cached result should still return 'v1'
        await fs.writeFile(
            path.join(tmpDir, 'package.json'),
            JSON.stringify({ name: 'v2' }),
        );
        const r2 = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(r2);
        assert.equal(r2.name, 'v1', 'should return cached value');

        // Clear cache and re-read
        clearProjectMetadataCache();
        const r3 = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(r3);
        assert.equal(r3.name, 'v2', 'should return refreshed value');
    });

    it('detects TypeScript language when typescript is a devDependency', async () => {
        clearProjectMetadataCache();
        await fs.writeFile(
            path.join(tmpDir, 'package.json'),
            JSON.stringify({
                name: 'ts-project',
                devDependencies: { typescript: '^5' },
            }),
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.ok(result.languages.includes('typescript'));
    });

    it('detects go.mod module name', async () => {
        clearProjectMetadataCache();
        await fs.writeFile(
            path.join(tmpDir, 'go.mod'),
            'module github.com/org/myservice\n\ngo 1.21\n',
        );
        const result = await collectProjectMetadata([folder(tmpDir)]);
        assert.ok(result);
        assert.equal(result.name, 'github.com/org/myservice');
        assert.ok(result.languages.includes('go'));
    });
});
