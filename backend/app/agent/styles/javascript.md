# JavaScript Code Style (Google Style)
Reference: https://google.github.io/styleguide/jsguide.html

## File Structure
- License/copyright (if present)
- @fileoverview JSDoc (if present)
- goog.module or ES imports
- The file's implementation

## Formatting
- Use 2 spaces for indentation (no tabs)
- Column limit: 80 characters
- Semicolons required at end of every statement
- Line-wrapping: break after operators (except `.`)
- Continuation lines indented at least +4 spaces

## Braces (K&R Style)
- Required for all control structures (if, else, for, do, while)
- No line break before opening brace
- Line break after opening brace and before closing brace

## Whitespace
- Single blank line between class methods
- Space after keywords (if, for, catch)
- Space before opening brace `{`
- Spaces around binary operators

## Naming Conventions
- Classes: UpperCamelCase (e.g., `MyClass`)
- Functions/methods: lowerCamelCase (e.g., `myFunction`)
- Constants: UPPER_SNAKE_CASE (e.g., `MAX_RETRY_COUNT`)
- Variables/parameters: lowerCamelCase (e.g., `myVariable`)
- Private properties: trailing underscore (e.g., `this.data_`)

## Declarations
- Use `const` by default
- Use `let` only when reassignment is needed
- Never use `var` (not block-scoped)
- One variable per declaration

## ES Modules
- Use named exports (not default exports)
- Import paths must include `.js` extension

## Strings
- Use single quotes for ordinary strings
- Use template literals for string interpolation
- Never use `eval()` or Function() constructor with strings

## Arrays and Objects
- Use trailing commas in multi-line literals
- Use literal syntax: `[]` not `new Array()`, `{}` not `new Object()`
- Prefer destructuring for accessing object properties

## JSDoc
- Use `/** */` for documentation
- Required for all public/exported functions
- Use `@param {Type} name`, `@return {Type}`, `@throws {Type}`
