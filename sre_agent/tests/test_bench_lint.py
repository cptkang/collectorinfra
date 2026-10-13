"""plans/151 W4 — 세계 린트 단위 테스트(`investigation_bench/lint.py`).

임시 세계 디렉터리(개발 1 · 홀드아웃 1)를 만들고 규칙 하나씩 어겨 그 규칙이 걸리는지 본다.
홀드아웃 토큰 검사는 임시 소스 경로(`source_root`)를 주입해 실제 `sre_agent/sre_agent/`를
건드리지 않는다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from investigation_bench import lint


def _world(wid: str, split: str, host: str, comp: str, alarm: str, cond: str,
           cite: str) -> dict[str, Any]:
    return {
        "id": wid,
        "split": split,
        "title": "린트 테스트 세계",
        "event": {"dbId": "polestar", "serverName": host, "hostname": host,
                  "ipAddress": "192.0.2.10", "alarmId": f"{wid}-1",
                  "alarmName": alarm, "conditionLog": cond},
        "label": {"root_entity": {"host": host, "component": comp}, "layer": "app",
                  "kind": "cpu_runaway", "change_related": False,
                  "aliases": {"kind": ["CPU 폭주", "프로세스 폭주"]}},
        "key_evidence": [
            {"tool": "polestar_metric_trend", "match": {"kind": "cpu"}, "cite": [cite]}],
        "expect": {"abstain": False},
        "hosts": [host],
        "tools": {"polestar_metric_trend": [
            {"match": {"kind": "cpu"}, "reply": {"rows": [{"value": cite}], "row_count": 1}}]},
        "script": {"model": [
            {"call": "polestar_metric_trend",
             "args": {"source": "polestar", "server_name": host, "kind": "cpu"}},
            {"answer": f"{host} CPU 폭주 (polestar_metric_trend)"}]},
    }


_DEV = _world("D01", "dev", "lt-dev-app01", "lt_dev_job",
              "연산 부하 높음 감지됨", "[처리기 점유 구간 지속 관측]", "97.1")
_HOLD = _world("H01", "holdout", "lt-hold-bat02", "lt_hold_job",
               "배치 서버 지연 신호", "[작업 큐 적체 길어짐 확인]", "88.4")


def _lint(tmp_path: Path, dev: dict[str, Any] | None = None, hold: dict[str, Any] | None = None,
          source: dict[str, str] | None = None) -> lint.LintReport:
    root = tmp_path / "worlds"
    for split, data in (("dev", dev or _DEV), ("holdout", hold or _HOLD)):
        (root / split).mkdir(parents=True, exist_ok=True)
        (root / split / f"{data['id']}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for name, text in (source or {}).items():
        (src / name).write_text(text, encoding="utf-8")
    return lint.lint(root, source_root=src)


def _rules(report: lint.LintReport, level: str = lint.ERROR) -> set[str]:
    return {f.rule for f in report.findings if f.level == level}


def test_baseline_clean(tmp_path: Path) -> None:
    report = _lint(tmp_path)
    assert report.findings == [], [str(f) for f in report.findings]


def test_leak_alias_in_event(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["event"]["alarmName"] = "프로세스 폭주 감지"
    assert _rules(_lint(tmp_path, dev=dev)) == {"leak"}


def test_leak_is_normalized(tmp_path: Path) -> None:
    """공백·대소문자를 달리해도 누수다(채점기 `kind_forms`와 같은 정규화)."""
    dev = copy.deepcopy(_DEV)
    dev["event"]["conditionLog"] = "[cpu폭주 의심]"
    assert "leak" in _rules(_lint(tmp_path, dev=dev))


def test_script_is_not_leak_checked(tmp_path: Path) -> None:
    """대본 답변에는 정답 문구가 있어도 된다(가짜 LLM 전용)."""
    assert "leak" not in _rules(_lint(tmp_path))


def test_alias_shortage(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["label"]["aliases"]["kind"] = ["CPU 폭주"]
    assert _rules(_lint(tmp_path, dev=dev)) == {"label"}


@pytest.mark.parametrize("field", ["layer", "kind", "change_related"])
def test_label_missing_field(tmp_path: Path, field: str) -> None:
    dev = copy.deepcopy(_DEV)
    del dev["label"][field]
    assert "label" in _rules(_lint(tmp_path, dev=dev))


def test_key_evidence_not_in_reply(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["key_evidence"][0]["cite"] = ["55.5"]
    assert _rules(_lint(tmp_path, dev=dev)) == {"evidence"}


def test_key_evidence_match_unreachable(tmp_path: Path) -> None:
    """핵심 증거의 match로 닿는 응답 규칙이 없으면 실패."""
    dev = copy.deepcopy(_DEV)
    dev["key_evidence"][0]["match"] = {"kind": "memory"}
    assert "evidence" in _rules(_lint(tmp_path, dev=dev))


def test_key_evidence_error_reply(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["tools"]["polestar_metric_trend"][0]["reply"] = {"error": "조회 실패 97.1", "inject": True}
    assert "evidence" in _rules(_lint(tmp_path, dev=dev))


def test_hosts_missing_event_host(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["hosts"] = ["lt-dev-other"]
    assert "hosts" in _rules(_lint(tmp_path, dev=dev))


def test_schema_unknown_tool_in_script(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["script"]["model"].insert(0, {"call": "polestar_no_such_tool", "args": {}})
    assert "schema" in _rules(_lint(tmp_path, dev=dev))


def test_layout_split_mismatch(tmp_path: Path) -> None:
    hold = copy.deepcopy(_HOLD)
    hold["split"] = "dev"
    assert "layout" in _rules(_lint(tmp_path, hold=hold))


def test_layout_duplicate_id(tmp_path: Path) -> None:
    root = tmp_path / "worlds"
    _lint(tmp_path)
    dup = copy.deepcopy(_DEV)
    (root / "dev" / "D01b.yaml").write_text(yaml.safe_dump(dup, allow_unicode=True),
                                            encoding="utf-8")
    report = lint.lint(root, source_root=tmp_path / "src")
    assert any(f.rule == "layout" and "중복" in f.message for f in report.errors)


def test_phrase_dup_whole_phrase(tmp_path: Path) -> None:
    hold = copy.deepcopy(_HOLD)
    hold["event"]["alarmName"] = _DEV["event"]["alarmName"]
    report = _lint(tmp_path, hold=hold)
    assert _rules(report) == {"phrase_dup"}
    assert lint.duplicate_phrases(report.worlds["H01"], report.worlds["D01"])


def test_phrase_dup_shingle(tmp_path: Path) -> None:
    """문구 전체가 달라도 연속 4토큰을 공유하면 중복이다."""
    hold = copy.deepcopy(_HOLD)
    reply = hold["tools"]["polestar_metric_trend"][0]["reply"]
    reply["note"] = "야간에 처리기 점유 구간 지속 관측 기록"
    assert "phrase_dup" in _rules(_lint(tmp_path, hold=hold))


def test_phrase_dup_ignores_identifiers_and_short(tmp_path: Path) -> None:
    """한 토큰 값(식별자·열거값)과 짧은 문구는 문구 단위가 아니다."""
    hold = copy.deepcopy(_HOLD)
    hold["event"]["dbId"] = "polestar"
    hold["tools"]["polestar_metric_trend"][0]["reply"]["unit"] = "%"
    hold["tools"]["polestar_metric_trend"][0]["reply"]["note"] = "연산 부하"  # 공백 제외 4자
    assert "phrase_dup" not in _rules(_lint(tmp_path, hold=hold))


def test_holdout_token_in_source(tmp_path: Path) -> None:
    report = _lint(tmp_path, source={"leaky.py": 'HINT = "LT_HOLD_JOB"\n'})
    hits = [f for f in report.errors if f.rule == "holdout_token"]
    assert hits and "leaky.py" in hits[0].message


def test_dev_token_in_source_is_fine(tmp_path: Path) -> None:
    """개발 세계 토큰은 대상이 아니다(홀드아웃만 판정 전용)."""
    assert "holdout_token" not in _rules(_lint(tmp_path, source={"ok.py": 'X = "lt-dev-app01"\n'}))


@pytest.mark.parametrize("text", ["10.20.30.40", "900101-1234567", "010-1234-5678",
                                  "ops@example.com", "4111-1111-1111-1111"])
def test_pii_warns_only(tmp_path: Path, text: str) -> None:
    dev = copy.deepcopy(_DEV)
    dev["tools"]["polestar_metric_trend"][0]["reply"]["memo"] = f"연락 {text}"
    report = _lint(tmp_path, dev=dev)
    assert report.ok, [str(f) for f in report.errors]
    assert _rules(report, lint.WARN) == {"pii"}


def test_doc_network_is_not_pii(tmp_path: Path) -> None:
    dev = copy.deepcopy(_DEV)
    dev["tools"]["polestar_metric_trend"][0]["reply"]["peer"] = "198.51.100.7 · 203.0.113.9"
    assert _lint(tmp_path, dev=dev).findings == []


def test_main_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dev = copy.deepcopy(_DEV)
    dev["event"]["alarmName"] = "CPU 폭주 경보"
    _lint(tmp_path, dev=dev)
    assert lint.main(tmp_path / "worlds") == 1
    out = capsys.readouterr().out
    assert "[error] leak · D01" in out and "오류 1" in out
