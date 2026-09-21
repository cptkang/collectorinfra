"""판정 계약 — 단언 미평가 턴을 분모에서 빼고 사유를 합치지 않는다.

`plans/110` §3.1 `108·G-6` ≡ `97·G-1` · 108 §5.2e · 97 §11.5 · D-241(d9 등재).
2026-09-21 사용자 승인("판정 계약 수용하라").

고정하는 계약은 넷이다.
  1. 사유 3종(`invalid` · `timeout` · `clarify_blocked`)이 **각각** 도출된다.
  2. **합치지 않는다** — 제거 사다리가 `제거 합 + 분모 == 전체` 를 항상 만족한다.
  3. `invalid` 를 **두 번 빼지 않는다**(T-c·D-218 이 이미 그 이름으로 빼고 있다).
  4. **자동응답으로 진행된 턴은 `clarify_blocked` 가 아니다** — 단언이 평가됐기 때문이다.
     20260918 run 에서 자동응답은 380턴 중 221턴(58.2%)에 발동했다. 그 턴들을 빼면
     분모가 절반 넘게 날아가 규칙의 의미가 달라진다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import INVALID_VERDICT
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.report import (
    UNEVALUATED_REASONS,
    build_summary,
    row_unevaluated,
    scored_rows,
    unevaluated_reason,
    unevaluated_summary,
    valid_rows,
)
from scripts.scenario.runner import RunConfig, execute


def _row(**kw) -> dict:
    base = {
        "group": "T", "scenario_id": "T-01", "turn": 1, "repeat": 0,
        "func_verdict": "pass", "response_mode": "answer", "error": None,
        "forbidden_mode": None, "failed_assertions": [],
    }
    base.update(kw)
    return base


# --- 사유 3종이 각각 도출된다 ---------------------------------------------

def test_invalid은_하네스_과실로_도출된다() -> None:
    assert unevaluated_reason(_row(func_verdict=INVALID_VERDICT)) == "invalid"
    # T-c 이전 적재분(판정값은 error 인데 401 인 행)도 같은 사유다.
    assert unevaluated_reason(_row(func_verdict="error", error="http 401")) == "invalid"


def test_timeout은_한국어_문구와_504와_hang에서_도출된다() -> None:
    """서버 문구가 한국어라 `"timeout" in error` 만으로는 한 건도 잡히지 않았다(199건 실측)."""
    assert unevaluated_reason(
        _row(func_verdict="fail", error="처리 시간이 초과되었습니다")) == "timeout"
    assert unevaluated_reason(
        _row(func_verdict="error", error="HTTP 504 Gateway Timeout")) == "timeout"
    assert unevaluated_reason(
        _row(func_verdict="fail", forbidden_mode="hang")) == "timeout"


def test_clarify_blocked는_역질문으로_끝난_턴이다() -> None:
    row = _row(func_verdict="fail", response_mode="clarify")
    assert unevaluated_reason(row, expected_question=False) == "clarify_blocked"


def test_평가된_턴은_None이다() -> None:
    assert unevaluated_reason(_row()) is None
    assert unevaluated_reason(_row(func_verdict="fail")) is None      # 진짜 불합격은 분모에 남는다
    assert unevaluated_reason(_row(func_verdict="manual")) is None


# --- ④ 자동응답으로 진행된 턴을 잘못 잡지 않는다 ---------------------------

def test_자동응답으로_진행된_턴은_clarify_blocked가_아니다() -> None:
    """러너가 자동 응답 뒤의 관측치로 `obs` 를 갈아끼운다.

    그래서 최종 `response_mode` 가 `clarify` 가 아니고, 단언은 평가된다.
    """
    row = _row(func_verdict="fail", response_mode="answer",
               auto_answers=[{"kind": "zone", "answer": "gongjon"}])
    assert unevaluated_reason(row) is None


def test_역질문을_기대한_턴은_평가된_것이다() -> None:
    """R3-03(존 선택 역질문)·I-01~I-06(폼필 선택지)은 역질문 자체가 판정 대상이다."""
    row = _row(func_verdict="fail", response_mode="clarify")
    assert unevaluated_reason(row, expected_question=True) is None


def test_역질문으로_끝났어도_합격이면_분모에_남는다() -> None:
    row = _row(func_verdict="pass", response_mode="clarify")
    assert unevaluated_reason(row, expected_question=False) is None


def test_옛_run_폴백은_실패_단언_키로_기대_역질문을_추정한다() -> None:
    """칸이 없던 run 에서도 '역질문이 판정 대상이었던' 턴을 분모에 남긴다."""
    judged = _row(func_verdict="fail", response_mode="clarify",
                  failed_assertions=[{"key": "clarification.options_len",
                                      "expected": 2, "actual": 86}])
    assert unevaluated_reason(judged) is None
    blocked = _row(func_verdict="fail", response_mode="clarify",
                   failed_assertions=[{"key": "row_count", "expected": 10, "actual": 0}])
    assert unevaluated_reason(blocked) == "clarify_blocked"


# --- ② 합치지 않는다 · ③ 이중 차감 없음 -----------------------------------

def test_제거_사다리는_합이_맞는다() -> None:
    rows = [
        _row(scenario_id="A", func_verdict=INVALID_VERDICT, unevaluated_reason="invalid"),
        _row(scenario_id="B", func_verdict="fail", error="처리 시간이 초과",
             unevaluated_reason="timeout"),
        _row(scenario_id="C", func_verdict="fail", response_mode="clarify",
             unevaluated_reason="clarify_blocked"),
        _row(scenario_id="D", unevaluated_reason=None),
        _row(scenario_id="E", func_verdict="fail", unevaluated_reason=None),
    ]
    summary = unevaluated_summary(rows)
    assert summary["by_reason"] == {"invalid": 1, "timeout": 1, "clarify_blocked": 1}
    assert summary["scored"] == 2
    assert summary["removed"] + summary["scored"] == summary["total_turns"] == 5
    assert summary["ladder_ok"] is True


def test_사유를_합치지_않는다() -> None:
    """세 사유가 각각 칸을 갖는다 - 합산 하나로 뭉개면 귀속처가 사라진다."""
    summary = unevaluated_summary([
        _row(func_verdict=INVALID_VERDICT, unevaluated_reason="invalid"),
        _row(func_verdict="fail", unevaluated_reason="timeout"),
    ])
    assert set(summary["by_reason"]) == set(UNEVALUATED_REASONS)
    assert summary["by_reason"]["clarify_blocked"] == 0


def test_invalid은_두_번_빠지지_않는다() -> None:
    """`valid_rows`(T-c)가 이미 빼고 있다 - `scored_rows` 가 또 빼도 분모는 같아야 한다."""
    rows = [
        _row(scenario_id="A", func_verdict=INVALID_VERDICT, unevaluated_reason="invalid"),
        _row(scenario_id="B", unevaluated_reason=None),
    ]
    assert len(valid_rows(rows)) == 1
    assert len(scored_rows(rows)) == 1
    summary = unevaluated_summary(rows)
    # 제거 1건 + 분모 1건 = 전체 2건. 401 턴이 invalid 로도 timeout 으로도 세어지지 않는다.
    assert summary["removed"] == 1 and summary["scored"] == 1


# --- ⑤ 분모 제외 전후 합격률 ----------------------------------------------

def test_분모_제외가_합격률을_바꾼다() -> None:
    """108 교차 대조와 같은 형태 - 지배 사유가 타임아웃이면 크게, 아니면 거의 안 움직인다."""
    rows = [_row(scenario_id="P", unevaluated_reason=None)]
    rows += [_row(scenario_id=f"T{i}", func_verdict="fail", error="처리 시간이 초과",
                  unevaluated_reason="timeout") for i in range(3)]
    summary = unevaluated_summary(rows)
    assert summary["pass"] == 1
    assert summary["scored"] == 1
    assert summary["pass_rate"] == 1.0          # 계약 적용
    assert summary["pass_rate_legacy"] == 0.25  # 전 턴을 분모로 하면 4분의 1


def test_분모가_0이면_비율을_만들지_않는다() -> None:
    summary = unevaluated_summary([
        _row(func_verdict=INVALID_VERDICT, unevaluated_reason="invalid")
    ])
    assert summary["pass_rate"] is None


# --- ① 칸이 산출물에 실린다 -----------------------------------------------

def _catalog() -> Catalog:
    return Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[Scenario(id="T-01", group="T", plans=[110], title="t", env="both",
                            turns=[Turn({"query": "q"}, {})])],
        profiles={"baseline": {}},
    )


def test_raw_jsonl_에_unevaluated_reason_칸이_실린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """코드 grep 이 아니라 **산출물**로 확인한다."""
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(_catalog(), RunConfig(mode="mock", env="sandbox", only=["T-01"]))
    raw = Path(summary["out_dir"]) / "raw.jsonl"
    rows = [json.loads(line)
            for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "행이 적재되지 않았다"
    for row in rows:
        assert "unevaluated_reason" in row, row.keys()
    # 칸이 1차 출처다 - 재도출하지 않는다.
    assert [row_unevaluated(row) for row in rows] == [row["unevaluated_reason"] for row in rows]


def test_summary에_판정_분모가_실린다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(_catalog(), RunConfig(mode="mock", env="sandbox", only=["T-01"]))
    built = build_summary(Path(summary["out_dir"]), _catalog())
    data = built["unevaluated"]
    assert data["ladder_ok"] is True
    assert set(data["by_reason"]) == set(UNEVALUATED_REASONS)
    assert data["derived_rows"] == 0        # 칸이 있는 새 run 이다
    assert "by_group" in data


# --- ① "최종" response_mode 를 실제 자동응답 경로로 확인한다 -----------------
#
# 러너는 `_answer_questions` 가 돌려준 **답변 뒤 관측치**로 `obs` 를 갈아끼우고
# (`runner._run_once`: `obs = _answer_questions(...)`), `evaluate_turn` 이 그 최종 `obs` 로
# `response_mode` 를 정한다. 아래 세 테스트는 그 사실을 **행으로** 확인한다 - 추론이 아니라.

class _FakeClient:
    """`_run_once` 가 쓰는 클라이언트 최소 구현. 준 순서대로 관측치를 돌려준다."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.sent: list = []

    def send(self, endpoint: str, payload: dict, upload=None):
        self.sent.append((endpoint, dict(payload)))
        return self.responses.pop(0)

    def download(self, *_a, **_kw):
        return None


def _zone_question():
    from scripts.scenario.assertions import Observation

    # `kind` 는 `clarify._ZONE_KINDS` 의 값이어야 자동 응답 경로를 탄다.
    return Observation(
        http_status=200, status="clarification",
        clarification={
            "kind": "zone_select",
            "question": "조회할 존이 지정되지 않았습니다.",
            "original_query": "전체 서버 수",
            "options": [{"db_id": "polestar_b0", "label": "은행존"},
                        {"db_id": "polestar_cm_gp", "label": "공동존(김포)"}],
        },
    )


def _run_turn(tmp_path: Path, turn: Turn, responses: list) -> dict:
    from scripts.scenario.runner import RawLog

    scenario = Scenario(id="T-01", group="T", plans=[110], title="t", env="closed",
                        turns=[turn])
    catalog = Catalog(groups={"T": Group("T", "t", 60000)}, scenarios=[scenario],
                      profiles={"baseline": {}})
    raw = RawLog(tmp_path / "raw.jsonl")
    runner_mod._run_once(
        catalog, RunConfig(mode="run"), {"run_id": "r", "env": "closed", "mode": "run"},
        "baseline", scenario, 0, _FakeClient(responses), raw, tmp_path, [],
        preference=["polestar_cm_gp"],
    )
    lines = (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0])


def test_자동응답이_답해서_진행된_턴은_분모에_남는다(tmp_path: Path) -> None:
    """역질문이 떴지만 최종 관측치는 완료다 - 단언이 평가됐으므로 빼지 않는다."""
    from scripts.scenario.assertions import Observation

    row = _run_turn(
        tmp_path,
        Turn({"query": "전체 서버 수"}, {"status": "completed"}),
        [_zone_question(),
         Observation(http_status=200, status="completed", row_count=3)],
    )
    assert row["response_mode"] == "answer"          # **최종** 값이다
    assert row["auto_answers"], "자동응답이 기록되지 않았다"
    assert row["unevaluated_reason"] is None


def test_자동응답이_먹지_않아_역질문으로_끝나면_clarify_blocked다(tmp_path: Path) -> None:
    """같은 역질문이 되풀이되면 러너가 멈춘다 - 그 턴은 단언에 도달하지 못했다.

    **자동응답이 기록돼 있어도** 역질문으로 끝났으면 제외 대상이다. 두 칸은 독립이다.
    """
    row = _run_turn(
        tmp_path,
        Turn({"query": "전체 서버 수"}, {"status": "completed"}),
        [_zone_question(), _zone_question()],
    )
    assert row["response_mode"] == "clarify"
    assert row["auto_answers"], "자동응답 시도는 기록돼야 한다"
    assert row["unevaluated_reason"] == "clarify_blocked"


def test_역질문을_기대한_턴은_자동응답하지_않고_분모에_남는다(tmp_path: Path) -> None:
    row = _run_turn(
        tmp_path,
        Turn({"query": "전체 서버 수"}, {"status": "clarification"}),
        [_zone_question()],
    )
    assert row["response_mode"] == "clarify"
    assert not row.get("auto_answers")
    assert row["unevaluated_reason"] is None


# --- ② 벤치와 통일한 표기 형태 --------------------------------------------

def test_역질문_차단_표기는_자동응답을_함께_적는다() -> None:
    """자동응답 유무에 따라 같은 숫자가 정반대로 읽힌다 - 표기가 그걸 막는다."""
    from scripts.scenario.report import clarify_blocked_label

    label = clarify_blocked_label({
        "by_reason": {"clarify_blocked": 12}, "auto_answer_turns": 221, "total_turns": 380,
    })
    assert label == "역질문 차단 12건 (자동응답 221건 · 발동률 58%)"


def test_자동응답이_없던_run은_없음으로_적는다() -> None:
    from scripts.scenario.report import clarify_blocked_label

    label = clarify_blocked_label({
        "by_reason": {"clarify_blocked": 3705}, "auto_answer_turns": 0, "total_turns": 6567,
    })
    assert label == "역질문 차단 3705건 (자동응답 없음)"


def test_발동률_분모는_벤치와_같다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """벤치 `RunHealth.auto_answered_turns` 와 같은 정의 - `auto_answers` 가 실린 턴 수 / 전체 턴."""
    rows = [
        _row(scenario_id="A", auto_answers=[{"kind": "zone"}], unevaluated_reason=None),
        _row(scenario_id="B", unevaluated_reason=None),
    ]
    summary = unevaluated_summary(rows)
    assert summary["auto_answer_turns"] == 1
    assert summary["total_turns"] == 2


# ── 옛 run 폴백의 `status` 구멍 (2026-09-21 · 109/d9 실측 지적) ────────────────
#
# run 20260914 실측: 역질문으로 끝나고 불합격인 3,453턴 중 2,311턴이 `status`
# (기대 completed · 실제 clarification) 실패였다. `status` 를 "역질문 기대" 신호로
# 보면 그 턴들이 분모에 남아, 이 런을 폐기하게 만든 바로 그 턴들이 다시 섞인다.


def _clarify_row(*failed):
    return {
        "func_verdict": "fail",
        "response_mode": "clarify",
        "failed_assertions": list(failed),
    }


def test_status_실패는_역질문_기대가_아니다():
    """기대가 clarification 이었다면 status 단언은 통과했을 것이다."""
    row = _clarify_row({"key": "status", "expected": "completed",
                        "actual": "clarification"})
    assert unevaluated_reason(row) == "clarify_blocked"


def test_기대값이_clarification_인_status_는_역질문_기대다():
    row = _clarify_row({"key": "status", "expected": "clarification",
                        "actual": "clarification"})
    assert unevaluated_reason(row) is None


def test_clarification_단언_실패는_여전히_분모에_남는다():
    """역질문이 정답인데 모양이 틀린 턴 - 결함이 숨으면 안 된다."""
    row = _clarify_row({"key": "clarification.options_len", "expected": 2,
                        "actual": 5})
    assert unevaluated_reason(row) is None


def test_status와_clarification_이_함께_실패하면_역질문_기대다():
    row = _clarify_row(
        {"key": "status", "expected": "completed", "actual": "clarification"},
        {"key": "clarification.options_len", "expected": 2, "actual": 5},
    )
    assert unevaluated_reason(row) is None
