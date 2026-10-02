"""DiagnosisResult 원시 출력 보존 확장 검증 (Plan 02 §3·§6).

holmesgpt 0.36.0 실측 구조(ToolCallResult.result: StructuredToolResult)로 픽스처를 만들어
to_tool_records가 **원시 출력**(get_stringified_data)·상태·에러·리턴코드를 보존하는지 확인한다.
"""

from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, to_tool_records


def _tool_call(tool_name, data=None, status=StructuredToolResultStatus.SUCCESS, error=None, return_code=None):
    result = StructuredToolResult(status=status, data=data, error=error, return_code=return_code)
    return ToolCallResult(
        tool_call_id="tc-1",
        tool_name=tool_name,
        description=f"{tool_name} 호출",
        result=result,
    )


def test_result_defaults_include_tool_outputs():
    r = DiagnosisResult(answer="ok")
    assert r.tool_calls == []
    assert r.tool_outputs == []
    assert r.total_tokens == 0


def test_to_tool_records_preserves_raw_string_output():
    raw = "kernel: Out of memory: Killed process 12345 (java)"
    records = to_tool_records([_tool_call("journalctl", data=raw)])
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, ToolCallRecord)
    assert rec.tool_name == "journalctl"
    assert rec.status == "success"
    assert raw in rec.output  # 원시 출력 보존(시그니처 매칭 입력)
    assert rec.error is None


def test_to_tool_records_preserves_structured_data():
    records = to_tool_records([_tool_call("polestar_metric_trend", data={"mem_util": 95, "n": 3})])
    # dict 데이터는 문자열화돼 보존된다.
    assert "mem_util" in records[0].output


def test_to_tool_records_preserves_error_and_return_code():
    records = to_tool_records(
        [_tool_call("bash", data=None, status=StructuredToolResultStatus.ERROR, error="boom", return_code=1)]
    )
    rec = records[0]
    assert rec.status == "error"
    assert rec.error == "boom"
    assert rec.return_code == 1


def test_to_tool_records_empty_and_none():
    assert to_tool_records(None) == []
    assert to_tool_records([]) == []
