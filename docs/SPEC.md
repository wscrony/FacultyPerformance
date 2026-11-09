# FacultyPerformance — 시스템 설계 (Draft v0.1, 2026-11-09)

목적: 한국 대학 CS 교수와 교수 후보자의 **분야별 top-venue 논문 실적**과 **학문적 나이**를 한 곳에 모은다.
이를 바탕으로 (1) 교수·학교를 서로 비교하고 (2) 나이·분야·실적 조건으로 교수 후보자를 찾는다.
주기적으로 자동 갱신한다.

---

## 1. 사전 조사 결과 (2026-11-09 확인)

### 1.1 CSrankings — 코드와 데이터 모두 공개
- GitHub: `emeryberger/CSrankings`, 기본 브랜치 `gh-pages`. 거의 매일 커밋된다 (최근 커밋 2026-11-08).
- **라이선스: CC BY-NC-ND 4.0** (`COPYING`).
  - 내부·비상업적 분석에 쓰는 것은 문제없다.
  - 가공한 데이터를 **외부에 재배포하는 것은 불가**하다 (ND 조항). 따라서 저장소는 private로 두고 대시보드도 내부에서만 쓴다.
- 쓸 수 있는 파일:

| 파일 | 내용 | 규모 |
|---|---|---|
| `csrankings.csv` | `name, affiliation, homepage, scholarid, orcid` | 35,028 명 |
| `generated-author-info.csv` | `name, dept, area(venue), count, adjustedcount, year`<br>교수별·venue별·연도별 집계 | 308K 행 |
| `institutions.csv` | `institution, region, countryabbrv, homepage` | 792 기관 |
| `dblp-aliases.csv` | DBLP 동명이인·별칭 매핑 | |
| `dblp.xml.xz` | CSrankings가 쓰는 venue만 걸러낸 DBLP (35MB) | |
| `acm-fellows.csv`, `orcid.csv` | 부가 정보 | |

- 한국 데이터 현황:
  - 기관 17곳: KAIST, SNU, KU, Yonsei, POSTECH, KNU, SKKU, Hanyang, UNIST, GIST, DGIST, KHU, Catholic, CAU, Dongguk, Ewha, Soongsil.
  - 교수 **775명**. 이 중 710명이 Google Scholar ID를 갖고 있다.
  - 논문 집계 8,632행, venue 76개.
- 한계:
  - **나이·학위 연도 정보가 없다.**
  - **csrankings에 등록된 교수만** 포함된다. 따라서 해외 포닥, 해외 교수, 박사과정 같은 *채용 후보자*는 빠진다.
  - 저자 순서(1저자/교신저자) 정보가 없고, 6쪽 미만 논문은 제외된다.

### 1.2 KCSS (alinlab.kaist.ac.kr/KCSS)
- KAIST ALIN Lab이 만든 "한국인 저자" 검색 도구다.
  - 약 70개 conference를 대상으로 한다.
  - 한국 이름 여부를 LSTM 분류기로 판정한다.
  - 1저자·마지막 저자 필터와 최소 페이지 수 옵션이 있다.
- **교수가 아닌 사람까지 포함**하므로 후보자 발굴에 맞는 접근이다.
- 코드와 데이터 다운로드는 제공하지 않는다. 따라서 같은 방식을 DBLP 위에 직접 구현한다.

### 1.3 나이 정보
- 생년은 공개된 출처가 거의 없고 개인정보이기도 하다.
- 그래서 **학문적 나이(academic age)를 주 지표**로 삼는다. 출처는 다음 우선순위로 쓴다.
  1. **PhD 취득 연도 — ORCID 공개 API** `/v3.0/{orcid}/educations`
     - 검증 결과 `Doctoral degree` 항목에 `end-date.year`가 들어 있다.
     - csrankings의 ORCID 필드에는 `0000-0000-0000-0000` 같은 placeholder가 섞여 있다. 실제 커버리지는 M1에서 측정한다.
  2. **PhD 연도 — 홈페이지·CV에서 추출**
     - Haiku급 LLM으로 추출하고, 근거 문장(evidence span)을 함께 저장한다.
  3. **수동 입력**: 관리 화면에서 override한다.
  4. **Fallback**: 첫 DBLP 논문 연도와 첫 top-venue 논문 연도.
     - 둘 다 항상 계산해 둔다.
- 화면에 표시하는 값:
  - `academic_age = 올해 − PhD 연도`
  - `estimated_birth_year ≈ PhD 연도 − 30`. "추정"으로 명시하고 기본 화면에서는 숨긴다.
  - 모든 나이 값에 `source`(orcid / homepage / manual / dblp_first)와 `confidence`를 붙인다.

---

## 2. 범위

### 대상 사용자
- 학과 인사위원회와 PI 본인. 내부 도구이며 로컬 또는 학내망에서 쓴다.

### 핵심 시나리오
1. **교수 프로필**: 분야별·연도별 top-venue 논문 수, adjusted count, 학문적 나이, 최근 N년 실적, 같은 학문적 나이대 안에서의 percentile.
2. **비교**:
   - 교수 A vs B (vs C): 분야 레이더 차트, 연도별 누적 곡선, 학문적 나이에 맞춘 곡선(PhD 후 k년차 기준).
   - 학교 vs 학교: 분야별 합계와 교수 1인당 실적.
3. **후보자 검색**: 필터 조합으로 찾는다.
   - 분야 / 세부 venue
   - 학문적 나이 범위 (예: PhD 후 0–7년)
   - 최근 N년 top-venue 수 ≥ k
   - 1저자 또는 마지막 저자 논문 수 ≥ k
   - 현재 소속 유형 (국내 교수 / 해외 교수 / 포닥·산업체 / 미상)
   - 한국인 여부
   - 결과는 정렬하고 CSV로 내보낸다.
4. **변화 추적**: 이전 스냅샷 대비 신규 논문과 소속 변경을 보여준다 (예: "이번 달 새로 SIGMOD 2편").

### 비범위 (v1)
- 인용수 기반 순위 (OpenAlex로 보조 표시만 함)
- 저널
- 비 CS 분야
- 외부 공개 웹서비스 (라이선스 때문)

---

## 3. 아키텍처

```
 ┌──────────── Sources (주기적 fetch) ────────────┐
 │ CSrankings git (gh-pages)   weekly  git pull    │
 │ DBLP full dump (CC0)        monthly dblp.xml.gz │
 │ ORCID public API            monthly, 증분       │
 │ OpenAlex API (CC0)          monthly, 증분       │
 │ Homepage/CV (LLM 추출)      on-demand / 분기    │
 └───────────────┬─────────────────────────────────┘
                 ▼
  ingest/  (소스별 raw → data/raw/<source>/<snapshot_date>/)
                 ▼
  normalize/ (venue→area 매핑, 저자 식별/병합, 한국인 판정)
                 ▼
  DuckDB  data/faculty.duckdb   (+ snapshot 테이블)
                 ▼
  metrics/ (집계 view, percentile, cohort)
                 ▼
  app/  Streamlit 대시보드  ·  CLI (fp query/export)
```

- **언어와 저장소**: Python 3.12, DuckDB (공개 데이터 DB와 개인정보 DB를 분리, §3.2). 원본은 Parquet 스냅샷으로 로컬에 보관한다.
- **스케줄러**: 로컬 macOS `launchd` (주 1회 CSrankings, 월 1회 DBLP, ORCID, OpenAlex).
  - 클라우드 CI(GitHub Actions 등)는 쓰지 않는다. 개인정보가 로컬 밖에서 처리되면 안 되기 때문이다 (§3.2).

### 3.2 개인정보 보관 원칙 (필수)
연구자 개인정보는 **로컬 DB에만** 둔다. 개인정보는 나이, 학위 연도, 소속 이력, 홈페이지 내용, 추출 근거, 수동 메모를 말한다.

- **DB 분리**
  - `data/public.duckdb`: 논문, venue, CSrankings 집계. 공개 출처에서 다시 만들 수 있는 데이터다.
  - `data/pii.duckdb`: `degree`, `affiliation` 이력, `homepage_cache`, `manual_note`, `estimated_birth_year`.
  - `pii.duckdb`는 **DuckDB 파일 암호화**(`ATTACH ... (ENCRYPTION_KEY ...)`)를 적용한다. 키는 macOS Keychain에 보관한다.
  - 분석할 때는 두 DB를 함께 ATTACH해서 조인한다.
- **git 제외**
  - `data/`, `cache/`, `exports/`, `*.duckdb`, `*.parquet`는 `.gitignore`에 넣는다.
  - 저장소에는 코드와 설정만 커밋한다. pre-commit hook에서 data 파일이 커밋되지 않는지 검사한다.
- **외부 전송 최소화**
  - ORCID, OpenAlex, DBLP 조회는 **식별자로 조회만** 한다.
  - 홈페이지 HTML 원문과 추출 결과는 로컬 `cache/`와 `pii.duckdb`에만 저장한다.
- **LLM 추출**
  - 공개 홈페이지 텍스트 중 학력/CV 구간만 잘라서 보낸다.
  - 결과(연도, 근거 문장)는 로컬에만 저장한다.
  - 완전 오프라인이 필요하면 LLM 단계를 끄고 규칙 기반 추출과 수동 입력만 쓰는 옵션(`--no-llm`)을 둔다.
- **Export**
  - CSV는 기본적으로 개인정보 컬럼을 제외한다.
  - `--include-pii`를 명시해야 포함하며, 파일은 `exports/`(git 제외)에만 쓴다.
- **삭제·정정**
  - `fp pii forget <person>`으로 특정인의 개인정보 레코드와 캐시를 삭제한다.
  - 다음 갱신 때 다시 수집되지 않도록 제외 목록에 등록한다.
- **UI**: Streamlit. 내부용이며 개발 속도가 우선이다. CLI도 함께 제공해 쿼리와 CSV export를 할 수 있게 한다.

### 3.1 데이터 소스 역할

| 소스 | 제공 정보 | 비고 |
|---|---|---|
| CSrankings | 국내·해외 교수 명단, 소속, venue→area 매핑, adjusted count | 정본 교수 명단 |
| DBLP full dump | 모든 저자의 논문, **저자 순서**, 페이지 수 | 후보자(비교수) 포함. KCSS 방식 재현 |
| ORCID | PhD 연도, 소속 이력 | 학문적 나이의 1순위 출처 |
| OpenAlex | 최신 소속 기관, 인용수·h-index(보조) | 비교수 후보자의 현재 소속 추정 |
| Homepage | PhD 연도·기관 (LLM 추출) | ORCID가 없는 경우 |

- venue와 area 분류는 **CSrankings 매핑을 그대로 재사용**한다. 그래야 CSrankings 수치와 대조할 수 있다.
- 사용자가 venue를 추가로 정의할 수 있게 한다 (예: KCSS 쪽 venue, 국내 학회).

---

## 4. 데이터 모델 (DuckDB)

```sql
person(person_id PK, dblp_pid, canonical_name, aliases[], orcid, scholar_id, openalex_id,
       is_korean BOOL, korean_conf REAL, homepage)
affiliation(person_id, institution, country, role ENUM(faculty,postdoc,industry,student,unknown),
            source, valid_from, valid_to)         -- 이력 보존
degree(person_id, degree ENUM(phd,ms,bs), year, institution, source, confidence, evidence)
venue(venue_key PK, dblp_key_pattern, area, parent_area, min_pages, is_csrankings BOOL)
paper(paper_id PK, dblp_key, venue_key, year, pages, title, n_authors)
authorship(paper_id, person_id, position, is_first BOOL, is_last BOOL)
snapshot(snapshot_id, source, fetched_at, git_commit/etag)
-- 파생 view
v_person_area_year(person_id, area, year, cnt, adj_cnt, first_cnt, last_cnt)
v_person_summary(person_id, academic_age, age_source, total_adj, recent5_adj, ... , cohort_pctl)
```

### 저자 식별
- **DBLP pid를 기본 키로** 쓴다. CSrankings 이름은 `dblp-aliases.csv`를 거쳐 pid에 매핑한다.
- ORCID와 OpenAlex는 ORCID 또는 DBLP의 외부 링크로 연결한다.
- 연결이 불확실하면 수동 검토 큐에 넣는다.

### 한국인 판정 (후보자 풀)
1. 한국 성씨와 로마자 표기 규칙(음절 패턴)으로 1차 필터를 건다.
2. 판정이 애매한 경우만 Haiku로 판별한다 (이름, 공저자, 소속을 단서로 줌).
3. 결과는 캐시한다.
4. 국내 기관 소속 경력이 있으면 확정으로 본다.

---

## 5. 지표 정의

| 지표 | 정의 |
|---|---|
| `count` | 해당 area/venue의 논문 수 (CSrankings 기준: 6쪽 이상, research paper만) |
| `adj_count` | Σ 1/저자수 (CSrankings `adjustedcount`와 동일) |
| `first/last_count` | 1저자 또는 마지막 저자인 논문 수 (DBLP 저자 순서 기준) |
| `recentN` | 최근 N년(기본 5년) 합계 |
| `rate` | 연간 생산성 = 논문 수 / max(1, academic_age) |
| `cohort_pctl` | 같은 area, 같은 academic_age ±2 집단에서의 percentile.<br>모집단은 국내 교수 / 전 세계 CSrankings 교수 중 선택 |
| `school_area` | 학교×area 합계, 교수 1인당 값, CSrankings 방식의 기하평균 점수 |

---

## 6. 갱신 파이프라인

```
fp update csrankings   # git fetch; 변경된 commit이면 재적재 (주 1회)
fp update dblp         # 월간 release 비교 → 스트리밍 파싱 → paper/authorship 적재
fp update orcid        # orcid 있고 degree 없음 or 90일 경과 대상만
fp update openalex     # 후보자 풀 대상, 소속/인용 갱신
fp enrich homepage     # PhD 연도 미확보자만, LLM 추출 + evidence 저장
fp diff --since <snap> # 신규 논문/소속 변경 리포트
```

- 모든 단계는 **idempotent**하게 만든다. snapshot 단위로 적재하므로 실패하면 이전 스냅샷을 그대로 유지한다.
- 외부 API 호출 규칙:
  - rate-limit을 지킨다.
  - User-Agent는 일반 클라이언트 이름으로 둔다.
  - 응답은 디스크에 캐시한다.
- LLM은 기존 ClaudeWrapper(로컬 claude CLI) 패턴을 따른다. **필터링 후 미확보자에게만** 호출한다.

---

## 7. 마일스톤

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **M1** | CSrankings만으로 국내 교수 775명 적재, 분야별·연도별 집계 + CLI | 국내 교수 학교별 수치가 csrankings.org 화면과 일치 |
| **M2** | ORCID PhD 연도 + DBLP 첫 논문 연도 → academic age, cohort percentile | 국내 교수의 X% 이상이 PhD 연도 확보 (기준값 X는 측정 후 결정) |
| **M3** | Streamlit: 프로필, 교수 비교, 학교 비교, 필터 검색, CSV export | 시나리오 1–3 동작 |
| **M4** | DBLP full ingest + 저자 순서 + 한국인 판정 → 비교수 후보자 풀 | KCSS 상위 결과와 대략 일치 |
| **M5** | OpenAlex 소속, 홈페이지 LLM 추출, launchd 스케줄, diff 리포트 | 무인 주간 갱신 1회 성공 |

---

## 8. 결정이 필요한 사항 (Open Questions)

1. **후보자 풀 범위**: 다음 중 무엇을 v1에 포함할지.
   - (a) 국내 교수만 — M1–M3로 충분
   - (b) CSrankings 해외 교수 중 한국인
   - (c) DBLP 전체 한국인 (포닥·학생 포함) — M4
2. **분야 범위**: CSrankings 전체 area인지, DB/ML/Systems 등 관심 분야를 우선할지.
3. **나이 표기**: 학문적 나이만 쓸지, 추정 생년도 노출할지 (개인정보 고려).
4. **UI**: Streamlit 로컬 앱으로 충분한지, 학과 구성원과 공유할 웹페이지(내부 인증)가 필요한지.
5. **비교 기준 집단**: percentile 모집단을 국내 교수로 할지, 전 세계 CSrankings 교수로 할지 (둘 다 제공 가능).
6. **저장소**: 별도 private GitHub repo를 만들지 (데이터 라이선스상 private 필수).
