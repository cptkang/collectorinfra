"""조치 권고(remediation_recommender) 계약 테스트 (Plan 02 §9 · Plan 66 4-A · D-011).

검증 범위:
  1. 결정적 도출: 시그니처 → 조치 후보(근거·신뢰도·위험도), 미등재 시그니처는 무권고.
  2. 근거 강제: 모든 권고에 시그니처 라벨·발췌가 붙는다(근거 없는 권고 금지).
  3. 고위험×저신뢰: "검토 필요"로만 표기(정식 권고 아님).
  4. 정렬·중복 제거: 위험도 낮은 순, 같은 조치는 1회.
  5. 옵트인: remediation_recommender_enabled off면 브리핑 권고 문구 불변(회귀 0).
  6. **실행 경로 부재**(D-003·D-011): 조치 문자열이 실행 API에 닿는 경로가 코드에 없다.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from sre_agent.domain.remediation import (
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    Remediation,
    recommend,
    recommend_lines,
)
from sre_agent.domain.severity_signatures import Signal


def _signal(name: str, category: str = "strong", label: str = "라벨",
            evidence: str = "발췌", source: str = "log") -> Signal:
    return Signal(name=name, category=category, source=source,
                  label=label, evidence=evidence)


# ── 1. 결정적 도출 ───────────────────────────────────────────────────────
class TestRecommend:
    def test_oom_kill_yields_candidates(self):
        out = recommend([_signal("oom_kill", label="메모리 고갈(OOM Killer)",
                                 evidence="Out of memory: Killed process 12345")])
        assert out
        assert all(isinstance(r, Remediation) for r in out)
        actions = [r.action for r in out]
        assert any("누수 점검" in a for a in actions)
        assert any("재기동" in a for a in actions)

    def test_unknown_signature_yields_nothing(self):
        # 카탈로그 미등재 시그니처에 대해 조치를 지어내지 않는다.
        assert recommend([_signal("unknown_sig")]) == []

    def test_empty_signals_yield_nothing(self):
        assert recommend([]) == []
        assert recommend(()) == []

    def test_every_recommendation_carries_rationale(self):
        out = recommend([_signal("fd_exhaustion", category="medium",
                                 label="파일 디스크립터 고갈",
                                 evidence="Too many open files")])
        assert out
        for r in out:
            assert "파일 디스크립터 고갈" in r.rationale
            assert "Too many open files" in r.rationale


# ── 2~3. 위험도·신뢰도 계약 ─────────────────────────────────────────────
class TestRiskAndConfidence:
    def test_strong_signature_gives_high_confidence(self):
        out = recommend([_signal("oom_kill", category="strong")])
        assert all(r.confidence == "high" for r in out)

    def test_medium_signature_gives_medium_confidence(self):
        out = recommend([_signal("fd_exhaustion", category="medium")])
        assert all(r.confidence == "medium" for r in out)

    def test_high_risk_with_low_confidence_is_review_only(self):
        # medium 신뢰도 × 고위험(ulimit 조정 후 재기동) → 정식 권고가 아니라 "검토 필요".
        out = recommend([_signal("fd_exhaustion", category="medium")])
        high = [r for r in out if r.risk == RISK_HIGH]
        assert high and all(r.review_only for r in high)
        assert all("[검토 필요]" in r.to_line() for r in high)

    def test_high_risk_with_high_confidence_is_normal_recommendation(self):
        out = recommend([_signal("oom_kill", category="strong")])
        high = [r for r in out if r.risk == RISK_HIGH]
        assert high and not any(r.review_only for r in high)

    def test_low_risk_never_review_only(self):
        out = recommend([_signal("fd_exhaustion", category="medium")])
        low = [r for r in out if r.risk == RISK_LOW]
        assert low and not any(r.review_only for r in low)


# ── 4. 정렬·중복 제거 ───────────────────────────────────────────────────
class TestOrderingAndDedup:
    def test_sorted_low_risk_first(self):
        out = recommend([_signal("oom_kill"), _signal("hung_task")])
        order = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}
        ranks = [order[r.risk] for r in out]
        assert ranks == sorted(ranks)

    def test_duplicate_actions_collapse(self):
        out = recommend([_signal("oom_kill"), _signal("oom_kill")])
        actions = [r.action for r in out]
        assert len(actions) == len(set(actions))

    def test_recommend_lines_render_risk_and_rationale(self):
        lines = recommend_lines([_signal("oom_kill", label="메모리 고갈",
                                         evidence="Killed process 1")])
        assert lines
        for line in lines:
            assert "위험도" in line and "신뢰도" in line and "근거:" in line


# ── 5. 옵트인 (dispatcher 배선) ─────────────────────────────────────────
class TestDispatcherOptIn:
    def _dispatcher(self, enabled: bool):
        from sre_agent.application.investigation_dispatcher import (
            InvestigationDispatcher,
        )
        from sre_agent.settings import AgentSettings

        settings = AgentSettings(
            severity_judge_enabled=True,
            remediation_recommender_enabled=enabled,
        )
        return InvestigationDispatcher(settings)

    def _verdict_with_signals(self):
        from sre_agent.domain.severity_signatures import ImportanceVerdict

        return ImportanceVerdict(
            level="심각", confidence="high", escalate=True,
            signals=[_signal("oom_kill", label="메모리 고갈", evidence="Killed process 1")],
        )

    def test_off_returns_none(self):
        d = self._dispatcher(enabled=False)
        assert d._recommend_remediation(self._verdict_with_signals()) is None

    def test_on_returns_lines(self):
        d = self._dispatcher(enabled=True)
        lines = d._recommend_remediation(self._verdict_with_signals())
        assert lines and any("위험도" in ln for ln in lines)

    def test_on_without_signals_returns_none(self):
        from sre_agent.domain.severity_signatures import ImportanceVerdict

        d = self._dispatcher(enabled=True)
        empty = ImportanceVerdict(level="경고", confidence="none", escalate=False)
        assert d._recommend_remediation(empty) is None

    def test_default_flag_is_off(self):
        from sre_agent.settings import AgentSettings

        assert AgentSettings().remediation_recommender_enabled is False


# ── 6. 실행 경로 부재 (D-003·D-011 불변식) ──────────────────────────────
class TestNoExecutionPath:
    """조치 문자열이 실행 API에 닿는 경로가 코드에 존재하지 않음을 고정한다.

    자동 실행은 읽기전용 원칙의 예외 결정 + 이중 승인 + 롤백·blast radius 설계가 선행돼야
    하며 본 계획 범위 밖이다(Plan 02 §9). 권고 모듈은 문자열만 만든다.
    """

    def test_module_has_no_execution_imports(self):
        import sre_agent.domain.remediation as mod

        source = inspect.getsource(mod)
        # 프로세스 실행·원격 접속 수단이 모듈에 존재하지 않는다.
        for banned in ("subprocess", "os.system", "popen", "paramiko", "asyncssh"):
            assert banned not in source.lower(), f"실행 수단 반입 금지: {banned}"

    def test_recommendations_are_plain_strings(self):
        out = recommend([_signal("hung_task")])
        assert out
        for r in out:
            assert isinstance(r.action, str) and isinstance(r.to_line(), str)
            # 실행 가능한 콜러블을 들고 있지 않다(제시 전용 dataclass).
            assert not any(callable(getattr(r, f)) for f in ("action", "risk", "rationale"))

    def test_no_change_commands_in_catalog(self):
        """조치 문구는 서술이며, 실행 가능한 변경 명령 문자열을 담지 않는다.

        `kill`·`systemctl restart` 등은 명령 형태(인자 포함)로 카탈로그에 존재하지 않는다 —
        존재하면 복사·실행으로 이어질 수 있고, toolset에도 변경 명령은 물리적으로 없다(§8).
        """
        import sre_agent.domain.remediation as mod

        banned_patterns = [
            r"systemctl\s+(restart|stop|start)",
            r"\bkill\s+-9\b",
            r"\brm\s+-rf\b",
            r"dmesg\s+-C",
        ]
        source = inspect.getsource(mod)
        for pattern in banned_patterns:
            assert not re.search(pattern, source, re.I), f"실행 명령 문자열 금지: {pattern}"

    def test_package_has_no_execution_wiring(self):
        """sre_agent 패키지 전체에 프로세스 실행 수단이 배선되지 않았음을 고정한다."""
        pkg = Path(__file__).resolve().parent.parent / "sre_agent"
        offenders: list[str] = []
        for path in pkg.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*import\s+subprocess|^\s*from\s+subprocess\s+import", text, re.M):
                offenders.append(str(path.relative_to(pkg)))
            if "os.system(" in text or "asyncssh" in text or "paramiko" in text:
                offenders.append(str(path.relative_to(pkg)))
        assert offenders == [], f"실행 수단 배선 발견: {offenders}"
