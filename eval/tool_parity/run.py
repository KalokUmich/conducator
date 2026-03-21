"""Compare tool outputs: Python tree-sitter vs TypeScript LSP vs grep fallback.

Usage:
    # Step 1: Generate Python (tree-sitter) baseline
    cd backend && python ../eval/tool_comparison.py --generate-baseline

    # Step 2: In VS Code, run command "Conductor: Compare Local Tools"
    #         This runs LSP + grep on the same files and saves results.

    # Step 3: Compare
    cd backend && python ../eval/tool_comparison.py --compare
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

WORKSPACE = str(Path(__file__).parent.parent.resolve())
BASELINE_FILE = Path(__file__).parent / "tool_baseline.json"
LSP_FILE = Path(__file__).parent / "tool_lsp_results.json"

# Test targets
TARGET_FILE = "backend/app/code_tools/executor.py"
TARGET_SYMBOL = "RemoteToolExecutor"
TARGET_FUNCTION = "execute_tool"


def generate_baseline():
    """Run Python tools (tree-sitter) and save results."""
    from app.code_tools.tools import execute_tool

    results = {}

    # file_outline
    r = execute_tool("file_outline", WORKSPACE, {"path": TARGET_FILE})
    symbols = r.data if isinstance(r.data, list) else r.data.get("symbols", [])
    results["file_outline"] = {
        "count": len(symbols),
        "names": [s.get("name", "") for s in symbols],
        "kinds": [s.get("kind", "") for s in symbols],
        "lines": [s.get("start_line", 0) for s in symbols],
    }

    # find_symbol
    r = execute_tool("find_symbol", WORKSPACE, {"name": TARGET_SYMBOL})
    matches = r.data.get("matches", []) if isinstance(r.data, dict) else r.data
    results["find_symbol"] = {
        "count": len(matches),
        "files": [m.get("file_path", "") for m in matches],
        "lines": [m.get("start_line", 0) for m in matches],
    }

    # expand_symbol
    r = execute_tool("expand_symbol", WORKSPACE, {
        "symbol_name": TARGET_SYMBOL, "file_path": TARGET_FILE,
    })
    results["expand_symbol"] = {
        "start_line": r.data.get("start_line", 0) if r.data else 0,
        "end_line": r.data.get("end_line", 0) if r.data else 0,
        "has_body": bool(r.data.get("source", "")),
    }

    # compressed_view
    r = execute_tool("compressed_view", WORKSPACE, {"file_path": TARGET_FILE})
    content = r.data.get("content", "") if isinstance(r.data, dict) else str(r.data)
    results["compressed_view"] = {
        "symbol_count": r.data.get("symbol_count", 0) if isinstance(r.data, dict) else 0,
        "has_ToolExecutor": "ToolExecutor" in content,
        "has_LocalToolExecutor": "LocalToolExecutor" in content,
        "has_RemoteToolExecutor": "RemoteToolExecutor" in content,
        "has_call_info": "calls:" in content,
    }

    # get_callers
    r = execute_tool("get_callers", WORKSPACE, {"function_name": TARGET_FUNCTION})
    callers = r.data if isinstance(r.data, list) else []
    results["get_callers"] = {
        "count": len(callers),
        "files": list(set(c.get("file_path", "") for c in callers)),
    }

    # module_summary
    r = execute_tool("module_summary", WORKSPACE, {"module_path": "backend/app/code_tools"})
    results["module_summary"] = {
        "success": r.success,
    }

    BASELINE_FILE.write_text(json.dumps(results, indent=2))
    print(f"Baseline saved to {BASELINE_FILE}")
    print(json.dumps(results, indent=2))


def compare():
    """Compare baseline (tree-sitter) with LSP results."""
    if not BASELINE_FILE.exists():
        print("ERROR: Run --generate-baseline first")
        sys.exit(1)
    if not LSP_FILE.exists():
        print("ERROR: Run 'Conductor: Compare Local Tools' in VS Code first")
        sys.exit(1)

    baseline = json.loads(BASELINE_FILE.read_text())
    lsp = json.loads(LSP_FILE.read_text())

    print("=" * 70)
    print("TOOL COMPARISON: Python (tree-sitter) vs TypeScript (LSP) vs (grep)")
    print("=" * 70)

    for tool_name in baseline:
        b = baseline[tool_name]
        l_lsp = lsp.get(tool_name, {}).get("lsp", {})
        l_grep = lsp.get(tool_name, {}).get("grep", {})

        print(f"\n--- {tool_name} ---")

        if tool_name == "file_outline":
            print(f"  Symbol count:  tree-sitter={b['count']}  LSP={l_lsp.get('count', '?')}  grep={l_grep.get('count', '?')}")
            b_names = set(b.get("names", []))
            l_names = set(l_lsp.get("names", []))
            g_names = set(l_grep.get("names", []))
            missing_lsp = b_names - l_names
            extra_lsp = l_names - b_names
            missing_grep = b_names - g_names
            if missing_lsp:
                print(f"  LSP missing:   {missing_lsp}")
            if extra_lsp:
                print(f"  LSP extra:     {extra_lsp}")
            if missing_grep:
                print(f"  grep missing:  {missing_grep}")
            if not missing_lsp and not extra_lsp:
                print(f"  LSP:           EXACT MATCH")

        elif tool_name == "find_symbol":
            print(f"  Match count:   tree-sitter={b['count']}  LSP={l_lsp.get('count', '?')}  grep={l_grep.get('count', '?')}")
            if b.get("lines") and l_lsp.get("lines"):
                print(f"  Lines:         tree-sitter={b['lines']}  LSP={l_lsp['lines']}")
                if b["lines"] == l_lsp["lines"]:
                    print(f"  Lines:         EXACT MATCH")

        elif tool_name == "expand_symbol":
            print(f"  Start line:    tree-sitter={b['start_line']}  LSP={l_lsp.get('start_line', '?')}")
            print(f"  End line:      tree-sitter={b['end_line']}  LSP={l_lsp.get('end_line', '?')}")
            if b["start_line"] == l_lsp.get("start_line") and b["end_line"] == l_lsp.get("end_line"):
                print(f"  Range:         EXACT MATCH")

        elif tool_name == "compressed_view":
            for key in ["has_ToolExecutor", "has_LocalToolExecutor", "has_RemoteToolExecutor"]:
                match = "MATCH" if b.get(key) == l_lsp.get(key) else "MISMATCH"
                print(f"  {key}: tree-sitter={b.get(key)}  LSP={l_lsp.get(key)}  [{match}]")
            print(f"  has_call_info: tree-sitter={b.get('has_call_info')}  LSP={l_lsp.get('has_call_info')}")

        elif tool_name == "get_callers":
            print(f"  Caller count:  tree-sitter={b['count']}  LSP={l_lsp.get('count', '?')}  grep={l_grep.get('count', '?')}")

        else:
            print(f"  tree-sitter: {json.dumps(b, indent=4)}")
            print(f"  LSP:         {json.dumps(l_lsp, indent=4)}")

    print("\n" + "=" * 70)
    print("DONE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate-baseline", action="store_true")
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    if args.generate_baseline:
        generate_baseline()
    elif args.compare:
        compare()
    else:
        parser.print_help()
