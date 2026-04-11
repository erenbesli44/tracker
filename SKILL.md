# Gemini Key Protection Skill

## Objective
Use Gemini safely without leaking `GEMINI_API_KEY` in code, logs, commits, pull requests, issues, screenshots, prompts, or chat outputs.

## Required Rules
1. Read Gemini credentials only from environment variables.
2. Use `GEMINI_API_KEY` from `.env` or runtime secret manager, never hardcode it.
3. Treat all values matching API key patterns as secrets and redact them in outputs.
4. Do not print raw secrets in terminal output, app logs, test snapshots, or exception traces.
5. Never commit `.env` or any file containing real API keys.
6. If a secret is exposed, rotate it immediately and replace it everywhere.

## Implementation Notes
- Preferred config source: `src/config.py` via `Settings` and `.env`.
- Commit only placeholders like `GEMINI_API_KEY=your_gemini_api_key_here`.
- For CI/CD or cloud runtimes, inject `GEMINI_API_KEY` via the platform secret store.

## Agent Policy
Any coding agent (Copilot, Codex, cloud agents) must follow these rules before generating code or docs.
