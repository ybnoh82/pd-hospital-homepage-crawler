# sample10 매트릭스 실험 — 발견·개선점 (작업 노트)

> 실험: sample10 10병원 × {sonnet, opus} × {low, medium, high} = 60런. 캡 $3/900s, 동시성 8.
> 이 문서는 진행 중 누적되는 작업 노트. 최종 보고는 별도.

## 발견 1 (중대) — 저비용 effort 조기 종료로 저장 실패 → EMPTY

**증상.** sonnet/low로 smado8(리프톤피부과) 크롤 시 83초 만에 EMPTY(exit 1), $0.51 소모.
에이전트는 **크롤을 잘 했다**: SPA 지점선택 페이지 인식 → 신사지점 클릭 → 정찰로
병원정보·진료시간·시술/제품명 다수 텍스트 확보. 그런데 결과 파일은 빈 스켈레톤.

**근인(트랜스크립트 확인).** 에이전트의 *마지막 도구 호출*이 다음이었다:
```
Bash: mkdir -p .../experiments/runs/sonnet_low
```
"지금 체크포인트 JSON을 먼저 저장한다"고 말한 뒤 **디렉토리만 만들고(준비 단계)
파일 쓰기 없이 턴을 끝냈다.** main.py 수신 루프는 *첫 ResultMessage*에서 `break` →
저장된 게 없어 repair가 빈 스켈레톤 생성 → EMPTY.

**두 겹의 원인:**
1. (모델) effort=low Sonnet은 준비 단계(mkdir) 후 "할 일을 했다"고 턴을 종료하는
   조기 종료 경향. 멀티스텝 자율 지시를 끝까지 안 끌고 감.
2. (러너/코드) `main.py`는 첫 ResultMessage를 무조건 완료로 간주하고 끊는다. 유효 출력이
   아직 없어도. 유일한 steering(`FINALIZE_MESSAGE`)은 소프트 데드라인(702s)에야 발사 —
   83초에 끝난 에이전트는 절대 못 잡는다.

**왜 중요한가.** 이건 "데이터 손실"이 아니라 "저장 누락"이다. 크롤은 성공했는데 러너가
일찍 손을 놔서 거둔 걸 못 적었다. 메모리의 미해결 꼬리("조기종료/지연저장")의 정확한 정체.

## 제안 수정 (매트릭스 완료 후 적용·검증)

**A. 러너 측(주·결정론적) — 조기 종료 시 재촉(nudge).**
`main.py` 수신 루프에서 ResultMessage 수신 시, 유효 출력이 없고 예산/시간이 남았으면
끊지 말고 "아직 저장 안 됐다 — 지금 파일을 써라"를 주입(`client.query`)하고 계속 수신.
N회(예: 3) 상한. CLAUDE.md 철학("유효성을 에이전트 신뢰도에서 분리")과 정합.

의사코드:
```python
nudges = 0
MAX_NUDGES = 3
...
if isinstance(msg, ResultMessage):
    last_result = msg
    if (out_path is not None and not has_useful_output(out_path)
            and not budget_reached and not time_reached and nudges < MAX_NUDGES):
        nudges += 1
        log(f"⚠️ 저장 전 조기 종료 감지 — 재촉 주입 ({nudges}/{MAX_NUDGES})")
        await client.query(SAVE_NOW_MESSAGE)
        continue  # break 하지 않고 계속 수신
    break
```
`has_useful_output`: 파일 존재 + (treatments/products/equipments 중 하나라도 비어있지
않거나 operation_info 존재). repair 전 원본 기준으로 판단.

**B. 스킬/프롬프트 측(보조).** build_prompt에 "출력 디렉토리는 이미 존재한다 — mkdir 금지,
곧장 파일을 써라" + "첫 저장은 디렉토리 준비가 아니라 **실제 파일 쓰기까지** 한 응답에
끝내라"를 명시. (모델 의존이라 보조. 주력은 A.)

**검증 계획.** 적용 후 sonnet/low·sonnet/medium 등 조기종료 다발 조합을 동일 캡으로
재크롤해 EMPTY→USEFUL 전환율 측정. 정상 베이스라인(opus/high 등) 무변동 확인.
