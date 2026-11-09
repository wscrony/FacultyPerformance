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


## 정기 실행 (M5)

```
fp update all                 # csrankings → dblp → korean → orcid → homepage → openalex → citations → snapshot → report
fp update all --skip homepage --no-llm
fp report                     # 마지막 두 실행 비교 리포트 (exports/report-YYYY-MM-DD.md, 로컬 전용)
fp schedule install --weekday 0 --hour 3   # 사용자 launchd 에이전트 (매주 일요일 03:00)
fp schedule status | uninstall
```

- 각 단계는 독립 실행: 하나가 실패해도 나머지는 진행, 실패 시 종료코드 1. 로그: `data/logs/update.log`
- 재조회 주기: ORCID 90일, OpenAlex 소속 180일, 피인용 30일, LLM 한국인 판별은 캐시 재사용
- OpenAlex 키(선택): `security add-generic-password -s FacultyPerformance -a openalex -w <key>`
- 기관명 매핑(inst_map): OpenAlex 기관명 → CSrankings 학교명 (정확 일치 / 캠퍼스·병원 접미어 / 양쪽 명단 교수로 학습)
