"""plans/152 W2 — APM 벤치 안전 가드(§3.7) 단위 테스트.

예산 초과 · 429 · 타임아웃 비율 · 지연 3배 · 뷰 서버 버전 미달이 각각 중단 사유가 되는지와,
무거운 등급이 맨 뒤·최대 5개로 잡히는지 본다.
"""

from __future__ import annotations

from scripts.apm_bench import guard as gd
from scripts.apm_bench.scenarios import Expect, Scenario, Turn

TIMEOUT_SEC = 10.0


def _entry(calls: int = 1, sources: dict[str, int] | None = None, error: str | None = None,
           elapsed_ms: float = 5.0) -> dict:
    return {"tool": "apm_app_health", "api_calls": calls,
            "sources": sources if sources is not None else {"common": calls},
            "error": error, "elapsed_ms": elapsed_ms}


def _scenario(sid: str, *, heavy: bool = False, was_touch: bool = False,
              e2_skip: str | None = None, covers: tuple[str, ...] = ("COV-X",)) -> Scenario:
    expect = Expect(agents=("apm_query",), views=("apm.instances",), target="none", window="none")
    return Scenario(id=sid, covers=covers, split="dev", turns=(Turn("q", expect),), heavy=heavy,
                    was_touch=was_touch, e2_skip=e2_skip)


# --- ① 버전 ------------------------------------------------------------------------------


def test_version_e2_required_and_minimum() -> None:
    assert gd.check_view_server_version("e2", None)[0] is False
    assert gd.check_view_server_version("e2", "abc")[0] is False
    assert gd.check_view_server_version("e2", "5.6.2.17")[0] is False
    assert gd.check_view_server_version("e2", "5.6.2")[0] is False  # 5.6.2.0 < 5.6.2.18
    assert gd.check_view_server_version("e2", "5.6.2.18") == (True, "5.6.2.18")
    assert gd.check_view_server_version("e2", "5.6.4")[0] is True


def test_version_e1_not_applicable() -> None:
    assert gd.check_view_server_version("e1", None) == (True, gd.E1_VERSION_LABEL)


# --- ② 예산 · ③ 자동 중단 ------------------------------------------------------------------


def test_budget_per_source_abort() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC, budget=10)
    assert guard.observe([_entry(5, {"bank": 5, "common": 5})]) is None
    assert guard.observe([_entry(6, {"bank": 6})]) == "budget:bank"
    assert guard.calls_by_source == {"bank": 11, "common": 5}
    assert not guard.healthy


def test_default_budget_is_3000() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe([_entry(3000, {"common": 3000})]) is None
    assert guard.observe([_entry(1, {"common": 1})]) == "budget:common"


def test_quota_429_aborts_once() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe([_entry(error=gd.QUOTA_ERROR)]) == "quota_429"
    assert guard.quota_hits == 1


def test_timeout_ratio_with_denominator_floor() -> None:
    slow = TIMEOUT_SEC * 1000
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    # 첫 호출 1건 타임아웃 — 분모 하한 20 → 5% (초과 아님)
    assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) is None
    # 2건째 → 2/20 = 10% > 5%
    assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) == "timeout_ratio"


def test_timeout_ratio_large_denominator() -> None:
    """비율 단위는 도구 호출(감사 줄) 수 — 분자·분모 모두."""
    slow = TIMEOUT_SEC * 1000
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe([_entry(1) for _ in range(100)]) is None
    for _ in range(5):  # 5/105 < 5%
        assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) is None
    assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) == "timeout_ratio"
    assert guard.timeouts == 6 and guard.tool_calls == 106  # 6/106 > 5%


def test_timeout_ratio_counts_tool_calls_not_api_calls() -> None:
    """제니퍼 호출이 많은 도구 1건이 분모를 부풀리지 않는다."""
    slow = TIMEOUT_SEC * 1000
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe([_entry(100)]) is None
    assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) is None  # 1/20
    assert guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=slow)]) == "timeout_ratio"
    assert guard.tool_calls == 3 and guard.total_calls == 102


def _http(entry: dict, responses: int, **statuses: int) -> dict:
    return {**entry, "http": {"responses": responses,
                              "statuses": {k.lstrip("s"): v for k, v in statuses.items()}}}


def test_http_429_line_aborts_without_audit_error() -> None:
    """팬아웃 도구의 부분 429 — 감사 줄 error 없이 응답 줄 status=429 만 있어도 중단."""
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe([_http(_entry(3), 3, s200=2, s429=1)]) == "quota_429"
    assert guard.quota_hits == 1


def test_http_partial_timeout_counted_and_fast_miss_not() -> None:
    """응답 줄 없는 호출 + 상한 근처 경과 = 타임아웃 · 빠른 누락(연결 실패)은 세지 않는다."""
    slow = TIMEOUT_SEC * 1000
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC, http_lines=True)
    guard.observe([_http(_entry(3, elapsed_ms=slow), 2, s200=2)])
    assert guard.timeouts == 1 and not guard.healthy
    guard.observe([_http(_entry(3, elapsed_ms=40.0), 2, s200=2)])
    assert guard.timeouts == 1 and guard.unanswered_fast == 1
    # http 모드가 아니면 응답 줄 누락으로 판정하지 않는다
    plain = gd.Guard(timeout_sec=TIMEOUT_SEC)
    plain.observe([_http(_entry(3, elapsed_ms=slow), 2, s200=2)])
    assert plain.timeouts == 0


def test_http_lines_carry_and_job_dedup() -> None:
    """동시 도구의 응답 줄이 앞 항목에 접혀도 뒤 항목에서 상쇄 · 승격 작업 수명 끝 줄은 증분만."""
    slow = TIMEOUT_SEC * 1000
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC, http_lines=True)
    synthetic = {"tool": None, "api_calls": 0, "sources": {}, "error": None, "elapsed_ms": 0.0}
    guard.observe([_http(synthetic, 2, s200=2)])  # 감사 줄 앞에 읽힌 응답 줄
    guard.observe([_entry(1, elapsed_ms=slow), _http(_entry(1, elapsed_ms=slow), 0)])
    assert guard.timeouts == 0  # 이월분 2 가 두 호출을 덮는다
    job = {**_entry(2, elapsed_ms=slow), "job_id": "j1"}
    guard.observe([_http(job, 2, s200=2)])
    guard.observe([_http({**job, "api_calls": 5}, 3, s200=3)])  # 수명 끝 줄(누적 5)
    assert guard.timeouts == 0 and guard.tool_calls == 3


def test_latency_floor_e2_is_plain_3x() -> None:
    """E2 는 절대 하한 없이 3배만 본다(§3.7)."""
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC, latency_floor_ms=0.0)
    guard.observe_latency(2.0)
    assert guard.observe_latency(7.0) == "latency_3x"
    assert guard.meta()["limits"]["latency_floor_ms"] == 0.0


def test_fast_source_unavailable_is_not_timeout() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    for _ in range(3):
        guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=30.0)])
    assert guard.timeouts == 0 and guard.aborted is None


def test_latency_3x_abort() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.observe_latency(200.0) is None  # 기준선
    assert guard.observe_latency(590.0) is None  # 2.95배
    assert guard.observe_latency(650.0) == "latency_3x"


def test_latency_floor_ignores_jitter() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    guard.observe_latency(2.0)
    assert guard.observe_latency(50.0) is None  # 25배지만 절대 증가 < 하한
    assert guard.aborted is None


def test_first_reason_kept() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC, budget=1)
    guard.observe([_entry(error=gd.QUOTA_ERROR)])
    guard.observe([_entry(5, {"bank": 5})])
    assert guard.aborted == "quota_429"
    assert guard.meta()["aborted"] == "quota_429"


# --- ④ 일정 ------------------------------------------------------------------------------


def test_schedule_heavy_last_and_capped() -> None:
    scenarios = [_scenario(f"H{i}", heavy=True) for i in range(7)] + [_scenario("L1"),
                                                                      _scenario("L2")]
    plan = gd.schedule(scenarios, env="e1")
    assert [s.id for s in plan.light] == ["L1", "L2"]
    assert [s.id for s in plan.heavy] == ["H0", "H1", "H2", "H3", "H4"]
    assert plan.skipped == {"H5": "heavy_cap:5", "H6": "heavy_cap:5"}


def test_schedule_e2_skip_and_was_once() -> None:
    scenarios = [
        _scenario("S1", e2_skip="외부망 전용"),
        _scenario("W1", was_touch=True, covers=("COV-ENV",)),
        _scenario("W2", was_touch=True, covers=("COV-ENV.key",)),
        _scenario("W3", was_touch=True, covers=("COV-LOADED-CLASS",)),
    ]
    e2 = gd.schedule(scenarios, env="e2")
    assert [s.id for s in e2.light] == ["W1", "W3"]
    assert e2.skipped == {"S1": "e2_skip", "W2": "was_once:COV-ENV"}
    # 덮는 경로가 여럿이면 전부 키 — 첫 경로만 보면 두 번째 경로를 다시 접촉한다
    multi = [_scenario("M1", was_touch=True, covers=("COV-A", "COV-Z")),
             _scenario("M2", was_touch=True, covers=("COV-Z",))]
    assert gd.schedule(multi, env="e2").skipped == {"M2": "was_once:COV-Z"}
    e1 = gd.schedule(scenarios, env="e1")
    assert [s.id for s in e1.light] == ["S1", "W1", "W2", "W3"] and not e1.skipped


def test_healthy_gate() -> None:
    guard = gd.Guard(timeout_sec=TIMEOUT_SEC)
    assert guard.healthy
    guard.observe([_entry(error=gd.TIMEOUT_ERROR, elapsed_ms=TIMEOUT_SEC * 1000)])
    assert guard.aborted is None and not guard.healthy  # 타임아웃 1건 → 무거운 등급 보류
