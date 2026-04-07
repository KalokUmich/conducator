#!/usr/bin/env bash
# download-grammars.sh
# Downloads pre-built tree-sitter .wasm grammar files for web-tree-sitter.
#
# Grammars are fetched from the tree-sitter GitHub releases. Each language
# grammar repo publishes a tree-sitter-<lang>.wasm artifact that is compatible
# with the web-tree-sitter runtime.
#
# Usage:
#   bash scripts/download-grammars.sh          # from extension/ directory
#   npm run download-grammars                  # via npm script
#
# The downloaded .wasm files are placed in extension/grammars/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_DIR="$(dirname "$SCRIPT_DIR")"
GRAMMARS_DIR="$EXTENSION_DIR/grammars"
USE_LATEST=false

# The grammar tags pinned below are known to produce wasms with an ABI
# compatible with this exact web-tree-sitter version. If package.json drifts,
# we abort instead of silently downloading mismatched grammars.
EXPECTED_WTS_VERSION="0.26.7"

# Parse flags
for arg in "$@"; do
    case "$arg" in
        --latest) USE_LATEST=true ;;
    esac
done

# ---- Sanity check: web-tree-sitter version match --------------------------
# Skipped under --latest, where the developer is intentionally re-pinning.
if [[ "$USE_LATEST" == false ]]; then
    if command -v node &>/dev/null; then
        ACTUAL_WTS_RAW=$(node -p "require('$EXTENSION_DIR/package.json').dependencies['web-tree-sitter']" 2>/dev/null || echo "")
        ACTUAL_WTS_VERSION="${ACTUAL_WTS_RAW#[\^~]}"
        if [[ -n "$ACTUAL_WTS_VERSION" && "$ACTUAL_WTS_VERSION" != "$EXPECTED_WTS_VERSION" ]]; then
            echo "[error] web-tree-sitter version mismatch:" >&2
            echo "        package.json:           $ACTUAL_WTS_VERSION" >&2
            echo "        download-grammars.sh:   $EXPECTED_WTS_VERSION" >&2
            echo "" >&2
            echo "        The grammar tags hardcoded in this script were tested" >&2
            echo "        against web-tree-sitter $EXPECTED_WTS_VERSION. Loading" >&2
            echo "        them under a different version risks silent ABI" >&2
            echo "        mismatch and degraded code analysis." >&2
            echo "" >&2
            echo "        If you intentionally upgraded web-tree-sitter:" >&2
            echo "          1. Update LANGUAGES tags below to ABI-compatible versions" >&2
            echo "          2. Run: bash scripts/download-grammars.sh --latest" >&2
            echo "          3. Regenerate grammars/grammars.sha256:" >&2
            echo "             (cd grammars && sha256sum tree-sitter-*.wasm > grammars.sha256)" >&2
            echo "          4. Update EXPECTED_WTS_VERSION at the top of this script" >&2
            exit 1
        fi
    else
        echo "[warn] node not found -- skipping web-tree-sitter version check" >&2
    fi
fi

mkdir -p "$GRAMMARS_DIR"

# ---- Configuration --------------------------------------------------------
# Each entry: "lang  wasm_filename  github_org/repo  pinned_tag"
# Pinned tags are known-good versions compatible with web-tree-sitter 0.26.x.
# Use --latest to ignore pinned tags and always fetch the latest release.

LANGUAGES=(
    "python      tree-sitter-python.wasm        tree-sitter/tree-sitter-python       v0.23.6"
    "javascript  tree-sitter-javascript.wasm     tree-sitter/tree-sitter-javascript   v0.23.1"
    "typescript  tree-sitter-typescript.wasm     tree-sitter/tree-sitter-typescript   v0.23.2"
    "java        tree-sitter-java.wasm           tree-sitter/tree-sitter-java         v0.23.5"
    "go          tree-sitter-go.wasm             tree-sitter/tree-sitter-go           v0.23.4"
    "rust        tree-sitter-rust.wasm           tree-sitter/tree-sitter-rust         v0.23.2"
    "c           tree-sitter-c.wasm              tree-sitter/tree-sitter-c            v0.23.4"
    "cpp         tree-sitter-cpp.wasm            tree-sitter/tree-sitter-cpp          v0.23.4"
)

# ---- Helpers --------------------------------------------------------------

download_grammar() {
    local lang="$1"
    local wasm_file="$2"
    local repo="$3"
    local tag="$4"
    local dest="$GRAMMARS_DIR/$wasm_file"

    if [[ -f "$dest" ]] && [[ "$USE_LATEST" == false ]]; then
        echo "  [skip] $wasm_file already exists (use --latest to force re-download)"
        return 0
    fi

    # With --latest, always use latest release URL; otherwise use pinned tag
    local url
    if [[ "$USE_LATEST" == true ]]; then
        url="https://github.com/$repo/releases/latest/download/$wasm_file"
        echo "  [download] $lang (latest): $url"
    else
        url="https://github.com/$repo/releases/download/$tag/$wasm_file"
        echo "  [download] $lang ($tag): $url"
    fi

    local fallback_url="https://github.com/$repo/releases/latest/download/$wasm_file"

    if command -v curl &>/dev/null; then
        if ! curl -fsSL --retry 3 -o "$dest" "$url"; then
            if [[ "$USE_LATEST" == false ]]; then
                echo "  [warn] Pinned tag failed, trying latest release..."
                if ! curl -fsSL --retry 3 -o "$dest" "$fallback_url"; then
                    echo "  [error] Could not download $wasm_file."
                    rm -f "$dest"
                    return 1
                fi
            else
                echo "  [error] Could not download $wasm_file."
                rm -f "$dest"
                return 1
            fi
        fi
    elif command -v wget &>/dev/null; then
        if ! wget -q -O "$dest" "$url"; then
            if [[ "$USE_LATEST" == false ]]; then
                if ! wget -q -O "$dest" "$fallback_url"; then
                    echo "  [error] Could not download $wasm_file."
                    rm -f "$dest"
                    return 1
                fi
            else
                echo "  [error] Could not download $wasm_file."
                rm -f "$dest"
                return 1
            fi
        fi
    else
        echo "  [error] Neither curl nor wget found. Cannot download grammars."
        return 1
    fi

    echo "  [ok] $wasm_file"
}

# ---- Main -----------------------------------------------------------------

echo "Downloading tree-sitter grammar .wasm files to $GRAMMARS_DIR"
echo ""

FAILED=0
for entry in "${LANGUAGES[@]}"; do
    # shellcheck disable=SC2086
    set -- $entry
    lang="$1"
    wasm_file="$2"
    repo="$3"
    tag="$4"

    if ! download_grammar "$lang" "$wasm_file" "$repo" "$tag"; then
        FAILED=$((FAILED + 1))
    fi
done

echo ""
if [[ "$FAILED" -gt 0 ]]; then
    echo "[error] $FAILED grammar(s) failed to download." >&2
    echo "        Refusing to proceed -- partial grammars cause silent regex" >&2
    echo "        fallback at runtime, which is hard to diagnose later." >&2
    exit 1
fi
echo "All grammars downloaded successfully."

# ---- Verify SHA256 against pinned manifest --------------------------------
# Skipped under --latest (you're intentionally fetching new bytes; you must
# regenerate grammars/grammars.sha256 yourself afterwards).
CHECKSUM_FILE="$GRAMMARS_DIR/grammars.sha256"
if [[ "$USE_LATEST" == false ]] && [[ -f "$CHECKSUM_FILE" ]]; then
    echo ""
    echo "Verifying SHA256 checksums against grammars.sha256..."
    if ! (cd "$GRAMMARS_DIR" && sha256sum -c grammars.sha256 --quiet); then
        echo "" >&2
        echo "[error] SHA256 verification failed." >&2
        echo "        Downloaded grammars do not match the pinned manifest." >&2
        echo "        Possible causes:" >&2
        echo "          - GitHub re-tagged a release (rare)" >&2
        echo "          - Download corruption / network MITM" >&2
        echo "          - LANGUAGES tags drifted from grammars.sha256" >&2
        echo "" >&2
        echo "        To regenerate the manifest after an intentional update:" >&2
        echo "          (cd grammars && sha256sum tree-sitter-*.wasm > grammars.sha256)" >&2
        exit 1
    fi
    echo "[ok] All grammar checksums match."
elif [[ "$USE_LATEST" == true ]]; then
    echo ""
    echo "[note] --latest used: skipping SHA256 verification."
    echo "       Remember to regenerate grammars/grammars.sha256 if these wasms"
    echo "       become the new pinned versions."
fi

# Also ensure the tree-sitter runtime .wasm file is available.
# web-tree-sitter ships it inside node_modules/web-tree-sitter/.
# 0.26.x renamed it from tree-sitter.wasm to web-tree-sitter.wasm.
TS_WASM_NEW="$EXTENSION_DIR/node_modules/web-tree-sitter/web-tree-sitter.wasm"
TS_WASM_OLD="$EXTENSION_DIR/node_modules/web-tree-sitter/tree-sitter.wasm"
if [[ -f "$TS_WASM_NEW" ]]; then
    cp "$TS_WASM_NEW" "$GRAMMARS_DIR/web-tree-sitter.wasm"
    echo "Copied web-tree-sitter.wasm runtime to grammars/."
elif [[ -f "$TS_WASM_OLD" ]]; then
    cp "$TS_WASM_OLD" "$GRAMMARS_DIR/tree-sitter.wasm"
    echo "Copied tree-sitter.wasm runtime to grammars/."
else
    echo "Note: web-tree-sitter runtime wasm not found."
    echo "      Run 'npm install' first, then re-run this script."
fi

echo "Done."
