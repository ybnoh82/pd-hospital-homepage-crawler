"""hospital-homepage-extract 스킬을 claude-agent-sdk로 실행하는 러너.

병원 홈페이지 URL(과 선택적 병원정보)을 받아 `hospital-homepage-extract`
스킬을 돌리고, 결과 JSON을 output/{병원ID}_{병원이름}_homepage.json에 저장한다.

예:
    uv run python main.py https://example-clinic.co.kr
    uv run python main.py https://example-clinic.co.kr --id xaji0y --name 365엠씨의원
    uv run python main.py https://example-clinic.co.kr --from-csv data/beauty_hospitals_gangnam.csv --id xaji0y
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from dotenv import load_dotenv

from triage import triage

PROJECT_ROOT = Path(__file__).resolve().parent
SKILL_NAME = "hospital-homepage-extract"

# 기본 모델. 8,000개 배치의 비용·속도 목표(병원당 평균 $1·5분 이내)와 품질의 균형점.
# Haiku 4.5($1/$5)는 가장 싸지만 이 멀티스텝 에이전트 스킬(SPA 탐색·매칭·vision)에서
# 조기 종료·미저장으로 완주에 실패했다(측정). Sonnet 4.6($3/$15, Opus의 0.6배)은
# 안정적으로 완주하며 effort로 비용을 더 줄일 수 있다. 하드 사이트는 --model로 Opus.
DEFAULT_MODEL = "claude-sonnet-4-6"

# 추론 effort. Sonnet 4.6/Opus만 지원(Haiku는 에러). 멀티스텝(SPA·매칭·vision·스키마
# 정합 저장)에서 추론량이 완주 품질을 좌우한다 — low는 정찰만 하다 조기 종료·미저장이
# 잦았다(실측). 비용·시간 캡을 넉넉히 둔 만큼 high로 올려 완성도를 우선한다.
DEFAULT_EFFORT = "high"

# 비용 하드캡($). 미용·성형 사이트는 콘텐츠가 풍부해 거의 캡에 닿는다. 타이트한 캡은
# 카탈로그 매칭·스키마 정합 최종저장을 못 끝내고 끊겼다(실측: 저비용 캡 재크롤 다수 EMPTY).
# 완전성을 우선해 $3로 올려 자연 완주 여지를 준다(품질↑·비용↑ 트레이드).
DEFAULT_BUDGET_USD = 3.0

# 시간 하드캡(초). $ 예산은 시간을 못 묶는다. 시간을 단일 바인딩 제약으로 삼고, 78%
# 지점에서 "지금 마무리하라" 메시지를 주입해 기요틴 대신 깨끗한 유효 저장을 시킨다
# (SOFT_DEADLINE_RATIO). 하드캡은 마지막 백스톱. 풍부한 사이트의 정찰→상세→매칭→저장
# 전 과정을 담도록 15분(900s)으로 둔다.
DEFAULT_TIME_LIMIT_S = 900
SOFT_DEADLINE_RATIO = 0.78

# 소프트 데드라인에 주입하는 마무리 지시. 기요틴 직전에 에이전트가 스스로 유효 JSON을
# 저장·검증하고 끝내게 한다 — 시계를 못 보는 에이전트에게 시간을 알려주는 셈이다.
FINALIZE_MESSAGE = (
    "시간이 거의 다 됐다. 지금부터 새 페이지를 열거나 추가 크롤링을 하지 마라. "
    "즉시 그때까지 수집한 것만으로 스키마에 맞는 유효한 JSON을 출력 경로에 저장하고, "
    "output_scheme.py로 검증해 통과시킨 뒤 종료하라. 못 끝낸 영역은 follow_up에 남겨라. "
    "유효성이 최우선이다 — 빈 필드·미매칭이 있어도 스키마만 통과하면 된다."
)

# 조기 종료(저장 전 턴 끝냄) 감지 시 주입하는 재촉. 저비용 effort(특히 Sonnet low/medium)는
# 정찰을 마치고도 "디렉토리만 만들고" 또는 계획만 말한 뒤 파일 쓰기 없이 턴을 끝내는 경향이
# 있다(실측: sample10 매트릭스). 그러면 ResultMessage가 와서 러너가 끊고, 저장된 게 없어
# 결과가 EMPTY가 된다 — 크롤은 됐는데 적지를 못한 손실. 이 메시지로 한 번 더 끌고 가 저장시킨다.
SAVE_NOW_MESSAGE = (
    "아직 결과 JSON 파일을 저장하지 않았다. 더 설명하거나 계획하지 말고, 지금 이 응답에서 "
    "그때까지 수집한 것만으로 스키마에 맞는 유효한 JSON을 출력 경로에 **실제로 파일로 써라**. "
    "디렉토리 생성·계획 설명은 저장이 아니다 — Write/Bash로 파일 쓰기를 끝까지 실행하라. "
    "카탈로그 매칭은 나중에 해도 되니, 거둔 제품·장비 이름은 unmatched에 그대로 넣고 먼저 저장하라."
)

# 조기 종료 재촉 최대 횟수. 유효 저장이 생기거나 소프트 데드라인에 닿으면 멈춘다.
# 진짜 빈 사이트(트리아지가 못 거른)면 몇 번 재촉 후 포기 — 그 낭비는 상한으로 묶는다.
MAX_SAVE_NUDGES = 3

# playwright MCP 서버. 배치(8,000개 병렬)를 위해:
# --headless: 가시 창 없이 실행(메모리·CPU↓, 헤드리스 서버 실행 가능, 브라우저 오버헤드↓).
#   단, 시간캡에 묶여 속도 이득은 주로 "같은 시간에 더 많은 페이지"(완전성)로 나타난다.
# --isolated: 프로필을 메모리에 둠(병렬 인스턴스 간 프로필 잠금 충돌 방지, 시작 빠름, 클린 상태).
PLAYWRIGHT_SERVER = {
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--headless", "--isolated"],
}

# CLI stdout 버퍼 상한. SDK 기본값은 1MB라 base64 스크린샷 한 장이 이를 넘으면
# 메시지 리더가 죽는다(vision은 스킬의 핵심이라 반드시 키운다). 50MB로 여유를 둔다.
MAX_BUFFER_SIZE = 50 * 1024 * 1024


def load_hospital_from_csv(csv_path: Path, hospital_id: str) -> dict[str, str]:
    """병원DB CSV에서 id로 한 행을 찾아 병원정보 dict로 반환한다."""
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("id") == hospital_id:
                return row
    raise SystemExit(f"CSV에서 id={hospital_id!r}를 찾지 못했다: {csv_path}")


# 트리아지 shape 힌트를 프롬프트 문장으로. 정찰 왕복을 줄이는 참고용(사이트에서 재확인).
SHAPE_HINTS = {
    "text": "텍스트형으로 보인다(본문에 정보가 텍스트로 노출) — vision을 최소화하고 evaluate 추출을 우선하라.",
    "image": "이미지형으로 보인다(내용이 이미지에 묻힘) — §4 vision 게이트를 적극 가동하되 효율 기법을 지켜라.",
    "spa": "SPA로 보인다(콘텐츠가 JS로 렌더되어 raw HTML엔 안 보임) — 반드시 browser로 열어 렌더 후 수집하라.",
}


def build_prompt(
    url: str,
    info: dict[str, str],
    shape_hint: str | None = None,
    output_dir: str = "output",
) -> str:
    """스킬을 호출하도록 유도하는 프롬프트를 만든다.

    info에 채워진 항목만 입력으로 넘긴다(빈 값은 생략).
    shape_hint(트리아지 분류 결과)가 있으면 정찰 참고용으로 한 줄 덧붙인다.
    output_dir로 에이전트가 저장할 디렉토리를 지정한다(기본 output).
    """
    fields = [
        ("hospital_id", info.get("id")),
        ("hospital_name", info.get("hospital_name") or info.get("name")),
        ("시도", info.get("sido")),
        ("시군구", info.get("sggu")),
        ("읍면동", info.get("emdong")),
        ("도로명주소", info.get("address")),
    ]
    lines = [f"- {label}: {value}" for label, value in fields if value]
    info_block = "\n".join(lines) if lines else "- (병원정보 없음 — URL만으로 진행)"
    hint_block = ""
    if shape_hint in SHAPE_HINTS:
        hint_block = (
            f"\n사전 분류 힌트(참고용): 이 사이트는 {SHAPE_HINTS[shape_hint]}\n"
        )

    return (
        f"{SKILL_NAME} 스킬을 사용해 아래 병원 공식 홈페이지를 크롤링·추출하고, "
        f"결과를 {output_dir}/{{병원ID}}_{{병원이름}}_homepage.json에 저장한 뒤 스키마로 검증한다.\n\n"
        f"- homepage_url: {url}\n"
        f"{info_block}\n"
        f"{hint_block}\n"
        "지금 바로 도구를 써서 끝까지 자율 수행한다. 너는 무인 배치로 실행 중이며, "
        "사용자는 보고 있지 않으니 질문하거나 허락을 구하지 말고, 계획만 설명하고 멈추지 마라. "
        "한 번의 응답에 작업을 다 담으려 하지 말고, browser_navigate/browser_evaluate 등 "
        "Playwright 도구를 실제로 호출해 페이지를 열고 수집하라. "
        "결과 JSON을 저장하고 스키마 검증까지 마쳤을 때, 또는 접속 불가·예산 소진 등으로 "
        "더 진행할 수 없을 때에만 턴을 끝낸다. 턴을 끝내기 전, 마지막 메시지가 계획·의도·"
        "다음 단계 목록이면 그 작업을 지금 도구 호출로 실제 수행하라.\n\n"
        "중요(증분 저장 — 캡이 예고 없이 끊는다): 비용·시간 한도는 보통 4~5분/약 $1 안에서 "
        "작업을 강제로 끊는다. 그 전에 저장이 안 돼 있으면 결과가 전부 사라진다. 그래서 "
        "**정찰(첫 browser_evaluate) 직후, 상세 페이지·카탈로그 매칭 전에** 그때까지 거둔 "
        "것만으로 유효한 JSON을 위 경로에 **반드시 먼저 저장**하라. 첫 저장에 카탈로그 매칭은 "
        "필요 없다 — 거둔 제품·장비 이름을 unmatched에 그대로 넣고 저장하면 된다(매칭 0건도 OK). "
        "큰 카탈로그를 읽어 매칭하는 일이 첫 저장을 늦추는 주범이다. 이후 페이지·매칭으로 보강할 "
        "때마다 같은 파일을 덮어써 갱신하라. '모두 수집한 뒤 한 번에 저장'은 금지 — 끊기면 0이 된다. "
        "출력 디렉토리는 이미 만들어져 있다 — mkdir 같은 준비 단계에 응답을 쓰지 말고 곧장 파일을 "
        "써라. 디렉토리 생성·계획 설명은 저장이 아니다. '저장하겠다'고 말한 응답에서 반드시 실제 "
        "파일 쓰기까지 끝내라 — 준비만 하고 턴을 끝내면 거둔 게 전부 사라진다."
    )


# --log 활성화 시 log()가 stdout과 함께 출력을 쓰는 파일 핸들(없으면 stdout만).
_LOG_FH = None


def log(*args: object) -> None:
    """stdout에 즉시(flush) 출력하고, 로그 파일이 열려 있으면 거기에도 같이 쓴다."""
    print(*args, flush=True)
    if _LOG_FH is not None:
        print(*args, file=_LOG_FH, flush=True)


def open_run_log(info: dict[str, str | None], log_dir: str = "logs"):
    """logs/{id}.log를 열어 전역 핸들에 건다(이후 log()가 자동 tee).

    파일명은 출력 JSON과 같은 식별자(id)를 쓴다 — id가 없으면 이름, 그것도 없으면 unknown.
    반환한 핸들은 호출자가 close한다(main의 finally).
    """
    global _LOG_FH
    log_root = PROJECT_ROOT / log_dir
    log_root.mkdir(exist_ok=True)
    stem = info.get("id") or info.get("hospital_name") or info.get("name") or "unknown"
    _LOG_FH = open(log_root / f"{stem}.log", "w", encoding="utf-8")
    return _LOG_FH


def render_message(msg: object) -> str | None:
    """스트리밍 메시지를 사람이 읽기 좋게 출력한다.

    AssistantMessage를 만나면 그 모델명을 반환한다(비용 메타 백필용).
    """
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                log(f"\n💭 {block.thinking.strip()[:500]}")
            elif isinstance(block, TextBlock):
                text = block.text.strip()
                if text:
                    log(f"\n{text}")
            elif isinstance(block, ToolUseBlock):
                detail = block.input.get("url") or block.input.get("skill") or ""
                log(f"  🔧 {block.name} {detail}".rstrip())
        return msg.model
    return None


def expected_output_path(
    info: dict[str, str | None], output_dir: str = "output"
) -> Path | None:
    """스킬이 저장할 결과 파일 경로. id·이름을 둘 다 알 때만 계산 가능."""
    hid = info.get("id")
    name = info.get("hospital_name") or info.get("name")
    if hid and name:
        return PROJECT_ROOT / output_dir / f"{hid}_{name}_homepage.json"
    return None


def backfill_cost(
    path: Path, result: ResultMessage, model: str | None, effort: str | None
) -> bool:
    """저장된 JSON의 crawl_metadata.cost를 러너가 관측한 실측값으로 채운다.

    에이전트는 자기 토큰·비용을 못 보므로 cost를 null로 남긴다(SKILL.md §8).
    러너는 ResultMessage로 실측을 알기에 여기서 보강한다 — 에이전트가 적은 값은 덮지 않는다.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    usage = result.usage or {}
    observed = {
        "model": model or next(iter(result.model_usage or {}), None),
        "effort": effort if (effort and supports_effort(model)) else None,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": round(result.total_cost_usd, 4)
        if result.total_cost_usd is not None
        else None,
        "duration_seconds": round(result.duration_ms / 1000),  # 스키마상 int
    }
    cm = data.setdefault("crawl_metadata", {})
    cost = cm.get("cost") or {}
    # 에이전트가 이미 채운 값은 보존하고, 비어 있는 칸만 실측으로 메운다.
    for key, value in observed.items():
        if cost.get(key) in (None, "") and value is not None:
            cost[key] = value
    cm["cost"] = cost
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return True


def backfill_timecap_duration(
    path: Path, duration_s: int, model: str | None, effort: str | None
) -> bool:
    """시간캡(TimeoutError)으로 ResultMessage가 없을 때, 러너가 아는 것만 채운다.

    비용·토큰은 ResultMessage 없이는 알 수 없어 null로 둔다. 다만 경과시간(≈시간캡)·모델·
    effort는 러너가 알기에 채워, 배치 비용·시간 모니터링에서 '가장 오래 돈(=잠재 최고가)'
    시간캡 런이 cost 통째 null로 누락되는 갭을 줄인다(CLAUDE.md '알려진 갭'의 부분 해소).
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    cm = data.setdefault("crawl_metadata", {})
    cost = cm.get("cost") or {}
    observed = {
        "model": model,
        "effort": effort if (effort and supports_effort(model)) else None,
        "duration_seconds": duration_s,
    }
    for key, value in observed.items():
        if cost.get(key) in (None, "") and value is not None:
            cost[key] = value
    cm["cost"] = cost
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return True


SCHEMA_VALIDATOR = (
    PROJECT_ROOT / ".claude/skills" / SKILL_NAME / "reference/output_scheme.py"
)


def repair_output(path: Path, info: dict[str, str | None], url: str) -> bool:
    """저장 JSON을 스키마 통과 형태로 고친다(없으면 유효 스켈레톤 생성).

    에이전트가 시간 압박에 쓴 무효 JSON에서 유효 부분만 살리고 무효 부분은 떨궈,
    **어떤 실행이든 100% 유효 파일을 남긴다.** 유효성을 에이전트 신뢰도에서 떼어낸다.
    반환값: 유의미한 데이터가 있으면 True(USEFUL), 빈 스켈레톤 수준이면 False(EMPTY).
    """
    import subprocess

    name = info.get("hospital_name") or info.get("name") or "unknown"
    r = subprocess.run(
        [
            sys.executable,
            str(SCHEMA_VALIDATOR),
            "--repair",
            str(path),
            "--id",
            info.get("id") or "unknown",
            "--name",
            name,
            *(["--url", url] if url else []),
        ],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip().startswith("USEFUL")


# 트리아지가 비타깃·죽은 사이트로 판단해 크롤 없이 제외한 종료 코드. USEFUL(0)·EMPTY(1)과
# 구분한다 — 배치 드라이버는 이걸 "정상 제외(재시도 금지)"로 다룬다(EMPTY는 재시도 대상).
TRIAGE_SKIP_EXIT = 2


def write_skip_output(
    out_path: Path, info: dict[str, str | None], url: str, verdict: dict
) -> None:
    """트리아지 SKIP 시, 크롤 없이 유효 스켈레톤을 쓰고 사유를 notes에 남긴다."""
    reason = verdict.get("reason", "트리아지 스킵")
    sig = verdict.get("signals", {})
    raw = {
        "crawl_metadata": {
            "crawl_method": "prefetch 트리아지 — 크롤 생략(비타깃/죽은 사이트)",
            "pages_crawled": 0,
            "notes": [f"트리아지 스킵: {reason}", f"트리아지 신호: {sig}"],
        }
    }
    out_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    # repair로 유효 스키마 형태로 정규화(id/name/url을 채우고 나머지는 기본값).
    repair_output(out_path, info, url)


def supports_effort(model: str | None) -> bool:
    """effort 파라미터를 받는 모델인지. Haiku·구형은 에러를 낸다."""
    m = model or ""
    return "sonnet-4-6" in m or "opus" in m


def has_useful_output(path: Path | None) -> bool:
    """에이전트가 유의미한 데이터를 이미 파일로 저장했는지(조기 종료 재촉 판단용).

    repair 전 원본을 본다. 저비용 모델이 별칭 필드명으로 쓰는 경우까지 관대하게 인정한다
    (operation_info↔business_info 등) — 여기선 "거둔 게 있나"만 보면 되고 정규화는 repair가 한다.
    핵심 가치(시술·제품·장비) 중 하나라도 있거나 운영정보가 채워졌으면 저장된 것으로 본다.
    """
    if path is None:
        return False
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(d, dict):
        return False

    def _len(x: object) -> int:
        return len(x) if isinstance(x, (list, dict)) else 0

    def _nested(container: object, *keys: str) -> int:
        if not isinstance(container, dict):
            return _len(container)
        return sum(_len(container.get(k)) for k in keys)

    treatments = d.get("treatments") or d.get("treatment_info")
    products = d.get("products")
    equipments = d.get("equipments")
    op = d.get("operation_info") or d.get("business_info")
    return bool(
        _len(treatments)
        or _nested(products, "matched_products", "unmatched_products")
        or _nested(products, "matched", "unmatched")  # 별칭형
        or _nested(equipments, "matched_equipments", "unmatched_equipments")
        or (isinstance(op, dict) and any(v for v in op.values()))
    )


async def run(
    url: str,
    info: dict[str, str | None],
    budget: float,
    model: str | None,
    effort: str | None,
    time_limit: float,
    no_triage: bool = False,
    output_dir: str = "output",
) -> int:
    (PROJECT_ROOT / output_dir).mkdir(exist_ok=True)
    out_path = expected_output_path(info, output_dir)

    # prefetch 트리아지: 크롤 전에 curl로 싸게 분류한다(~$0·수초). 죽은 도메인·비타깃
    # 진료과(안과·내과 등)면 비싼 에이전트를 띄우지 않고 유효 스켈레톤만 쓰고 제외한다.
    # 스킵은 보수적이다(강한 미용 신호가 있으면 무조건 크롤) — 자세한 규칙은 triage.py.
    shape_hint: str | None = None
    if not no_triage:
        name = info.get("hospital_name") or info.get("name") or ""
        verdict = triage(url, name)
        log(f"🔎 트리아지: {verdict['decision']} — {verdict['reason']}")
        if verdict["decision"] == "SKIP":
            if out_path is not None:
                out_path.unlink(missing_ok=True)
                write_skip_output(out_path, info, url, verdict)
                log(
                    f"결과 저장: {out_path.relative_to(PROJECT_ROOT)} — "
                    "트리아지 제외(크롤 생략)"
                )
            return TRIAGE_SKIP_EXIT
        shape_hint = verdict.get("signals", {}).get("hint")

    options = ClaudeAgentOptions(
        cwd=str(PROJECT_ROOT),
        skills=[SKILL_NAME],
        mcp_servers={"playwright": PLAYWRIGHT_SERVER},
        permission_mode="bypassPermissions",
        max_budget_usd=budget,
        model=model,
        # Haiku 등 미지원 모델에 넘기면 에러나므로 지원 모델일 때만 설정한다.
        effort=effort if (effort and supports_effort(model)) else None,
        # base64 스크린샷이 기본 1MB 버퍼를 넘겨 리더가 죽는 것을 막는다.
        max_buffer_size=MAX_BUFFER_SIZE,
        # 스킬 파일·CLAUDE.md 로드를 위해 프로젝트 설정을 읽는다.
        setting_sources=["project", "local"],
    )

    # 이전 실행의 잔존 결과 파일을 지우고 새로 크롤링한다. 남겨두면 약한 모델이
    # 크롤링 대신 그 파일을 읽어 재사용하는 오염이 생기고, 크래시 후 잔존 파일을
    # "저장됨"으로 오보하게 된다. (resume은 별도 기능으로 다룬다.)
    if out_path is not None:
        out_path.unlink(missing_ok=True)

    last_result: ResultMessage | None = None
    seen_model: str | None = None
    budget_reached = False
    time_reached = False
    steered = False
    nudges = 0
    elapsed: float | None = None  # 시간캡 시 경과시간(ResultMessage 없을 때 duration 백필용)
    fatal_error: str | None = None

    # 양방향 클라이언트로 크롤링을 돌리되, 소프트 데드라인에서 "마무리하라" 메시지를
    # 주입해 에이전트가 스스로 유효 JSON을 저장·검증하게 한다. 하드캡(asyncio.timeout)은
    # 에이전트가 마무리 지시를 무시할 때만 작동하는 마지막 백스톱이다.
    soft_deadline = time_limit * SOFT_DEADLINE_RATIO
    loop = asyncio.get_event_loop()
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(build_prompt(url, info, shape_hint, output_dir))
            start = loop.time()
            try:
                async with asyncio.timeout(time_limit):
                    async for msg in client.receive_messages():
                        seen_model = render_message(msg) or seen_model
                        if isinstance(msg, ResultMessage):
                            last_result = msg
                            # 조기 종료 구제: 에이전트가 유효 저장 전에 턴을 끝냈고, 소프트
                            # 데드라인 전이며 재촉 여유가 있으면 끊지 말고 "지금 저장하라"를
                            # 주입해 한 번 더 끌고 간다. (저비용 effort의 EMPTY를 결정론적으로
                            # 줄인다 — 유효성을 에이전트 신뢰도에서 떼어내는 러너의 역할.)
                            if (
                                nudges < MAX_SAVE_NUDGES
                                and (loop.time() - start) < soft_deadline
                                and not has_useful_output(out_path)
                            ):
                                nudges += 1
                                log(
                                    f"\n⚠️  저장 전 조기 종료 감지 — 저장 재촉 주입 "
                                    f"({nudges}/{MAX_SAVE_NUDGES})"
                                )
                                await client.query(SAVE_NOW_MESSAGE)
                                continue
                            break
                        if not steered and (loop.time() - start) > soft_deadline:
                            steered = True
                            log(
                                f"\n⏱️  소프트 데드라인({soft_deadline:.0f}s) — 마무리 지시 주입"
                            )
                            await client.query(FINALIZE_MESSAGE)
            except TimeoutError:
                time_reached = True
                elapsed = loop.time() - start
    except Exception as exc:  # noqa: BLE001 — SDK가 에러 결과를 예외로 던진다
        # 예산 한도 도달은 이 프로젝트에서 정상 종료다(스킬이 follow_up을 남기고 멈춘다).
        if "budget" in str(exc).lower():
            budget_reached = True
        else:
            fatal_error = str(exc)

    if last_result is not None and last_result.subtype == "error_max_budget_usd":
        budget_reached = True

    # 러너가 결과 유효성을 보장한다: 에이전트가 쓴 (무효일 수 있는) JSON을 repair로
    # 스키마 통과 형태로 고치고(없으면 스켈레톤 생성), 그 뒤 실측 비용을 백필한다.
    useful = False
    if out_path is not None:
        useful = repair_output(out_path, info, url)
        if last_result is not None:
            backfill_cost(out_path, last_result, model or seen_model, effort)
        elif time_reached and elapsed is not None:
            # 시간캡으로 ResultMessage가 없으면 비용·토큰은 모르나, 러너가 아는
            # 경과시간·모델·effort는 채워 배치 모니터링에서 통째 누락되지 않게 한다.
            backfill_timecap_duration(
                out_path, round(elapsed), model or seen_model, effort
            )

    log("\n" + "─" * 48)
    if last_result is not None:
        cost = (
            f"${last_result.total_cost_usd:.4f}"
            if last_result.total_cost_usd is not None
            else "n/a"
        )
        log(
            f"턴 {last_result.num_turns} · {last_result.duration_ms / 1000:.1f}s · {cost}"
        )
    if budget_reached:
        log(f"예산 한도(${budget}) 도달 — 정상 종료. 미완료 영역은 follow_up 참고.")
    if time_reached:
        log(
            f"시간 한도({time_limit:.0f}s) 도달 — 정상 종료. 미완료 영역은 follow_up 참고."
        )
    if fatal_error:
        log(f"오류로 종료: {fatal_error}")
    if out_path is not None:
        mark = "유효·데이터 있음" if useful else "유효·빈 스켈레톤(재시도 권장)"
        log(f"결과 저장: {out_path.relative_to(PROJECT_ROOT)} — {mark}")

    if fatal_error:
        return 1
    # 유효 파일은 항상 남지만, 유의미한 데이터가 있을 때만 성공(아니면 배치가 재시도).
    return 0 if useful else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="claude-agent-sdk로 hospital-homepage-extract 스킬을 실행한다.",
    )
    p.add_argument("url", help="수집할 병원 공식 홈페이지 URL")
    p.add_argument("--id", dest="id", help="병원DB 아이디")
    p.add_argument("--name", dest="name", help="병원이름")
    p.add_argument("--sido", help="시도")
    p.add_argument("--sggu", help="시군구")
    p.add_argument("--emdong", help="읍면동")
    p.add_argument("--address", help="도로명주소")
    p.add_argument(
        "--from-csv",
        type=Path,
        help="병원DB CSV 경로. --id로 행을 찾아 병원정보를 채운다",
    )
    p.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help=f"최대 비용(USD) 상한. 기본 ${DEFAULT_BUDGET_USD}",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"사용할 모델. 기본 {DEFAULT_MODEL}",
    )
    p.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=["low", "medium", "high", "max"],
        help=f"추론 effort(Sonnet/Opus만). 기본 {DEFAULT_EFFORT}",
    )
    p.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT_S,
        help=f"최대 소요시간(초) 상한. 기본 {DEFAULT_TIME_LIMIT_S}",
    )
    p.add_argument(
        "--no-triage",
        action="store_true",
        help="prefetch 트리아지(비타깃·죽은 사이트 사전 차단)를 끄고 무조건 크롤한다",
    )
    p.add_argument(
        "--log",
        action="store_true",
        help="실행 출력을 {log-dir}/{id}.log 파일로도 남긴다(stdout과 함께). 배치 운영용",
    )
    p.add_argument(
        "--output-dir",
        default="output",
        help="결과 JSON을 저장할 디렉토리. 기본 output. 조합 테스트 시 분리용",
    )
    p.add_argument(
        "--log-dir",
        default="logs",
        help="--log 사용 시 로그를 남길 디렉토리. 기본 logs",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.from_csv:
        if not args.id:
            raise SystemExit("--from-csv 사용 시 --id가 필요하다")
        info = load_hospital_from_csv(args.from_csv, args.id)
    else:
        info = {
            "id": args.id,
            "name": args.name,
            "sido": args.sido,
            "sggu": args.sggu,
            "emdong": args.emdong,
            "address": args.address,
        }

    # --log면 logs/{id}.log를 열어 두면 run() 내내 log()가 stdout과 함께 tee한다.
    # run()의 여러 return 지점을 건드리지 않도록 여기서 열고 finally로 닫는다.
    global _LOG_FH
    log_fh = open_run_log(info, args.log_dir) if args.log else None
    try:
        return asyncio.run(
            run(
                args.url,
                info,
                args.budget,
                args.model,
                args.effort,
                args.time_limit,
                args.no_triage,
                args.output_dir,
            )
        )
    finally:
        if log_fh is not None:
            log_fh.close()
            _LOG_FH = None


if __name__ == "__main__":
    raise SystemExit(main())
