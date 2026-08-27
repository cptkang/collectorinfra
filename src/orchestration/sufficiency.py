"""오케스트레이션 수준 충족도 검증·재계획 (Plan 78 W5 · P7 · V 계층).

VMAO의 Verify를 **최소 형태로만** 도입한다 — ADaPT 교훈(과설계 경계)에 따라 LLM을 쓰지 않고,
재계획은 **1회로 못 박는다**.

## 무엇을 보는가

| # | 검증 | 무엇을 막는가 |
|---|---|---|
| 1 | **충족도** — 선행이 M개 대상을 냈는데 후속이 그보다 적게 조사했는가 | 부분 결과가 전체로 오인되는 것 |
| 2 | **빈손 반환** — 후속 task가 대상 미확정으로 아무것도 못 냈는가 | 조용한 실패 |
| 3 | **준비 검증** — 조사 경로가 **착수 전에** 가용한가 | *"평가 인프라 잡음이 모델 실패로 위장한다"* — 백엔드 미가용을 조사 실패로 기록하면 원인 귀책이 어긋난다 |
| 4 | **대상 정합 사후 대조** — 결과의 hostname이 `prior_targets`에 실제 있었는가 | *"엉뚱한 호스트를 조사했는데 형태가 정상이라 통과"* |

## 갭 ②의 잔여 한계 (명시)

위 4는 *대상이 맞았는지*만 본다. **조사 결과의 내용이 옳은지**(수집은 성공했으나 값이
무의미·오래됨·잘못된 프로파일)는 **검증하지 않는다.** 완전한 V 계층은 평가 하네스와 골든셋을
요구하며 본 계획 범위 밖이다(78 W5 주석 그대로).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 충족도 검증 대상 agent — 대상 집합을 받아 조사하는 경로만 본다.
_INVESTIGATION_AGENTS: frozenset[str] = frozenset({"process_query", "fault_diagnosis"})

#: 미충족 사유 코드 — 응답·감사에 그대로 싣는다(침묵 폴백 금지).
REASON_TARGET_SHORTFALL = "target_shortfall"
REASON_EMPTY_HANDED = "empty_handed"
REASON_TARGET_MISMATCH = "target_mismatch"

#: **재계획은 1회뿐**이다(78 W5-2). 상수로 못 박아 설정으로 늘어나지 않게 한다 —
#: 무한 루프 방지가 이 값의 존재 이유이므로 조정 가능하게 두면 의미가 없다.
MAX_SUFFICIENCY_RETRIES = 1


@dataclass
class Shortfall:
    """미충족 1건."""

    task_id: str
    reason: str
    expected: int = 0
    actual: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "reason": self.reason,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class SufficiencyReport:
    """충족도 검증 결과."""

    shortfalls: list[Shortfall] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return not self.shortfalls

    @property
    def task_ids(self) -> list[str]:
        """재실행 후보 task id(중복 제거·순서 유지)."""
        seen: list[str] = []
        for s in self.shortfalls:
            if s.task_id not in seen:
                seen.append(s.task_id)
        return seen

    def as_reasons(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.shortfalls]


def _result_rows(result: dict) -> list:
    """결과에서 행 목록을 꺼낸다(결과 형태 3종 방어)."""
    rows = result.get("query_results")
    if rows is None:
        rows = (result.get("organized_data") or {}).get("rows")
    return rows or []


def _investigated_count(result: dict) -> int:
    """결과가 실제로 조사한 대상 수. fan-out 결과는 명시 카운트를 쓴다."""
    pq = result.get("process_query") or {}
    if "succeeded_count" in pq:
        return int(pq.get("succeeded_count") or 0)
    # 단일 대상 경로는 성공 시 1건이다(행이 없어도 조사는 했다).
    if pq:
        return 1 if pq.get("total_count") is not None else 0
    return 1 if _result_rows(result) else 0


def _result_hostnames(result: dict) -> set[str]:
    """결과에 실린 호스트명 집합(fan-out 표는 행마다 귀속을 갖는다)."""
    names: set[str] = set()
    pq = result.get("process_query") or {}
    if pq.get("hostname"):
        names.add(str(pq["hostname"]))
    for row in _result_rows(result):
        if isinstance(row, dict) and row.get("hostname"):
            names.add(str(row["hostname"]))
    return names


def check_sufficiency(
    tasks: list[dict], results: dict[str, dict], expected_targets: dict[str, list[dict]]
) -> SufficiencyReport:
    """복합 질의의 충족도를 **결정적으로** 판정한다 (78 W5-1 · LLM 미사용).

    Args:
        tasks: 실행된 TaskSpec 목록
        results: {task_id: 정규화된 결과}
        expected_targets: {task_id: [TargetRef dict, ...]} — 그 task에 주입된 대상 집합

    Returns:
        `SufficiencyReport` — 미충족이 없으면 `sufficient`가 True
    """
    report = SufficiencyReport()
    for task in tasks or []:
        if task.get("agent") not in _INVESTIGATION_AGENTS:
            continue
        task_id = task.get("task_id") or ""
        result = results.get(task_id) or {}
        if result.get("error"):
            continue  # 실패는 이미 사유가 있다 — 충족도로 이중 계상하지 않는다

        expected = expected_targets.get(task_id) or []
        if not expected:
            continue  # 대상 주입이 없었으면 충족도를 잴 기준이 없다

        actual = _investigated_count(result)
        if actual == 0:
            report.shortfalls.append(
                Shortfall(task_id, REASON_EMPTY_HANDED, len(expected), 0,
                          "대상 미확정으로 빈손 반환")
            )
            continue
        if actual < len(expected):
            report.shortfalls.append(
                Shortfall(task_id, REASON_TARGET_SHORTFALL, len(expected), actual,
                          "선행이 낸 대상보다 적게 조사됐다")
            )

        # 대상 정합 사후 대조(W5-5) — "엉뚱한 호스트를 조사했는데 형태가 정상이라 통과"를 막는다.
        mismatched = reconcile_targets(expected, result)
        if mismatched:
            report.shortfalls.append(
                Shortfall(task_id, REASON_TARGET_MISMATCH, len(expected), actual,
                          f"대상 밖 호스트가 결과에 있다: {', '.join(sorted(mismatched))}")
            )
    return report


def reconcile_targets(expected: list[dict], result: dict) -> set[str]:
    """결과에 실린 hostname이 대상 집합에 **실제로 있었는지** 대조한다 (78 W5-5).

    Args:
        expected: 주입된 대상 [{server_name, hostname, ip, db_id}]
        result: task 결과

    Returns:
        대상 집합에 없던 호스트명 집합(없으면 빈 집합)
    """
    allowed: set[str] = set()
    for t in expected or []:
        if not isinstance(t, dict):
            continue
        for key in ("hostname", "server_name", "ip"):
            if t.get(key):
                allowed.add(str(t[key]))
    if not allowed:
        return set()
    return {h for h in _result_hostnames(result) if h not in allowed}


def check_investigation_readiness(app_config: Any) -> tuple[bool, str]:
    """조사 경로가 **착수 전에** 가용한지 확인한다 (78 W5-4 · V 계층 준비 검증).

    문서의 지적 — *"평가 인프라 잡음이 모델 실패로 위장한다. 실패한 실행은 모델 한계가
    아니라 망가진 도구·낡은 컨텍스트·리셋되지 않은 샌드박스에서 비롯될 수 있다."*
    **미가용을 조사 실패로 기록하면 원인 귀책이 어긋난다** — 조사를 시작하지 않고 사유를 돌려준다.

    Args:
        app_config: 앱 설정

    Returns:
        (가용 여부, 사유). 가용하면 사유는 빈 문자열
    """
    gate = getattr(app_config, "noise_gate", None)
    if gate is None:
        return False, "조사 서비스 설정이 없습니다(noise_gate 미구성)"
    if not getattr(gate, "fault_diagnosis_enabled", False):
        return False, "장애 진단 경로가 비활성(fault_diagnosis_enabled=false)입니다"
    if not (getattr(gate, "investigation_service_url", "") or "").strip():
        return False, "조사 서비스 URL이 설정되지 않았습니다"
    return True, ""


def summarize_shortfalls(report: SufficiencyReport, *, retried: bool) -> Optional[str]:
    """미충족을 사용자 응답에 실을 한 문장으로 만든다 (78 W5-3).

    재시도 후에도 미충족이면 **사유를 노출**한다 — 조용히 부분 결과만 보여주지 않는다.
    """
    if report.sufficient:
        return None
    parts = []
    for s in report.shortfalls:
        if s.reason == REASON_TARGET_SHORTFALL:
            parts.append(f"대상 {s.expected}건 중 {s.actual}건만 조사됐습니다")
        elif s.reason == REASON_EMPTY_HANDED:
            parts.append(f"대상 {s.expected}건에 대해 조사 결과가 비어 있습니다")
        elif s.reason == REASON_TARGET_MISMATCH:
            parts.append(f"대상 밖 결과가 섞였습니다({s.detail})")
    suffix = " 1회 재시도했으나 개선되지 않았습니다." if retried else ""
    return "⚠ " + " / ".join(parts) + "." + suffix
