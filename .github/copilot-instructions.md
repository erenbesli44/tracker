# Copilot Instructions: Secret Safety

## Mandatory
1. Use `GEMINI_API_KEY` only from environment variables.
2. Do not place real keys in source code, tests, markdown, comments, or examples.
3. Redact secrets in generated output and logs.
4. Keep `.env` untracked; use `.env.example` placeholders for documentation.
5. If a key was shared accidentally, recommend key rotation immediately.

## Repo Context
- Config class: `src/config.py`
- Expected key name: `GEMINI_API_KEY`
