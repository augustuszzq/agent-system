# Auto Research Working Rules

## Project goal
Build a lab-server control plane for paper radar, run registry, incident triage, safe retry, and daily reports.

## Hard constraints
- Polaris is a remote executor only.
- Never automate or bypass ALCF MFA.
- Remote managed files must live under `/eagle/lc-mpi/Zhiqing/auto-research/`.
- Prefer Python 3.11+, Typer, SQLite, YAML, and JSONL.

## Engineering rules
- Keep modules small and typed.
- Prefer explicit schemas over loose dicts.
- Add tests for every new behavior.
