"""Pydantic schemas for code intelligence tools."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tool parameter schemas
# ---------------------------------------------------------------------------


class GrepParams(BaseModel):
    pattern: str = Field(..., description="Regex pattern to search for.")
    path: Optional[str] = Field(None, description="Relative path within workspace to search (file or directory).")
    include_glob: Optional[str] = Field(None, description="Glob to filter files, e.g. '*.py' or '**/*.ts'.")
    max_results: int = Field(default=50, ge=1, le=200)


class ReadFileParams(BaseModel):
    path: str = Field(..., description="Relative file path within workspace.")
    start_line: Optional[int] = Field(None, ge=1, description="First line to read (1-based).")
    end_line: Optional[int] = Field(None, ge=1, description="Last line to read (1-based, inclusive).")


class ListFilesParams(BaseModel):
    directory: str = Field(default=".", description="Relative directory within workspace.")
    max_depth: Optional[int] = Field(default=3, ge=1, le=10)
    include_glob: Optional[str] = Field(None, description="Glob to filter, e.g. '*.py'.")


class FindSymbolParams(BaseModel):
    name: str = Field(..., description="Symbol name to find (exact or substring).")
    kind: Optional[str] = Field(None, description="Symbol kind filter: function, class, method, interface, type.")


class FindReferencesParams(BaseModel):
    symbol_name: str = Field(..., description="Symbol name to find references for.")
    file: Optional[str] = Field(None, description="Limit search to this relative file path.")


class FileOutlineParams(BaseModel):
    path: str = Field(..., description="Relative file path within workspace.")


class GetDependenciesParams(BaseModel):
    file_path: str = Field(..., description="Relative file path to find dependencies of.")


class GetDependentsParams(BaseModel):
    file_path: str = Field(..., description="Relative file path to find dependents of.")


class GitLogParams(BaseModel):
    file: Optional[str] = Field(None, description="Relative file path to filter log.")
    n: int = Field(default=10, ge=1, le=50, description="Number of commits to show.")


class GitDiffParams(BaseModel):
    ref1: Optional[str] = Field(default="HEAD~1", description="First git ref.")
    ref2: Optional[str] = Field(default="HEAD", description="Second git ref.")
    file: Optional[str] = Field(None, description="Limit diff to this file.")


class AstSearchParams(BaseModel):
    pattern: str = Field(..., description="ast-grep pattern (e.g. 'def $F($$$ARGS)', 'if $COND: $$$BODY').")
    language: Optional[str] = Field(None, description="Language hint: python, javascript, typescript, go, rust, java, c, cpp.")
    path: Optional[str] = Field(None, description="Relative path within workspace to search (file or directory).")
    max_results: int = Field(default=30, ge=1, le=100)


class GetCalleesParams(BaseModel):
    function_name: str = Field(..., description="Name of the function to inspect.")
    file: str = Field(..., description="Relative file path containing the function.")


class GetCallersParams(BaseModel):
    function_name: str = Field(..., description="Name of the function to find callers of.")
    path: Optional[str] = Field(None, description="Relative path to limit the search.")


# ---------------------------------------------------------------------------
# Tool result schemas
# ---------------------------------------------------------------------------


class GrepMatch(BaseModel):
    file_path: str
    line_number: int
    content: str


class SymbolLocation(BaseModel):
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: str = ""


class ReferenceLocation(BaseModel):
    file_path: str
    line_number: int
    content: str


class FileEntry(BaseModel):
    path: str
    is_dir: bool
    size: Optional[int] = None


class AstMatch(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    text: str
    meta_variables: Dict[str, str] = Field(default_factory=dict)


class CallerInfo(BaseModel):
    caller_name: str
    caller_kind: str  # "function", "method", "class"
    file_path: str
    line: int
    content: str


class CalleeInfo(BaseModel):
    callee_name: str
    file_path: str
    line: int


class DependencyInfo(BaseModel):
    file_path: str
    symbols: List[str] = Field(default_factory=list)
    weight: int = 1


class GitCommit(BaseModel):
    hash: str
    message: str
    author: str = ""
    date: str = ""


class ToolResult(BaseModel):
    """Unified tool result wrapper."""
    tool_name: str
    success: bool = True
    data: Any = None
    error: Optional[str] = None
    truncated: bool = False


# ---------------------------------------------------------------------------
# Tool definition schema (for LLM tool_use protocol)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "grep",
        "description": (
            "Search for a regex pattern across files in the workspace. "
            "Returns matching lines with file paths and line numbers. "
            "Use include_glob to filter by file type (e.g. '*.py')."
        ),
        "input_schema": GrepParams.model_json_schema(),
    },
    {
        "name": "read_file",
        "description": (
            "Read file contents. Supports line ranges for reading specific sections. "
            "Use start_line/end_line to read a portion of a large file."
        ),
        "input_schema": ReadFileParams.model_json_schema(),
    },
    {
        "name": "list_files",
        "description": (
            "List files and directories in the workspace. "
            "Use max_depth to control how deep to recurse. "
            "Use include_glob to filter by pattern."
        ),
        "input_schema": ListFilesParams.model_json_schema(),
    },
    {
        "name": "find_symbol",
        "description": (
            "Find symbol definitions (functions, classes, methods, interfaces) by name using AST parsing. "
            "Returns exact file locations with line numbers and signatures. "
            "More precise than grep for finding where something is defined."
        ),
        "input_schema": FindSymbolParams.model_json_schema(),
    },
    {
        "name": "find_references",
        "description": (
            "Find all references (usages) of a symbol across the codebase. "
            "Combines grep with AST validation for accurate results."
        ),
        "input_schema": FindReferencesParams.model_json_schema(),
    },
    {
        "name": "file_outline",
        "description": (
            "Get the structure of a file: all classes, functions, methods with line numbers. "
            "Useful for understanding a file's organization before reading specific sections."
        ),
        "input_schema": FileOutlineParams.model_json_schema(),
    },
    {
        "name": "get_dependencies",
        "description": (
            "Find what files a given file depends on (imports/references). "
            "Uses the dependency graph to show structural relationships."
        ),
        "input_schema": GetDependenciesParams.model_json_schema(),
    },
    {
        "name": "get_dependents",
        "description": (
            "Find what files depend on a given file (reverse dependencies). "
            "Useful for understanding impact of changes."
        ),
        "input_schema": GetDependentsParams.model_json_schema(),
    },
    {
        "name": "git_log",
        "description": (
            "Show recent git commits, optionally filtered to a specific file. "
            "Useful for understanding what changed recently."
        ),
        "input_schema": GitLogParams.model_json_schema(),
    },
    {
        "name": "git_diff",
        "description": (
            "Show differences between two git refs (commits, branches). "
            "Useful for understanding what changed between versions."
        ),
        "input_schema": GitDiffParams.model_json_schema(),
    },
    {
        "name": "ast_search",
        "description": (
            "Structural AST search using ast-grep patterns. "
            "More precise than regex grep — matches code structure, not text. "
            "Use $VAR for single nodes, $$$VAR for multiple nodes. "
            "Examples: 'def $F($$$ARGS)', 'if $COND: $$$BODY', '$OBJ.$METHOD($$$ARGS)'."
        ),
        "input_schema": AstSearchParams.model_json_schema(),
    },
    {
        "name": "get_callees",
        "description": (
            "Find all functions/methods called within a specific function body. "
            "Requires the function name and file path. "
            "Useful for understanding what a function does internally."
        ),
        "input_schema": GetCalleesParams.model_json_schema(),
    },
    {
        "name": "get_callers",
        "description": (
            "Find all functions/methods that call a given function. "
            "Searches across the entire codebase (or a specific path). "
            "Useful for understanding impact and usage patterns."
        ),
        "input_schema": GetCallersParams.model_json_schema(),
    },
]
