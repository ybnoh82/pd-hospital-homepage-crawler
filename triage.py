"""prefetch 트리아지 — 크롤 전 curl로 싸게 분류해 비타깃·죽은 사이트를 거른다.

목표: 미용·성형 외 진료과(안과·내과·가정의학 등)나 죽은/주차 도메인에 비싼 에이전트
크롤을 쓰지 않는다. LLM·브라우저 없이 (1) 주어진 병원명의 진료과 (2) curl로 받은 raw
HTML 키워드만으로 판정한다 — 사실상 무료(~수초·$0).

**스킵은 보수적으로** — 잘못 스킵하면 진짜 타깃을 잃는다(이 프로젝트 최악의 에러).
그래서 SKIP은 다음일 때만:
  ① 도메인이 죽음(접속 불가·HTTP 4xx/5xx — 객관적)
  ② 주차/광고/준비중 페이지인데 본문에 미용 신호가 전혀 없음
  ③ 병원명이 명백한 비타깃 진료과(안과 등)인데 본문에도 미용 신호가 0
SPA 쉘(raw HTML로는 내용 판단 불가)·미용 신호가 조금이라도 있는 경우·애매한 경우는
모두 CRAWL로 넘긴다. (미인피부과처럼 SEO 스팸이 섞인 진짜 사이트를 스킵하지 않도록,
미용 신호가 있으면 주차/스팸 판정으로 스킵하지 않는다 — 실측이 가르쳐준 함정.)

직접 실행하면 분류 결과(JSON)를 출력한다(테스트용):
    uv run python triage.py <URL> --name 병원이름
"""

from __future__ import annotations

import argparse
import re
import subprocess
import unicodedata

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 비타깃 진료과(병원명 신호). 이름에 있고 본문에 미용 신호가 없으면 스킵 후보.
NONTARGET_TYPES = [
    "안과", "이비인후과", "가정의학", "내과", "산부인과", "비뇨기", "비뇨의학",
    "정형외과", "소아", "치과", "한의원", "한방", "신경과", "정신건강", "정신의학",
    "재활의학", "흉부외과", "영상의학", "진단검사", "마취통증",
]
# 강한 미용 신호(본문·이름). 하나라도 있으면 타깃으로 보고 스킵하지 않는다.
# **모호한 단어는 일부러 뺐다** — "레이저"(라식), "성형"(비중격성형술), "비급여"(모든
# 의원), "피부"(일반)는 비타깃 의료 사이트에도 흔해 미용 신호로 못 쓴다. 비타깃 진료과
# 이름을 덮으려면 명백한 미용 신호여야 한다(내과가 보톡스를 하면 그건 본문에 강하게 뜬다).
STRONG_BEAUTY_KW = [
    "피부과", "성형외과", "미용", "쁘띠", "필러", "톡신", "보톡스", "보툴리눔",
    "리프팅", "스킨부스터", "울쎄라", "슈링크", "써마지", "인모드", "쥬베룩", "리쥬란",
    "스컬트라", "윤곽주사", "제모", "여드름", "모공", "색소침착", "안티에이징",
    "에스테틱", "더모톡신", "쁘띠성형", "보톡스", "물광주사", "백옥주사",
    "filler", "botox", "skinbooster", "dermatolog", "aesthetic", "botulinum",
]
# 주차/준비중/광고 도메인 신호.
PARKED_KW = [
    "this domain", "domain is for sale", "도메인이 만료", "도메인 판매",
    "sedoparking", "parkingcrew", "bodis.com", "준비중입니다", "서비스 준비 중",
    "사이트를 찾을 수 없", "페이지를 찾을 수 없", "site not found",
]
# SPA 프레임워크 흔적(내용이 JS로 렌더되어 raw HTML엔 안 보임).
SPA_MARKERS = [
    "__next_data__", "data-reactroot", 'id="root"', 'id="app"', "ng-app",
    "window.__nuxt__", "/_next/", "data-server-rendered",
]


def fetch(url: str, timeout: int = 15) -> tuple[int | None, str, str]:
    """curl로 URL을 받아 (http_code, 최종 URL, 본문 HTML)을 반환한다.

    접속 자체가 안 되면(DNS·연결·TLS·타임아웃) http_code=None.
    """
    marker = "\n__CURLMETA__"
    try:
        p = subprocess.run(
            ["curl", "-sL", "-A", UA, "--max-time", str(timeout),
             "-w", f"{marker}%{{http_code}}\t%{{url_effective}}", url],
            capture_output=True, text=True, errors="replace", timeout=timeout + 5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, url, ""
    # curl 비정상 종료(DNS·연결거부·TLS·타임아웃)는 dead. 이때 -w가 http_code=000을
    # 찍어 marker는 남으므로 returncode로 잡는다. 404 등은 returncode=0이라 통과한다.
    if p.returncode != 0:
        return None, url, ""
    out = p.stdout
    if marker not in out:
        return None, url, ""  # 접속 실패
    body, _, meta = out.rpartition(marker)
    code: int | None = None
    final = url
    parts = meta.split("\t")
    try:
        code = int(parts[0])
    except (ValueError, IndexError):
        code = None
    if len(parts) > 1 and parts[1]:
        final = parts[1]
    return code, final, body


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").casefold()


def _hits(text: str, kws: list[str]) -> list[str]:
    t = _norm(text)
    return [k for k in kws if _norm(k) in t]


def scan_content(html: str) -> dict:
    """raw HTML에서 본문 텍스트 길이·미용/주차 키워드·SPA 여부·이미지 수를 뽑는다."""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    low = _norm(html)
    return {
        "text_len": len(text),
        "beauty": _hits(html, STRONG_BEAUTY_KW),
        "parked": _hits(html, PARKED_KW),
        "is_spa": len(text) < 800 and any(m in low for m in SPA_MARKERS),
        "img_count": len(re.findall(r"<img\b", html, flags=re.I)),
    }


def triage(url: str, name: str = "") -> dict:
    """크롤 전 분류. {decision: SKIP|CRAWL, reason, signals}를 반환한다."""
    code, final, html = fetch(url)
    if code is None:
        return {"decision": "SKIP", "reason": "dead: 접속 불가(DNS·연결·타임아웃)",
                "signals": {"http": None}}
    if code >= 400:
        return {"decision": "SKIP", "reason": f"dead: HTTP {code}",
                "signals": {"http": code, "final_url": final}}

    c = scan_content(html)
    name_nontarget = _hits(name, NONTARGET_TYPES)
    # 강한 미용 신호: 본문 또는 병원명 어느 쪽에든 있으면 타깃으로 보고 스킵하지 않는다.
    has_beauty = bool(c["beauty"]) or bool(_hits(name, STRONG_BEAUTY_KW))
    sig = {
        "http": code, "final_url": final, "text_len": c["text_len"],
        "beauty_hits": c["beauty"][:6], "is_spa": c["is_spa"],
        "img": c["img_count"], "name_nontarget": name_nontarget,
    }

    # ② 주차/준비중: 강한 미용 신호 0일 때만(스팸 섞인 진짜 사이트 보호 — 미인피부과).
    if c["parked"] and not has_beauty:
        return {"decision": "SKIP",
                "reason": f"주차/준비중/광고 도메인 ({', '.join(c['parked'][:3])})",
                "signals": sig}

    # ③ 비타깃 진료과: 이름이 명백한 비타깃 과목(안과·이비인후과 등)인데 강한 미용 신호 0.
    # 본문 길이·SPA 가드를 두지 않는다 — 이름이 "○○안과의원"이면 SPA든 빈 HTML이든 안과다.
    # (내과·가정의학이 미용을 하면 본문에 강한 미용 신호가 떠 has_beauty로 걸러진다.)
    if name_nontarget and not has_beauty:
        return {"decision": "SKIP",
                "reason": f"비타깃 진료과({'/'.join(name_nontarget)}) — 강한 미용 신호 0",
                "signals": sig}

    # 그 외엔 CRAWL. 사이트 형태 힌트를 프롬프트에 주입할 수 있게 남긴다.
    if c["is_spa"]:
        hint = "spa"
    elif c["img_count"] > 30 and c["text_len"] < 2000:
        hint = "image"
    else:
        hint = "text"
    sig["hint"] = hint
    return {"decision": "CRAWL", "reason": f"타깃 또는 판단보류(shape={hint})", "signals": sig}


def main() -> int:
    import json

    ap = argparse.ArgumentParser(description="prefetch 트리아지(curl 기반)")
    ap.add_argument("url")
    ap.add_argument("--name", default="", help="병원이름(진료과 신호)")
    a = ap.parse_args()
    print(json.dumps(triage(a.url, a.name), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
