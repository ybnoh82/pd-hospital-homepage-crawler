"""sample10 × (model × effort) 매트릭스 크롤 드라이버 (실험용).

main.py(러너)를 (병원 × 조합)마다 서브프로세스로 돌린다. 조합마다 별도
--output-dir로 격리하고, 각 잡의 벽시계 시간·종료코드를 meta/<id>.json에 영구
기록한다(시간캡 cost=None 갭을 드라이버가 벽시계로 메운다). 동시성은 메모리
바운드라 기본 8(24GB·~1GB/런). 분석은 experiments/analyze.py가 따로 한다.

    uv run python experiments/run_matrix.py                 # 전체 60런
    uv run python experiments/run_matrix.py --concurrency 6
    uv run python experiments/run_matrix.py --skip-done     # meta 있으면 건너뜀(재개)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"
CSV_PATH = PROJECT_ROOT / "data" / "sample10.csv"

MODELS = {"sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-8"}
EFFORTS = ["low", "medium", "high"]
BUDGET = "3"
TIME_LIMIT = "900"
# 서브프로세스 자체 하드킬(초): main.py가 900s asyncio.timeout으로 끊지만,
# 브라우저 정리·SDK 종료가 늘어질 수 있어 여유를 둔 백스톱.
PROC_HARD_KILL_S = 1100


def load_hospitals() -> list[dict]:
    with CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def combo_key(model_short: str, effort: str) -> str:
    return f"{model_short}_{effort}"


SPEND_CAP_MARK = "monthly spend limit"


def is_capped_cell(combo: str, hid: str) -> bool:
    """이 (조합, 병원) 셀이 지출한도로 즉시 실패했는지 — 로그에 한도 마커가 있으면 True."""
    log_path = RUNS_DIR / combo / "logs" / f"{hid}.log"
    if not log_path.exists():
        return False
    try:
        return SPEND_CAP_MARK in log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def build_jobs(skip_done: bool, rerun_capped: bool = False) -> list[dict]:
    """병원-outer, 조합-inner 순서 — 풀이 한 조합으로 쏠리지 않게 섞는다.

    rerun_capped=True면 지출한도로 실패했던 셀만 골라 다시 돌린다(한도 상향 후 재개용).
    """
    hospitals = load_hospitals()
    jobs = []
    for h in hospitals:
        for model_short, model_id in MODELS.items():
            for effort in EFFORTS:
                key = combo_key(model_short, effort)
                meta_path = RUNS_DIR / key / "meta" / f"{h['id']}.json"
                if rerun_capped:
                    # 지출한도로 실패한 셀만 — 나머지(실측 완료)는 건드리지 않는다.
                    if not is_capped_cell(key, h["id"]):
                        continue
                elif skip_done and meta_path.exists():
                    continue
                jobs.append(
                    {
                        "combo": key,
                        "model_short": model_short,
                        "model_id": model_id,
                        "effort": effort,
                        "id": h["id"],
                        "name": h["hospital_name"],
                        "url": h["homepage_url"],
                        "meta_path": meta_path,
                    }
                )
    return jobs


async def run_job(job: dict, sem: asyncio.Semaphore, idx: int, total: int) -> None:
    async with sem:
        combo_dir = RUNS_DIR / job["combo"]
        logs_dir = combo_dir / "logs"
        meta_dir = combo_dir / "meta"
        logs_dir.mkdir(parents=True, exist_ok=True)
        meta_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{job['id']}.log"

        cmd = [
            "uv",
            "run",
            "python",
            "main.py",
            job["url"],
            "--from-csv",
            str(CSV_PATH),
            "--id",
            job["id"],
            "--model",
            job["model_id"],
            "--effort",
            job["effort"],
            "--budget",
            BUDGET,
            "--time-limit",
            TIME_LIMIT,
            "--output-dir",
            str(combo_dir.relative_to(PROJECT_ROOT)),
        ]

        print(
            f"[{idx}/{total}] ▶ START {job['combo']} {job['id']} ({job['name']})",
            flush=True,
        )
        start = time.time()
        exit_code: int | None = None
        killed = False
        with log_path.open("w", encoding="utf-8") as lf:
            lf.write(f"# CMD: {' '.join(cmd)}\n\n")
            lf.flush()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(PROJECT_ROOT),
                stdout=lf,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                exit_code = await asyncio.wait_for(
                    proc.wait(), timeout=PROC_HARD_KILL_S
                )
            except asyncio.TimeoutError:
                killed = True
                proc.kill()
                await proc.wait()
                exit_code = -9
        wall = time.time() - start

        meta = {
            "combo": job["combo"],
            "model_short": job["model_short"],
            "model_id": job["model_id"],
            "effort": job["effort"],
            "id": job["id"],
            "name": job["name"],
            "url": job["url"],
            "exit_code": exit_code,
            "wall_seconds": round(wall, 1),
            "hard_killed": killed,
            "start_ts": round(start, 1),
        }
        job["meta_path"].write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verdict = {0: "USEFUL", 1: "EMPTY", 2: "TRIAGE-SKIP"}.get(
            exit_code, f"exit={exit_code}"
        )
        kmark = " [HARD-KILLED]" if killed else ""
        print(
            f"[{idx}/{total}] ✔ DONE  {job['combo']} {job['id']} — "
            f"{verdict} · {wall:.0f}s{kmark}",
            flush=True,
        )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument(
        "--rerun-capped",
        action="store_true",
        help="지출한도로 실패한 셀만 다시 돌린다(한도 상향 후 재개용)",
    )
    ap.add_argument("--stagger", type=float, default=2.0, help="잡 시작 간 지연(초)")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N개만(스모크 테스트용)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 크롤 없이 선택된 잡 목록만 출력한다(비용 0)",
    )
    args = ap.parse_args()

    jobs = build_jobs(args.skip_done, args.rerun_capped)
    if args.limit:
        jobs = jobs[: args.limit]

    if args.dry_run:
        print(f"[dry-run] 선택된 잡 {len(jobs)}개 (크롤 안 함):", flush=True)
        for j in jobs:
            print(f"  {j['combo']:<15} {j['id']}  ({j['name']})", flush=True)
        return
    total = len(jobs)
    print(
        f"매트릭스: {total}개 잡 · 동시성 {args.concurrency} · "
        f"캡 ${BUDGET}/{TIME_LIMIT}s · stagger {args.stagger}s",
        flush=True,
    )
    sem = asyncio.Semaphore(args.concurrency)
    batch_start = time.time()

    async def launch(job, idx):
        # 동시 콜드스타트(npx/uv 해석) 폭주를 막으려 살짝 지연 후 세마포어 진입.
        await asyncio.sleep(idx * args.stagger)
        await run_job(job, sem, idx + 1, total)

    await asyncio.gather(*(launch(job, i) for i, job in enumerate(jobs)))
    print(
        f"\n전체 완료: {total}개 잡 · {(time.time() - batch_start) / 60:.1f}분",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
