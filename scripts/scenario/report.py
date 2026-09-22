"""산출물 B - 리포트 생성기 (plans/94 §5).

**없는 통계를 만들지 않는다.** p95 는 표본 20건 이상일 때만 내고, 반복 1회 결과에는
통계 표기를 붙이지 않는다(§5.3). 수동 검토·제외 목록은 **비우지 않는다** - 판정하지
못한 것을 리포트에서 지우면 커버리지가 부풀려진다.

섹션 순서는 고정이다(11개). 순서가 흔들리면 사람이 매번 리포트 구조를 다시 익혀야 한다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from . import utf8_open
from .assertions import INVALID_VERDICT, row_is_invalid
from .catalog import Catalog

# 표본이 이보다 적으면 p95 를 내지 않는다 (§5.3 · group_metrics.py 와 같은 철학).
P95_MIN_SAMPLE = 20

_VERDICT_ORDER = ("fail", "error", "manual", "pass")

#: 무효 턴 비율이 이 값을 넘으면 리포트 최상단 경고 + 회귀 비교 제외 (T-e · D-218).
#: run 20260915-131903 은 26.9% 였는데 리포트 어디에도 그 사실이 없었다.
INVALID_RATIO_WARN = 0.05


#: 기준 실행 단(D-225 ① - 3단). 이것이 아니면 리포트 최상단에 경고를 올린다(O-c).
CANONICAL_TIER = "semantic_router"

#: 부가 경로 단(D-225 ② - 1단 opt-in). 강등이 아니므로 경고하지 않고 **안내**만 올린다 -
#: 판정표가 기준 단의 수치가 아니라는 사실은 여전히 해석을 바꾸기 때문이다.
OPTIN_TIER = "deep_agent"

#: 2단. 존 선택 재진입 경로가 알람 의도 교정을 건너뛴다(plans/111 §2.4) - 111 G-5 확정:
#: *"2단을 재측정 arm 으로 쓰면 측정 왜곡(알람 질의 전건 오분류)을 리포트에 고지한다"*.
INTENT_TIER = "intent_orchestration"
#: 2단이 섞인 run 에서 알람 시나리오 행에 붙이는 문구(plans/114 M-7).
TIER2_ALARM_NOTE = "2단 알람 교정 우회(plans/111 §2.4) - 해석 제외 권고"
#: 알람 군(카탈로그 `d_alarm.yaml`).
ALARM_GROUP = "D"


def tier2_alarm_caveat(
    verdicts: dict[str, dict[str, Any]],
    profiles: list[dict[str, Any]],
    catalog: Optional[Catalog],
) -> Optional[dict[str, Any]]:
    """2단 프로파일이 섞인 run 이면 **해석에서 뺄 알람 시나리오**를 고른다(plans/114 M-7).

    알람 시나리오 판별(결정적): 군이 `D` 이거나, 카탈로그의 어느 턴 질의문에서
    `has_alarm_signal`(`src/orchestration/intent_planner.py`)이 참인 시나리오. 2단 교정
    `_coerce_alarm_intent` 가 쓰는 판정 **그대로**다 - 우회되는 교정과 같은 술어로 골라야
    고지 범위가 결함 범위와 맞는다. 목록을 손으로 두면 카탈로그가 바뀔 때 낡는다.
    카탈로그 없이 부르면(`--report` 재생성 등) D군만 고르고 그 사실을 `criterion` 에 남긴다.
    """
    tier2 = [str(p.get("name")) for p in profiles or [] if p.get("tier") == INTENT_TIER]
    if not tier2:
        return None
    signalled: set[str] = set()
    if catalog:
        # 함수 안 import - `optin_failure_profiles` 와 같은 관행(리포트는 산출물만 읽는 경로다).
        from src.orchestration.intent_planner import has_alarm_signal

        signalled = {
            s.id for s in catalog.scenarios
            if any(has_alarm_signal(str((t.send or {}).get("query") or "")) for t in s.turns)
        }
    return {
        "profiles": tier2,
        "scenario_ids": sorted(
            sid for sid, info in verdicts.items()
            if info.get("group") == ALARM_GROUP or sid in signalled
        ),
        "criterion": ("D군 + 질의문 알람 신호(has_alarm_signal)" if catalog
                      else "D군만(카탈로그 없이 생성 - 다른 군의 알람 시나리오는 고르지 못했다)"),
    }


def _degraded_profiles(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """기준 단도 부가 경로 단도 아닌 단으로 돈 프로파일 (O-c).

    `tier` 를 **읽지 못한 경우는 경고하지 않는다** - 모의 실행(`tier="mock"`)과
    기동 로그 미확인은 강등이 아니라 미관측이다. 미관측을 강등으로 세면 경고가 상시
    켜져 사람이 읽지 않게 된다.
    """
    out: list[dict[str, Any]] = []
    for profile in summary.get("profiles", []) or []:
        tier = profile.get("tier")
        if not tier or tier in (CANONICAL_TIER, OPTIN_TIER, "mock"):
            continue
        out.append(profile)
    return out


def optin_failure_profiles(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """1단 opt-in 이 가용성 때문에 성립하지 않아 **INVALID 로 제외된** 프로파일 (권고 B).

    러너는 이 프로파일의 시나리오를 통째로 `skipped` 로 돌린다(`server.py`
    `UNINTENDED_DEGRADATION` → `status.valid = False`). 그 사실이 10절에만 남으면, 리포트를
    읽는 사람은 **측정하지 않은 시나리오군을 측정한 것으로 읽는다**.

    `_degraded_profiles` 와 겹치지 않는다 - 확정된 단은 기준 단(3단)이라 그 값 자체는 유효하다.

    사유 집합은 **정본**(`src/observability/ladder.py` `OPTIN_FAILURE_REASONS`)에서 읽는다 -
    `server.py` 의 `UNINTENDED_DEGRADATION` 은 같은 값을 러너 쪽에 다시 적은 사본이다(D-053).
    **함수 안에서 import** 하는 이유: 리포트는 산출물만 읽는 경로인데 `server.py` 를 통하면
    `client.py` 의 httpx 까지 딸려 온다(`preflight.py` 와 같은 관행).
    """
    from src.observability.ladder import OPTIN_FAILURE_REASONS

    return [
        p for p in summary.get("profiles", []) or []
        if p.get("degraded_reason") in OPTIN_FAILURE_REASONS
    ]


def optin_failure_excluded(summary: dict[str, Any], profiles: list[dict[str, Any]]) -> int:
    """opt-in 실패 프로파일 때문에 제외된 시나리오 건수 - 「몇 건을 못 쟀는가」."""
    names = {str(p.get("name")) for p in profiles}
    return sum(
        1 for item in summary.get("skipped", []) or []
        if any(f"프로파일 {name} INVALID" in str(item.get("reason") or "") for name in names)
    )


def valid_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기능 판정의 **분모**. 무효 턴은 여기서 빠진다(T-c)."""
    return [row for row in rows if not row_is_invalid(row)]


def invalid_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """무효 턴의 건수·비율·구간(T-c·T-e).

    **구간을 명시한다.** run 20260915-131903 의 103턴은 실행 순서 280번째부터 마지막까지
    연속이었고, 그 사실이 곧 원인(토큰 만료)이었다. 흩어진 무효와 연속된 무효는 다른 사고다.
    """
    invalid = [
        {
            "index": index,
            "scenario_id": row.get("scenario_id"),
            "turn": row.get("turn"),
            "repeat": row.get("repeat"),
            "group": row.get("group"),
            "reason": row.get("invalid_reason") or row.get("error"),
        }
        for index, row in enumerate(rows, start=1)
        if row_is_invalid(row)
    ]
    total = len(rows)
    by_group: Counter[str] = Counter(str(item["group"]) for item in invalid)
    return {
        "count": len(invalid),
        "total_turns": total,
        "ratio": round(len(invalid) / total, 4) if total else 0.0,
        "over_threshold": bool(total) and (len(invalid) / total) > INVALID_RATIO_WARN,
        "by_group": dict(sorted(by_group.items())),
        "first_index": invalid[0]["index"] if invalid else None,
        "last_index": invalid[-1]["index"] if invalid else None,
        "first_scenario": invalid[0]["scenario_id"] if invalid else None,
        "turns": invalid,
    }


#: 단언이 평가되지 않은 턴의 사유 3종 (`108·G-6` ≡ `97·G-1` · D-241 · 2026-09-21 사용자 승인).
#:
#: **합치지 않는다.** 셋은 귀속처가 다르다 - 합쳐서 한 숫자로 내면 "하네스를 고쳐야 할 것"과
#: "제품을 고쳐야 할 것"이 구별되지 않는다. 20260918 run 에서 이 규칙은 기능 합격률을
#: 25.0% -> 66.7%(+41.7%p)로 바꾸지만, 20260914 스위프에서는 12.5% -> 12.8%(+0.3%p)밖에
#: 움직이지 않는다 - 특정 run 을 좋게 보이려는 규칙이 아니라는 증거다(`plans/110` §3.1 교차 대조).
UNEVALUATED_REASONS = ("invalid", "timeout", "clarify_blocked")

#: 사유별 **귀속처**. 이 칸이 셋을 합치지 못하게 하는 장치다.
UNEVALUATED_OWNER = {
    "invalid": "하네스 과실 - 러너 401 · teardown 오염",
    "timeout": "제품 성능 축 - 요청 상한 초과",
    "clarify_blocked": "하네스 구성 - 역질문으로 끝나 단언에 도달하지 못함",
}

#: 옛 run 폴백에서 "역질문 자체가 판정 대상이었다"를 추정하는 단언 키(보수적 추정).
#: **`status` 를 넣지 않는다**(2026-09-21 · 109/d9 실측 지적). `response_mode == "clarify"` 인데
#: `status` 단언이 **실패**했다면 기대값이 `clarification` 이 아니었다는 뜻이다 - 기대가
#: `clarification` 이었다면 그 단언은 통과했을 것이다. 넣으면 "역질문을 기대하지 않은 질의가
#: 역질문에 막힌 턴"까지 판정 대상으로 잡혀 제외가 무력해진다(run 20260914 실측: 역질문으로
#: 끝나고 불합격인 3,453턴 중 **2,311턴**이 `status`(기대 completed · 실제 clarification)였다).
_CLARIFY_ASSERTION_KEYS = ("clarification", "options_contains")


def unevaluated_reason(
    row: dict[str, Any], *, expected_question: Optional[bool] = None
) -> Optional[str]:
    """이 턴에서 **단언이 평가되지 않았나**. 평가됐으면 None (`108·G-6`).

    우선순위는 `invalid` > `timeout` > `clarify_blocked` 다. `invalid` 를 먼저 보는 이유는
    T-c(D-218)가 이미 그 이름으로 분리하고 있어서다 - 같은 턴이 두 사유로 두 번 세어지면
    제거 건수의 합이 실제 제거 수와 어긋난다.

    **`clarify_blocked` 는 "역질문이 뜬 턴"이 아니라 "역질문으로 끝난 턴"이다.** 러너는
    자동 응답 뒤의 관측치로 `obs` 를 갈아끼우므로(`runner._answer_questions`), 자동응답이
    답을 주고 진행된 턴은 최종 `response_mode` 가 `clarify` 가 아니다. 20260918 run 에서
    존 자동응답은 380턴 중 221턴(58.2%)에 발동했는데, 그 턴들은 단언이 **평가됐으므로**
    제외 대상이 아니다 - "역질문이 뜬 모든 턴"으로 잡으면 분모가 절반 넘게 날아간다.

    Args:
        expected_question: 그 턴이 역질문을 **기대했는지**(`clarify.expects_question`).
            기대한 역질문은 그 자체가 판정 대상이라 평가된 것이다(R3-03 · I-01~I-06).
            `None` 이면 칸이 없던 옛 run 으로 보고 실패 단언 키로 보수적으로 추정한다.
    """
    if row_is_invalid(row):
        return "invalid"
    if _is_timeout(row) or row.get("forbidden_mode") == "hang":
        return "timeout"
    if row.get("response_mode") != "clarify":
        return None
    if row.get("func_verdict") == "pass":
        # 역질문으로 끝났지만 판정은 통과했다 - 단언이 평가된 것이다. 합격을 지우지 않는다.
        return None
    if expected_question is None:
        fails = row.get("failed_assertions") or []
        expected_question = any(
            str(f.get("key") or "").startswith(_CLARIFY_ASSERTION_KEYS)
            # `status` 는 기대값이 `clarification` 일 때만 역질문 기대로 본다(위 주석).
            or (str(f.get("key") or "") == "status"
                and "clarification" in str(f.get("expected") or ""))
            for f in fails
        )
    return None if expected_question else "clarify_blocked"


def clarify_blocked_label(data: dict[str, Any]) -> str:
    """`역질문 차단 N건 (자동응답 M건 · 발동률 P%)` — **벤치와 같은 표기**(2026-09-21 합의).

    `clarify_blocked` 수치는 자동응답 유무에 따라 의미가 정반대로 읽힌다. 20260918(자동응답
    있음 · 221/380턴)과 20260914 스위프(자동응답 없음 · 역질문 차단 3,705/6,567)를 나란히
    놓으면 이 표기 없이는 두 값이 비교되는 것처럼 오해된다.

    발동률의 분모·정의는 벤치 `RunHealth.auto_answered_turns`(`sweep.py` - `auto_answers` 가
    실린 턴 수 / 전체 턴)와 **같다**. 두 하네스가 다른 분모를 쓰면 통일한 의미가 없다.
    """
    blocked = int((data.get("by_reason") or {}).get("clarify_blocked") or 0)
    auto = int(data.get("auto_answer_turns") or 0)
    total = int(data.get("total_turns") or 0)
    if not auto:
        return f"역질문 차단 {blocked}건 (자동응답 없음)"
    rate = f"{auto / total:.0%}" if total else "-"
    return f"역질문 차단 {blocked}건 (자동응답 {auto}건 · 발동률 {rate})"


def row_unevaluated(row: dict[str, Any]) -> Optional[str]:
    """행의 **칸**이 1차 출처다. 칸이 없는 옛 run 만 규칙으로 되살린다.

    `arm`·`base_profile` 과 같은 원칙이다 - 소비자는 재도출하지 않고 칸을 읽는다.
    """
    if "unevaluated_reason" in row:
        return row.get("unevaluated_reason")
    return unevaluated_reason(row)


def scored_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기능 합격률의 **분모**(`108·G-6`). 무효(T-c)에 더해 단언 미평가 턴을 뺀다.

    `valid_rows` 는 무효만 뺀다 - 그쪽은 성능·대응 등급 집계처럼 "측정은 됐다"를 분모로 쓰는
    곳이 계속 쓴다. 여기는 **기능 판정 전용**이다.
    """
    return [row for row in rows if not row_unevaluated(row)]


def unevaluated_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """사유 3종을 **각각** 센다 - 합산 하나로 뭉개지 않는다.

    **제거 사다리**로 만든다: 전체 -> -invalid -> -timeout -> -clarify_blocked -> 분모.
    각 단계는 `unevaluated_reason` 이 이미 단일 사유를 돌려주므로 한 턴이 한 번만 세어지고,
    `제거 합 + 분모 == 전체` 가 항상 성립한다. 이중 차감이 구조적으로 불가능하다.
    """
    reasons = [row_unevaluated(row) for row in rows]
    counts = Counter(reason for reason in reasons if reason)
    scored = sum(1 for reason in reasons if not reason)
    total = len(rows)
    passed = sum(
        1 for row, reason in zip(rows, reasons)
        if not reason and row.get("func_verdict") == "pass"
    )
    legacy = sum(1 for row in rows if "unevaluated_reason" not in row)

    by_group: dict[str, dict[str, Any]] = {}
    for row, reason in zip(rows, reasons):
        cell = by_group.setdefault(
            str(row.get("group")),
            {"total": 0, "scored": 0, "pass": 0, "pass_rate": None,
             **{key: 0 for key in UNEVALUATED_REASONS}},
        )
        cell["total"] += 1
        if reason:
            cell[reason] += 1
        else:
            cell["scored"] += 1
            if row.get("func_verdict") == "pass":
                cell["pass"] += 1
    for cell in by_group.values():
        cell["pass_rate"] = (
            round(cell["pass"] / cell["scored"], 4) if cell["scored"] else None
        )

    return {
        "total_turns": total,
        "scored": scored,
        "pass": passed,
        # 분모가 0이면 비율을 만들지 않는다 - 없는 통계를 만들지 않는다(§5.3).
        "pass_rate": round(passed / scored, 4) if scored else None,
        "pass_rate_legacy": round(passed / total, 4) if total else None,
        "by_reason": {reason: counts.get(reason, 0) for reason in UNEVALUATED_REASONS},
        "removed": sum(counts.values()),
        "ladder_ok": sum(counts.values()) + scored == total,
        "derived_rows": legacy,
        "auto_answer_turns": sum(1 for row in rows if row.get("auto_answers")),
        "by_group": dict(sorted(by_group.items())),
    }


def turn_key(row: dict[str, Any]) -> tuple[str, str, int, int]:
    """한 턴을 유일하게 가리키는 키 - 94 러너 `row_key` 와 **같은 정의**다(테스트가 대조한다).

    러너를 임포트하지 않는다 - 러너가 이 모듈을 임포트한다.
    """
    return (str(row.get("profile")), str(row.get("scenario_id")),
            int(row.get("turn", 0)), int(row.get("repeat", 0)))


def load_rows(run_dir: Path) -> list[dict[str, Any]]:
    """`raw.jsonl` 을 읽되 **같은 턴은 마지막 행만** 남긴다(109·CS-17).

    재개는 같은 키를 한 번 더 적재한다 - 무효 턴 재실행(X-1)과 일부만 끝난 멀티턴의 1턴부터
    재실행(CS-17). 러너(`RawLog._remember`)와 벤치(`sweep.read_raw_rows`)는 *"파일 순서 = 시간
    순서, 뒤 행이 결과"* 로 읽는데 리포트만 전부 세면 무효·지연·판정이 두 번 들어간다. 순서는
    **처음 적재된 위치**를 지킨다(무효 구간의 실행 순서가 이것을 쓴다). `turn` 칸이 없는 행은
    접지 않는다.
    """
    path = run_dir / "raw.jsonl"
    if not path.exists():
        return []
    rows: dict[Any, dict[str, Any]] = {}
    with utf8_open(path, "r") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows[turn_key(row) if "turn" in row else ("#", index)] = row
    return list(rows.values())


def load_run_meta(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run.json"
    if not path.exists():
        return {"meta": {}, "profiles": [], "skipped": []}
    with utf8_open(path, "r") as handle:
        return json.load(handle)


def _worst(verdicts: list[str]) -> str:
    for candidate in _VERDICT_ORDER:
        if candidate in verdicts:
            return candidate
    return "skipped"


def scenario_verdicts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """시나리오별 판정. 반복 간 결과가 갈리면 `불안정` 이다(§2-4).

    불안정은 합격으로도 불합격으로도 세지 않는다 - 갈리는 것 자체가 보고할 사실이다.

    **무효 턴(T-c)은 분모에서 뺀다.** 유효 턴이 하나도 없는 시나리오만 `invalid` 이고,
    일부만 무효인 시나리오는 **남은 유효 턴으로** 판정한다 - 401 한 번에 시나리오 전체가
    불합격이 되던 것이 「과잉 거부 의심」 26건 허위의 절반이었다.
    """
    per_repeat: dict[tuple[str, int], list[str]] = defaultdict(list)
    invalid_repeats: set[tuple[str, int]] = set()
    info: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = (row["scenario_id"], int(row.get("repeat", 0)))
        if row_is_invalid(row):
            invalid_repeats.add(key)
        else:
            per_repeat[key].append(row.get("func_verdict", "error"))
        info.setdefault(
            row["scenario_id"],
            {
                "group": row.get("group"),
                "plans": row.get("plans", []),
                "kind": row.get("kind"),
                "pair_id": row.get("pair_id"),
                "profile": row.get("profile"),
            },
        )

    by_scenario: dict[str, list[str]] = defaultdict(list)
    for (scenario_id, _repeat), verdicts in per_repeat.items():
        by_scenario[scenario_id].append(_worst(verdicts))
    invalid_counts: Counter[str] = Counter(sid for sid, _repeat in invalid_repeats)

    result: dict[str, dict[str, Any]] = {}
    for scenario_id in info:
        verdicts = by_scenario.get(scenario_id, [])
        if not verdicts:
            # 유효 턴이 0개다 - 판정한 적이 없는 시나리오를 불합격으로 세지 않는다.
            result[scenario_id] = {
                **info[scenario_id],
                "verdict": INVALID_VERDICT,
                "repeats": 0,
                "distinct": [],
                "invalid_repeats": invalid_counts.get(scenario_id, 0),
            }
            continue
        distinct = set(verdicts)
        flaky = len(distinct) > 1 and "pass" in distinct and {"fail", "error"} & distinct
        result[scenario_id] = {
            **info[scenario_id],
            "verdict": "flaky" if flaky else _worst(verdicts),
            "repeats": len(verdicts),
            "distinct": sorted(distinct),
            "invalid_repeats": invalid_counts.get(scenario_id, 0),
        }
    return result


def profile_breakdown(
    rows: list[dict[str, Any]], run: dict[str, Any]
) -> list[dict[str, Any]]:
    """프로파일(= arm 조합)별 판정 집계 - **arm 을 섞지 않는다**(`110·N-1`).

    §2 군별 표는 `scenario_verdicts` 가 시나리오 id 로 접는다. 같은 시나리오를 arm 2개로
    돌리면 두 프로파일의 판정이 **한 칸으로 합쳐지고 나쁜 쪽이 이긴다**(`_worst`) - 그 표만
    보면 어느 arm 이 무엇을 냈는지 알 수 없다. 여기서 프로파일별로 갈라 둔다.

    순서는 `run.json` 의 기동 순서를 따른다(실행 순서가 곧 해석 순서다). `run.json` 에 없는
    프로파일이 원시 로그에 있으면 뒤에 붙인다 - 빠뜨리지 않는 쪽을 택한다.
    """
    info = {str(p.get("name")): p for p in (run.get("profiles") or [])}
    names = [name for name in info if any(str(r.get("profile")) == name for r in rows)]
    names += [
        name for name in dict.fromkeys(str(r.get("profile")) for r in rows)
        if name not in info
    ]

    out: list[dict[str, Any]] = []
    for name in names:
        subset = [row for row in rows if str(row.get("profile")) == name]
        verdicts = scenario_verdicts(subset)
        counts = Counter(v["verdict"] for v in verdicts.values())
        live = valid_rows(subset)
        meta = info.get(name, {})
        out.append({
            "name": name,
            "arm": meta.get("arm"),
            "base_profile": meta.get("base_profile"),
            "tier": meta.get("tier"),
            "scenarios": len(verdicts),
            "turns": len(subset),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
            "manual": counts.get("manual", 0),
            "flaky": counts.get("flaky", 0),
            "invalid": counts.get(INVALID_VERDICT, 0),
            "latency": _latency_stats([
                float(row["processing_time_ms"]) for row in live
                if row.get("processing_time_ms") is not None
            ]),
        })
    return out


def _latency_stats(values: list[float]) -> dict[str, Any]:
    """지연 통계. 표본이 부족하면 수치를 만들지 않는다."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "p50": None, "p95": None, "max": None, "note": "표본 없음"}
    clean.sort()
    stats: dict[str, Any] = {
        "n": len(clean),
        "p50": round(statistics.median(clean), 1),
        "max": round(clean[-1], 1),
        "p95": None,
        "note": "",
    }
    if len(clean) >= P95_MIN_SAMPLE:
        index = max(0, int(round(0.95 * len(clean))) - 1)
        stats["p95"] = round(clean[index], 1)
    else:
        stats["note"] = f"표본 부족 (n={len(clean)} < {P95_MIN_SAMPLE}) - p95 생략"
    return stats


def build_summary(
    run_dir: Path, catalog: Optional[Catalog] = None
) -> dict[str, Any]:
    """summary.json - 분석기와의 계약 (§7.2)."""
    rows = load_rows(run_dir)
    run = load_run_meta(run_dir)
    verdicts = scenario_verdicts(rows)
    invalid = invalid_summary(rows)
    unevaluated = unevaluated_summary(rows)
    by_profile = profile_breakdown(rows, run)
    # 성능·판정·R군 집계는 전부 **유효 턴**만 본다(T-c·T-d). 무효 턴은 별도 절에서 센다.
    live_rows = valid_rows(rows)

    groups: dict[str, dict[str, Any]] = {}
    for group_id in sorted({v["group"] for v in verdicts.values() if v.get("group")}):
        members = [v for v in verdicts.values() if v.get("group") == group_id]
        counts = Counter(v["verdict"] for v in members)
        latencies = [
            row.get("processing_time_ms")
            for row in live_rows
            if row.get("group") == group_id and row.get("processing_time_ms") is not None
        ]
        perf = Counter(
            row.get("perf_verdict") for row in live_rows if row.get("group") == group_id
        )
        target = (
            catalog.groups[group_id].latency_target_ms
            if catalog and group_id in catalog.groups
            else None
        )
        groups[group_id] = {
            "total": len(members),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
            "manual": counts.get("manual", 0),
            "flaky": counts.get("flaky", 0),
            "invalid": counts.get(INVALID_VERDICT, 0),
            "target_ms": target,
            "latency": _latency_stats([float(v) for v in latencies]),
            "perf_pass": perf.get("pass", 0),
            "perf_fail": perf.get("fail", 0),
            "perf_na": perf.get("n/a", 0),
        }

    plans_coverage: dict[str, dict[str, Any]] = {}
    if catalog:
        for plan, scenario_ids in sorted(catalog.plans_index().items()):
            executed = [verdicts[s] for s in scenario_ids if s in verdicts]
            plans_coverage[str(plan)] = {
                "scenarios": len(scenario_ids),
                "executed": len(executed),
                "pass": sum(1 for v in executed if v["verdict"] == "pass"),
                "scenario_ids": scenario_ids,
            }

    misuse: dict[str, dict[str, Any]] = {}
    for kind_group in ("R1", "R2", "R3", "R4"):
        # T-d: 대응 등급·`control_broken` 은 **유효 턴에서만** 센다. 401 구간의 R3·R4 96턴은
        # 본군과 대조군이 함께 401 이라 「과잉 거부 의심」 26건으로 올라갔었다 - 전건 허위였다.
        members = [
            row for row in live_rows if str(row.get("group", "")).upper() == kind_group
        ]
        if not members:
            continue
        modes = Counter(row.get("response_mode") for row in members)
        forbidden = Counter(
            row["forbidden_mode"] for row in members if row.get("forbidden_mode")
        )
        pairs_broken = _broken_pairs(verdicts, kind_group)
        misuse[kind_group] = {
            "total": len({row["scenario_id"] for row in members}),
            "mode_dist": dict(modes),
            "forbidden": dict(forbidden),
            "control_broken": len(pairs_broken),
            "control_broken_pairs": pairs_broken,
        }

    failures = [
        {
            "scenario_id": row["scenario_id"],
            "turn": row.get("turn"),
            "kind": classify_failure(row),
            "plans": row.get("plans", []),
            "failed_assertions": row.get("failed_assertions", []),
            "forbidden_mode": row.get("forbidden_mode"),
        }
        for row in live_rows
        if row.get("func_verdict") in ("fail", "error")
    ]

    silent_wrong = sum(
        1 for row in live_rows if row.get("forbidden_mode") == "silent_wrong"
    )

    return {
        "meta": run.get("meta", {}),
        "profiles": run.get("profiles", []),
        "by_profile": by_profile,
        "groups": groups,
        "plans_coverage": plans_coverage,
        "misuse": misuse,
        "failures": failures,
        "skipped": run.get("skipped", []),
        "silent_wrong_total": silent_wrong,
        "scenario_verdicts": verdicts,
        "invalid": invalid,
        "unevaluated": unevaluated,
        "tier2_alarm_caveat": tier2_alarm_caveat(verdicts, run.get("profiles", []), catalog),
    }


def _broken_pairs(verdicts: dict[str, dict[str, Any]], group_id: str) -> list[list[str]]:
    """대조군이 함께 깨진 쌍 - 가드가 정상 동작까지 막았다는 뜻이다(§5.2-5 · R12)."""
    broken: list[list[str]] = []
    for scenario_id, info in verdicts.items():
        if str(info.get("group", "")).upper() != group_id:
            continue
        pair = info.get("pair_id")
        if not pair or pair not in verdicts:
            continue
        if info["verdict"] in ("fail", "error") and verdicts[pair]["verdict"] in ("fail", "error"):
            pair_key = sorted([scenario_id, pair])
            if pair_key not in broken:
                broken.append(pair_key)
    return broken


def _is_timeout(row: dict[str, Any]) -> bool:
    """턴이 타임아웃으로 끝났는가.

    서버가 내는 문구는 한국어(`처리 시간이 초과되었습니다...`)라 종전의
    `"timeout" in error` 검사로는 **한 건도 잡히지 않았다**(run 20260918-182507 실측 199건).
    HTTP 504 와 한국어 문구를 함께 본다.
    """
    err = str(row.get("error") or "").lower()
    return "timeout" in err or "504" in err or "시간이 초과" in err


def classify_failure(row: dict[str, Any]) -> str:
    """실패 분류 14규칙 (§6.2 · Y-7 · plans/94 §19 `rewrite`). 위에서부터 먼저 맞는 것을 적용한다.

    `clarify`·`volume`·`contract` 세 유형은 run 20260915-131903 의 `unclassified` 12건을
    보고 추가했다. 그 12건은 분류 체계의 갭이 아니라 **판정 계약 결함의 그림자**였다 -
    `row_count`(D-02·D-04 팬아웃 합계) · `clarification.options_len`(I-01~I-06 미매핑 열
    2개) · `response_must_contain`(H-06·H-12·H-13·I-07 안내 문구)이 전부였다.
    유형을 갈라 놓아야 "계약을 고칠 것"과 "제품을 고칠 것"이 섞이지 않는다.
    """
    keys = {f.get("key") for f in row.get("failed_assertions", [])}
    status_error = row.get("error") or row.get("forbidden_mode") in ("crash",)

    # `timeout` 이 맨 위다(2026-09-21 · run 20260918-182507 실측). 종전에는 `generation`
    # 규칙(`executed_sql` 부재 + 오류)이 위에 있어 **타임아웃이 이 규칙에 도달한 적이 없다** -
    # 그 run 의 타임아웃 199턴이 전건 `generation` 191 · `routing` 8 로 분류됐고,
    # `failure_taxonomy.md` 에 타임아웃이 한 건도 나오지 않았다. 그 결과
    # `improvement_backlog.md` 가 `generation 191`(점수 382)을 2순위 제품 결함으로 올렸는데
    # 실제로는 전부 60초 벽이었다. 타임아웃 턴은 `executed_sql` 이 없고 오류가 있는 것이
    # **정상**이므로 두 규칙은 구조적으로 겹친다 - 더 구체적인 쪽(오류 문자열·`hang` 표지로
    # 확정되는 타임아웃)이 먼저 와야 한다. 타임아웃 턴의 다른 단언은 애초에 평가가 성립하지
    # 않으므로(중단된 지점까지만 관측된다) 그 단언으로 유형을 매기면 엉뚱한 곳을 고치게 된다.
    if row.get("forbidden_mode") == "hang" or _is_timeout(row):
        return "timeout"
    if {"intent", "db_ids"} & keys:
        return "routing"
    # `executed_sql`(단수) 단독으로 보지 않는다(2026-09-21 실측). 2단·멀티 DB 경로에서 그 키는
    # **구조적으로 항상 null** 이라(run 20260918-182507: 380턴 전건 null · 실제 SQL 은
    # `executed_sqls` 복수 키에 273턴 적재) 이 규칙이 사실상 "오류면 무조건 generation" 으로
    # 퇴화해 있었다. SQL 이 실제로 나왔는지는 두 키를 함께 봐야 한다.
    if not (row.get("executed_sql") or row.get("executed_sqls")) and status_error:
        return "generation"
    if {"sql_must_not_match", "column_must_not_map", "response_must_not_contain"} & keys:
        return "guard"
    if row.get("row_count") == 0 and "row_count.min" in keys:
        # 0건은 데이터 부재일 수도 SQL 오류일 수도 있다. 섞으면 엉뚱한 곳을 고친다.
        return "empty_result"
    if {"file.columns", "file.sheets", "file.filled_rows.min", "has_file"} & keys:
        return "document"
    if any(str(key).startswith("clarification") for key in keys):
        # 되물었는가 · 무엇을 되물었는가. 조회 단계 이전의 실패다.
        return "clarify"
    if any(str(key).startswith("row_count") for key in keys):
        # 행 수가 기대와 다르다. **어느 축으로 쟀는지**를 먼저 확인할 것(단일 DB vs 팬아웃).
        return "volume"
    if "response_must_contain" in keys:
        # 처리는 했는데 사유·안내를 말하지 않았다(침묵 처리). 응답 계약 위반이다.
        return "contract"
    if any(str(key).startswith("rewrite") for key in keys):
        # 재작성문이 확정 해석과 어긋났다(위치 누출·스코프 축소) 또는 게이트 판정이 기대와 다르다
        # (plans/107 · Y-11·Y-12). SQL 의미 오류(semantics)와 처방 위치가 달라 따로 센다.
        return "rewrite"
    if "retries.max" in keys:
        return "retry_exhaustion"
    if {"sql_must_match", "period_covers"} & keys:
        return "semantics"
    if status_error:
        return "execution"
    return "unclassified"


# --- Markdown 렌더 ------------------------------------------------------


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(lines)


def _arm_section(summary: dict[str, Any]) -> str:
    """프로파일(arm)별 판정. 프로파일이 하나뿐인 run 에서는 §2 와 같은 표라 싣지 않는다.

    **arm 을 쓴 run 에서는 여기가 먼저 읽히는 표다**(`plans/110` 가이드 ⑥ 판독 순서 1~2).
    사다리 단이 arm 마다 기대한 값인지, 그리고 두 단의 수치가 어떻게 갈리는지를 한 화면에서
    본다. 아래 §2 는 프로파일을 가로질러 접으므로 arm 비교에 쓰지 않는다.
    """
    breakdown = summary.get("by_profile") or []
    if len(breakdown) < 2:
        return ""
    armed = any(row.get("arm") for row in breakdown)
    lines = ["### 프로파일(arm)별 판정", ""]
    if armed:
        lines.append(
            "arm 은 전 시나리오에 덧씌운 측정 축이다. **`사다리 단` 칸이 arm 마다 기대한 "
            "값인지 먼저 본다** - 모든 arm 이 같은 단이면 주입이 먹지 않은 것이고 arm 비교는 "
            "성립하지 않는다. 아래 §2 군별 표는 프로파일을 가로질러 접으므로(같은 시나리오의 "
            "여러 arm 판정이 한 칸으로 합쳐지고 나쁜 쪽이 이긴다) **arm 비교에는 이 표를 쓴다.**"
        )
    else:
        lines.append("프로파일마다 서버를 따로 띄웠다. §2 는 이들을 합쳐서 센다.")
    lines.append("")
    lines.append(_table(
        ["프로파일", "arm", "사다리 단", "시나리오", "턴", "합격", "불합격", "오류",
         "수동", "불안정", "무효", "지연 p50(ms)"],
        [
            [
                row.get("name"), row.get("arm") or "-", row.get("tier") or "-",
                row.get("scenarios"), row.get("turns"),
                row.get("pass"), row.get("fail"), row.get("error"),
                row.get("manual"), row.get("flaky"), row.get("invalid"),
                (row.get("latency") or {}).get("p50"),
            ]
            for row in breakdown
        ],
    ))
    lines.append("")
    return "\n".join(lines)


def _unevaluated_section(summary: dict[str, Any]) -> str:
    """판정 계약 — 단언 미평가 턴을 분모에서 빼고 **사유별로 갈라** 보인다(`108·G-6` · D-241).

    ⚠ 여기 `timeout` 은 **제거 사유**이고, 6절 실패 분류의 `timeout` 은 **실패 유형**이다.
    같은 단어, 다른 축이다 - 그래서 두 절을 갈라 제목에 축을 적는다.
    """
    data = summary.get("unevaluated") or {}
    total = data.get("total_turns") or 0
    if not total:
        return ""
    by_reason = data.get("by_reason") or {}
    scored = data.get("scored") or 0
    rate = data.get("pass_rate")
    legacy_rate = data.get("pass_rate_legacy")

    def pct(value: Optional[float]) -> str:
        return "-" if value is None else f"{value * 100:.1f}%"

    lines = ["### 판정 분모 (턴 단위 · 제거 축)", ""]
    lines.append(
        "**단언이 평가되지 않은 턴은 기능 합격률의 분모에서 뺀다**(`108·G-6` ≡ `97·G-1` · "
        "2026-09-21 사용자 승인 · D-241). 사유 셋은 **합치지 않는다** - 귀속처가 다르다."
    )
    lines.append("")
    lines.append(_table(
        ["단계", "사유", "귀속처", "제거", "남은 분모"],
        _removal_ladder(total, by_reason, scored),
    ))
    lines.append("")
    lines.append(_table(
        ["지표", "값"],
        [
            ["기능 합격 턴", data.get("pass")],
            ["판정 분모(단언 평가된 턴)", scored],
            ["**기능 합격률(계약 적용)**", f"**{pct(rate)}**"],
            ["참고: 전 턴을 분모로 한 값", pct(legacy_rate)],
            ["제거 합 + 분모 == 전체", "일치" if data.get("ladder_ok") else "**불일치 - 이중 차감**"],
            ["역질문 차단 · 자동응답", clarify_blocked_label(data)],
            ["칸 없이 규칙으로 되살린 행(옛 run)", data.get("derived_rows")],
        ],
    ))
    lines.append("")
    lines.append(
        "- `timeout` 은 감추는 것이 아니다. 성능 축(3절 `성능 불합격`)과 단 무관 계수 지표가 "
        "그대로 드러낸다. 여기서 빼는 것은 **기능** 판정의 분모뿐이다."
    )
    lines.append(
        f"- **{clarify_blocked_label(data)}** - 자동응답이 켜진 run 은 역질문이 답을 받고 "
        "진행하므로 `clarify_blocked` 가 작게 나온다. 자동응답이 없는 run 과 이 수치를 "
        "**표기 없이** 나란히 놓으면 두 값이 비교되는 것처럼 오해된다(20260918 자동응답 "
        "221/380턴 대 20260914 스위프 자동응답 없음·역질문 차단 3,705/6,567). 표기 형태와 "
        "발동률 분모는 벤치와 같다."
    )
    lines.append("")
    lines.append(_table(
        ["군", "판정 분모", "합격", "합격률", "invalid", "timeout", "clarify_blocked", "전체 턴"],
        [
            [g, v["scored"], v["pass"], pct(v["pass_rate"]),
             v["invalid"], v["timeout"], v["clarify_blocked"], v["total"]]
            for g, v in (data.get("by_group") or {}).items()
        ] or [["(없음)", 0, 0, "-", 0, 0, 0, 0]],
    ))
    lines.append("")
    return "\n".join(lines)


def _removal_ladder(
    total: int, by_reason: dict[str, Any], scored: int
) -> list[list[Any]]:
    """제거 사다리 행. **남은 수를 단계마다 적어** 이중 차감이 눈에 보이게 한다."""
    rows: list[list[Any]] = [["0", "전체 턴", "-", "-", total]]
    remaining = total
    for step, reason in enumerate(UNEVALUATED_REASONS, start=1):
        removed = int(by_reason.get(reason) or 0)
        remaining -= removed
        rows.append([str(step), f"`{reason}`", UNEVALUATED_OWNER[reason], removed, remaining])
    rows.append(["=", "**판정 분모**", "단언이 평가된 턴", "-", scored])
    return rows


def render_markdown(summary: dict[str, Any], run_dir: Path, catalog: Optional[Catalog]) -> str:
    meta = summary.get("meta", {})
    out: list[str] = []
    add = out.append

    add(f"# 시나리오 실행 리포트 - {meta.get('run_id', '?')}")
    add("")
    if meta.get("mode") == "mock":
        add(
            "> **모의 실행(--mock)이다.** LLM도 DB도 호출하지 않았다. 기능 판정은 러너·단언기·"
            "리포트 배관이 도는지를 본 것이고 **시스템 품질의 근거가 아니다.**"
        )
        add("")
    degraded = _degraded_profiles(summary)
    if degraded:
        # O-c: **기준 단이 아닌 단으로 측정됐다**는 사실은 판정표 전체의 해석을 바꾼다.
        # run 20260915-131903 은 2단(`intent_orchestration`)으로 돌았는데 그 사실이
        # 1절 표의 한 칸에만 있었다. 기준 단은 D-225(2026-09-17)부터 3단 `semantic_router`다
        # (그 run 당시 기록 어휘는 1단 정본 기준의 `degraded_reason=flag_off`).
        add(
            "> **[경고] 기준 단이 아닌 실행 단으로 측정됐다.** "
            + " · ".join(
                f"`{p.get('name')}` = **{p.get('tier')}**(사유 `{p.get('degraded_reason')}`)"
                for p in degraded
            )
            + ". 기준 경로는 사다리 3단 `semantic_router`이고(D-225) 단마다 노드 구성·지연 특성이 "
            "다르다(`docs/21_orchestration_ladder.md`) - **아래 판정·지연을 기준 단의 성능으로 "
            "읽지 말 것.** 의도한 구성이면 그 사실을 run 기록에 남기고, 아니면 `.env` 플래그를 "
            "확인한 뒤 다시 측정한다."
        )
        add("")
    alarm_caveat = summary.get("tier2_alarm_caveat") or {}
    alarm_ids = set(alarm_caveat.get("scenario_ids") or [])
    if alarm_caveat:
        # plans/114 M-7 · 111 G-5 확정: 2단을 측정 arm 으로 쓰면 알람 왜곡을 고지한다.
        add(
            f"> **[{TIER2_ALARM_NOTE}]** 2단(`{INTENT_TIER}`) 프로파일 "
            + " · ".join(f"`{name}`" for name in alarm_caveat.get("profiles") or [])
            + " 이 섞였다. 2단 `intent_planner` 의 사전 처리 단락(존 선택·DB 매핑)이 알람 의도 "
            "교정을 건너뛰어 알람 질의가 `data_query` 로 가고 알람 테이블이 빠진다 - "
            "아래 알람 시나리오 "
            f"{len(alarm_ids)}건의 판정·지연은 알람 기능이 아니라 이 우회를 잰 값이다. "
            f"대상: {' · '.join(f'`{sid}`' for sid in sorted(alarm_ids)) or '없음'} "
            f"(판별: {alarm_caveat.get('criterion')})."
        )
        add("")
    optin_failed = optin_failure_profiles(summary)
    if optin_failed:
        # 권고 B: 강등이 **아니다** - 확정 단은 기준 단(3단)이고 그 수치는 유효하다. 다만 이
        # 프로파일의 시나리오는 한 건도 돌지 않았다(INVALID 제외). 위 「기준 단이 아님」 경고와
        # 구분되게 [안내]로 올리고, 못 잰 건수를 여기서 바로 말한다.
        excluded = optin_failure_excluded(summary, optin_failed)
        add(
            "> **[안내] 1단(deep_agent) opt-in 이 성립하지 않아 기준 단(3단 "
            "`semantic_router`)에서 측정됐다.** "
            + " · ".join(
                f"`{p.get('name')}`(사유 `{p.get('degraded_reason')}`)" for p in optin_failed
            )
            + f". 이 프로파일은 러너가 INVALID 로 제외해 **시나리오 {excluded}건을 재지 않았다** "
            "(사유·목록은 10절). 기준 단에서 측정된 값 자체는 유효하므로 아래 판정표를 "
            "그대로 읽되, **제외된 만큼은 커버리지가 아니다** - 1단을 의도했다면 "
            "오케스트레이터 서빙과 deepagents 설치를 확인한 뒤 다시 잰다."
        )
        add("")
    optin = [
        p for p in summary.get("profiles", []) or [] if p.get("tier") == OPTIN_TIER
    ]
    if optin:
        # 1단은 강등이 아니라 부가 경로 opt-in 이라 경고하지 않는다(D-225 ②). 다만 판정표가
        # 기준 단(3단)의 수치가 아니라는 사실은 남긴다 - 안 남기면 두 단의 run 이 섞여 비교된다.
        add(
            "> **[안내] 부가 경로(opt-in) 단으로 측정됐다.** "
            + " · ".join(f"`{p.get('name')}` = **{p.get('tier')}**" for p in optin)
            + ". 기준 경로는 사다리 3단 `semantic_router`다(D-225) - 아래 판정·지연은 "
            "부가 경로의 수치이므로 기준 단 run 과 섞어 비교하지 말 것."
        )
        add("")
    invalid = summary.get("invalid") or {}
    if invalid.get("over_threshold"):
        # T-e: 무효율이 5% 를 넘으면 **판정표를 읽기 전에** 이 사실을 본다.
        # run 20260915-131903 은 26.9% 였는데 리포트 어디에도 그 사실이 없어,
        # 「R3·R4 전건 error」가 제품 결함으로 읽혔다.
        add(
            f"> **[경고] 무효 턴 {invalid['count']}건 / {invalid['total_turns']}턴 "
            f"({invalid['ratio']:.1%}).** 러너 자신의 인증 실패로 측정이 성립하지 않은 구간이 "
            f"5%를 넘는다 - **이 run 은 회귀 비교 대상에서 제외한다.** "
            f"구간은 실행 순서 {invalid.get('first_index')}~{invalid.get('last_index')}번째 턴이고 "
            f"건수·사유는 10절에 있다. 아래 판정표는 무효 턴을 **뺀** 분모다."
        )
        add("")
    if summary.get("silent_wrong_total"):
        add(
            f"> **[최우선] 조용한 오답(silent_wrong) {summary['silent_wrong_total']}건 관측.** "
            "거부와 되묻기는 사용자가 알아차리지만 조용한 오답은 알아차리지 못한다. "
            "5절·6절에서 원인을 먼저 본다."
        )
        add("")

    # 1
    add("## 1. 실행 요약")
    add("")
    summary_rows: list[list[Any]] = [
        ["run_id", meta.get("run_id")],
        ["실행 성격", meta.get("mode")],
        ["대상 환경", meta.get("env")],
        ["LLM 프로바이더", meta.get("provider")],
        ["커밋", meta.get("commit")],
        ["작업 트리 dirty", meta.get("dirty")],
        ["시작 시각", meta.get("started_at")],
        ["반복", meta.get("repeat")],
        ["플랫폼", (meta.get("platform") or {}).get("os")],
        ["콘솔 인코딩", (meta.get("platform") or {}).get("encoding")],
        ["PYTHONUTF8", (meta.get("platform") or {}).get("pythonutf8")],
    ]
    # 재개한 run 만 싣는다(109·CS-17·CS-19③) - 새 run 의 표는 종전과 같다.
    attempts = meta.get("attempts") or []
    if len(attempts) > 1:
        summary_rows.append(["시도(재개)", f"{len(attempts)}회 - 커밋·시작 시각은 마지막 "
                                         "시도 값이다(`run.json` `meta.attempts`)"])
    if meta.get("provenance_mixed"):
        summary_rows.append(["출처 섞임", f"**{meta['provenance_mixed']}** - 한 `raw.jsonl` 에 "
                                        "다른 판의 결과가 함께 있다"])
    if meta.get("rerun_partial"):
        summary_rows.append(["1턴부터 다시 돈 멀티턴", f"{len(meta['rerun_partial'])}건 - 재개 때 "
                                                 "일부 턴만 끝나 있었다(`meta.rerun_partial`)"])
    add(_table(["항목", "값"], summary_rows))
    add("")
    add("### 프로파일별 기동 결과")
    add("")
    add(_table(
        ["프로파일", "arm", "포트", "유효", "사다리 단", "설정 에코", "사유"],
        [
            [
                p.get("name"), p.get("arm") or "-", p.get("port"),
                "O" if p.get("valid") else "X",
                p.get("tier"),
                {True: "일치", False: "불일치", None: "미확인"}.get(p.get("echo_ok")),
                "; ".join(p.get("reasons") or []) or "-",
            ]
            for p in summary.get("profiles", [])
        ] or [["(없음)", "-", "-", "-", "-", "-", "기동된 프로파일이 없다"]],
    ))
    add("")
    add(_arm_section(summary))

    # 2
    add("## 2. 기능 판정 요약")
    add("")
    add(_unevaluated_section(summary))
    add("### 시나리오별 판정 (턴을 접은 값)")
    add("")
    add("한 칸도 비우지 않는다. `불안정`은 합격으로도 불합격으로도 세지 않는다.")
    add(
        "`무효`는 러너 자신의 인증 실패로 **측정이 성립하지 않은** 시나리오다(T-c) - "
        "합격률의 분모에 넣지 않는다. 사유는 10절."
    )
    add(
        "이 표는 시나리오 단위다. **기능 합격률은 위 턴 단위 분모로 읽는다**(`108·G-6`) - "
        "여기 `합격`/`계`를 나누면 단언이 평가되지 않은 턴이 분모에 남는다."
    )
    add("")
    add(_table(
        ["군", "합격", "불합격", "오류", "수동 검토", "불안정", "무효", "계"],
        [
            [g, v["pass"], v["fail"], v["error"], v["manual"], v["flaky"],
             v.get("invalid", 0), v["total"]]
            for g, v in sorted(summary.get("groups", {}).items())
        ] or [["(없음)", 0, 0, 0, 0, 0, 0, 0]],
    ))
    add("")
    if alarm_ids:
        verdicts = summary.get("scenario_verdicts") or {}
        groups = sorted({str(verdicts[sid].get("group")) for sid in alarm_ids if sid in verdicts})
        add(f"※ {TIER2_ALARM_NOTE}: {' · '.join(sorted(alarm_ids))} - 이 시나리오가 섞인 군"
            f"({', '.join(groups)})의 수치를 알람 기능의 성적으로 읽지 말 것.")
        add("")

    # 3
    add("## 3. 성능 목표 대조")
    add("")
    add(_table(
        ["군", "목표(ms)", "n", "p50", "p95", "최댓값", "성능 합격", "성능 불합격", "비고"],
        [
            [
                g, v.get("target_ms"), v["latency"]["n"], v["latency"]["p50"],
                v["latency"]["p95"] if v["latency"]["p95"] is not None else "표본 부족",
                v["latency"]["max"], v["perf_pass"], v["perf_fail"],
                v["latency"]["note"] or "-",
            ]
            for g, v in sorted(summary.get("groups", {}).items())
        ] or [["(없음)", "-", 0, "-", "-", "-", 0, 0, "-"]],
    ))
    add("")

    # 4
    add("## 4. 계획서 커버리지")
    add("")
    coverage = summary.get("plans_coverage", {})
    add(_table(
        ["계획서", "시나리오 수", "실행", "합격"],
        [[f"plans/{k}", v["scenarios"], v["executed"], v["pass"]]
         for k, v in sorted(coverage.items(), key=lambda kv: int(kv[0]))]
        or [["(없음)", 0, 0, 0]],
    ))
    add("")
    add("시나리오 0건인 구현 기능은 `coverage_gap.md`(분석기 산출)에 사유와 함께 나온다.")
    add("")

    # 5
    add("## 5. 오용·실수·착각 대응(R군)")
    add("")
    misuse = summary.get("misuse", {})
    if not misuse:
        add("R군 실행 결과가 없다. (선택 범위에 R군이 포함되지 않았다)")
    else:
        add(_table(
            ["하위군", "시나리오", "대응 등급 분포", "금지 등급", "대조군 동반 실패"],
            [
                [
                    key, v["total"],
                    ", ".join(f"{m}:{c}" for m, c in sorted(v["mode_dist"].items())),
                    ", ".join(f"{m}:{c}" for m, c in sorted(v["forbidden"].items())) or "0",
                    v["control_broken"],
                ]
                for key, v in sorted(misuse.items())
            ],
        ))
        add("")
        for key, v in sorted(misuse.items()):
            for pair in v.get("control_broken_pairs", []):
                add(f"- **과잉 거부 의심** {key}: `{pair[0]}` 와 대조군 `{pair[1]}` 가 함께 깨졌다.")
    add("")

    # 6
    add("## 6. 불합격 상세 (실패 유형 축)")
    add("")
    add(
        "⚠ 여기 `kind` 의 **`timeout` 은 실패 유형**이고, 2절 「판정 분모」의 `timeout` 은 "
        "**제거 사유**다. 같은 단어, 다른 축이다 - 두 숫자를 같은 것으로 읽지 말 것."
    )
    add("")
    failures = summary.get("failures", [])
    if not failures:
        add("불합격 0건.")
    else:
        for item in failures:
            add(f"### {item['scenario_id']} (턴 {item.get('turn')}) - `{item['kind']}`")
            add("")
            if item["scenario_id"] in alarm_ids:
                add(f"- **{TIER2_ALARM_NOTE}**")
            if item.get("forbidden_mode"):
                add(f"- **금지 등급**: `{item['forbidden_mode']}`")
            for failed in item.get("failed_assertions", []):
                add(
                    f"- 단언 `{failed.get('key')}` 기대 `{failed.get('expected')}` "
                    f"실제 `{failed.get('actual')}`"
                )
            add(f"- 관련 계획서: {', '.join(f'plans/{p}' for p in item.get('plans', [])) or '-'}")
            add(f"- 재현: `python -m scripts.scenario --only {item['scenario_id']} --mock`")
            add("")

    # 7
    add("## 7. 노드별 지연 분해")
    add("")
    node_totals: dict[str, list[float]] = defaultdict(list)
    for row in load_rows(run_dir):
        for node, elapsed in (row.get("node_elapsed_ms") or {}).items():
            node_totals[node].append(float(elapsed))
    ranked = sorted(
        node_totals.items(), key=lambda kv: statistics.median(kv[1]), reverse=True
    )
    add(_table(
        ["노드", "표본", "중앙값(ms)", "합계(ms)"],
        [[node, len(vals), round(statistics.median(vals), 1), round(sum(vals), 1)]
         for node, vals in ranked[:15]] or [["(없음)", 0, "-", "-"]],
    ))
    add("")
    add("기전 설명 없는 지연은 신뢰하지 않는다. 표본 5건 미만 노드는 분석기가 순위에 올리지 않는다.")
    add("")

    # 8
    add("## 8. 직전 run 대비 회귀")
    add("")
    add(_regression_section(run_dir, summary))
    add("")

    # 9
    add("## 9. 수동 검토 목록")
    add("")
    manual_rows = [
        [row["scenario_id"], row.get("turn"), note]
        for row in load_rows(run_dir)
        for note in (row.get("manual_notes") or [])
    ]
    add(_table(["시나리오", "턴", "무엇을 눈으로 봐야 하는가"], manual_rows)
        if manual_rows else "수동 검토 항목 없음.")
    add("")

    # 10
    add("## 10. 제외·무효 목록")
    add("")
    add(_render_invalid(summary.get("invalid") or {}))
    add("")
    add("### 실행하지 않은 것(제외)")
    add("")
    skipped = summary.get("skipped", [])
    add(_table(
        ["시나리오", "턴", "사유"],
        [[s.get("scenario_id"), s.get("turn", "-"), s.get("reason")] for s in skipped],
    ) if skipped else "제외 0건.")
    add("")

    # 11
    add("## 11. 재현 명령")
    add("")
    add("```bash")
    add(f"python -m scripts.scenario --report {meta.get('run_id')}   # 이 리포트 재생성 (무과금)")
    add(f"python -m scripts.scenario --analyze {meta.get('run_id')}  # 분석·대안 수립 (무과금)")
    add("```")
    add("")
    return "\n".join(out) + "\n"


def _render_invalid(invalid: dict[str, Any]) -> str:
    """무효 턴 절 (T-c 수용 기준 - 「무효 턴 N건(사유)」).

    **건수와 구간을 함께 낸다.** 연속 구간은 원인이 하나(토큰 만료)라는 신호이고,
    흩어져 있으면 다른 사고다 - 둘을 같은 숫자로 보고하면 처방이 갈리지 않는다.
    """
    count = int(invalid.get("count") or 0)
    if not count:
        return "### 무효 턴(측정 미성립)\n\n무효 0건."
    lines = [
        "### 무효 턴(측정 미성립)",
        "",
        f"**{count}건 / {invalid.get('total_turns')}턴 ({float(invalid.get('ratio') or 0):.1%})** - "
        "러너 자신의 인증 실패(401/403)로 측정이 성립하지 않았다. "
        "판정표·실패 분류·대안 수립의 **분모에서 제외**했고, 여기에만 센다.",
        "",
        f"- 실행 순서 구간: **{invalid.get('first_index')}~{invalid.get('last_index')}번째 턴** "
        f"(첫 무효 시나리오 `{invalid.get('first_scenario')}`)",
        f"- 군별: {', '.join(f'{g}:{c}' for g, c in (invalid.get('by_group') or {}).items()) or '-'}",
        "",
        "재개하면 **이 턴들만** 다시 돈다(X-1 - `already()` 는 「기록됨」이 아니라 「성공」을 본다):",
        "",
        "```bash",
        "python -m scripts.scenario --resume <RUN_ID>",
        "```",
        "",
    ]
    lines.append(_table(
        ["순서", "시나리오", "턴", "반복", "사유"],
        [[t.get("index"), t.get("scenario_id"), t.get("turn"), t.get("repeat"),
          str(t.get("reason") or "")[:160]] for t in (invalid.get("turns") or [])[:50]],
    ))
    if count > 50:
        lines.append("")
        lines.append(f"(상위 50건만 표기 - 전체 {count}건은 `raw.jsonl` 의 `invalid_reason` 참조)")
    return "\n".join(lines)


def _regression_section(run_dir: Path, summary: dict[str, Any]) -> str:
    """직전 run 과 비교한다. **같은 프로파일·같은 환경**하고만 비교한다(§5.3).

    **무효율이 5% 를 넘는 run 은 비교하지 않는다**(T-e) - 판정이 바뀐 것인지 측정되지
    않은 것인지 구별되지 않는다.
    """
    invalid = summary.get("invalid") or {}
    if invalid.get("over_threshold"):
        return (
            f"회귀 비교 **제외** - 무효 턴 {invalid.get('count')}건"
            f"({float(invalid.get('ratio') or 0):.1%})으로 5% 상한을 넘겼다(T-e). "
            "무효 구간을 재개(`--resume`)로 복구한 뒤 다시 비교한다."
        )
    parent = run_dir.parent
    others = sorted(
        (p for p in parent.iterdir() if p.is_dir() and p.name < run_dir.name), reverse=True
    )
    meta = summary.get("meta", {})
    for candidate in others:
        prev = build_summary(candidate, None)
        prev_meta = prev.get("meta", {})
        if prev_meta.get("env") != meta.get("env") or prev_meta.get("mode") != meta.get("mode"):
            continue
        if (prev.get("invalid") or {}).get("over_threshold"):
            # 직전 run 이 무효투성이면 "판정이 바뀌었다"가 아니라 "저쪽이 측정되지 않았다"다.
            continue
        lines = [f"직전 비교 대상: `{candidate.name}` (같은 환경 `{meta.get('env')}` · 같은 성격 `{meta.get('mode')}`)", ""]
        current = summary.get("scenario_verdicts", {})
        previous = prev.get("scenario_verdicts", {})
        rows = [
            [sid, previous[sid]["verdict"], info["verdict"]]
            for sid, info in sorted(current.items())
            if sid in previous and previous[sid]["verdict"] != info["verdict"]
        ]
        if not rows:
            lines.append("판정이 바뀐 시나리오 없음.")
        else:
            lines.append(_table(["시나리오", "직전", "이번"], rows))
        if meta.get("repeat", 1) < 3:
            lines.append("")
            lines.append(
                "지연 회귀는 판정하지 않았다 - 반복 3회 미만이라 편차와 구별되지 않는다(`판정 불가`)."
            )
        return "\n".join(lines)
    return "비교 가능한 직전 run 이 없다 (같은 환경·같은 성격의 run 필요)."


def write_report(run_dir: Path, catalog: Optional[Catalog] = None) -> dict[str, Path]:
    """report.md 와 summary.json 을 쓴다."""
    summary = build_summary(run_dir, catalog)
    summary_path = run_dir / "summary.json"
    with utf8_open(summary_path, "w") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    report_path = run_dir / "report.md"
    with utf8_open(report_path, "w") as handle:
        handle.write(render_markdown(summary, run_dir, catalog))
    return {"summary": summary_path, "report": report_path}
