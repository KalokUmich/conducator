"""Tests for the symbol-aware code chunker."""
import pytest

from app.rag.chunker import (
    CodeChunk,
    chunk_file,
    _extract_python_symbols_ast,
    _extract_ts_js_symbols_ast,
    _extract_java_symbols_ast,
    _extract_go_symbols_ast,
)


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

class TestChunkPython:
    def test_basic_functions(self):
        code = (
            "import os\n"
            "import sys\n"
            "\n"
            "def greet(name):\n"
            "    print(f'Hello, {name}')\n"
            "\n"
            "def farewell(name):\n"
            "    print(f'Goodbye, {name}')\n"
        )
        chunks = chunk_file(code, "app.py", "python")
        assert len(chunks) >= 2
        # Each chunk should contain the import header
        for c in chunks:
            assert c.file_path == "app.py"
            assert c.language == "python"

    def test_class_extraction(self):
        code = (
            "class Dog:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def bark(self):\n"
            "        return 'Woof!'\n"
            "\n"
            "class Cat:\n"
            "    def meow(self):\n"
            "        return 'Meow!'\n"
        )
        chunks = chunk_file(code, "animals.py", "python")
        assert len(chunks) >= 2
        names = [c.symbol_name for c in chunks]
        assert "Dog" in names
        assert "Cat" in names

    def test_async_function(self):
        code = (
            "async def fetch_data(url):\n"
            "    response = await aiohttp.get(url)\n"
            "    return response\n"
        )
        chunks = chunk_file(code, "client.py", "python")
        assert len(chunks) >= 1
        assert chunks[0].symbol_name == "fetch_data"
        assert chunks[0].symbol_type == "function"

    def test_import_header_prepended(self):
        code = (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "def process():\n"
            "    pass\n"
        )
        chunks = chunk_file(code, "proc.py", "python")
        assert len(chunks) >= 1
        # Import header should be in the content
        assert "import os" in chunks[0].content


# ---------------------------------------------------------------------------
# TypeScript / JavaScript
# ---------------------------------------------------------------------------

class TestChunkTypeScript:
    def test_function_and_class(self):
        code = (
            "import { Router } from 'express';\n"
            "\n"
            "export function handleRequest(req: Request): Response {\n"
            "    return new Response('ok');\n"
            "}\n"
            "\n"
            "export class Server {\n"
            "    start(): void {\n"
            "        console.log('started');\n"
            "    }\n"
            "}\n"
        )
        chunks = chunk_file(code, "server.ts", "typescript")
        assert len(chunks) >= 2
        names = [c.symbol_name for c in chunks]
        assert "handleRequest" in names
        assert "Server" in names

    def test_arrow_function(self):
        code = (
            "export const greet = (name: string) => {\n"
            "    return `Hello, ${name}`;\n"
            "};\n"
        )
        chunks = chunk_file(code, "utils.ts", "typescript")
        assert len(chunks) >= 1
        assert "greet" in chunks[0].symbol_name

    def test_javascript_same_as_typescript(self):
        code = (
            "function add(a, b) {\n"
            "    return a + b;\n"
            "}\n"
        )
        chunks = chunk_file(code, "math.js", "javascript")
        assert len(chunks) >= 1
        assert chunks[0].symbol_name == "add"


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

class TestChunkJava:
    def test_class(self):
        code = (
            "package com.example;\n"
            "\n"
            "import java.util.List;\n"
            "\n"
            "public class UserService {\n"
            "    public List<User> getUsers() {\n"
            "        return List.of();\n"
            "    }\n"
            "}\n"
        )
        chunks = chunk_file(code, "UserService.java", "java")
        assert len(chunks) >= 1
        assert any(c.symbol_name == "UserService" for c in chunks)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

class TestChunkGo:
    def test_function_and_type(self):
        code = (
            "package main\n"
            "\n"
            "import \"fmt\"\n"
            "\n"
            "type Server struct {\n"
            "    port int\n"
            "}\n"
            "\n"
            "func (s *Server) Start() {\n"
            "    fmt.Println(\"starting\")\n"
            "}\n"
            "\n"
            "func main() {\n"
            "    s := &Server{port: 8080}\n"
            "    s.Start()\n"
            "}\n"
        )
        chunks = chunk_file(code, "main.go", "go")
        assert len(chunks) >= 2
        names = [c.symbol_name for c in chunks]
        assert "Server" in names
        assert "Start" in names or "main" in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestChunkEdgeCases:
    def test_empty_file(self):
        chunks = chunk_file("", "empty.py", "python")
        assert chunks == []

    def test_whitespace_only_file(self):
        chunks = chunk_file("   \n  \n  ", "blank.py", "python")
        assert chunks == []

    def test_no_symbols_falls_back_to_blocks(self):
        """A file with no recognisable symbols should still produce chunks."""
        code = "x = 1\ny = 2\nz = 3\n"
        chunks = chunk_file(code, "data.py", "python")
        assert len(chunks) >= 1
        assert chunks[0].symbol_type == "block"

    def test_unsupported_language_produces_blocks(self):
        code = "some content\nmore content\n"
        chunks = chunk_file(code, "file.rb", "ruby")
        assert len(chunks) >= 1
        assert chunks[0].symbol_type == "block"

    def test_oversized_symbol_is_split(self):
        """A function longer than max_lines should be split."""
        lines = ["def big_function():"]
        for i in range(300):
            lines.append(f"    x_{i} = {i}")
            if i % 50 == 49:
                lines.append("")  # blank line for splitting
        code = "\n".join(lines)
        chunks = chunk_file(code, "big.py", "python", max_lines=50)
        assert len(chunks) > 1

    def test_chunk_line_numbers(self):
        code = (
            "def a():\n"
            "    pass\n"
            "\n"
            "def b():\n"
            "    pass\n"
        )
        chunks = chunk_file(code, "lines.py", "python")
        # First chunk starts at line 1
        assert chunks[0].start_line == 1
        # Second chunk starts later
        if len(chunks) > 1:
            assert chunks[1].start_line > 1


# ---------------------------------------------------------------------------
# Python AST extraction
# ---------------------------------------------------------------------------

class TestChunkPythonAST:
    def test_decorated_function(self):
        """Chunk should start at @decorator, not at def."""
        code = (
            "import functools\n"
            "\n"
            "@functools.lru_cache\n"
            "def expensive(x):\n"
            "    return x ** 2\n"
        )
        chunks = chunk_file(code, "deco.py", "python")
        func_chunks = [c for c in chunks if c.symbol_name == "expensive"]
        assert len(func_chunks) == 1
        # start_line should be at the decorator (line 3, 1-based)
        assert func_chunks[0].start_line == 3
        assert "@functools.lru_cache" in func_chunks[0].content

    def test_decorated_class(self):
        """@dataclass should be included in the class chunk."""
        code = (
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Point:\n"
            "    x: float\n"
            "    y: float\n"
        )
        chunks = chunk_file(code, "point.py", "python")
        cls_chunks = [c for c in chunks if c.symbol_name == "Point"]
        assert len(cls_chunks) == 1
        assert "@dataclass" in cls_chunks[0].content

    def test_multiline_signature(self):
        """Multi-line function signatures should parse correctly."""
        code = (
            "def create_user(\n"
            "    name: str,\n"
            "    age: int,\n"
            "    email: str,\n"
            ") -> dict:\n"
            "    return {'name': name, 'age': age, 'email': email}\n"
        )
        chunks = chunk_file(code, "users.py", "python")
        assert len(chunks) >= 1
        assert chunks[0].symbol_name == "create_user"
        assert chunks[0].symbol_type == "function"

    def test_nested_class_not_extracted_separately(self):
        """Nested class should stay inside the outer class chunk."""
        code = (
            "class Outer:\n"
            "    class Inner:\n"
            "        pass\n"
            "\n"
            "    def method(self):\n"
            "        return self.Inner()\n"
        )
        chunks = chunk_file(code, "nested.py", "python")
        sym_chunks = [c for c in chunks if c.symbol_type != "block"]
        names = [c.symbol_name for c in sym_chunks]
        assert "Outer" in names
        # Inner should NOT appear as a separate top-level symbol
        assert "Inner" not in names

    def test_async_generator(self):
        """async def with yield should parse correctly."""
        code = (
            "async def stream_data(url):\n"
            "    async for chunk in fetch(url):\n"
            "        yield chunk\n"
        )
        chunks = chunk_file(code, "stream.py", "python")
        assert len(chunks) >= 1
        assert chunks[0].symbol_name == "stream_data"
        assert chunks[0].symbol_type == "function"

    def test_syntax_error_falls_back_to_regex(self):
        """Broken Python should still produce chunks via regex fallback."""
        code = (
            "def valid_func():\n"
            "    pass\n"
            "\n"
            "def broken(:\n"  # syntax error
            "    pass\n"
        )
        # AST will fail, regex should still find valid_func
        symbols = _extract_python_symbols_ast(code.splitlines())
        assert symbols == []  # AST returns empty on syntax error

        # But chunk_file should still work (regex fallback)
        chunks = chunk_file(code, "broken.py", "python")
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# TypeScript/JavaScript AST extraction
# ---------------------------------------------------------------------------

_ts_available = None

def _check_ts_available():
    global _ts_available
    if _ts_available is None:
        try:
            import tree_sitter
            import tree_sitter_typescript
            _ts_available = True
        except ImportError:
            _ts_available = False
    return _ts_available


class TestChunkTypeScriptAST:
    def test_complex_arrow_function(self):
        if not _check_ts_available():
            pytest.skip("tree-sitter not installed")
        code = (
            "export const fetchData = async <T>(url: string): Promise<T> => {\n"
            "    const res = await fetch(url);\n"
            "    return res.json();\n"
            "};\n"
        )
        chunks = chunk_file(code, "api.ts", "typescript")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "fetchData" in names

    def test_interface_extraction(self):
        if not _check_ts_available():
            pytest.skip("tree-sitter not installed")
        code = (
            "export interface User {\n"
            "    id: number;\n"
            "    name: string;\n"
            "    email: string;\n"
            "}\n"
        )
        chunks = chunk_file(code, "types.ts", "typescript")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "User" in names

    def test_enum_declaration(self):
        if not _check_ts_available():
            pytest.skip("tree-sitter not installed")
        code = (
            "export enum Direction {\n"
            "    Up = 'UP',\n"
            "    Down = 'DOWN',\n"
            "    Left = 'LEFT',\n"
            "    Right = 'RIGHT',\n"
            "}\n"
        )
        chunks = chunk_file(code, "enums.ts", "typescript")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "Direction" in names

    def test_generator_function(self):
        if not _check_ts_available():
            pytest.skip("tree-sitter not installed")
        code = (
            "export function* idGenerator(): Generator<number> {\n"
            "    let id = 0;\n"
            "    while (true) {\n"
            "        yield id++;\n"
            "    }\n"
            "}\n"
        )
        chunks = chunk_file(code, "gen.ts", "typescript")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "idGenerator" in names


# ---------------------------------------------------------------------------
# Java AST extraction
# ---------------------------------------------------------------------------

_java_ts_available = None

def _check_java_ts_available():
    global _java_ts_available
    if _java_ts_available is None:
        try:
            import tree_sitter
            import tree_sitter_java
            _java_ts_available = True
        except ImportError:
            _java_ts_available = False
    return _java_ts_available


class TestChunkJavaAST:
    def test_annotated_class(self):
        if not _check_java_ts_available():
            pytest.skip("tree-sitter-java not installed")
        code = (
            "package com.example;\n"
            "\n"
            "import javax.persistence.Entity;\n"
            "\n"
            "@Entity\n"
            "public class User {\n"
            "    private String name;\n"
            "    private int age;\n"
            "}\n"
        )
        chunks = chunk_file(code, "User.java", "java")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "User" in names

    def test_generic_method_in_class(self):
        if not _check_java_ts_available():
            pytest.skip("tree-sitter-java not installed")
        code = (
            "public class SortUtil {\n"
            "    public <T extends Comparable<T>> void sort(List<T> items) {\n"
            "        Collections.sort(items);\n"
            "    }\n"
            "}\n"
        )
        chunks = chunk_file(code, "SortUtil.java", "java")
        assert len(chunks) >= 1
        names = [c.symbol_name for c in chunks]
        assert "SortUtil" in names

    def test_interface_and_enum(self):
        if not _check_java_ts_available():
            pytest.skip("tree-sitter-java not installed")
        code = (
            "public interface Readable {\n"
            "    String read();\n"
            "}\n"
            "\n"
            "public enum Status {\n"
            "    ACTIVE, INACTIVE, DELETED\n"
            "}\n"
        )
        chunks = chunk_file(code, "Types.java", "java")
        names = [c.symbol_name for c in chunks]
        assert "Readable" in names
        assert "Status" in names

    def test_record_declaration(self):
        if not _check_java_ts_available():
            pytest.skip("tree-sitter-java not installed")
        code = (
            "public record Point(double x, double y) {\n"
            "    public double distance() {\n"
            "        return Math.sqrt(x * x + y * y);\n"
            "    }\n"
            "}\n"
        )
        chunks = chunk_file(code, "Point.java", "java")
        names = [c.symbol_name for c in chunks]
        assert "Point" in names


# ---------------------------------------------------------------------------
# Go AST extraction
# ---------------------------------------------------------------------------

_go_ts_available = None

def _check_go_ts_available():
    global _go_ts_available
    if _go_ts_available is None:
        try:
            import tree_sitter
            import tree_sitter_go
            _go_ts_available = True
        except ImportError:
            _go_ts_available = False
    return _go_ts_available


class TestChunkGoAST:
    def test_receiver_method(self):
        if not _check_go_ts_available():
            pytest.skip("tree-sitter-go not installed")
        code = (
            "package main\n"
            "\n"
            "type Server struct {\n"
            "    port int\n"
            "}\n"
            "\n"
            "func (s *Server) Start() {\n"
            "    fmt.Println(\"starting\")\n"
            "}\n"
        )
        chunks = chunk_file(code, "server.go", "go")
        names = [c.symbol_name for c in chunks]
        assert "Start" in names
        start_chunk = [c for c in chunks if c.symbol_name == "Start"][0]
        assert start_chunk.symbol_type == "function"

    def test_interface_type(self):
        if not _check_go_ts_available():
            pytest.skip("tree-sitter-go not installed")
        code = (
            "package io\n"
            "\n"
            "type Reader interface {\n"
            "    Read(p []byte) (n int, err error)\n"
            "}\n"
        )
        chunks = chunk_file(code, "reader.go", "go")
        names = [c.symbol_name for c in chunks]
        assert "Reader" in names

    def test_struct_type(self):
        if not _check_go_ts_available():
            pytest.skip("tree-sitter-go not installed")
        code = (
            "package config\n"
            "\n"
            "type Config struct {\n"
            "    Host     string\n"
            "    Port     int\n"
            "    Debug    bool\n"
            "}\n"
        )
        chunks = chunk_file(code, "config.go", "go")
        names = [c.symbol_name for c in chunks]
        assert "Config" in names

    def test_multiple_type_specs(self):
        """type ( ... ) block with multiple types."""
        if not _check_go_ts_available():
            pytest.skip("tree-sitter-go not installed")
        code = (
            "package models\n"
            "\n"
            "type (\n"
            "    Request struct {\n"
            "        URL string\n"
            "    }\n"
            "\n"
            "    Response struct {\n"
            "        Status int\n"
            "        Body   string\n"
            "    }\n"
            ")\n"
        )
        symbols = _extract_go_symbols_ast(code.splitlines())
        names = [s["name"] for s in symbols]
        assert "Request" in names
        assert "Response" in names
