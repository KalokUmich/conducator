# Java Code Style (Google Style)
Reference: https://google.github.io/styleguide/javaguide.html

## Source File Structure
- License/copyright info (if present)
- Package statement (not line-wrapped)
- Import statements (no wildcards, no line-wrapping)
- Exactly one top-level class

## Formatting
- Use 2 spaces for indentation (no tabs)
- Column limit: 100 characters
- One statement per line
- Line-wrapping: prefer to break at higher syntactic level
- Continuation lines indented at least +4 spaces

## Braces (K&R Style)
- Opening brace on same line (no line break before)
- Line break after opening brace
- Line break before closing brace
- Always use braces for if/else/for/do/while (even single statements)
- Empty blocks may be concise: `{}`

## Whitespace
- Single blank line between consecutive members of a class
- Space after keywords (if, for, catch), not before
- Space before opening brace `{`
- Spaces around binary/ternary operators

## Naming Conventions
- Classes/Interfaces: UpperCamelCase (e.g., `MyClass`, `Readable`)
- Methods: lowerCamelCase (e.g., `sendMessage`, `stop`)
- Constants: UPPER_SNAKE_CASE (e.g., `MAX_VALUE`, `EMPTY_ARRAY`)
- Non-constant fields: lowerCamelCase (e.g., `computedValues`)
- Parameters/Local variables: lowerCamelCase
- Type variables: single capital letter or name + T (e.g., `E`, `T`, `RequestT`)

## Imports
- No wildcard imports (static or otherwise)
- Static imports in one group, non-static in another
- Alphabetical order within groups (ASCII sort order)

## Javadoc
- Required for every public class, method, field
- Use `@param`, `@return`, `@throws` in that order
- First sentence is a summary fragment
- Block tags never empty

## Best Practices
- Always use @Override annotation
- Caught exceptions: never ignore silently
- Static members: qualify with class name, not instance
