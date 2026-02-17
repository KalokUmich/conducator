# Universal Code Style Guidelines

These are language-agnostic guidelines that apply to all code produced in this project.

## Readability
- Write code for humans first, machines second
- Prefer clarity over cleverness
- Keep functions short and focused on a single responsibility
- Use meaningful, descriptive names for variables, functions, and classes

## Formatting
- Be consistent with indentation (follow the project's existing convention)
- Keep lines at a reasonable length (80-120 characters)
- Use blank lines to separate logical sections
- Group related code together

## Naming
- Use descriptive names that reveal intent
- Avoid single-letter variables except in small loops or lambdas
- Constants should be clearly distinguished (e.g., UPPER_CASE)
- Boolean variables/functions should read as questions (e.g., `isReady`, `hasAccess`)

## Structure
- Organize imports/includes at the top of the file
- Keep public API surface small
- Prefer composition over inheritance
- Limit nesting depth (max 3-4 levels)

## Error Handling
- Handle errors explicitly; never silently ignore them
- Provide meaningful error messages with context
- Fail fast on invalid input
- Clean up resources in all code paths (use try-finally, defer, or equivalent)

## Documentation
- Document "why", not "what" — code should explain itself
- Keep comments up to date with code changes
- Document public APIs with expected inputs, outputs, and side effects
- Use examples in documentation when behavior is non-obvious

## Best Practices
- Don't repeat yourself (DRY), but don't over-abstract
- Write testable code: inject dependencies, avoid global state
- Make illegal states unrepresentable when possible
- Prefer immutability where practical
- Use version control commit messages that explain the reasoning behind changes
