"""조사 결과·해결방안 **소비** (Plan 78 W4 / Plan 80 WU-19 · SPEC M8).

> **v6에서 조치 권고 *생성*은 폐기됐다**(78 §0.3). `sre_agent/domain/remediation.py`가
> 결정적 표 기반으로 이미 생성한다 — 78이 또 만들면 권고가 **두 벌**이 된다.
> 78은 **소비·표시만** 한다.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.nodes.fault_diagnosis import (
    _append_briefing_extras,
    _briefing_to_text,
    _extract_diagnosis_text,
)

REMEDIATION = [
    "디스크 정리 스크립트 실행 (위험도 low·신뢰도 high) — 근거: disk_full 시그니처",
    "[검토 필요] DB 재기동 (위험도 high·신뢰도 medium) — 근거: conn_pool 시그니처",
]


# ──────────────────────────────────────────────
# 소비 (W4-1)
# ──────────────────────────────────────────────

def test_briefing_six_elements_are_rendered():
    """브리핑 6요소를 `_BRIEFING_ORDER`로 조립한다 — 기존 자산 재사용(사본 금지)."""
    text = _briefing_to_text({
        "timeline": "12:00 알람", "bottleneck": "CPU", "cause": "배치 폭주",
        "evidence": "top 출력", "recommendation": REMEDIATION, "limitation": "샘플 1회",
    })
    for label in ("[타임라인]", "[병목]", "[원인]", "[근거]", "[권고]", "[한계]"):
        assert label in text


def test_recommendation_list_is_flattened_not_stringified():
    """권고는 `list[str]`로 온다 — 문자열화하면 `['...']`가 그대로 노출된다."""
    text = _briefing_to_text({"recommendation": REMEDIATION})
    assert "['" not in text
    assert REMEDIATION[0] in text and REMEDIATION[1] in text


def test_answer_field_no_longer_swallows_remediation():
    """★ 자연어 필드가 있어도 **권고가 유실되지 않는다**.

    종전에는 `answer`가 있으면 곧장 return해 `Remediation` 목록이 통째로 사라졌다 —
    `sre_agent`가 위험도 3등급까지 계산해 보낸 것을 사용자가 못 보는 상태였다.
    """
    out = _extract_diagnosis_text({
        "answer": "CPU 포화가 원인입니다.",
        "briefing": {"recommendation": REMEDIATION, "limitation": "디스크 미수집"},
    })
    assert "CPU 포화가 원인입니다." in out
    assert REMEDIATION[0] in out
    assert "디스크 미수집" in out


def test_no_duplication_when_already_present():
    """자연어에 이미 담겨 있으면 두 번 보이지 않는다."""
    answer = f"조치: {REMEDIATION[0]}"
    out = _extract_diagnosis_text({
        "answer": answer, "briefing": {"recommendation": [REMEDIATION[0]]},
    })
    assert out.count(REMEDIATION[0]) == 1


def test_briefing_only_path_still_works():
    """자연어 필드가 없으면 종전대로 브리핑을 조립한다(회귀 0)."""
    out = _extract_diagnosis_text({"briefing": {"cause": "배치 폭주"}})
    assert "[원인] 배치 폭주" in out


def test_non_dict_briefing_is_passthrough():
    assert _append_briefing_extras("본문", None) == "본문"
    assert _append_briefing_extras("본문", "문자열 브리핑") == "본문"


# ──────────────────────────────────────────────
# 위험도·신뢰도 무손실 (W4-2)
# ──────────────────────────────────────────────

def test_risk_and_confidence_are_preserved_verbatim():
    """★ 위험도·신뢰도를 **깎지 않고 그대로** 노출한다."""
    out = _extract_diagnosis_text({"briefing": {"recommendation": REMEDIATION}})
    assert "위험도 low·신뢰도 high" in out
    assert "위험도 high·신뢰도 medium" in out


def test_review_only_prefix_is_not_stripped():
    """★ "[검토 필요]"로 강등된 항목을 **정식 권고처럼 보이게 하지 않는다**.

    고위험×저신뢰 강등은 `remediation.py`의 안전 장치다 — 접두를 떼면 그 장치가 무력화된다.
    """
    out = _extract_diagnosis_text({"briefing": {"recommendation": REMEDIATION}})
    assert "[검토 필요] DB 재기동" in out


def test_consumer_does_not_rewrite_recommendation_text():
    """78은 권고를 **문자열로만** 다룬다 — 재작성·요약·재정렬하지 않는다(W4-3)."""
    src = pathlib.Path("src/nodes/fault_diagnosis.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_append_briefing_extras"
    )
    called = {
        n.func.attr for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    # 문자열 결합(join)·공백 판정(strip)만 쓴다. sort·replace 같은 변형이 없어야 한다.
    assert not ({"sort", "sorted", "replace", "format"} & called)


# ──────────────────────────────────────────────
# 생성 부재 · 실행 경로 부재 (W4-3 · D-003)
# ──────────────────────────────────────────────

def test_core_does_not_generate_remediation():
    """★ 권고 **생성** 코드를 78이 만들지 않는다(중복 부재 단언 — 78 §0.3).

    `remediation.py`가 이미 위험도 3등급·고위험×저신뢰 강등을 결정적으로 계산한다.
    본체가 또 만들면 권고가 두 벌이 되어 어느 쪽이 정본인지 알 수 없다.
    """
    for rel in ("src/nodes/fault_diagnosis.py", "src/orchestration/process_query.py"):
        tree = ast.parse(pathlib.Path(rel).read_text())
        names = {
            n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assert not any(
            "remediat" in n.lower() or "recommend" in n.lower() for n in names
        ), f"{rel}이 권고를 생성한다"


def test_no_execution_path_for_change_commands():
    """★ 실행 경로 부재 — 78은 권고를 **문자열로만** 다룬다(D-003 · P9).

    *"인젝션이 성공하는 것을 전제하고도 피해가 없으려면 실행될 명령이 아예 없어야 한다."*
    """
    tree = ast.parse(pathlib.Path("src/nodes/fault_diagnosis.py").read_text())
    modules = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    imported = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    forbidden = {"subprocess", "os", "shlex", "pty", "paramiko", "fabric"}
    assert not (forbidden & (modules | imported)), "실행 가능한 표면이 들어왔다"

    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not ({"exec", "eval", "compile", "system"} & called)


def test_sre_agent_is_never_imported():
    """D-118 — 통신은 MCP JSON 계약뿐. 패키지 import 0."""
    tree = ast.parse(pathlib.Path("src/nodes/fault_diagnosis.py").read_text())
    modules = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not any(m.startswith("sre_agent") for m in modules)


# ──────────────────────────────────────────────
# 부분 결과 표시 (W4-4)
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "poll,expect_empty",
    [
        ({}, True),
        ({"briefing": {}}, True),
        ({"answer": "   "}, True),
        ({"answer": "정상"}, False),
    ],
)
def test_empty_results_are_reported_not_faked(poll, expect_empty):
    """빈 결과를 그럴듯하게 채우지 않는다 — 호출부가 사유를 안내한다(침묵 금지)."""
    assert (_extract_diagnosis_text(poll) == "") is expect_empty


def test_stub_briefing_surfaces_its_message():
    """조사 미실행(스텁)도 사유가 드러난다."""
    assert "조사 미실행" in _briefing_to_text({"stub": True, "message": "조사 미실행(스텁)"})
