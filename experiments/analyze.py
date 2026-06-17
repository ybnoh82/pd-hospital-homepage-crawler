"""매트릭스 결과 분석 — 드라이버 meta + 출력 JSON + 로그를 합쳐 비교표를 낸다.

experiments/runs/<combo>/meta/<id>.json (벽시계·종료코드)와
experiments/runs/<combo>/<id>_<name>_homepage.json (데이터)을 병합하고,
로그(experiments/runs/<combo>/logs/<id>.log)에서 배치 이슈를 마이닝한다.

출력: experiments/results.jsonl, 그리고 stdout에 마크다운 비교 리포트.

    uv run python experiments/analyze.py
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"
RESULTS_JSONL = PROJECT_ROOT / "experiments" / "results.jsonl"

COMBO_ORDER = [
    "sonnet_low",
    "sonnet_medium",
    "sonnet_high",
    "opus_low",
    "opus_medium",
    "opus_high",
]


def _find_output_json(combo_dir: Path, hid: str) -> Path | None:
    hits = list(combo_dir.glob(f"{hid}_*_homepage.json"))
    return hits[0] if hits else None


def _safe_load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _count_priced(treatments: list) -> int:
    n = 0
    for t in treatments:
        price = t.get("price") or {}
        if isinstance(price, dict) and any(
            v not in (None, "", [], {}) for v in price.values()
        ):
            n += 1
        pkg = t.get("package") or {}
        if isinstance(pkg, dict) and pkg.get("price") not in (None, "", [], {}):
            n += 1
    return n


def _has_hours(op: dict) -> bool:
    oh = (op or {}).get("operating_hours") or {}
    return any(v for v in oh.values()) if isinstance(oh, dict) else False


def extract_data(data: dict) -> dict:
    products = data.get("products") or {}
    equip = data.get("equipments") or {}
    treatments = data.get("treatments") or []
    op = data.get("operation_info") or {}
    lang = data.get("language_support") or {}
    cm = data.get("crawl_metadata") or {}
    cost = cm.get("cost") or {}
    completeness = cm.get("completeness") or {}

    pm = len(products.get("matched_products") or [])
    pu = len(products.get("unmatched_products") or [])
    em = len(equip.get("matched_equipments") or [])
    eu = len(equip.get("unmatched_equipments") or [])
    t_total = len(treatments)
    return {
        "identity_status": data.get("identity_status"),
        "products_matched": pm,
        "products_unmatched": pu,
        "products_total": pm + pu,
        "equip_matched": em,
        "equip_unmatched": eu,
        "equip_total": em + eu,
        "treatments_total": t_total,
        "treatments_priced": _count_priced(treatments),
        "doctors": len(data.get("doctors") or []),
        "has_phone": bool(op.get("phone")),
        "has_hours": _has_hours(op),
        "languages": len(lang.get("supported_languages") or []),
        "pages_crawled": cm.get("pages_crawled") or 0,
        "follow_up": len(cm.get("follow_up") or []),
        "completeness_true": sum(1 for v in completeness.values() if v),
        "cost_usd": cost.get("cost_usd"),
        "cost_duration_s": cost.get("duration_seconds"),
        "input_tokens": cost.get("input_tokens"),
        "output_tokens": cost.get("output_tokens"),
        # 핵심 가치 데이터(제품+장비+시술) — CLAUDE.md 1순위.
        "value_core": (pm + pu) + (em + eu) + t_total,
    }


# 러너(main.py)가 찍는 distinctive 라인에만 매칭한다. 에이전트 thinking/text에
# "시간 한도 내에…" 같은 표현이 섞여 들어와 false-positive 나는 걸 막으려 접미사로 앵커.
LOG_PATTERNS = {
    "soft_deadline_injected": re.compile(r"소프트 데드라인\(.*?\) — 마무리"),
    "budget_reached": re.compile(r"예산 한도\(.*?\) 도달 — 정상 종료"),
    "time_reached": re.compile(r"시간 한도\(.*?\) 도달 — 정상 종료"),
    "fatal_error": re.compile(r"오류로 종료:"),
    "empty_skeleton": re.compile(r"유효·빈 스켈레톤"),
    "triage_skip": re.compile(r"트리아지: SKIP"),
}
# 조직 월 지출 한도 도달 — 잡이 즉시 $0로 실패(크롤 안 됨). 통계에서 제외한다.
SPEND_CAP_RE = re.compile(r"monthly spend limit|spend limit")
RATE_LIMIT_RE = re.compile(r"429|overloaded|rate.?limit|too many requests", re.I)
SCREENSHOT_RE = re.compile(r"browser_take_screenshot")
BROWSER_TOOL_RE = re.compile(r"🔧 (?:mcp__playwright__)?browser_")


def mine_log(path: Path | None) -> dict:
    out: dict[str, object] = {k: False for k in LOG_PATTERNS}
    out["rate_limit_hits"] = 0
    out["screenshots"] = 0
    out["browser_calls"] = 0
    if not path or not path.exists():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    for k, pat in LOG_PATTERNS.items():
        out[k] = bool(pat.search(text))
    out["spend_capped"] = bool(SPEND_CAP_RE.search(text))
    out["rate_limit_hits"] = len(RATE_LIMIT_RE.findall(text))
    out["screenshots"] = len(SCREENSHOT_RE.findall(text))
    out["browser_calls"] = len(BROWSER_TOOL_RE.findall(text))
    return out


def collect() -> list[dict]:
    rows = []
    for combo in COMBO_ORDER:
        combo_dir = RUNS_DIR / combo
        meta_dir = combo_dir / "meta"
        if not meta_dir.exists():
            continue
        for meta_path in sorted(meta_dir.glob("*.json")):
            meta = _safe_load(meta_path)
            hid = meta.get("id") or meta_path.stem
            out_json = _find_output_json(combo_dir, hid)
            data = _safe_load(out_json) if out_json else {}
            log_path = combo_dir / "logs" / f"{hid}.log"
            row = {
                "combo": combo,
                "model": meta.get("model_short"),
                "effort": meta.get("effort"),
                "id": hid,
                "name": meta.get("name"),
                "exit_code": meta.get("exit_code"),
                "wall_seconds": meta.get("wall_seconds"),
                "hard_killed": meta.get("hard_killed"),
                **extract_data(data),
                **mine_log(log_path),
            }
            rows.append(row)
    return rows


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


def report(rows: list[dict]) -> str:
    # 지출한도로 즉시 실패한 잡은 실측이 아니다 — 통계에서 분리.
    capped = [r for r in rows if r.get("spend_capped")]
    valid = [r for r in rows if not r.get("spend_capped")]

    by_combo: dict[str, list[dict]] = defaultdict(list)
    for r in valid:
        by_combo[r["combo"]].append(r)

    # 병원별 best(각 지표의 조합 across max) — recall proxy 기준. valid만.
    by_hid: dict[str, list[dict]] = defaultdict(list)
    for r in valid:
        by_hid[r["id"]].append(r)
    best_core = {
        hid: max((r["value_core"] for r in rs), default=0)
        for hid, rs in by_hid.items()
    }
    # 모든 조합 valid인 병원(균형 비교 대상).
    full_hids = sorted(
        hid for hid, rs in by_hid.items() if len({r["combo"] for r in rs}) == 6
    )

    lines = []
    lines.append("# sample10 매트릭스 결과 (model × effort)\n")
    lines.append(
        f"총 {len(rows)}개 잡 = 실측 {len(valid)} + 지출한도제외 {len(capped)}개\n"
    )
    lines.append(
        f"**전조합(6/6) 완전 병원: {len(full_hids)}개** "
        f"({', '.join(full_hids)}) — 균형 비교 기준\n"
    )
    if capped:
        capped_h = sorted({r["id"] for r in capped})
        lines.append(
            f"⚠️ 지출한도 도달로 제외된 병원/셀: {len(capped)}셀 "
            f"({', '.join(capped_h)}) — $0·즉시실패, 통계 제외\n"
        )

    # ── 조합별 요약 ──
    lines.append("## 조합별 요약\n")
    hdr = (
        "| 조합 | N | USEFUL | EMPTY | 벽시계s(중앙/최대) | cost$(평균/합) | "
        "cost누락 | 제품 | 장비 | 시술 | 가격有 | 의사 | core(평균) | "
        "recall% | softDL | budget캡 | time캡 | RL히트 | 스샷 |"
    )
    sep = "|" + "---|" * 19
    lines.append(hdr)
    lines.append(sep)
    for combo in COMBO_ORDER:
        rs = by_combo.get(combo)
        if not rs:
            continue
        n = len(rs)
        useful = sum(1 for r in rs if r["exit_code"] == 0)
        empty = sum(1 for r in rs if r["exit_code"] == 1)
        walls = [r["wall_seconds"] for r in rs if r["wall_seconds"] is not None]
        costs = [r["cost_usd"] for r in rs if r["cost_usd"] is not None]
        cost_missing = sum(1 for r in rs if r["cost_usd"] is None)
        recalls = [
            (r["value_core"] / best_core[r["id"]]) if best_core[r["id"]] else 1.0
            for r in rs
        ]
        recall_avg = _avg(recalls) or 0.0
        row = [
            combo,
            n,
            useful,
            empty,
            f"{statistics.median(walls):.0f}/{max(walls):.0f}" if walls else "—",
            f"{_fmt(_avg(costs),2)}/{_fmt(sum(costs),2)}" if costs else "—",
            cost_missing,
            _fmt(_avg([r["products_total"] for r in rs])),
            _fmt(_avg([r["equip_total"] for r in rs])),
            _fmt(_avg([r["treatments_total"] for r in rs])),
            _fmt(_avg([r["treatments_priced"] for r in rs])),
            _fmt(_avg([r["doctors"] for r in rs])),
            _fmt(_avg([r["value_core"] for r in rs])),
            f"{recall_avg * 100:.0f}%" if recalls else "—",
            sum(1 for r in rs if r["soft_deadline_injected"]),
            sum(1 for r in rs if r["budget_reached"]),
            sum(1 for r in rs if r["time_reached"]),
            sum(r["rate_limit_hits"] for r in rs),
            _fmt(_avg([r["screenshots"] for r in rs])),
        ]
        lines.append("| " + " | ".join(str(x) for x in row) + " |")

    # ── 병원 × 조합: core 값 매트릭스 ──
    lines.append("\n## 병원 × 조합 — value_core (제품+장비+시술)\n")
    hids = sorted(by_hid)
    h = "| 병원 | " + " | ".join(COMBO_ORDER) + " | best |"
    lines.append(h)
    lines.append("|" + "---|" * (len(COMBO_ORDER) + 2))
    for hid in hids:
        name = by_hid[hid][0]["name"]
        cells = []
        cmap = {r["combo"]: r for r in by_hid[hid]}
        for combo in COMBO_ORDER:
            r = cmap.get(combo)
            if not r:
                cells.append("—")
            else:
                mark = "" if r["exit_code"] == 0 else "⚠"
                cells.append(f"{r['value_core']}{mark}")
        cells.append(str(best_core[hid]))
        lines.append(f"| {name[:14]}({hid}) | " + " | ".join(cells) + " |")

    lines.append("\n⚠ = exit≠0 (EMPTY 또는 에러)\n")
    return "\n".join(lines)


def main() -> None:
    rows = collect()
    with RESULTS_JSONL.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(report(rows))
    print(f"\n(상세 행: {RESULTS_JSONL.relative_to(PROJECT_ROOT)})")


if __name__ == "__main__":
    main()
