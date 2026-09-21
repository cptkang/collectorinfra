# 벤치 스위프 20260914-185540 — 산출물 묶음

## 산출 경로가 문서와 다른 이유

`plans/93` 문서는 산출을 `results/bench/<run_id>/` 로 적고 있으나, 실제 구현은
`results/scenario/<run_id>/` 에 씁니다. `--sweep` 은 자기 결과 폴더를 만들지 않고
`scripts/scenario/`(plans/94) 러너에 실행을 위임하며, 그 러너가 경로를 정합니다.

- scripts/scenario/runner.py:40   RESULTS_ROOT = results/scenario
- scripts/scenario/runner.py:779  out_dir = RESULTS_ROOT / run_id
- scripts/bench/__main__.py:364   벤치가 그 폴더에 axis_verdicts.md 만 추가
- plans/94 §582 "산출물 경로를 results/scenario/ 로 분리해 혼동을 없앤다"

`SUMMARY.md` 는 스위프가 만들지 않습니다. 설정 검증 트랙(`--sweep` 없는 실행)
전용이며 scripts/bench/report.py:312 에서만 생성됩니다. `results/bench/` 에 있는
SUMMARY.md 는 같은 날 17:52 의 별도 검증 실행 산출물입니다.

## 파일

| 파일 | 내용 |
|---|---|
| raw.jsonl | 6567턴 원시 관측 (정본) |
| run.json | 커밋·dirty·프로바이더·62 프로파일 상태·skip 사유 |
| axis_verdicts.md | 축 판정표 62줄 — **무효, 아래 참조** |
| sweep_console.log | 실행 콘솔 전문 (건전성 블록·판정표) |
| server_logs.tar.gz | arm 62개 서버 로그 |
| artifacts.tar.gz | 시나리오 산출 문서 |
| bench_validate/ | 별도 설정 검증 트랙 산출 |

체크포인트 DB(checkpoints-*.db, 약 14GB)는 재분석에 불필요해 제외했습니다.

## 실행 제원

mode=run · env=closed · provider=fabrix · host=nclago01
commit=82e411e (dirty=True) · started 2026-09-14 18:55:40 KST · arm 62개 완주

## 축 판정표를 쓸 수 없는 이유

판정: pass 617 · fail 4268 · manual 1630 · error 52 (총 6567턴, pass 12.5%)

전 arm 의 pass 율이 동일합니다. 설정 축의 효과가 없어서가 아니라, 역질문(clarify)
단계에서 균일하게 막혀 SQL 생성에 도달하지 못했기 때문입니다. 역질문 자동응답
하네스 scripts/scenario/clarify.py 는 커밋 9bc94f0(2026-09-15 12:03)에 추가됐는데
이 런은 2026-09-14 18:55 에 시작해 그 코드가 없었습니다.

건전성 게이트는 오류율 50% 기준만 보므로(scripts/bench/sweep.py:390) error 0.8%
인 이 런을 통과시켰고, 그 결과 사고가 정상 형태의 판정표로 산출됐습니다.

## 그래도 유효한 것

- 실 결함 목록: fail 4268건의 failed_assertions (폐쇄망·fabrix 실측)
- 배관 실증: 62/62 프로파일 유효, 93시간 연속 구동, error 0.8%
- 역질문이 없는 A군은 clarify 결함의 영향을 받지 않아 축 신호가 남아 있을 수 있음
