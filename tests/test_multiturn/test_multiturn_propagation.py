"""Plan 50 멀티턴 컨텍스트 전파 + process_query 라우팅 테스트.

- M1: intent_planner 압축 맥락 주입(_build_context_block / _llm_decompose).
- M2: data_query DB 승계(_apply_db_succession / _has_new_location_db_signal).
- M4: process_query subagent — db_id/hostname 해소 + 프로세스 API 재사용.

회귀: 첫 턴/단일 의도/맥락 없음 경로는 기존 동작을 유지해야 한다.
"""

from __future__ import annotations

import pytest

from src.config import load_config
from src.orchestration.intent_planner import _build_context_block, _llm_decompose
from src.orchestration.process_query import (
    _infer_alarm_kind,
    _resolve_db_id,
    _resolve_hostname,
    run_process_query,
)
from src.orchestration.subagents import (
    _apply_db_succession,
    _has_new_location_db_signal,
    _inject_demonstrative_hostname,
    _refers_to_specific_server,
)


# ──────────────────────────────────────────────
# M1 — intent_planner 압축 맥락 주입
# ──────────────────────────────────────────────

class TestContextBlock:
    def test_first_turn_no_block(self):
        """첫 턴(맥락 None)이면 빈 블록."""
        assert _build_context_block(None) == ""

    def test_turn_count_one_no_block(self):
        """turn_count<=1이면 빈 블록(회귀 방지)."""
        assert _build_context_block({"turn_count": 1}) == ""

    _SIGNAL_CTX = {
        "turn_count": 2,
        "previous_location": "김포 운영",
        "previous_db_ids": ["polestar_cm_gp"],
        "previous_entities": [{"field": "hostname", "value": "###"}],
        "previous_results_summary": "1건 조회됨",
    }

    def test_follow_up_block_contains_signals(self):
        """지시어 후속 턴이면 위치·DB·서버 식별자가 블록에 포함된다."""
        block = _build_context_block(self._SIGNAL_CTX, "해당 서버의 OS 확인")
        assert "김포 운영" in block
        assert "polestar_cm_gp" in block
        assert "hostname=###" in block
        assert "해당 서버" in block  # 지시어 해소 규칙 포함

    def test_non_demonstrative_follow_up_omits_entities(self):
        """지시어 없는 후속 턴은 직전 서버 엔티티를 주입하지 않는다 (D-153 후속1).

        previous_entities는 직전 턴이 대량 조회였으면 상한 샘플일 뿐 스코프가 아니다 —
        "대상 미명시 → 직전 값 보존" 규칙과 결합되면 새 전량 후속 질의가 샘플 서버
        몇 대로 축소된다(2026-08-04 라이브 실측: gp/yd 전량 조회 후 "OS 종류 확인"
        후속이 4개 서버만 반환).
        """
        block = _build_context_block(
            self._SIGNAL_CTX, "OS 종류, OS 버전 및 OS패치버전 확인"
        )
        assert "hostname=###" not in block
        assert "직전 대상 서버/장비" not in block
        # 위치/DB 승계 신호는 유지(DB 라우팅 승계는 정상 동작)
        assert "김포 운영" in block
        assert "polestar_cm_gp" in block


class _FakeLLM:
    """ainvoke로 고정 JSON을 반환하는 가짜 LLM. 마지막 입력 메시지를 보관한다."""

    def __init__(self, response_json: str):
        self._json = response_json
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages

        class _R:
            content = self._json

        return _R()


class TestLlmDecomposeInjection:
    async def test_context_block_injected_into_human_message(self):
        """후속 턴이면 압축 맥락이 HumanMessage 앞에 주입된다."""
        llm = _FakeLLM(
            '{"clarification_needed": null, "tasks": '
            '[{"task_id":"t1","agent":"process_query","sub_query":"김포 ### 프로세스",'
            '"depends_on":[],"input_from":[],"order":1}]}'
        )
        cfg = load_config()
        ctx = {
            "turn_count": 2,
            "previous_location": "김포 운영",
            "previous_db_ids": ["polestar_cm_gp"],
            "previous_entities": [{"field": "hostname", "value": "###"}],
            "previous_results_summary": "1건",
        }
        out = await _llm_decompose(
            llm, "해당 서버 프로세스 보여줘", cfg, conversation_context=ctx
        )
        # human 메시지에 맥락 블록 + 원 질의가 함께 들어갔는지
        human = llm.last_messages[-1].content
        assert "이전 대화 맥락" in human
        assert "해당 서버 프로세스 보여줘" in human
        assert out["tasks"][0]["agent"] == "process_query"

    async def test_first_turn_no_context_block(self):
        """첫 턴(맥락 없음)이면 원 질의만 전달(회귀)."""
        llm = _FakeLLM(
            '{"tasks": [{"task_id":"t1","agent":"data_query","sub_query":"서버",'
            '"depends_on":[],"input_from":[],"order":1}]}'
        )
        cfg = load_config()
        await _llm_decompose(llm, "서버 목록", cfg, conversation_context=None)
        human = llm.last_messages[-1].content
        assert human == "서버 목록"
        assert "이전 대화 맥락" not in human


# ──────────────────────────────────────────────
# M2 — data_query DB 승계
# ──────────────────────────────────────────────

class TestDbSuccession:
    def test_new_location_signal_detected(self):
        assert _has_new_location_db_signal("여의도 폴스타 서버")
        assert _has_new_location_db_signal("polestar에서 조회")
        assert not _has_new_location_db_signal("해당 서버의 프로세스")

    def test_succession_applied_when_no_new_signal(self):
        """새 신호 없고 직전 DB 있으면 승계되어 단일 후보로 좁혀진다."""
        targets = [
            {"db_id": "polestar_b0", "relevance_score": 0.5},
            {"db_id": "polestar_cm_gp", "relevance_score": 0.5},
        ]
        ctx = {"previous_db_ids": ["polestar_cm_gp"]}
        out, ok = _apply_db_succession(
            targets, "해당 서버의 데이터", ctx, ["polestar_b0", "polestar_cm_gp"]
        )
        assert ok is True
        assert [t["db_id"] for t in out] == ["polestar_cm_gp"]

    def test_succession_skipped_with_new_signal(self):
        """이번 턴에 새 위치 신호가 있으면 승계하지 않는다(사용자 새 의도 최우선)."""
        targets = [{"db_id": "polestar_cm_yd", "relevance_score": 0.9}]
        ctx = {"previous_db_ids": ["polestar_cm_gp"]}
        out, ok = _apply_db_succession(
            targets, "여의도 폴스타 서버 보여줘", ctx, ["polestar_cm_gp", "polestar_cm_yd"]
        )
        assert ok is False
        assert out == targets

    def test_succession_skipped_without_context(self):
        """맥락 없으면(첫 턴) 승계 없음(회귀)."""
        targets = [{"db_id": "polestar_cm_gp", "relevance_score": 0.9}]
        out, ok = _apply_db_succession(targets, "서버 목록", None, ["polestar_cm_gp"])
        assert ok is False
        assert out == targets

    def test_succession_skipped_when_already_converged(self):
        """분류가 이미 직전 DB 1개로 수렴했으면 승계 불필요."""
        targets = [{"db_id": "polestar_cm_gp", "relevance_score": 0.9}]
        ctx = {"previous_db_ids": ["polestar_cm_gp"]}
        out, ok = _apply_db_succession(
            targets, "해당 서버", ctx, ["polestar_cm_gp", "polestar_cm_yd"]
        )
        assert ok is False


# ──────────────────────────────────────────────
# 지시어("해당 서버") data/alarm_query hostname 결정적 주입 (D-056 후속)
# ──────────────────────────────────────────────

class TestDemonstrativeHostnameInjection:
    def test_refers_to_specific_server(self):
        assert _refers_to_specific_server("해당 서버는 특이사항이 없는가?")
        assert _refers_to_specific_server("지난 한 달 해당 서버의 알람 분석")
        assert _refers_to_specific_server("그 장비의 알람")
        # 전체 조회 신호가 있으면 서버 스코프 강제 안 함
        assert not _refers_to_specific_server("전체 서버의 알람")
        assert not _refers_to_specific_server("모든 서버 CPU")
        # 지시어 없는 일반 질의
        assert not _refers_to_specific_server("CPU 80% 이상 서버 목록")

    def _isolated(self, original, filters, prev_entities):
        return {
            "parsed_requirements": {
                "original_query": original,
                "filter_conditions": filters,
            },
            "conversation_context": {"previous_entities": prev_entities},
        }

    def test_injects_hostname_for_demonstrative(self):
        """지시어 후속 + 직전 서버 있음 → hostname 필터 주입."""
        iso = self._isolated(
            "해당 서버 알람 분석", [], [{"field": "hostname", "value": "###"}]
        )
        parsed = _inject_demonstrative_hostname(iso)
        assert {"field": "hostname", "op": "=", "value": "###"} in parsed["filter_conditions"]

    def test_skips_when_filter_already_has_server(self):
        """concrete 서버 필터가 있으면 주입 안 함(원본 유지)."""
        iso = self._isolated(
            "webdb01 서버 알람",
            [{"field": "hostname", "op": "=", "value": "webdb01"}],
            [{"field": "hostname", "value": "###"}],
        )
        parsed = _inject_demonstrative_hostname(iso)
        assert {c["value"] for c in parsed["filter_conditions"]} == {"webdb01"}

    def test_skips_for_all_servers_query(self):
        """'전체 서버' 등 전역 조회는 서버 필터 강제 금지."""
        iso = self._isolated(
            "전체 서버 알람", [], [{"field": "hostname", "value": "###"}]
        )
        assert _inject_demonstrative_hostname(iso)["filter_conditions"] == []

    def test_skips_when_no_previous_server(self):
        iso = self._isolated("해당 서버 알람", [], [])
        assert _inject_demonstrative_hostname(iso)["filter_conditions"] == []

    def test_skips_demonstrative_previous_value(self):
        """previous_entities 값이 지시어/플레이스홀더면 주입 안 함(오염 방지)."""
        iso = self._isolated(
            "해당 서버 알람", [], [{"field": "hostname", "value": "해당 서버"}]
        )
        assert _inject_demonstrative_hostname(iso)["filter_conditions"] == []


# ──────────────────────────────────────────────
# M4 — process_query subagent
# ──────────────────────────────────────────────

class _ProcCfg:
    """get_process_api_base_url만 갖춘 가짜 alarm 설정."""

    process_top_n = 5

    def __init__(self, mapping):
        self._m = mapping

    def get_process_api_base_url(self, db_id):
        return self._m.get(db_id)


class _AppCfgStub:
    def __init__(self, alarm, active_db_ids):
        self.alarm = alarm

        class _Multi:
            def get_active_db_ids(self_inner):
                return active_db_ids

        self.multi_db = _Multi()


class TestProcessQueryResolution:
    def test_infer_alarm_kind(self):
        assert _infer_alarm_kind("메모리 점유 높은 프로세스") == "memory"
        assert _infer_alarm_kind("현재 프로세스 리스트") == "cpu"

    def test_resolve_db_id_prefers_mapped_base_url(self):
        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp", "polestar_b0"])
        task = {"db_ids": ["polestar_b0", "polestar_cm_gp"]}
        # b0은 base_url 없음 → base_url 있는 gp 선택
        assert _resolve_db_id(task, {}, "프로세스", cfg) == "polestar_cm_gp"

    def test_resolve_db_id_from_previous(self):
        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp"])
        isolated = {"conversation_context": {"previous_db_ids": ["polestar_cm_gp"]}}
        assert _resolve_db_id({}, isolated, "해당 서버 프로세스", cfg) == "polestar_cm_gp"

    def test_resolve_db_id_from_location(self):
        alarm = _ProcCfg({"polestar_cm_yd": "http://yd"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_yd"])
        assert _resolve_db_id({}, {}, "여의도 폴스타 프로세스", cfg) == "polestar_cm_yd"

    def test_resolve_hostname_from_previous_entities(self):
        isolated = {
            "conversation_context": {
                "previous_entities": [{"field": "hostname", "value": "###"}]
            }
        }
        assert _resolve_hostname(isolated) == "###"

    def test_resolve_hostname_this_turn_filter_priority(self):
        isolated = {
            "parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "value": "new-host"}]
            },
            "conversation_context": {
                "previous_entities": [{"field": "hostname", "value": "###"}]
            },
        }
        assert _resolve_hostname(isolated) == "new-host"

    def test_resolve_hostname_accepts_korean_field_names(self):
        """LLM이 한글 필드명(서버명/장비명 등)으로 추출해도 서버 식별자로 인정한다."""
        for field in ("서버명", "장비명", "호스트명", "서버"):
            isolated = {
                "parsed_requirements": {
                    "filter_conditions": [{"field": field, "value": "webdb01"}]
                }
            }
            assert _resolve_hostname(isolated) == "webdb01", field


class TestProcessQueryHandler:
    async def test_missing_db_id_graceful(self):
        """대상 DB 미식별 시 SQL 폴백 없이 안내 메시지(SQL0204N 방지)."""
        alarm = _ProcCfg({})
        cfg = _AppCfgStub(alarm, [])
        out = await run_process_query(
            {"sub_query": "프로세스 보여줘"}, {}, llm=None, app_config=cfg
        )
        assert out["query_results"] == []
        assert "DB" in out["organized_data"]["summary"]

    async def test_no_base_url_graceful(self):
        """base_url 없는 db_id면 graceful 안내."""
        alarm = _ProcCfg({})  # 매핑 없음
        cfg = _AppCfgStub(alarm, ["polestar_b0"])
        isolated = {
            "conversation_context": {
                "previous_db_ids": ["polestar_b0"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
            }
        }
        out = await run_process_query(
            {"sub_query": "해당 서버 프로세스"}, isolated, llm=None, app_config=cfg
        )
        assert out["query_results"] == []
        assert "프로세스 API" in out["organized_data"]["summary"]

    async def test_success_path_masked_rows(self, monkeypatch):
        """정상 경로: 프로세스 API 결과를 상위 N으로 선별·마스킹하여 반환."""
        from noise_gate.infrastructure.polestar_process_api import ProcessApiResult

        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp"])
        isolated = {
            "conversation_context": {
                "previous_db_ids": ["polestar_cm_gp"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
            }
        }

        async def _fake_list(self, db_id, hostname):
            return ProcessApiResult(
                captured_at=None,
                processes=[
                    {"name": "java", "pid": 1, "p100cpu": 90.0, "pmem": 10.0,
                     "args": "java -Dpassword=secret123 App"},
                    {"name": "nginx", "pid": 2, "p100cpu": 5.0, "pmem": 2.0, "args": "nginx"},
                ],
            )

        monkeypatch.setattr(
            "noise_gate.infrastructure.polestar_process_api.PolestarProcessApiClient.list_by_hostname",
            _fake_list,
        )
        out = await run_process_query(
            {"sub_query": "해당 서버 현재 프로세스"}, isolated, llm=None, app_config=cfg
        )
        rows = out["query_results"]
        assert len(rows) == 2
        # CPU 내림차순 — java 먼저
        assert rows[0]["name"] == "java"
        # 민감정보 마스킹 (평문 secret 노출 금지)
        assert "secret123" not in rows[0]["args"]
        assert out["target_db_ids"] == ["polestar_cm_gp"]

    async def test_chat_topN_csv_full_split(self, monkeypatch):
        """채팅 표시는 상위 N(=2)으로 제한, query_results(CSV)는 전체 보존(D-047)."""
        from noise_gate.infrastructure.polestar_process_api import ProcessApiResult

        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        alarm.process_top_n = 2  # 상위 2개만 채팅 표시
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp"])
        isolated = {
            "conversation_context": {
                "previous_db_ids": ["polestar_cm_gp"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
            }
        }

        procs = [
            {"name": f"p{i}", "pid": i, "p100cpu": float(i), "pmem": 1.0, "args": f"p{i}"}
            for i in range(5)
        ]

        async def _fake_list(self, db_id, hostname):
            return ProcessApiResult(captured_at=None, processes=procs)

        monkeypatch.setattr(
            "noise_gate.infrastructure.polestar_process_api.PolestarProcessApiClient.list_by_hostname",
            _fake_list,
        )
        out = await run_process_query(
            {"sub_query": "해당 서버 현재 프로세스"}, isolated, llm=None, app_config=cfg
        )
        # 채팅 표시(organized_data.rows)는 상위 2개
        assert len(out["organized_data"]["rows"]) == 2
        # CSV/row_count용 query_results는 전체 5개
        assert len(out["query_results"]) == 5
        # 정렬 내림차순(CPU 큰 순) — 첫 행은 p4
        assert out["organized_data"]["rows"][0]["name"] == "p4"
        assert out["process_query"]["total_count"] == 5
        assert out["process_query"]["shown_count"] == 2
        # 전체>표시이면 요약에 CSV 안내
        assert "CSV" in out["organized_data"]["summary"]
