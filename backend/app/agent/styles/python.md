# Python Code Style (Google Style)
Reference: https://google.github.io/styleguide/pyguide.html

## Formatting
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 80 characters
- Two blank lines between top-level definitions
- One blank line between method definitions in a class
- Use trailing commas in sequences when multi-line

## Naming Conventions
- Modules: lower_with_underscores (e.g., `my_module.py`)
- Classes/Exceptions: CapWords (e.g., `MyClass`, `MyError`)
- Functions/methods: lower_with_underscores (e.g., `my_function`)
- Constants: CAPS_WITH_UNDERSCORES (e.g., `MAX_VALUE`)
- Variables: lower_with_underscores (e.g., `my_variable`)
- Protected: single leading underscore (e.g., `_internal_var`)

## Imports
- One import per line (except `from x import a, b, c`)
- Group imports: stdlib, third-party, local application
- Absolute imports preferred over relative
- Avoid wildcard imports (`from x import *`)

## Docstrings (Google Style)
- Use triple double quotes
- First line: brief summary (imperative mood)
- Blank line after summary if more content
- Sections: Args, Returns, Raises, Yields, Examples

## Type Annotations
- Annotate function signatures (public APIs required)
- Use `Optional[X]` for parameters that can be None
- Use `Sequence`, `Mapping` over `list`, `dict` for params

## Exceptions
- Use built-in exceptions when appropriate
- Custom exceptions should inherit from `Exception`
- Never use bare `except:`, catch specific exceptions
- Minimize code in try block

## Best Practices
- Use `is None` or `is not None` for None checks
- Use implicit boolean evaluation for sequences (`if seq:`)
- Prefer list/dict comprehensions for simple cases
- Use `with` statement for resource management
- Avoid mutable default arguments
- Use f-strings for string formatting
