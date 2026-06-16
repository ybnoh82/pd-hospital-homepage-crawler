# pd-hospital-crawler

플랜닥스(Plandocs) 잠재고객인 **미용·성형 전문 병원의 공식 홈페이지를 크롤링해 정규화 JSON으로 추출**하는 도구. 추출 대상: 운영정보·의료진·취급제품·취급장비(필러·톡신·스킨부스터·리프팅/레이저)·시술·가격·다국어. **한 실행 = 한 병원**(배치는 이 단위를 바깥에서 반복).

핵심: 병원마다 홈페이지 구조가 제각각이라 **크롤링 로직은 고정 파싱 스크립트가 아니라 Claude Code 스킬**(`hospital-homepage-extract`)로 구현돼 있다. 에이전트가 Playwright MCP + vision으로 페이지를 직접 탐색·판독·매칭한다. `main.py`는 그 스킬을 `claude-agent-sdk`로 무인 실행하는 러너다.

## 빠른 시작

```bash
uv sync                                  # 의존성 (uv 기반, Python >=3.14)

# 인증: .env의 ANTHROPIC_API_KEY 또는 Claude Code 로그인(구독) 중 하나
# 한 병원 크롤링 (기본 Sonnet 4.6 · effort low · 예산캡 $0.90 · 시간캡 270s)
uv run python main.py <홈페이지URL> --id <병원ID> --name <병원이름>

# 병원DB CSV에서 행을 찾아 주소까지 채워 넘기기 (identity 판정에 사용)
uv run python main.py <홈페이지URL> --from-csv data/sample10.csv --id <병원ID>

# 결과: output/{병원ID}_{병원이름}_homepage.json
```

실행하면 트리아지(대상 아닌 병원·죽은 사이트를 curl로 사전 차단) → 크롤 → repair를 거쳐 **항상 유효 JSON이 나오고**, exit `0`/`1`/`2`(쓸만함 / 빈약함 / 제외)로 결과가 갈린다. 상세는 `CLAUDE.md`.

## 문서 지도 (읽는 순서)

| 문서 | 내용 |
|---|---|
| **`CLAUDE.md`** | 운영·아키텍처의 **1차 진실원** — 명령어, 러너 책임, 데이터 원칙, **측정된 운영 사실(다시 측정 말 것)**. 먼저 읽을 것. |
| **`.claude/skills/hospital-homepage-extract/SKILL.md`** | 크롤 행동규칙(어디를 방문·언제 vision·어떻게 매칭, §0~§8). 추출 작업의 진실원. |
| **`DESIGN.md`** | **앞으로의 방향·로드맵** — "스스로 진화하는 크롤러" 전체 설계도(Phase 0~6). |
| **`.../reference/output_scheme.py`** | 출력 스키마(`HospitalHomepageResult`). 각 필드 형식의 진실원이자 검증·repair 도구. |

## 입력 데이터

- `data/beauty_hospitals_gangnam.csv` — 대상 병원 목록(약 1,400개). **홈페이지 URL 없음** — URL은 러너에 별도 인자로 넘긴다(URL 확보 파이프라인은 별도 — `DESIGN.md` 참고).
- `data/sample10.csv` — 위 컬럼에 `homepage_url`을 추가한 10개 표본(배치 입력 형식의 예, 회귀 테스트용).

## 코드

- `main.py` — 러너(스킬을 무인 실행하는 진입점). 책임 상세는 `CLAUDE.md`.
- `triage.py` — curl 기반 사전 분류(LLM·브라우저 없음). `--no-triage`로 끔.
- 린트: `uv run ruff check .`
