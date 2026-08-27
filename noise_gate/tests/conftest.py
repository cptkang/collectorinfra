"""noise_gate 테스트 공통 설정 — MVP 실행 기록기 플러그인 등록 (Plan 66 · docs/23).

`mvp_record` 픽스처와 `@pytest.mark.mvp` 마커를 제공한다. MVP 테스트가 어떤 방식으로
실행되든(pytest 직접·IDE·CI) 실행 기록이 `docs/24_plan66_mvp_test_log.md`에 남는다.
"""

pytest_plugins = ["noise_gate.tests.mvp_record"]
