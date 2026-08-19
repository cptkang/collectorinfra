"""실패 트레이스 JSONL 덤프 테스트 (D-141).

실 파일에 쓰고 다시 읽어 검증한다 — mock 통과는 프로덕션 동작을 보장하지 않는다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from src.observability import trace_collector as tc
from src.observability import trace_writer as tw
from src.observability.levels import TraceLevel


@pytest.fixture(autouse=True)
def _clean():
    tc.reset_all()
    yield
    tc.reset_all()


def _trace_dir(root: Path) -> Path:
    return root / "logs" / "trace" / datetime.now().strftime("%Y-%m-%d")


def _seed(request_id: str = "req1", *, steps: int = 3) -> None:
    tc.start_request(request_id, thread_id="t1", user_query="CPU 사용률 조회")
    for i in range(steps):
        tc.record_step(
            request_id,
            tc.TraceStep(
                step=i + 1, node=f"node{i}", level=TraceLevel.INFO,
                event="node.exit", elapsed_ms=1.5,
            ),
        )


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestDumpTrigger:
    def test_healthy_request_writes_nothing(self, tmp_path):
        """정상 요청은 파일을 만들지 않는다 (정상 경로 디스크 비용 0)."""
        _seed()
        state = {"routing_intent": "data_query", "query_results": [{"a": 1}], "retry_count": 0}

        written = tw.flush_if_failed("req1", state, project_root=tmp_path)

        assert written is None
        assert not _trace_dir(tmp_path).exists()

    def test_exception_writes_error_trace(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "error_message": "boom",
                 "query_results": [{"a": 1}], "retry_count": 0}

        written = tw.flush_if_failed("req1", state, project_root=tmp_path)

        assert written is not None and written.exists()
        assert written.name == "req1.jsonl"
        assert _read_lines(written)[0]["severity"] == "error"

    def test_zero_rows_writes_warn_trace(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        written = tw.flush_if_failed("req1", state, project_root=tmp_path)

        header = _read_lines(written)[0]
        assert header["severity"] == "warn"
        assert header["triggers"] == ["zero_rows"]

    def test_retry_writes_warn_even_when_successful(self, tmp_path):
        """재시도 후 성공해도 원인 추적용으로 남긴다."""
        _seed()
        state = {"routing_intent": "data_query", "query_results": [{"a": 1}], "retry_count": 2}

        header = _read_lines(tw.flush_if_failed("req1", state, project_root=tmp_path))[0]
        assert header["severity"] == "warn" and "retry" in header["triggers"]

    def test_all_triggers_recorded_highest_severity_wins(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "error_message": "boom",
                 "query_results": [], "retry_count": 3}

        header = _read_lines(tw.flush_if_failed("req1", state, project_root=tmp_path))[0]
        assert header["severity"] == "error"
        assert set(header["triggers"]) == {"exception", "zero_rows", "retry"}


class TestFormat:
    def test_header_is_first_line(self, tmp_path):
        _seed(steps=2)
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        lines = _read_lines(tw.flush_if_failed("req1", state, project_root=tmp_path))

        assert lines[0]["kind"] == "summary"
        assert lines[0]["request_id"] == "req1"
        assert lines[0]["thread_id"] == "t1"
        assert lines[0]["user_query"] == "CPU 사용률 조회"
        assert lines[0]["node_path"] == ["node0", "node1"]
        assert isinstance(lines[0]["total_ms"], (int, float))
        assert len(lines) == 3  # 헤더 + 단계 2

    def test_step_line_fields(self, tmp_path):
        _seed(steps=1)
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        step = _read_lines(tw.flush_if_failed("req1", state, project_root=tmp_path))[1]

        for key in ("ts", "request_id", "step", "node", "level", "event", "elapsed_ms"):
            assert key in step, f"필수 필드 누락: {key}"

    def test_sql_is_hashed_not_stored(self, tmp_path):
        """SQL 원문 대신 해시를 담는다 — logs/sql/과 중복 저장하지 않는다."""
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="query_executor", level=TraceLevel.ERROR, event="node.exception",
            elapsed_ms=1.0, reason="db2_error",
            payload={"sql": "SELECT secret_col FROM t", "error": "SQLCODE=-206"},
        ))
        state = {"routing_intent": "data_query", "error_message": "x", "query_results": [], "retry_count": 0}

        step = _read_lines(tw.flush_if_failed("req1", state, project_root=tmp_path))[1]

        assert "sql" not in step["payload"]
        assert len(step["payload"]["sql_hash"]) == 16
        assert "SELECT secret_col" not in json.dumps(step, ensure_ascii=False)


class TestSecurity:
    def test_file_mode_is_600(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        written = tw.flush_if_failed("req1", state, project_root=tmp_path)

        assert oct(written.stat().st_mode)[-3:] == "600"

    @pytest.mark.parametrize("secret", [
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "eyJhbGciOiJIUzI1NiJ9.payload",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    ])
    def test_secret_values_are_masked(self, tmp_path, secret):
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="node.exception",
            elapsed_ms=1.0, reason="auth", payload={"token": secret},
        ))
        state = {"routing_intent": "data_query", "error_message": "x", "query_results": [], "retry_count": 0}

        raw = tw.flush_if_failed("req1", state, project_root=tmp_path).read_text(encoding="utf-8")

        assert secret not in raw

    def test_sensitive_key_names_are_masked(self, tmp_path):
        """키 이름이 민감하면 값 패턴과 무관하게 가린다."""
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="node.exception",
            elapsed_ms=1.0, reason="auth",
            payload={"password": "hunter2", "api_key": "plain", "hostname": "web01"},
        ))
        state = {"routing_intent": "data_query", "error_message": "x", "query_results": [], "retry_count": 0}

        raw = tw.flush_if_failed("req1", state, project_root=tmp_path).read_text(encoding="utf-8")

        assert "hunter2" not in raw and "plain" not in raw
        assert "web01" in raw  # 민감하지 않은 값은 보존

    def test_connection_string_credentials_masked(self, tmp_path):
        """DB 에러 메시지에 흔한 URL 자격증명을 가린다."""
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="node.exception", elapsed_ms=1.0,
            reason="conn",
            payload={"error": "could not connect: postgres://admin:s3cr3tpw@db.internal:5432/x"},
        ))
        state = {"routing_intent": "data_query", "error_message": "x", "query_results": [], "retry_count": 0}

        raw = tw.flush_if_failed("req1", state, project_root=tmp_path).read_text(encoding="utf-8")

        assert "s3cr3tpw" not in raw
        assert "db.internal" in raw  # 진단에 필요한 호스트는 남긴다


class TestIsolation:
    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        """쓰기 실패가 요청 처리로 전파되지 않는다 (0600 생성은 os.open 경유)."""
        _seed()
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}
        monkeypatch.setattr(
            "src.observability.trace_writer.os.open",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )

        assert tw.flush_if_failed("req1", state, project_root=tmp_path) is None
        assert tc.active_request_count() == 0  # 실패해도 버퍼는 해제

    def test_unknown_request_is_noop(self, tmp_path):
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        assert tw.flush_if_failed("ghost", state, project_root=tmp_path) is None

    def test_buffer_released_after_flush(self, tmp_path):
        """덤프 후 버퍼가 해제된다 (누수 방지)."""
        _seed()
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        tw.flush_if_failed("req1", state, project_root=tmp_path)

        assert tc.active_request_count() == 0

    def test_buffer_released_even_when_healthy(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "query_results": [{"a": 1}], "retry_count": 0}

        tw.flush_if_failed("req1", state, project_root=tmp_path)

        assert tc.active_request_count() == 0

    def test_disabled_flag_skips_dump(self, tmp_path):
        _seed()
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        written = tw.flush_if_failed("req1", state, project_root=tmp_path, enabled=False)

        assert written is None
        assert not _trace_dir(tmp_path).exists()


class TestNestedPayloadSanitization:
    """중첩 구조 안의 민감값·SQL도 top-level과 **같은 규칙**으로 처리되어야 한다.

    Prove-It: top-level `sql`은 키가 `sql_hash`로 바뀌는데 중첩된 `sql`은 키가 그대로여서,
    로그 소비자가 `payload.outer.sql`을 SQL 원문으로 오인할 수 있다(2026-08-19 실측).
    """

    def _dump(self, tmp_path, payload):
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="node.exception",
            elapsed_ms=1.0, reason="x", payload=payload,
        ))
        state = {"routing_intent": "data_query", "error_message": "x",
                 "query_results": [], "retry_count": 0}
        path = tw.flush_if_failed("req1", state, project_root=tmp_path)
        return _read_lines(path)[1]["payload"]

    def test_nested_sql_key_becomes_sql_hash(self, tmp_path):
        out = self._dump(tmp_path, {"outer": {"sql": "SELECT secret FROM t"}})

        assert "sql" not in out["outer"], "중첩 sql 키가 그대로면 원문으로 오인된다"
        assert len(out["outer"]["sql_hash"]) == 16

    def test_nested_secret_values_masked(self, tmp_path):
        out = self._dump(tmp_path, {"outer": {"password": "hunter2"},
                                    "items": [{"api_key": "plain"}]})

        assert out["outer"]["password"] == "********"
        assert out["items"][0]["api_key"] == "********"

    def test_nested_sql_original_never_written(self, tmp_path):
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="e", elapsed_ms=1.0, reason="x",
            payload={"a": {"b": {"sql": "SELECT topsecret FROM t"}}},
        ))
        state = {"routing_intent": "data_query", "error_message": "x",
                 "query_results": [], "retry_count": 0}
        raw = tw.flush_if_failed("req1", state, project_root=tmp_path).read_text(encoding="utf-8")

        assert "topsecret" not in raw

    def test_non_dict_payload_values_pass_through(self, tmp_path):
        """숫자·불리언 등은 변형 없이 보존된다."""
        out = self._dump(tmp_path, {"row_count": 42, "ok": True, "ratio": 1.5})

        assert out == {"row_count": 42, "ok": True, "ratio": 1.5}


class TestInlineSecretMasking:
    """문장 **안에** 섞인 자격증명도 가려야 한다.

    Prove-It: 값 전체가 시크릿일 때만 마스킹돼, `"auth failed with sk-..."` 같은
    에러 메시지의 키가 트레이스에 그대로 남았다(2026-08-19 실측). 자격증명이 에러 메시지에
    섞이는 것은 흔한 일이고, 트레이스는 실패 시 반드시 기록되므로 노출 경로가 된다.
    """

    def _payload_value(self, tmp_path, payload):
        tc.start_request("req1")
        tc.record_step("req1", tc.TraceStep(
            step=1, node="n", level=TraceLevel.ERROR, event="node.exception",
            elapsed_ms=1.0, reason="x", payload=payload,
        ))
        state = {"routing_intent": "data_query", "error_message": "x",
                 "query_results": [], "retry_count": 0}
        path = tw.flush_if_failed("req1", state, project_root=tmp_path)
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("secret,message", [
        ("sk-abcdefghijklmnopqrstuvwxyz123456", "auth failed with {}"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "invalid token {} rejected"),
        ("AKIAIOSFODNN7EXAMPLE", "using {} for upload"),
        ("ghp_abcdefghijklmnopqrstuvwxyz1234567890", "clone failed: {}"),
        ("glpat-abcdefghijklmnopqrst", "gitlab auth {} expired"),
    ])
    def test_secret_inside_sentence_is_masked(self, tmp_path, secret, message):
        raw = self._payload_value(tmp_path, {"error": message.format(secret)})

        assert secret not in raw, f"문장 안 시크릿이 노출됨: {secret[:12]}..."

    def test_surrounding_context_is_preserved(self, tmp_path):
        """진단에 필요한 문맥은 남긴다 — 통째로 지우지 않는다."""
        raw = self._payload_value(
            tmp_path, {"error": "auth failed with sk-abcdefghijklmnopqrstuvwxyz123456 on host db01"}
        )

        assert "auth failed with" in raw and "on host db01" in raw

    @pytest.mark.parametrize("benign", [
        "SELECT hostname FROM server WHERE id = 12345",
        "connection timeout after 30s on polestar_b0",
        "SQLCODE=-206: BAD_COL is not valid",
        "sk-",
        "eyJ",
    ])
    def test_benign_text_is_not_over_masked(self, tmp_path, benign):
        """오탐으로 정상 진단 정보를 지우면 트레이스의 존재 이유가 사라진다."""
        raw = self._payload_value(tmp_path, {"error": benign})

        assert benign in raw
