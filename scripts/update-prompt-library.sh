#!/usr/bin/env bash
# Download latest prompts.chat CSV as a local reference library.
#
# Usage:
#   bash scripts/update-prompt-library.sh
#
# The CSV is stored at config/prompt-library/prompts.csv and committed to the repo.
# It serves as a reference when designing new agent personas (config/agents/*.md)
# and will power a future "prompt search" feature in the WebView.

set -euo pipefail

DEST_DIR="config/prompt-library"
CSV_URL="https://raw.githubusercontent.com/f/prompts.chat/main/prompts.csv"
README_URL="https://raw.githubusercontent.com/f/prompts.chat/main/README.md"

mkdir -p "$DEST_DIR"

echo "Downloading prompts.csv from prompts.chat..."
curl -fsSL "$CSV_URL" -o "$DEST_DIR/prompts.csv"

ROW_COUNT=$(python3 -c "import csv,sys; csv.field_size_limit(sys.maxsize); print(sum(1 for _ in csv.DictReader(open('$DEST_DIR/prompts.csv'))))")
echo "  → $ROW_COUNT prompts downloaded"

# Write a metadata file with source info and timestamp
cat > "$DEST_DIR/SOURCE.md" << EOF
# Prompt Library — prompts.chat

Source: https://github.com/f/prompts.chat (CC0 license)
Downloaded: $(date -u '+%Y-%m-%d %H:%M UTC')
Rows: $ROW_COUNT

## Purpose

1. **Agent design reference** — consult when creating new \`config/agents/*.md\` files
2. **Future WebView search** — users can browse prompts when defining custom agent roles
3. **Pattern learning** — study role assignment, constraint framing, and example usage

## CSV columns

| Column | Description |
|--------|-------------|
| \`act\` | Role name (e.g. "Linux Terminal", "Travel Guide") |
| \`prompt\` | Full prompt text |
| \`for_devs\` | TRUE if developer-focused |
| \`type\` | TEXT or STRUCTURED |
| \`contributor\` | GitHub username |

## How to use

\`\`\`python
import csv
with open("config/prompt-library/prompts.csv") as f:
    for row in csv.DictReader(f):
        print(row["act"], "—", row["prompt"][:80])
\`\`\`
EOF

echo "Done. Library at $DEST_DIR/"
