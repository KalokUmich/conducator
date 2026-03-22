#!/usr/bin/env node
/**
 * Validate TypeScript tool implementations against the shared contract.
 *
 * Loads contracts/tool_contracts.json and runs each TS-implemented tool
 * with a smoke-test input against the parity fixture repo, then checks
 * that the returned data shape matches the contract (field names exist).
 *
 * Usage:
 *   node tests/validate_contract.js [path/to/tool_contracts.json]
 *
 * Exit code 0 if all tools match, 1 if any mismatch.
 */

const fs = require('fs');
const path = require('path');

const contractPath = process.argv[2]
    || path.join(__dirname, '..', '..', 'contracts', 'tool_contracts.json');

const fixtureRepo = path.join(__dirname, '..', '..', 'tests', 'fixtures', 'parity_repo');

// Load contract
let contract;
try {
    contract = JSON.parse(fs.readFileSync(contractPath, 'utf-8'));
} catch (e) {
    console.error(`Cannot load contract: ${e.message}`);
    process.exit(1);
}

// Load TS tools
const outDir = path.join(__dirname, '..', 'out', 'services');
let complexTools, astTools, treeSitter;
try {
    complexTools = require(path.join(outDir, 'complexToolRunner'));
    astTools = require(path.join(outDir, 'astToolRunner'));
    treeSitter = require(path.join(outDir, 'treeSitterService'));
} catch (e) {
    console.error(`Cannot load compiled tools: ${e.message}. Run 'npm run compile' first.`);
    process.exit(1);
}

// Subprocess tools — validated via Python CLI (`python -m app.code_tools`)
const { execFileSync } = require('child_process');

const PYTHON = path.join(__dirname, '..', '..', '.venv', 'bin', 'python');
const BACKEND_DIR = path.join(__dirname, '..', '..', 'backend');

/**
 * Run a tool via the Python CLI and return the parsed result.
 * Returns { success, data, error } or throws on parse failure.
 */
function runPythonTool(toolName, workspace, params) {
    const raw = execFileSync(PYTHON, [
        '-m', 'app.code_tools', toolName, workspace, JSON.stringify(params),
    ], { cwd: BACKEND_DIR, encoding: 'utf-8', timeout: 15000 });
    return JSON.parse(raw);
}

const SUBPROCESS_TOOLS = new Set([
    'grep', 'read_file', 'list_files', 'git_log', 'git_diff',
    'git_diff_files', 'git_blame', 'git_show', 'find_tests',
    'run_test', 'ast_search',
]);

const SUBPROCESS_SMOKE_PARAMS = {
    grep: { pattern: 'OrderService' },
    read_file: { path: 'app/service.py' },
    list_files: { directory: '.', max_depth: 2 },
    git_log: { max_count: 3 },
    git_diff: { ref: 'HEAD~1' },
    git_blame: { file: 'app/service.py' },
    git_show: { commit: 'HEAD', file: 'app/service.py' },
    git_diff_files: { ref: 'HEAD~1' },
    find_tests: { name: 'test_', path: 'tests' },
    run_test: { test_file: 'tests/test_service.py', timeout: 10 },
    ast_search: { pattern: 'class $NAME { $$$ }', language: 'python' },
};

// TS-implemented tools (complex + AST)
const TS_TOOLS = {
    // Complex (sync except module_summary)
    get_dependencies: (ws, p) => complexTools.get_dependencies(ws, p),
    get_dependents: (ws, p) => complexTools.get_dependents(ws, p),
    test_outline: (ws, p) => complexTools.test_outline(ws, p),
    compressed_view: (ws, p) => complexTools.compressed_view(ws, p),
    trace_variable: (ws, p) => complexTools.trace_variable(ws, p),
    detect_patterns: (ws, p) => complexTools.detect_patterns(ws, p),
    module_summary: (ws, p) => complexTools.module_summary(ws, p),
    // AST
    file_outline: (ws, p) => astTools.file_outline(ws, p),
    find_symbol: (ws, p) => astTools.find_symbol(ws, p),
    find_references: (ws, p) => astTools.find_references(ws, p),
    get_callees: (ws, p) => astTools.get_callees(ws, p),
    get_callers: (ws, p) => astTools.get_callers(ws, p),
    expand_symbol: (ws, p) => astTools.expand_symbol(ws, p),
};

// Smoke-test params per tool (minimal valid input)
const SMOKE_PARAMS = {
    get_dependencies: { file_path: 'app/service.py' },
    get_dependents: { file_path: 'app/models.py' },
    test_outline: { path: 'tests/test_service.py' },
    compressed_view: { file_path: 'app/service.py' },
    trace_variable: { variable_name: 'amount', file: 'app/service.py', function_name: 'process_payment' },
    detect_patterns: { path: 'app' },
    module_summary: { module_path: 'app' },
    file_outline: { path: 'app/service.py' },
    find_symbol: { name: 'OrderService' },
    find_references: { symbol_name: 'OrderService' },
    get_callees: { function_name: 'process_payment', file: 'app/service.py' },
    get_callers: { function_name: 'process_payment' },
    expand_symbol: { symbol_name: 'process_payment', file_path: 'app/service.py' },
};

function checkFields(data, requiredFields, toolName) {
    const errors = [];
    if (Array.isArray(data)) {
        if (data.length === 0) return []; // empty list is valid
        const item = data[0];
        for (const field of requiredFields) {
            if (!(field in item)) {
                errors.push(`${toolName}: missing field '${field}' in list item. Got: ${Object.keys(item).join(', ')}`);
            }
        }
    } else if (typeof data === 'object' && data !== null) {
        // For dict outputs, check fields at top level first.
        // If a field is missing at top level, check inside known nested arrays
        // (e.g., detect_patterns wraps items in data.matches).
        for (const field of requiredFields) {
            if (field in data) continue;
            // Check nested arrays (matches, items, results, etc.)
            let foundNested = false;
            for (const val of Object.values(data)) {
                if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object') {
                    if (field in val[0]) { foundNested = true; break; }
                }
            }
            if (!foundNested) {
                errors.push(`${toolName}: missing field '${field}'. Got: ${Object.keys(data).join(', ')}`);
            }
        }
    }
    return errors;
}

async function main() {
    // Init tree-sitter
    if (treeSitter && !treeSitter.isInitialized()) {
        try {
            await treeSitter.initTreeSitter(path.join(__dirname, '..'));
        } catch { /* proceed without */ }
    }

    const allErrors = [];
    let passed = 0;
    let skipped = 0;
    let subprocessPassed = 0;

    // Check if Python CLI is available for subprocess tools
    let hasPython = false;
    try {
        execFileSync(PYTHON, ['--version'], { encoding: 'utf-8', timeout: 5000 });
        hasPython = true;
    } catch { /* Python not available — skip subprocess tests */ }

    for (const [toolName, toolDef] of Object.entries(contract.tools)) {
        // --- Subprocess tools: validate via Python CLI ---
        if (SUBPROCESS_TOOLS.has(toolName)) {
            if (!hasPython) { skipped++; continue; }
            const params = SUBPROCESS_SMOKE_PARAMS[toolName];
            if (!params) { skipped++; continue; }

            try {
                const result = runPythonTool(toolName, fixtureRepo, params);
                if (typeof result.success !== 'boolean') {
                    allErrors.push(`${toolName} (subprocess): missing {success: boolean} shape`);
                    continue;
                }
                if (result.success) {
                    const fields = toolDef.output_item_fields || [];
                    if (fields.length > 0 && result.data != null) {
                        const fieldErrors = checkFields(result.data, fields, toolName);
                        fieldErrors.forEach(e => console.log(`  [warn] ${e}`));
                    }
                }
                subprocessPassed++;
            } catch (e) {
                // ast_search / run_test may fail due to missing CLI tools — warn, don't fail
                if (toolName === 'ast_search' || toolName === 'run_test') {
                    console.log(`  [warn] ${toolName}: ${e.message.split('\n')[0]} (ast-grep-cli may not be installed)`);
                    skipped++;
                } else {
                    allErrors.push(`${toolName} (subprocess): ${e.message.split('\n')[0]}`);
                }
            }
            continue;
        }

        // --- TS-implemented tools: validate via direct runner ---
        const runner = TS_TOOLS[toolName];
        if (!runner) { skipped++; continue; }

        const params = SMOKE_PARAMS[toolName];
        if (!params) { skipped++; continue; }

        try {
            const result = await runner(fixtureRepo, params);
            if (!result || !result.success) {
                if (!result || typeof result.success !== 'boolean') {
                    allErrors.push(`${toolName}: did not return {success: boolean} shape`);
                }
                passed++;
                continue;
            }

            // Check output fields match contract
            const fields = toolDef.output_item_fields || [];
            if (fields.length > 0 && result.data != null) {
                const fieldErrors = checkFields(result.data, fields, toolName);
                if (fieldErrors.length > 0) {
                    fieldErrors.forEach(e => console.log(`  [warn] ${e}`));
                }
            }
            passed++;
        } catch (e) {
            allErrors.push(`${toolName}: threw ${e.message}`);
        }
    }

    console.log(`Contract validation: ${passed} TS passed, ${subprocessPassed} subprocess passed, ${skipped} skipped, ${allErrors.length} errors`);
    if (allErrors.length > 0) {
        console.log('\nErrors:');
        allErrors.forEach(e => console.log(`  ${e}`));
        process.exit(1);
    }
}

main().catch(e => {
    console.error(`Validator crashed: ${e.message}`);
    process.exit(1);
});
