# JSON Style Guide (Google Style)
Reference: https://google.github.io/styleguide/jsoncstyleguide.xml

## General Rules
- No comments in JSON (not valid JSON syntax)
- Use double quotes for all strings and property names
- Property values: boolean, number, string, object, array, or null
- Consider removing empty/null properties unless semantically required

## Property Names
- Use camelCase (e.g., `propertyName`, `firstName`)
- First character: letter, underscore, or dollar sign
- Avoid reserved JavaScript keywords
- Choose meaningful, descriptive names
- Plural names for arrays (e.g., `items`, `users`)
- Singular names for non-arrays (e.g., `user`, `address`)

## Data Structure
- Prefer flat structure over unnecessary nesting
- Group related data only when semantically meaningful

## Enum Values
- Represent enums as strings, not numbers
- Use UPPER_CASE for enum string values

## Date/Time Values
- Use RFC 3339 format for dates: `"2024-01-15T14:30:00.000Z"`
- Use ISO 8601 for durations

## Arrays
- Use for collections of similar items
- Items should be of consistent type
- Empty array `[]` preferred over null for missing collections

## Property Ordering
- `kind` property first (if present) - identifies object type
- `items` array last in data objects
- Other properties in logical order

## Reserved Properties (for APIs)
- `apiVersion`: API version string
- `data`: container for response data
- `error`: error information object
- `id`: unique identifier
- `items`: array of result items
- `kind`: object type identifier

## Best Practices
- Consistent property naming across entire API
- Document property types and constraints
- Use null for explicitly missing values, omit for optional
- Keep payloads reasonably sized
