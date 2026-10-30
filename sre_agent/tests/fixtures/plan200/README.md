# plans/200 합성 녹화 픽스처 (W0 · 2026-10-30)

플레이북 실행 시간축 하네스(`scripts/investigation_bench/playbook_timeline.py`)의 host 계열 입력이다.
형식·녹화 조회 규칙은 `../plan187/README.md`와 같다.

| 파일 | 시나리오 | 플레이북 |
|---|---|---|
| `host_cpu.json` | 호스트 CPU 95% — WAS A1 프로세스 CPU 93%(CPU 주인) · 배포 50분 뒤 | host.cpu |
| `host_process.json` | 같은 호스트 프로세스 다운 — 서버 생존 · java 서브리소스 다운 · 최근 20일 재발 2건 | host.process |

- **plan187 폴더에 두지 않은 이유**: `tests/test_plan187_w0_fixtures.py`가 plan187 폴더의 픽스처 이름 집합을
  고정 단언한다. `playbook_fixture_eval`(W7 적중표)도 plan187 폴더만 읽는다 — 적중표 사례 수를 바꾸지 않는다.
- 응답은 `plan187/case3_cpu_loop.json`의 실 응답 모양을 재사용했다. 바꾼 것: 알람(폴스타 서버 CPU·프로세스
  알람) · A1 `apm_app_health`의 `was_signals`(S0r degraded → S1·S5·S7 제니퍼 축이 돈다) · A2 런타임·풀
  (A1 사본 · 프로세스 CPU 8%) · `apm_active_services` args(`n` 무관) · `apm_active_detail` 3건 ·
  `polestar_resource_status`·`polestar_alarm_history`(host_process).
- 녹화는 실제로 조회된 것만 남겼다 — 두 건 모두 미녹화 0 · 전 스텝 `ok`
  (`sre_agent/tests/test_plan200_w0_instrumentation.py`).
- 생성기는 일회성이라 저장소에 두지 않았다(plan187과 같다).
