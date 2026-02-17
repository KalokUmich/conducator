# Go Code Style (Google Style)
Reference: https://google.github.io/styleguide/go/

## Style Principles
1. Clarity: Code's purpose and rationale is clear to the reader
2. Simplicity: Accomplishes goals in the simplest way possible
3. Concision: High signal-to-noise ratio
4. Maintainability: Easy to modify correctly
5. Consistency: Consistent with broader codebase

## Formatting
- Use `gofmt` - all Go code must conform to its output
- Tabs for indentation (gofmt default)
- No fixed line length, but prefer refactoring over splitting

## Naming Conventions
- Use MixedCaps or mixedCaps, not underscores
- Exported names: UpperCamelCase (e.g., `MaxLength`)
- Unexported names: lowerCamelCase (e.g., `maxLength`)
- Package names: lowercase, single word, no underscores
- Interface names: method name + "er" suffix (e.g., `Reader`, `Writer`)
- Acronyms: consistent case (e.g., `URL` not `Url`)

## Package Design
- Short, lowercase names without underscores
- Package name should not repeat in exported names
  - Good: `http.Client`, Bad: `http.HTTPClient`
- Avoid meaningless names like `util`, `common`, `base`

## Error Handling
- Return errors as last return value
- Check errors immediately after function call
- Use `fmt.Errorf` with `%w` for error wrapping
- Error strings: lowercase, no punctuation at end

## Comments
- Package comment: precedes package clause
- Exported functions: start with function name
- Complete sentences with proper punctuation

## Declarations
- Group related declarations
- Prefer short variable names in small scopes
- Use `:=` for local variables, `var` for zero values
- Constants: use `const` block, iota for enums

## Best Practices
- Prefer returning early to reduce nesting
- Use named return values sparingly
- Use interfaces for abstraction, not implementation
- Keep interfaces small (1-3 methods)
- Accept interfaces, return concrete types
- Use context.Context for cancellation/timeouts
- Handle all error cases explicitly
- Prefer table-driven tests
