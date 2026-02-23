"""Symbol-aware code chunking for the RAG pipeline.

Splits source files into semantically meaningful chunks suitable for
embedding.  Each chunk includes the file's import header so the embedding
model has dependency context.

Symbol extraction uses AST parsing where available (Python stdlib ``ast``,
tree-sitter for JS/TS/Java/Go) with transparent fallback to regex when
parsing fails or libraries are not installed.
"""
import ast
import logging
import re
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class CodeChunk:
    """A single chunk of code ready for embedding."""

    content: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str = ""
    symbol_type: str = ""  # function | class | method | block
    language: str = ""
    import_header: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_file(
    content: str,
    file_path: str,
    language: str,
    max_lines: int = 200,
) -> List[CodeChunk]:
    """Split a source file into semantically meaningful chunks.

    Steps:
    1. Extract import block (first N lines until first non-import).
    2. Extract top-level symbols via language-specific regex.
    3. Remaining code → "block" chunks.
    4. Oversized symbols split at blank-line boundaries.
    5. Import header (first 30 lines) prepended to each chunk for context.

    Args:
        content:   Full file text.
        file_path: Workspace-relative path.
        language:  Language ID (python, typescript, javascript, java, go).
        max_lines: Maximum lines per chunk before splitting.

    Returns:
        List of CodeChunk instances.
    """
    if not content.strip():
        return []

    lines = content.splitlines()
    import_header = _extract_import_header(lines, language)

    # Extract symbols
    symbols = _extract_symbols(lines, language)

    if not symbols:
        # No symbols found — chunk the whole file as blocks
        return _chunk_as_blocks(lines, file_path, language, import_header, max_lines)

    chunks: List[CodeChunk] = []
    covered = set()  # line indices covered by symbols

    for sym in symbols:
        sym_lines = lines[sym["start"]:sym["end"]]
        sym_content = "\n".join(sym_lines)

        if len(sym_lines) <= max_lines:
            chunk = CodeChunk(
                content=_prepend_header(import_header, sym_content),
                file_path=file_path,
                start_line=sym["start"] + 1,
                end_line=sym["end"],
                symbol_name=sym["name"],
                symbol_type=sym["type"],
                language=language,
                import_header=import_header,
            )
            chunks.append(chunk)
        else:
            # Split oversized symbol at blank-line boundaries
            sub_chunks = _split_oversized(
                sym_lines, sym["start"], sym["name"], sym["type"],
                file_path, language, import_header, max_lines,
            )
            chunks.extend(sub_chunks)

        for i in range(sym["start"], sym["end"]):
            covered.add(i)

    # Remaining uncovered lines → block chunks
    block_lines: List[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i not in covered:
            block_lines.append((i, line))
        else:
            if block_lines:
                _flush_block(block_lines, chunks, file_path, language, import_header, max_lines)
                block_lines = []

    if block_lines:
        _flush_block(block_lines, chunks, file_path, language, import_header, max_lines)

    return chunks


# ---------------------------------------------------------------------------
# Import header extraction
# ---------------------------------------------------------------------------

_IMPORT_PATTERNS: dict[str, re.Pattern] = {
    "python":     re.compile(r"^(?:import |from )\S"),
    "typescript": re.compile(r"^(?:import |const .+ = require)"),
    "javascript": re.compile(r"^(?:import |const .+ = require)"),
    "java":       re.compile(r"^(?:import |package )"),
    "go":         re.compile(r"^(?:import |package )"),
}

# Lines that are not import lines but are acceptable within the import block
_PASSTHROUGH = re.compile(r"^\s*$|^\s*//|^\s*#|^\s*\*|^\s*/\*|^\s*\*/")

MAX_IMPORT_HEADER_LINES = 30


def _extract_import_header(lines: list[str], language: str) -> str:
    """Return the import block from the top of the file (max 30 lines)."""
    pattern = _IMPORT_PATTERNS.get(language, re.compile(r"^(?:import |from |require|use )"))
    header_lines: list[str] = []
    found_import = False

    for line in lines:
        stripped = line.strip()
        if pattern.match(stripped):
            found_import = True
            header_lines.append(line)
        elif found_import and _PASSTHROUGH.match(stripped):
            header_lines.append(line)
        elif found_import:
            break  # Non-import, non-blank line after imports → done
        elif _PASSTHROUGH.match(stripped):
            header_lines.append(line)  # Comments/blanks before first import
        else:
            break  # Non-import code before any import → no header

        if len(header_lines) >= MAX_IMPORT_HEADER_LINES:
            break

    return "\n".join(header_lines)


# ---------------------------------------------------------------------------
# Tree-sitter lazy loading
# ---------------------------------------------------------------------------

_TS_AVAILABLE: bool | None = None  # None = not yet checked
_TS_PARSERS: dict = {}


def _get_ts_parser(language_key: str):
    """Return a cached tree-sitter ``Parser`` for *language_key*, or ``None``.

    Supported keys: ``javascript``, ``typescript``, ``tsx``, ``java``, ``go``.
    On first ``ImportError`` the global ``_TS_AVAILABLE`` flag is set to
    ``False`` so subsequent calls short-circuit immediately.
    """
    global _TS_AVAILABLE

    if _TS_AVAILABLE is False:
        return None

    if language_key in _TS_PARSERS:
        return _TS_PARSERS[language_key]

    try:
        from tree_sitter import Language, Parser  # noqa: F811

        lang_obj: Language | None = None
        if language_key == "javascript":
            import tree_sitter_javascript as ts_js
            lang_obj = Language(ts_js.language())
        elif language_key == "typescript":
            import tree_sitter_typescript as ts_ts
            lang_obj = Language(ts_ts.language_typescript())
        elif language_key == "tsx":
            import tree_sitter_typescript as ts_ts
            lang_obj = Language(ts_ts.language_tsx())
        elif language_key == "java":
            import tree_sitter_java as ts_java
            lang_obj = Language(ts_java.language())
        elif language_key == "go":
            import tree_sitter_go as ts_go
            lang_obj = Language(ts_go.language())
        else:
            return None

        parser = Parser(lang_obj)
        _TS_PARSERS[language_key] = parser
        _TS_AVAILABLE = True
        return parser

    except (ImportError, Exception) as exc:
        logger.debug("tree-sitter not available for %s: %s", language_key, exc)
        _TS_AVAILABLE = False
        return None


# ---------------------------------------------------------------------------
# AST-based symbol extraction
# ---------------------------------------------------------------------------

def _extract_python_symbols_ast(lines: list[str]) -> list[dict]:
    """Extract top-level Python symbols using the stdlib ``ast`` module.

    Returns ``[]`` on ``SyntaxError`` so the caller can fall back to regex.
    """
    source = "\n".join(lines)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    symbols: list[dict] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno - 1  # ast uses 1-based
            if node.decorator_list:
                start = node.decorator_list[0].lineno - 1
            end = node.end_lineno  # end_lineno is 1-based inclusive; we want exclusive index
            symbols.append({
                "name": node.name,
                "type": "function",
                "start": start,
                "end": end,
            })
        elif isinstance(node, ast.ClassDef):
            start = node.lineno - 1
            if node.decorator_list:
                start = node.decorator_list[0].lineno - 1
            end = node.end_lineno
            symbols.append({
                "name": node.name,
                "type": "class",
                "start": start,
                "end": end,
            })

    return symbols


def _extract_ts_js_symbols_ast(lines: list[str], is_tsx: bool = False) -> list[dict]:
    """Extract JS/TS symbols using tree-sitter."""
    lang_key = "tsx" if is_tsx else "typescript"  # TS grammar is a superset of JS
    parser = _get_ts_parser(lang_key)
    if parser is None:
        return []

    source = "\n".join(lines).encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node

    symbols: list[dict] = []

    for node in root.children:
        actual = node
        # Unwrap export_statement to get inner declaration
        if node.type == "export_statement":
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "generator_function_declaration",
                    "class_declaration",
                    "abstract_class_declaration",
                    "interface_declaration",
                    "type_alias_declaration",
                    "enum_declaration",
                    "lexical_declaration",
                ):
                    actual = child
                    break
            else:
                continue

        sym = _ts_node_to_symbol(actual, node)
        if sym:
            symbols.append(sym)

    return symbols


def _ts_node_to_symbol(actual, outer) -> dict | None:
    """Convert a tree-sitter node to a symbol dict, or return ``None``."""
    start = outer.start_point[0]
    end = outer.end_point[0] + 1  # exclusive

    if actual.type in ("function_declaration", "generator_function_declaration"):
        name_node = actual.child_by_field_name("name")
        if name_node:
            return {"name": name_node.text.decode(), "type": "function", "start": start, "end": end}

    elif actual.type in ("class_declaration", "abstract_class_declaration"):
        name_node = actual.child_by_field_name("name")
        if name_node:
            return {"name": name_node.text.decode(), "type": "class", "start": start, "end": end}

    elif actual.type == "interface_declaration":
        name_node = actual.child_by_field_name("name")
        if name_node:
            return {"name": name_node.text.decode(), "type": "class", "start": start, "end": end}

    elif actual.type == "type_alias_declaration":
        name_node = actual.child_by_field_name("name")
        if name_node:
            return {"name": name_node.text.decode(), "type": "class", "start": start, "end": end}

    elif actual.type == "enum_declaration":
        name_node = actual.child_by_field_name("name")
        if name_node:
            return {"name": name_node.text.decode(), "type": "class", "start": start, "end": end}

    elif actual.type == "lexical_declaration":
        # const foo = async (...) => { ... }
        for child in actual.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node and value_node and value_node.type in ("arrow_function", "function"):
                    return {"name": name_node.text.decode(), "type": "function", "start": start, "end": end}

    return None


def _extract_java_symbols_ast(lines: list[str]) -> list[dict]:
    """Extract Java symbols using tree-sitter."""
    parser = _get_ts_parser("java")
    if parser is None:
        return []

    source = "\n".join(lines).encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node

    symbols: list[dict] = []
    for node in root.children:
        if node.type in (
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "annotation_type_declaration",
            "record_declaration",
        ):
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "name": name_node.text.decode(),
                    "type": "class",
                    "start": node.start_point[0],
                    "end": node.end_point[0] + 1,
                })

    return symbols


def _extract_go_symbols_ast(lines: list[str]) -> list[dict]:
    """Extract Go symbols using tree-sitter."""
    parser = _get_ts_parser("go")
    if parser is None:
        return []

    source = "\n".join(lines).encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node

    symbols: list[dict] = []
    for node in root.children:
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "name": name_node.text.decode(),
                    "type": "function",
                    "start": node.start_point[0],
                    "end": node.end_point[0] + 1,
                })

        elif node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "name": name_node.text.decode(),
                    "type": "function",
                    "start": node.start_point[0],
                    "end": node.end_point[0] + 1,
                })

        elif node.type == "type_declaration":
            # type_declaration contains type_spec children
            for child in node.children:
                if child.type == "type_spec":
                    name_node = child.child_by_field_name("name")
                    type_node = child.child_by_field_name("type")
                    if name_node:
                        sym_type = "class"  # struct/interface → "class" for consistency
                        symbols.append({
                            "name": name_node.text.decode(),
                            "type": sym_type,
                            "start": node.start_point[0],
                            "end": node.end_point[0] + 1,
                        })

    return symbols


# ---------------------------------------------------------------------------
# Symbol extraction — two-tier dispatcher (AST → regex fallback)
# ---------------------------------------------------------------------------

def _extract_symbols(lines: list[str], language: str) -> list[dict]:
    """Extract top-level symbols from the file.

    Tries AST-based extraction first; falls back to regex on failure or
    empty result.

    Returns a list of dicts with keys: name, type, start, end (line indices).
    """
    ast_extractors = {
        "python":     _extract_python_symbols_ast,
        "typescript": lambda l: _extract_ts_js_symbols_ast(l, is_tsx=False),
        "javascript": lambda l: _extract_ts_js_symbols_ast(l, is_tsx=False),
        "java":       _extract_java_symbols_ast,
        "go":         _extract_go_symbols_ast,
    }

    regex_extractors = {
        "python":     _extract_python_symbols,
        "typescript": _extract_ts_js_symbols,
        "javascript": _extract_ts_js_symbols,
        "java":       _extract_java_symbols,
        "go":         _extract_go_symbols,
    }

    # Try AST first
    ast_fn = ast_extractors.get(language)
    if ast_fn is not None:
        try:
            result = ast_fn(lines)
            if result:
                return result
        except Exception as exc:
            logger.debug("AST extraction failed for %s, falling back to regex: %s", language, exc)

    # Fallback to regex
    regex_fn = regex_extractors.get(language)
    if regex_fn is not None:
        return regex_fn(lines)

    return []


def _extract_python_symbols(lines: list[str]) -> list[dict]:
    """Extract Python functions and classes."""
    pattern = re.compile(r"^(async\s+)?def\s+(\w+)|^class\s+(\w+)")
    symbols: list[dict] = []

    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            is_async = m.group(1) is not None
            name = m.group(2) or m.group(3)
            sym_type = "class" if m.group(3) else "function"
            symbols.append({
                "name": name,
                "type": sym_type,
                "start": i,
                "end": i,  # will be extended
            })

    # Determine end of each symbol: next top-level definition or EOF
    for idx, sym in enumerate(symbols):
        if idx + 1 < len(symbols):
            sym["end"] = symbols[idx + 1]["start"]
        else:
            sym["end"] = len(lines)

    return symbols


def _extract_ts_js_symbols(lines: list[str]) -> list[dict]:
    """Extract TypeScript/JavaScript functions, classes, and arrow functions."""
    patterns = [
        (re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^(?:export\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("), "function"),
    ]
    symbols: list[dict] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat, sym_type in patterns:
            m = pat.match(stripped)
            if m and _is_top_level_ts(line):
                symbols.append({
                    "name": m.group(1),
                    "type": sym_type,
                    "start": i,
                    "end": i,
                })
                break

    # Determine end of each symbol
    for idx, sym in enumerate(symbols):
        if idx + 1 < len(symbols):
            sym["end"] = symbols[idx + 1]["start"]
        else:
            sym["end"] = len(lines)

    return symbols


def _is_top_level_ts(line: str) -> bool:
    """Check if a line is at the top level (no indentation)."""
    return not line or not line[0].isspace()


def _extract_java_symbols(lines: list[str]) -> list[dict]:
    """Extract Java classes, interfaces, enums, and methods."""
    class_pattern = re.compile(
        r"^(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|static\s+|final\s+)*"
        r"(?:class|interface|enum)\s+(\w+)"
    )
    symbols: list[dict] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = class_pattern.match(stripped)
        if m and _is_top_level_java(line):
            symbols.append({
                "name": m.group(1),
                "type": "class",
                "start": i,
                "end": i,
            })

    for idx, sym in enumerate(symbols):
        if idx + 1 < len(symbols):
            sym["end"] = symbols[idx + 1]["start"]
        else:
            sym["end"] = len(lines)

    return symbols


def _is_top_level_java(line: str) -> bool:
    """Check if a Java line is at class level (no or minimal indentation)."""
    return len(line) - len(line.lstrip()) <= 4


def _extract_go_symbols(lines: list[str]) -> list[dict]:
    """Extract Go functions and type declarations."""
    func_pattern = re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)")
    type_pattern = re.compile(r"^type\s+(\w+)\s+(?:struct|interface)")
    symbols: list[dict] = []

    for i, line in enumerate(lines):
        m = func_pattern.match(line)
        if m:
            symbols.append({
                "name": m.group(1),
                "type": "function",
                "start": i,
                "end": i,
            })
            continue
        m = type_pattern.match(line)
        if m:
            symbols.append({
                "name": m.group(1),
                "type": "class",
                "start": i,
                "end": i,
            })

    for idx, sym in enumerate(symbols):
        if idx + 1 < len(symbols):
            sym["end"] = symbols[idx + 1]["start"]
        else:
            sym["end"] = len(lines)

    return symbols


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

def _prepend_header(header: str, content: str) -> str:
    """Prepend import header to chunk content if non-empty."""
    if header:
        return header + "\n\n" + content
    return content


def _split_oversized(
    sym_lines: list[str],
    start_offset: int,
    name: str,
    sym_type: str,
    file_path: str,
    language: str,
    import_header: str,
    max_lines: int,
) -> List[CodeChunk]:
    """Split an oversized symbol at blank-line boundaries."""
    chunks: List[CodeChunk] = []
    current: list[str] = []
    current_start = 0

    for i, line in enumerate(sym_lines):
        current.append(line)
        # Split at blank lines once we exceed max_lines
        if len(current) >= max_lines and line.strip() == "":
            chunk_content = "\n".join(current)
            part_num = len(chunks) + 1
            chunks.append(CodeChunk(
                content=_prepend_header(import_header, chunk_content),
                file_path=file_path,
                start_line=start_offset + current_start + 1,
                end_line=start_offset + i + 1,
                symbol_name=f"{name} (part {part_num})",
                symbol_type=sym_type,
                language=language,
                import_header=import_header,
            ))
            current = []
            current_start = i + 1

    # Flush remaining lines
    if current:
        chunk_content = "\n".join(current)
        part_num = len(chunks) + 1
        chunks.append(CodeChunk(
            content=_prepend_header(import_header, chunk_content),
            file_path=file_path,
            start_line=start_offset + current_start + 1,
            end_line=start_offset + len(sym_lines),
            symbol_name=f"{name} (part {part_num})" if len(chunks) > 0 else name,
            symbol_type=sym_type,
            language=language,
            import_header=import_header,
        ))

    return chunks


def _chunk_as_blocks(
    lines: list[str],
    file_path: str,
    language: str,
    import_header: str,
    max_lines: int,
) -> List[CodeChunk]:
    """Chunk lines into fixed-size blocks."""
    chunks: List[CodeChunk] = []
    for start in range(0, len(lines), max_lines):
        end = min(start + max_lines, len(lines))
        block = lines[start:end]
        content = "\n".join(block)
        if not content.strip():
            continue
        chunks.append(CodeChunk(
            content=_prepend_header(import_header, content),
            file_path=file_path,
            start_line=start + 1,
            end_line=end,
            symbol_name="",
            symbol_type="block",
            language=language,
            import_header=import_header,
        ))
    return chunks


def _flush_block(
    block_lines: list[tuple[int, str]],
    chunks: List[CodeChunk],
    file_path: str,
    language: str,
    import_header: str,
    max_lines: int,
) -> None:
    """Flush accumulated uncovered lines as one or more block chunks."""
    if not block_lines:
        return

    content = "\n".join(line for _, line in block_lines)
    if not content.strip():
        return

    start_idx = block_lines[0][0]
    end_idx = block_lines[-1][0]

    if len(block_lines) <= max_lines:
        chunks.append(CodeChunk(
            content=_prepend_header(import_header, content),
            file_path=file_path,
            start_line=start_idx + 1,
            end_line=end_idx + 1,
            symbol_name="",
            symbol_type="block",
            language=language,
            import_header=import_header,
        ))
    else:
        # Split into max_lines-sized sub-blocks
        for i in range(0, len(block_lines), max_lines):
            sub = block_lines[i:i + max_lines]
            sub_content = "\n".join(line for _, line in sub)
            if sub_content.strip():
                chunks.append(CodeChunk(
                    content=_prepend_header(import_header, sub_content),
                    file_path=file_path,
                    start_line=sub[0][0] + 1,
                    end_line=sub[-1][0] + 1,
                    symbol_name="",
                    symbol_type="block",
                    language=language,
                    import_header=import_header,
                ))
