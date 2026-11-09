# FacultyPerformance

Local-only analytics over CSrankings / DBLP / ORCID for Korean CS faculty and candidates.
Design: [docs/SPEC.md](docs/SPEC.md). All data lives under `data/` (gitignored) — researcher PII never leaves this machine.

```
uv venv && uv pip install -e '.[dev]'
.venv/bin/fp update csrankings            # fetch latest gh-pages commit (no-op if unchanged)
.venv/bin/fp schools --from 2016 --to 2026                # KR ranking, matches csrankings.org
.venv/bin/fp schools --area mod,mlmining --country world
.venv/bin/fp faculty --school POSTECH --area mod
.venv/bin/fp person "Wook-Shin Han"
.venv/bin/fp compare "Name A" "Name B" --from 2018
.venv/bin/fp areas
```
`--csv <file>` writes to `exports/` instead of printing.

Personal data (encrypted `data/pii.duckdb`, key in macOS Keychain):
```
.venv/bin/fp update orcid                 # PhD/MS/BS years from ORCID
.venv/bin/fp update homepage [--no-llm]   # homepage/CV extraction (Haiku via ClaudeWrapper)
.venv/bin/fp candidates --area mod --age-max 10 --from 2021
.venv/bin/fp pii {stats,show,set-degree,set-birth,forget}
```

Dashboard (127.0.0.1 only): `uv pip install -e '.[app]' && .venv/bin/fp app`
