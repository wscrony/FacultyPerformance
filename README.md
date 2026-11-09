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

## 다른 컴퓨터에서 서비스하기

`data/pii.duckdb`는 암호화되어 있고 키는 이 Mac의 키체인에만 있습니다. 서버에서 쓰려면 키를 키 파일로 옮깁니다.

1. 이 Mac: `fp pii export-key ~/pii.key` (권한 600 파일 생성)
2. 서버로 복사: 코드 + `data/public.duckdb` + `data/pii.duckdb` + 키 파일 (`scp`/`rsync`, 메일·메신저 금지)
3. 서버: `uv sync && .venv/bin/fp pii import-key ~/pii.key && rm ~/pii.key`
   (키는 `~/.config/faculty-performance/pii.key`, 권한 600에 저장. 다른 위치는 `FP_PII_KEY_FILE`)
4. 서버 실행: `FP_APP_PASSWORD='<비밀번호>' .venv/bin/fp app --host 0.0.0.0 --port 8501`
   비밀번호 없이 네트워크에 열면 실행을 거부합니다. 가능하면 HTTPS 리버스 프록시(또는 VPN·SSH 터널) 뒤에 두세요.

데이터 갱신(`fp update all`)은 이 Mac에서 하고, 끝나면 두 `.duckdb` 파일을 서버에 다시 복사한 뒤 앱을 재시작합니다
(실행 중인 앱이 DB를 잠그므로 복사 전에 서버 앱을 멈춥니다).

## streamlit.app 서비스 (Community Cloud)

모든 데이터는 공개 데이터(CSrankings · DBLP · OpenAlex · ORCID · 공개 홈페이지)로 만들었고, 나이는 추정치라 정확하지 않습니다
(앱 사이드바·로그인 화면에 표시).

1. `fp export-app-bundle` → `exports/app_bundle/fp_data.tar.gz` (public DB + 암호화 해제한 개인 DB, 크롤링 원문 제외)
2. GitHub release에 올림: `gh release create data-YYYYMMDD exports/app_bundle/fp_data.tar.gz -R wscrony/FacultyPerformance`
3. share.streamlit.io → New app → 저장소 `wscrony/FacultyPerformance`, 브랜치 `main`, main file `src/fp/main.py`
4. App settings → Secrets:
   ```toml
   FP_APP_PASSWORD = "…"
   FP_DATA_URL = "https://github.com/wscrony/FacultyPerformance/releases/download/data-YYYYMMDD/fp_data.tar.gz"
   ```
데이터를 갱신하면 1–2를 새 태그로 다시 하고 `FP_DATA_URL`만 바꾸면 됩니다. 호스팅에서는 Top 학회 설정이 사용자별(세션)로만 바뀝니다.
