# DESIGN — 자기진화 크롤러 설계도

> 이 프로젝트를 **크롤링하면서 스스로 진화하는 크롤러**로 키우기 위한 설계 진실원.
> 운영규칙·측정된 운영 사실은 `CLAUDE.md`, 크롤 행동규칙은 `SKILL.md`.
> 이 문서는 **앞으로 무엇을 어떤 순서로 짓는지**의 청사진이다. 다음 세션은 Phase 0부터 이어받는다.

## Context — 왜

목표: 8,000개 병원을 크롤링하면서 크롤러가 **효율**(왕복↓·트리아지 정확도↑·vision 예산 보정)과 **데이터 품질**(놓친 제품·필드↓·gotcha 축적) 양면에서 스스로 나아지게 한다.

**왜 이 프로젝트에선 진화가 가능한가:** 이 크롤러의 "뇌"는 가중치가 아니라 **파일**이다 — `SKILL.md`(행동규칙, "Gotchas" 섹션은 늘리라고 설계됨), `triage.py`(키워드 리스트), 카탈로그 JSON 2개, `main.py`의 프롬프트. 그래서 에이전트가 **실패를 관찰하고 자기 파일을 고칠 수 있다.** 지금 사람이 손으로 하는 "새 실패 → Gotchas에 추가"를 자동화하는 것이다.

**단 하나의 전제 — 회귀 안전망:** 자기 파일을 고치는 루프는 *틀린 방향*으로도 고친다(Addy Osmani "loop engineering"의 첫 경고: *무인 루프 = 무인으로 실수 쌓는 루프*). 두 함정:
- **Goodhart** — 지표(matched 수)는 올리고 실제 품질은 떨어뜨림 (예: 거짓 매칭 남발 — `국산보톡스→보톡스`).
- **과적합** — 한 사이트에 맞춰 고쳤더니 나머지 9개가 회귀.

**그래서 골든 회귀 세트 없이는 "자기진화"가 "자기표류"가 된다.** 기존 opus/high 10개 기준선(`output/`)이 그 골든이다.

**이 설계의 핵심 신규는 생성≠검증 분리(sub-agent)**다 — 나머지 loop-engineering 요소(Skills·Memory·Automations·Connectors)는 이미 부분~완전 갖춰져 있다. 무인 루프의 세 경고(검증부담·이해부채·인지적 항복)는 "사람이 작은 diff를 검토하고 엔지니어로 남는다"로 대응한다.

## 진화 루프 (한 장)

```
크롤 → 독립 검증(다른·싼 모델이 페이지와 대조해 "놓친 제품·빈 필드·잘못된 스킵" 적발)
     → 실패 신호를 디스크 메모리에 누적
     → [N건마다] 개선안 생성: triage 키워드 / SKILL.md gotcha / 카탈로그 추가 (한 번에 한 파일·최소 편집)
     → 골든 세트로 검증 (하드 불변식 위반? 카운트 회귀? 개선?)
     → 통과한 것만 git diff로 사람에게 제시 (자동 머지 금지 = "엔지니어로 남기")
     → 사람 머지 후 다음 크롤이 더 나아짐  ↺
```

## 단계별 시퀀스 (싼 것·안전망부터, 반짝이는 자기수정은 마지막)

| Phase | 만드는 것 | 비용 | 가치 | loop 요소 |
|---|---|---|---|---|
| **0** | 10개 골든 동결 → `golden/expected/` + `manifest.json` | $0 | 모든 것의 토대 | Memory |
| **1** | `golden/score.py` `score` 모드 + 누수/스키마 하드 불변식 + pytest sanity | $0 | 회귀를 공짜로 잡는 첫 안전망 | Memory+검증 |
| **2** | `batch.py` (풀·exit라우팅·429백오프·resume·진행률·inbox) — sample10으로 | $ | 실제 배치 가동, 즉시 운영가치 | Automations |
| **3** | `golden/score.py` `compare`(재크롤) 모드 | $$ | 진화 엔진이 쓸 유료 게이트 | 검증 |
| **4** | 검증 sub-agent 스킬 + `verify_scheme.py` + 무료 skip-audit + 샘플링 | $(샘플) | 체계적 누락 표면화·최악에러 $0 방어 | Sub-agents |
| **5** | `evolve.py` 최소판: **triage 키워드 제안만**, 무료 검증, diff 제시 | $0 | 자기개선 루프 전체를 공짜로 증명 | Connectors+Memory+사람게이트 |
| **6** | `evolve.py` 확장: SKILL.md gotcha → 카탈로그 추가, 유료 `compare`로 게이트 | $$ | 고가치·고위험 뇌편집, 루프 신뢰 후에만 | 풀 루프 |

**원칙:** 공짜 안전망(P1)·작동 배치(P2)를 먼저, 자기수정은 P5에서 $0로 증명한 뒤 P6에서만 유료.

## 컴포넌트 설계

### Phase 0 — 기준선 동결 (먼저, ~1h, $0)
`output/`은 gitignore라 커밋 불가 → **새 committed 위치 `golden/expected/`** 에 10개 JSON 복사 + `golden/manifest.json`(병원별 `{id, name, homepage_url, model, effort, budget, time_limit, crawled_at, git_sha}` + `frozen_metrics` 블록). 작아서(15–70KB) 커밋해 **계약**으로 삼는다.
- **사람 결정점:** 이 10개 opus 출력이 진짜 "금"인가? (sample10의 추가 5개 사이트는 아직 풀리뷰 전일 수 있다 — 동결 전 한 번 더 확인.)

### 1. 골든 회귀 하니스 (토대)
**권장: 독립 스크립트 + 얇은 pytest 래퍼**(pytest-only 아님). 재점수는 **재크롤 없이** 임의 JSON에 돌고 사람이 읽을 표를 내야 하며 batch/evolve에서도 호출된다. pytest는 CI pass/fail 게이트용으로 같은 함수를 import.
- **신규 `golden/score.py`** — 순수 함수 + CLI, 2모드:
  - **`score`(공짜):** 결과 JSON + 골든 → `ScoreReport`. `output_scheme.py`의 `_norm`·`_split_names`·`_has_useful_data`·`HospitalHomepageResult` **재사용**(재구현 금지).
  - **`compare`(유료):** `manifest.json`의 URL로 `main.run(...)` 재크롤 → 동결 골든과 델타 표. 진화안 적용 전 유료 게이트.
- **메트릭(ScoreReport):** schema_valid(하드), **leak_count(하드: 0이어야)**, matched/unmatched 제품·장비(카운트), treatments/doctors(카운트), identity(범주), has_useful_data(bool), cost/duration(천장 체크, compare에서만).
  - **누수 불변식:** 각 `treatments[].product_name/equipment_name`을 `_split_names`→`_norm`한 게 products/equipments 이름집합에 있어야 한다(= `aggregate_from_treatments`가 보장; 프로토타입으로 10개 leak=0 확인).
- **점수 철학(반-Goodhart, 여기 박는다):**
  - 카운트는 **밴드**로: `≥골든`=개선/유지, `-10%이내`=유지, `<골든-10%`=**회귀**. "많을수록 좋다" 무한보상 금지.
  - **하드 불변식(schema, leak==0)은 게이트:** 골든 중 하나라도 깨지면 제안 전체 기각(공짜·재크롤 불필요). 과거 데이터손실 버그가 바로 이 게이트에 — 재크롤 없이 — 걸렸을 종류다.
  - 종합 판정: `HOLD` / `IMPROVED` / `REGRESSED`.
- **순서:** `score` 먼저 → pytest sanity(`tests/test_golden.py`: `expected/` 자기대조 HOLD) → `compare` 마지막(유일하게 유료).

### 2. 독립 검증 sub-agent
생성≠검증 분리(자기채점 편향 방지). 크롤러(Sonnet/Opus)가 만든 출력을 **다른·싼** 모델이 재검토.
- **신규 스킬 `.claude/skills/hospital-output-verify/SKILL.md`** + 러너. 입력: 저장된 JSON, URL, 카탈로그2, 읽기전용 Playwright. **재추출이 아니라 갭 적발**(스팟체크라 싸다).
- **태스크(싼 것 먼저·바운드):**
  1. **skip-audit(공짜·curl만):** exit2 건을 `triage` 재실행해 SKIP이 정당했나 확인 — 잘못된 SKIP=영구 데이터손실=최악에러라 최고가치·최저비용.
  2. **빈 필드 audit:** null인 필드(영업시간·가격·의료진)가 실제 페이지에 있나 ~3–4페이지 바운드.
  3. **놓친 제품·장비 audit:** 메뉴·시술목록 토큰 중 출력에 없는 것 + 카탈로그 브랜드명 대조 → *후보* 보고(확정매칭 아님).
- **신규 `.../hospital-output-verify/reference/verify_scheme.py`**(pydantic, `output_scheme.py` 패턴): `VerifyFinding{hospital_id, area, kind(missed_item|empty_field_present|suspicious_skip|stale_pattern), evidence, confidence, suggested_signal}`, `VerifyReport{..., findings[], cost_usd, pages_checked}`. `suggested_signal`이 진화로 가는 다리(뇌가 뭘 놓쳤나를 사람말로).
- **비용통제(전수 아님):** EMPTY(exit1) 100% + TRIAGE_SKIP(exit2) 100%(공짜 curl-audit) + USEFUL(exit0) **롤링 ~10% 랜덤**(풀 agent). per-verify 캡 $0.30, 페이지 바운드.
- **검증기가 틀릴 위험(또 다른 무인 루프):** 검증기는 **findings만 내고 파일 편집·출력 덮어쓰기 절대 안 함**(메모리에 격리). confidence med/high만 진화에 투입, low는 N회 반복돼야 카운트. **카탈로그 근거 필수**(놓친 제품은 실제 카탈로그 엔트리 참조 — 환각 방지, `국산보톡스→보톡스` 거짓매칭의 거울). 제안이 골든에서 깨지면 그 신호는 검증기 오류로 로깅.
- 정직한 한계: 싼 검증기도 SPA·vision 사각 있음 → "정밀 오라클"이 아니라 "체계적 누락의 recall 부스터". 신뢰는 N회 반복+골든 검증에서.

### 3. 진화 엔진
누적 findings + 메트릭 → **뇌 파일 편집 제안** → 골든 검증 → 사람 게이트.
- **신규 `evolve.py`**(top-level). 주기적(N건마다 또는 수동):
  1. **신호 채굴:** `memory/findings.jsonl`을 `(area, kind, suggested_signal)`로 클러스터, **≥N개 distinct 병원**(N=3 시작)이고 `rejected_proposals.jsonl`에 없으면 후보. (과적합 방지 — 한 이상한 사이트가 뇌를 못 바꾼다.)
  2. **제안 초안**(파일 하나·최소편집):
     - **`triage.py` 키워드**(`STRONG_BEAUTY_KW`/`NONTARGET_TYPES`/`PARKED_KW`) — 가장 기계적·고신뢰. **여기서 시작.**
     - **`SKILL.md` Gotchas** — 불릿 1개 추가(가장 낮은 폭발반경, 코드 아닌 가이드).
     - **카탈로그 추가**(제품/장비 JSON) — *추가만*, 매칭로직 편집 금지. matched 카운트를 직접 움직여 Goodhart-prone이라 **가장 강하게 게이트**(반복 + 실제 제조사/브랜드 메타데이터 필수).
  3. **골든 검증(처분자):**
     - **공짜 사전체크:** 스크래치 복사본에 편집 적용 → triage/키워드·gotcha 제안은 재크롤 없이 평가가능(골든 10 URL의 triage 결정 + 기록된 SKIP케이스) → `score` 돌려 하드불변식 깨지면 즉시 기각. $0.
     - **유료 재크롤 게이트(사전체크 통과 & 크롤출력 바뀔 수 있을 때만):** `compare`로 10개 재크롤·델타, `HOLD`/`IMPROVED`만 유지(~$10–30, 배치로).
  4. **사람 게이트(엔지니어로 남기):** **자동 머지 절대 금지.** 살아남은 제안을 **git diff(브랜치) + 근거(어떤 findings·몇 병원·골든 델타표)** 로 `proposals/` 또는 `gh` PR. 사람이 작은 증거기반 diff만 검토.
  5. **결과 기록:** 수락→`accepted_proposals.jsonl`+뇌버전; 기각→`rejected_proposals.jsonl`(재채굴 금지, 검증기오류 플래그).
- **반-Goodhart(§1 점수철학 적용):** §3 고유 가드는 **정밀 카운터지표**(matched 대비 junk-unmatched 증가 추적 — unmatched만 부풀면 "개선 아님"), N-병원 반복, 카탈로그(헤드라인 지표 직격 레버)는 최강 게이트.
- **최소 1판:** **triage 키워드 제안만**, **공짜** triage 재실행 + `score` 사전체크, diff 제시. 재크롤·카탈로그·SKILL편집 없음 → 전체 파이프라인(채굴→제안→검증→게이트)을 $0로 증명.

### 4. 배치 드라이버
- **신규 `batch.py`**(top-level). 입력: `homepage_url` 있는 CSV(`sample10.csv` 형식). `load_hospital_from_csv` 행 형식 재사용.
- **URL 확보 갭(명시):** `beauty_hospitals_gangnam.csv`(1,405행)엔 URL 없음. 드라이버는 URL-enriched CSV를 먹어야 하고, **그 CSV 생산(검색/스크랩/수기)은 별도 상류 파이프라인**으로 사람이 소유. URL없는 행을 조용히 실패처리하지 말 것.
- **동시성 풀:** asyncio 세마포어 기본 8·최대 12(24GB/12코어, ~1GB/run, **메모리 바인딩**). 각 슬롯이 `main.run(...)`.
- **exit 라우팅(`CLAUDE.md`의 0/1/2 계약 재사용):** 0→완료·10% 검증샘플 후보 / 1 EMPTY→백오프 재시도(최대 2회)→소진 시 human inbox / 2 SKIP→재시도금지·공짜 curl 검증 큐.
- **429 백오프:** 진짜 천장은 API TPM/RPM(머신 아님 — `CLAUDE.md` "측정된 운영 사실"). 글로벌 토큰버킷/지수백오프, transient로 재큐(실패 아님). per-run 예산캡과 별개.
- **진행·집계:** done/empty/skip/failed, 평균비용(time-cap런은 `ResultMessage` 미수신이라 비용 미측정 — `CLAUDE.md` 알려진 갭), 유효율(~100%), ETA.
- **resume 상태(§5):** 모든 상태전이 영속화, 재시작시 done 스킵·in-flight/empty 재큐. 멱등.
- **주기적 진화 트리거:** N건(예 250)마다 신규런 멈추고 검증샘플 + `evolve.py` 제안, diff를 inbox로, 재개. 진화는 배치를 **막지 않음**(제안은 큐잉, 사람 머지 전까진 현재 뇌로 계속).
- **human inbox** `memory/inbox.jsonl`: 소진된 EMPTY·의심 SKIP·차단 사이트 + 병원별 `crawl_metadata.follow_up`의 배치레벨 롤업.

### 5. 메모리/상태 레이아웃
신규 `memory/`. 기존 per-file `crawl_metadata.follow_up`(per-병원, in-band) 및 `MEMORY.md`(세션연속)와 연계.

| 파일 | 용도 | 방식 |
|---|---|---|
| `memory/batch_state.json` | 병원별 `{id:{status, attempts, last_exit, cost, ts}}` — resume 진실원 | 원자적 덮어쓰기 |
| `memory/findings.jsonl` | 누적 `VerifyReport` — 원시 신호풀 | append |
| `memory/rejected_proposals.jsonl` | 골든/사람이 거부한 제안 — **재채굴 금지** | append |
| `memory/accepted_proposals.jsonl` | 적용 편집 + 뇌버전 + 적용시 골든델타 (커밋 가치) | append |
| `memory/metrics_history.jsonl` | 배치 체크포인트 집계(평균비용·유효율·누수율·매칭분포) — 드리프트 감지 (커밋) | append |
| `memory/inbox.jsonl` | human inbox + follow_up 롤업 | append |

- **gitignore 결정(사람):** `golden/`(계약)=커밋. `memory/*.jsonl`=런타임이라 `.gitkeep`만 두고 내용 ignore, **단 `accepted_proposals`·`metrics_history`는 감사이력이라 커밋**. (`output/` ignore 관례와 동일.)
- `output_scheme.py` 스키마 변경 불필요. `MEMORY.md`에 `golden/manifest.json` + `memory/metrics_history.jsonl`을 "지금 어디까지"의 새 출처로 한 줄 추가.

## 재사용 맵 (재구현 금지)
- `output_scheme.py`: `repair_to_valid`·`aggregate_from_treatments`·`_has_useful_data`·`_norm`·`_split_names`·`HospitalHomepageResult` → 스코어러 + 누수체크.
- `main.py`: `run`·`expected_output_path`·`repair_output`·`backfill_cost`·`load_hospital_from_csv` → 배치/재크롤게이트가 호출.
- `triage.py`: `triage`·`fetch`·`scan_content` → 검증기 무료 skip-audit + 첫 진화타깃.
- `SKILL.md`의 "Gotchas" 섹션 → 설계된 진화 타깃.
- `data/sample10.csv` → 배치 입력 형식 + 골든 10 IDs.

## 위험 & 정직한 한계
1. **자기수정(최상위 위험):** 뇌 편집이 이후 모든 크롤 망칠 수 있음. *완화:* 자동머지 금지(사람 diff)·골든 하드불변식 거부권·N병원 반복·rejected 메모리·최저폭발반경(triage 키워드)부터 $0.
2. **검증기 신뢰도:** 싼 검증기 사각·환각. *완화:* findings만(편집·덮어쓰기 금지)·카탈로그 근거·confidence 임계·골든이 최종심판.
3. **비용 증폭:** 검증+재크롤 게이트가 배치 위에 곱셈. *완화:* 10%+EMPTY+공짜SKIP만·유료게이트 주기적·예산화·최소루프 $0.
4. **트리아지 SPA 사각:** raw HTML로 JS 내용 못 봄(SPA는 항상 CRAWL, 스킵 안 함) → 검증기 무료 audit도 같은 사각. *수정 안 됨:* "SPA→무조건 CRAWL" 보수적 입장 유지.
5. **URL 확보 갭(실배치 차단):** `gangnam.csv`에 URL 없음. URL-enrichment 파이프라인 전엔 자기진화 기계 전체가 production엔 무의미. 상류에서 따로 소유.
6. **Goodhart/골든 과적합:** 10개는 좁은 금본위. *완화:* 밴드 점수·정밀 카운터지표·**골든 세트를 천천히 키우기**(잘 검증된 배치출력을 사람검토 후 `golden/expected/`로 승격).
7. **이해 부채:** 진화하는 SKILL.md/triage가 사람 이해에서 멀어짐. *완화:* 모든 수락편집은 근거 있는 diff(`accepted_proposals.jsonl`)·`metrics_history`로 드리프트 가시화.

## 사람 결정점 (STOP)
- **Phase 0 후:** 10개 opus 출력이 진짜 금인가?
- **Phase 2 실런 전:** 인증 모델(구독 vs API키+tier — 지속 8k엔 API키; `CLAUDE.md` Commands 절).
- **Phase 3/6 유료게이트 전:** 재크롤 예산(~$10–30/사이클) 승인.
- **매 진화 제안:** diff + 골든 델타 검토 후 머지(핵심 사람 게이트).
- **실 8,000 배치 전:** URL 확보 파이프라인 해결.

---
*영감: Addy Osmani, "Loop Engineering". 현 구현 상태·운영 규칙·측정된 사실은 `CLAUDE.md`.*
