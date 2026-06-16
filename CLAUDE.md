# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

플랜닥스(Plandocs)의 잠재고객인 미용·성형 전문 병원의 **공식 홈페이지를 크롤링해 정규화 JSON으로 추출**하는 도구. 추출 대상은 운영정보·의료진·취급제품·취급장비(필러·톡신·스킨부스터·리프팅/레이저 장비)·시술정보·시술가격·다국어 지원이다.

**목표: 한 병원에 대해, 정해진 비용·시간 예산 안에서 가능한 한 완벽하게 크롤링한다.** 한 실행 = 한 병원이다(배치는 이 단위 실행을 바깥에서 반복). 핵심 긴장은 *완전성 ↔ 예산*이다 — 무제한으로 파고드는 게 아니라, 상한 안에서 가장 가치 높은 정보(취급 제품·장비·가격)부터 빠짐없이 거두고, 예산이 다하면 깔끔히 멈추되 못 끝낸 부분을 기록으로 넘긴다. 모든 설계 결정은 이 절충을 따른다:
- **완전성**: 상세 페이지 전수 방문, 이미지에 묻힌 가격·제품을 vision으로 판독, 카탈로그 재조회로 누락 방지(SKILL.md §1·§4·§5).
- **예산 통제**: 텍스트 추출 우선(vision 최소화), vision 사이트당 약 10장 상한, `max_budget_usd`로 $ 단위 하드캡, 수확 체감 시 조기 종료.
- **멈출 때의 정직함**: 못 끝낸 영역은 조용히 생략하지 않고 `crawl_metadata.follow_up`에 구조화해 남겨 후속 실행이 이어받게 한다.

핵심: **크롤링 로직은 Python 코드가 아니라 Claude Code 스킬로 구현되어 있다.** 병원마다 홈페이지 구조가 제각각이라 고정 파싱 스크립트 대신 에이전트(`hospital-homepage-extract` 스킬 + Playwright MCP + vision)가 페이지를 직접 탐색·판독·매칭한다. `main.py`는 그 스킬을 `claude-agent-sdk`로 무인 실행하는 **러너**다 — 한 병원 URL을 받아 예산을 걸고 스킬을 돌린다.

> **`DESIGN.md`** — "스스로 진화하는 크롤러" 전체 설계도(골든 회귀 하니스 → 배치 드라이버 → 독립 검증 sub-agent → 진화 엔진, Phase 0~6). **앞으로의 방향·다음 작업의 진실원** — 다음 작업은 여기 Phase 0부터. (운영 규칙·측정된 사실은 이 문서, 크롤 행동규칙은 `SKILL.md`.)

## Commands

```bash
# 의존성 동기화 (uv 기반, Python >=3.12)
uv sync

# 한 병원 크롤링 실행 (러너). 기본 Sonnet 4.6 · effort low · 예산캡 $0.90 · 시간캡 270s
uv run python main.py <홈페이지URL> --id <병원ID> --name <병원이름>
# 병원DB CSV에서 id로 행을 찾아 시도·주소까지 자동으로 채워 넘기기 (identity 판정에 사용)
uv run python main.py <홈페이지URL> --from-csv data/beauty_hospitals_gangnam.csv --id <병원ID>
# 품질 우선(비용·시간↑): 캡을 올려 자연완주 유도
uv run python main.py <홈페이지URL> --from-csv data/sample10.csv --id <병원ID> --budget 2 --time-limit 600
# 가장 어려운 사이트: --model claude-opus-4-8 --effort high

# 추출 결과 JSON 스키마 검증 (스킬 마무리 단계에서 필수)
uv run python .claude/skills/hospital-homepage-extract/reference/output_scheme.py output/{병원ID}_{병원이름}_homepage.json

# 린트 / 포맷
uv run ruff check .
uv run ruff format .
```

Playwright MCP 서버는 `.mcp.json`(대화형 Claude Code용)에 정의되어 있고, 러너(`main.py`)는 자체 `PLAYWRIGHT_SERVER`로 **`--headless --isolated`**를 명시해 띄운다(배치 운영성). 러너는 `permission_mode="bypassPermissions"`로 무인 실행한다. 인증은 `.env`의 `ANTHROPIC_API_KEY`가 있으면 그것을, 없으면 **Claude Code 로그인 자격(구독)**을 쓴다 — 둘 중 하나면 사람 개입 없이 돈다. **지속적 8,000개 배치엔 구독 한도보다 API 키+tier 권장**: 배치의 진짜 천장은 머신이 아니라 **API 레이트리밋(TPM/RPM)**이다(머신 측면은 아래 "측정된 운영 사실" 참고).

## 추출 작업 흐름 (the actual workflow)

병원 홈페이지 URL이 주어지거나 "이 병원이 무슨 제품·장비·시술을 취급하는지" 추출이 필요하면 **`hospital-homepage-extract` 스킬을 호출한다.** 전체 행동 규칙(방문 순서, vision 사용 시점, 카탈로그 매칭, 예산·멈춤 신호)은 `.claude/skills/hospital-homepage-extract/SKILL.md`에 있다 — 추출 작업을 할 때 그 문서가 1차 진실원이다.

스킬 디렉터리 구성:
- `SKILL.md` — 행동 규칙(어디를 방문, 언제 vision, 어떻게 판단). 섹션 §0~§8은 의존 순서(수집→이미지 보강→매칭→정리).
- `reference/output_scheme.py` — 출력 스키마. `HospitalHomepageResult`가 루트. **각 필드에 무엇을 어떤 형식으로 담는지는 이 파일의 Field description이 진실원**이다(SKILL.md는 행동, 스키마는 형식). 직접 실행하면 검증기로 동작한다.
- `reference/aesthetic_products.json` / `aesthetic_equipments.json` — 매칭 대상 카탈로그(제조사·브랜드·제품·성분 등). §5 매칭에서 사이트 표기를 이 카탈로그와 한↔영·NFKC·수식어제거로 대조한다.
- `reference/sample_output.json` — 출력 예시.
- `reference/snippets.md` — 재사용 `browser_evaluate`/캡처 스니펫(고정 파이프라인 아님, 복붙 출발점).

산출물은 `output/{병원ID}_{병원이름}_homepage.json`에 저장한다.

### 러너 (`main.py`)

`main.py`는 스킬을 호출하는 프롬프트를 만들어 `ClaudeSDKClient`(양방향)로 넘기는 얇은 진입점이다. 크롤 전에 **사전 트리아지(`triage.py`)**를 먼저 돌린다 — curl로 싸게(LLM·브라우저 없이 ~$0·수초) 분류해, 죽은/접속불가 도메인이나 명백한 비타깃 진료과(안과·이비인후과 등, 강한 미용 신호 0)면 에이전트를 띄우지 않고 유효 스켈레톤만 쓰고 **exit 2**로 제외한다. 그 외엔 크롤로 넘기며 사이트 형태(text/image/spa) 힌트를 프롬프트에 준다. 스킵은 보수적이다 — 강한 미용 신호가 조금이라도 있거나 SPA면 무조건 크롤한다(잘못 스킵하면 진짜 타깃을 잃는 게 최악). `--no-triage`로 끄고, 상세는 `triage.py` docstring. 크롤 실행의 책임은:
1. **입력 정리** — URL + 병원정보(직접 인자 또는 `--from-csv`로 CSV 행 조회)를 스킬 입력 형식으로 묶고, **자율 실행 + 증분(체크포인트) 저장**을 지시하는 프롬프트를 만든다.
2. **모델·effort·이중 캡** — `model`(기본 Sonnet 4.6)·`effort`(기본 low, Sonnet/Opus만 — Haiku는 미지원이라 모델 가드)·`max_budget_usd`(기본 $0.90, 느슨한 백스톱)를 건다. **시간이 단일 바인딩 제약**이다: `DEFAULT_TIME_LIMIT_S`(270s) wall-clock 하드캡 + 78%(`SOFT_DEADLINE_RATIO`) 소프트 데드라인. `max_buffer_size`도 50MB로 키운다(기본 1MB는 base64 스크린샷 한 장에 리더가 죽는다 — vision 핵심).
3. **소프트 데드라인 steering** — 소프트 데드라인에 도달하면 `client.query(FINALIZE_MESSAGE)`로 "지금 유효 JSON 저장·검증하고 종료하라"를 주입한다. 에이전트는 시계를 못 보므로, 하드 기요틴 대신 이 신호로 깨끗이 마무리하게 한다. 무시하면 하드캡(`asyncio.timeout`)이 백스톱.
4. **무인 실행 환경** — `skills=["hospital-homepage-extract"]`, playwright MCP, `bypassPermissions`, 프로젝트 setting_sources. 스트리밍 메시지를 flush해 실시간 출력.
5. **신선도 보장** — 실행 전 기존 출력 파일을 삭제한다(남겨두면 약한 모델이 크롤링 대신 재사용하는 오염 + 크래시 후 stale 오보). 실행 후 파일이 있으면 이번 실행이 쓴 것.
6. **유효성·무손실 보장(repair)·비용 백필** — 끝나면 `output_scheme.py --repair`로 결과를 **항상 스키마 통과 형태로 고친다**(잘 쓴 파일은 no-op, 무효 부분만 제거, 미저장이면 스켈레톤 생성). 같은 단계에서 **`aggregate_from_treatments`가 시술(`treatments[].product_name/equipment_name`)에 거둔 제품·장비 이름을 최상위 `unmatched`로 자동 집계**해 거둔 1순위 데이터를 잃지 않게 한다. 그 뒤 `ResultMessage`의 실측 비용·소요시간을 `crawl_metadata.cost`에 채운다. 결과는 exit 코드로 갈린다 — **0=USEFUL(유의미한 데이터), 1=EMPTY(빈약 — 배치 재시도 신호), 2=트리아지 제외(재시도 안 함)**.

**예산/시간 도달은 실패가 아니라 정상 종료다.** "상한 안에서 최대한 → 닿으면 멈춤"이 설계된 happy path. 진짜 실패(접속 불가·기타 에러)만 fatal.

**체크포인트 저장이 컷의 안전망이다.** 하드캡은 저장 전에 끊을 수 있으므로, SKILL.md §0-6은 정찰 직후 — **카탈로그 매칭(큰 파일 읽기) 전, `sample_output.json`을 골격으로** — 유효 JSON을 먼저 저장하고 이후 덮어쓰게 한다. 그래야 컷에 걸려도 best-so-far가 남는다.

크롤링 판단(어디까지 파고들지)은 모두 스킬/에이전트가 한다. 러너에 파싱·매칭 로직을 넣지 않는다.

### 비용·속도 튜닝 (다양한 실사이트 측정 기반)

목표는 병원당 **평균 $1·5분 이내**(8,000개 배치). 강남 5개 실사이트(SPA·성형외과·미용의원·프랜차이즈·피부과)로 측정한 결론:

- **비용·시간 목표는 캡으로 구조적으로 보장된다.** 시간 하드캡 270s(4.5분) + 비용 백스톱으로, **모든 실행이 ≤4.5분·≤~$1**. 실측 자연완주/캡 비용 $0.77–$1.03. ⇒ 평균 $1·5분 충족.
- **모델이 가장 큰 레버.** Haiku 4.5는 이 멀티스텝 에이전트(SPA·매칭·vision)에서 조기 종료·미저장으로 **완주 실패**(측정). Sonnet 4.6이 기본, Opus는 하드 사이트용(`--model`).
- **핵심 가치 데이터(제품·장비·시술)는 타이트한 캡에서도 Opus 베이스라인과 동등 이상**(실측). 캡의 희생은 주로 의료진 등 부차 정보 — follow_up에 남는다.

### 측정된 운영 사실 (다시 측정하지 말 것)

- **머신/병렬:** 24GB·12코어 기준 한 실행 ~1GB. **동시 8–12개 안전**, CPU 아닌 **메모리 바인딩**. 단 8,000 배치의 진짜 천장은 머신이 아니라 **API 레이트리밋(TPM/RPM)** — 배치 드라이버는 429 백오프 필수(DESIGN §4).
- **매칭 품질은 이미 양호 — 결정론적 매칭 패스 금지.** unmatched의 낮은 매칭률은 누락이 아니라 정당한 보류(일반명·다른 제품)다. 기계적으로 매칭하면 거짓매칭을 양산한다(`국산보톡스→보톡스`, `피코프락셀→프락셀`). 매칭은 에이전트(또는 DESIGN의 검증 sub-agent) 판단에 맡긴다.
- **트리아지 효과(실측):** 실제 안과·이비인후과·가정의학·dead 7개 전부 SKIP(각 ~$2–3 절약), sample10 타깃 10개 전부 CRAWL. 강남 CSV 약 1,400개 중 ~90개(6%)가 명백한 비타깃.
- **알려진 갭(미해결):** 시간캡으로 끊긴 실행은 `ResultMessage`가 안 와 비용 백필이 안 된다(`cost=None`). 배치 비용 집계엔 경과시간 기반 추정 등 보완이 필요(DESIGN §4).

## 입력 데이터

`data/beauty_hospitals_gangnam.csv` — 추출 대상 병원 목록. 컬럼: `id, hospital_name, sido, sggu, emdong, address, longitude, latitude`. `id`는 출력 파일명·`hospital_id`에, 전체 행은 주어진 URL이 그 병원 홈페이지가 맞는지(`identity_status`) 판정하는 데 쓴다. **이 CSV엔 홈페이지 URL이 없다** — 러너에 URL은 별도 인자로 넘긴다.

`data/sample10.csv` — 위 원본 컬럼에 **`homepage_url` 컬럼을 추가**한 10개 표본(사이트 형태 다양: SPA·대형브랜드·체인·프랜차이즈·소형). URL이 들어 있어 배치 입력 형식의 예이자 회귀 테스트용 표본이다.

## 데이터 원칙 (스킬 작업 시 반드시)

- **출처는 주어진 홈페이지 하나.** 사이트 밖에서 데이터를 가져오지 않는다. 유일한 예외는 같은 병원의 별도 도메인 사이트(SKILL.md §1-1).
- **원문 그대로 담는다** — 제품·장비·시술 이름은 번역·요약·축약하지 않는다. 정식명 정리는 §5 매칭의 일, 표기 통일(전화·시간)은 §6 정규화의 일.
- **비용 기준은 페이지 수가 아니라 vision 판독 횟수**(사이트당 약 10장 상한)와 순차 왕복 수. 텍스트(`browser_evaluate`) 추출은 사실상 무료이므로 우선한다.
- 못 끝낸 영역은 조용히 생략하지 말고 `crawl_metadata.follow_up`에 구조화해 남긴다 — 후속 실행이 이어받는다.
