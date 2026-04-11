# Agent Security Instructions

## Secret Handling
1. Never hardcode API keys, tokens, passwords, or credentials.
2. Never echo or log the value of `GEMINI_API_KEY`.
3. Keep secrets only in environment variables or managed secret stores.
4. Commit only placeholder values in tracked files.
5. If a secret is exposed in conversation or code, instruct immediate rotation.

## Project Requirement
- Gemini access must use `GEMINI_API_KEY` from environment configuration.
- `.env` is local-only and must stay untracked.
