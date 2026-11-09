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
