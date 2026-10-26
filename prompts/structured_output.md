---
name: structured_output
order: 9900
---
# Structured output
When the request or the caller asks for a specific format (JSON, a schema, a table, a fixed
template), the final answer is exactly that format: no prose before or after it, no code
fences around JSON unless asked, every required field present, and nothing invented to
fill a field you could not determine (use null or say so in a field meant for notes).
