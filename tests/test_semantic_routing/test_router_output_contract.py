"""라우터 응답 **처리** 계약 테스트 (Plan 79 트랙 E-1·E-2 · SPEC-router-output-contract).

`test_router_prompt_structure.py`가 *프롬프트 구조*를 검사한다면, 이 파일은 *응답을 받은 뒤의
처리*를 검사한다. 실 LLM 없이 대역 LLM으로 응답을 주입한다(D-127 무관).

검증 대상(SPEC §Success Criteria):
    S1 미상 intent가 data_query로 강등되고 사유가 남는다            (E-1 · F1)
    S2 허용 집합이 fault_diagnosis_enabled에 종속된다               (E-1 · 옵트인 계약 C-A)
    S3 허용 집합 == 프롬프트 「출력 형식」 정의 클래스 집합          (E-1 · 사본 금지 D-053)
    S4 한 항목의 형식 오류가 나머지 후보를 죽이지 않는다             (E-2 · F2 ★)
    S5 탈락 사유가 구조화되어 남는다                                 (E-2 · 침묵 폴백 금지)
    S7 MIN_RELEVANCE_SCORE가 잠정값임이 코드에 고정되고 값은 0.3     (S-3 · WU-01)
    S8 프롬프트 렌더 결과가 변경 전과 바이트 동일                    (R1 — S-1 측정 기준 보호)

배경(Plan 79 §3.6): 종전에는 ① intent가 허용 집합과 대조되지 않아 환각 intent가 조용히 DB 조회로
낙하했고 ② `float()`가 raw로 걸려 한 항목의 형식 오류가 `except`에 삼켜져 **분류 전체**가
단일 DB로 폴백했다 — 임계와 무관하게 멀티 DB 불변식(§1.1)이 깨지는 경로였다.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re

import pytest

from src.routing.domain_config import DB_DOMAINS

# ⚠ `from src.routing import semantic_router`는 **함수**를 준다 — 패키지 `__init__`이 동명의
# 노드 함수를 re-export해 모듈 속성을 덮는다(실측). 모듈이 필요하므로 importlib로 가져온다.
sr = importlib.import_module("src.routing.semantic_router")


# ─────────────────────────────── 대역 ───────────────────────────────

class _FakeResponse:
    def __init__(self, content): self.content = content


class _FakeLLM:
    """`ainvoke`만 갖는 최소 대역. 지정한 JSON을 그대로 돌려준다."""

    def __init__(self, payload):
        self._payload = payload
        self.calls: list = []

    async def ainvoke(self, messages, **_kw):
        self.calls.append(messages)
        body = (
            self._payload if isinstance(self._payload, str)
            else json.dumps(self._payload, ensure_ascii=False)
        )
        return _FakeResponse(f"```json\n{body}\n```")


def _domains(n: int = 2):
    return DB_DOMAINS[:n]


def _db(db_id: str, score, ctx: str = "ctx"):
    return {
        "db_id": db_id,
        "relevance_score": score,
        "sub_query_context": ctx,
        "user_specified": False,
        "reason": "",
    }


async def _classify(payload, *, fault_diagnosis_enabled: bool = False, n: int = 2):
    doms = _domains(n)
    return await sr._llm_classify(
        _FakeLLM(payload), "테스트 질의", doms,
        fault_diagnosis_enabled=fault_diagnosis_enabled,
    )


# ─────────────────────── S1·S2·S3 — E-1 허용 집합 ───────────────────────

class TestIntentAllowlist:
    """E-1 — LLM이 산출한 intent를 알려진 클래스와 대조한다(F1)."""

    @pytest.mark.asyncio
    async def test_unknown_intent_is_demoted(self, caplog):
        """S1 — 환각·오타 intent는 data_query로 강등되고 사유가 남는다.

        종전에는 `parsed.get("intent", "data_query")`가 그대로 흘러, 하류의
        `if intent == "cache_management"` 동등 비교에 걸리지 않고 **조용히** DB 조회로 낙하했다.
        """
        doms = _domains()
        payload = {"intent": "prosess_query", "databases": [_db(doms[0].db_id, 0.9)]}
        with caplog.at_level("WARNING"):
            out = await _classify(payload)
        assert out["intent"] == "data_query", (
            f"미상 intent가 강등되지 않았다: {out['intent']!r}"
        )
        # LogRecord.message는 포맷 전엔 없다 → getMessage()로 %-인자까지 전개한다.
        assert any("prosess_query" in r.getMessage() for r in caplog.records), (
            "강등 사유가 로그에 남지 않았다 — 침묵 강등은 환각을 관측 불가로 만든다."
        )

    @pytest.mark.asyncio
    async def test_known_intent_passes_through(self):
        """정상 intent는 그대로 통과한다(과잉 강등 방지)."""
        doms = _domains()
        for intent in ("data_query", "alarm_query", "cache_management", "general_inference"):
            out = await _classify({"intent": intent, "databases": [_db(doms[0].db_id, 0.9)]})
            assert out["intent"] == intent, f"{intent}가 부당하게 강등됐다"

    @pytest.mark.asyncio
    async def test_fault_diagnosis_allowed_only_when_enabled(self):
        """S2 — 옵트인 클래스는 플래그에 종속된다(Plan 64 CW-B · plans/80 C-A).

        off인데 허용하면 그래프에 없는 노드로 라우팅된다.
        """
        doms = _domains()
        payload = {"intent": "fault_diagnosis", "databases": [_db(doms[0].db_id, 0.9)]}

        off = await _classify(payload, fault_diagnosis_enabled=False)
        assert off["intent"] == "data_query", (
            "옵트인 off인데 fault_diagnosis가 통과했다 — CW-B 계약 위반."
        )

        on = await _classify(payload, fault_diagnosis_enabled=True)
        assert on["intent"] == "fault_diagnosis", "옵트인 on인데 강등됐다"

    def test_allowlist_matches_prompt_definitions(self):
        """S3 — 허용 집합과 프롬프트 클래스 정의가 **같은 출처**여야 한다.

        코드가 집합을 사본으로 다시 적으면 D-053 위반이자 드리프트 원천이다.
        """
        from src.prompts.semantic_router import allowed_intents

        for flag in (False, True):
            prompt = sr._build_router_prompt(_domains(), fault_diagnosis_enabled=flag)
            defined = set(re.findall(r"^- ([a-z_]+):\s", prompt, re.MULTILINE))
            assert allowed_intents(fault_diagnosis_enabled=flag) == defined, (
                f"허용 집합과 프롬프트 정의가 어긋난다(flag={flag}): "
                f"allow={sorted(allowed_intents(fault_diagnosis_enabled=flag))} "
                f"vs prompt={sorted(defined)}"
            )


# ─────────────────── S4·S5·S6 — E-2 항목 단위 격리 ───────────────────

class TestRelevanceScoreIsolation:
    """E-2 — 한 항목의 형식 오류가 분류 전체를 죽이지 않는다(F2)."""

    @pytest.mark.asyncio
    async def test_bad_score_drops_only_that_item(self):
        """S4 ★ — `"높음"` 하나 때문에 나머지 DB가 사라지면 안 된다.

        종전에는 `float("높음")` → ValueError → 호출부 except가 삼켜 **분류 전체**를 버리고
        `active_db_ids[0]` 단일 폴백으로 갔다. **임계와 무관하게 멀티 DB(§1.1)가 축소되는 경로**다.
        """
        doms = _domains(2)
        payload = {"intent": "data_query", "databases": [
            _db(doms[0].db_id, "높음"),
            _db(doms[1].db_id, 0.85),
        ]}
        out = await _classify(payload)
        survived = [d["db_id"] for d in out["databases"]]
        assert doms[1].db_id in survived, (
            f"형식 오류 항목 하나 때문에 정상 후보까지 사라졌다: {survived}"
        )
        assert doms[0].db_id not in survived, "판정 불가 항목이 후보로 남았다"

    @pytest.mark.asyncio
    async def test_bad_score_gets_no_default_value(self):
        """S5 — 임의 기본값(0.5)을 부여하지 않는다.

        0.5를 주면 MIN_RELEVANCE_SCORE(0.3) 게이트를 그냥 통과해 버린다.
        """
        doms = _domains(1)
        out = await _classify({"intent": "data_query",
                               "databases": [_db(doms[0].db_id, "높음")]})
        assert out["databases"] == [], (
            "형식 오류에 기본값이 부여돼 후보로 살아남았다 — 게이트가 무력화된다."
        )

    @pytest.mark.asyncio
    async def test_dropped_items_are_reported(self, caplog):
        """S5 — 탈락 사유가 구조화되어 남는다(침묵 폴백 금지)."""
        doms = _domains(2)
        payload = {"intent": "data_query", "databases": [
            _db(doms[0].db_id, "높음"),
            _db("존재하지않는db", 0.9),
            _db(doms[1].db_id, 0.7),
        ]}
        with caplog.at_level("WARNING"):
            out = await _classify(payload)

        dropped = out.get("dropped")
        assert dropped, "탈락 항목이 구조화되어 반환되지 않았다"
        reasons = {d.get("reason") for d in dropped}
        assert len(dropped) == 2, f"탈락 2건이어야 한다: {dropped}"
        assert reasons, f"탈락 사유가 비어 있다: {dropped}"
        assert any("탈락" in r.getMessage() or "drop" in r.getMessage().lower()
                   for r in caplog.records), "탈락이 로그로 남지 않았다"

    @pytest.mark.asyncio
    async def test_unknown_db_id_is_reported_not_silent(self):
        """부수 발견 — 무효 db_id 침묵 탈락은 '못 골랐다'와 '환각했다'를 구분 불가로 만든다."""
        doms = _domains(1)
        out = await _classify({"intent": "data_query",
                               "databases": [_db("환각디비", 0.9)]})
        assert out["databases"] == []
        assert out.get("dropped"), "무효 db_id 탈락이 보고되지 않았다"


# ─────────────────────── S7 — 임계 잠정값 고정 ───────────────────────

class TestProvisionalThreshold:
    """WU-01(S-3) — 근거 없는 상수가 근거 있는 상수로 오인되는 것을 막는다."""

    def test_threshold_value_unchanged(self):
        """값 변경은 「Ask first」 대상이다 — 이번 작업은 문서 고정뿐."""
        assert sr.MIN_RELEVANCE_SCORE == 0.3

    def test_threshold_documented_as_provisional(self):
        """정의부 주석에 잠정값 근거가 있어야 한다."""
        src = inspect.getsource(sr)
        # 정의부 **직전 주석 블록**을 본다 — 선행 빈 줄까지 거슬러 올라간다.
        head = src[: src.find("MIN_RELEVANCE_SCORE = 0.3")]
        block = head.rsplit("\n\n", 1)[-1]
        assert "잠정" in block, (
            "MIN_RELEVANCE_SCORE 정의부에 잠정값 표기가 없다 — "
            "자기보고 스케일 기준 관성값임이 드러나야 한다(plans/79 §8 ⑧ · C-4로 정산)."
        )


# ─────────────────────── S8 — 프롬프트 불변 ───────────────────────

class TestPromptUnchanged:
    """R1 — 프롬프트 텍스트가 바뀌면 트랙 A 측정(S-1) 기준이 흔들린다."""

    def test_class_lines_still_rendered_from_prompt_body(self):
        """정본 상수를 신설해도 프롬프트 본문의 클래스 나열은 그대로여야 한다."""
        prompt = sr._build_router_prompt(_domains(), fault_diagnosis_enabled=False)
        for line in (
            "- data_query: 서버 사양, 성능 지표, 프로세스 등 일반 인프라 데이터 조회",
            "- alarm_query: 알람 현황, 알람 이력, 임계값 초과, 모니터링 alert·이벤트(event) 조회",
            "- cache_management: 캐시 생성/갱신/삭제, 유사어 관리, 컬럼 설명 변경",
        ):
            assert line in prompt, f"프롬프트 클래스 줄이 변경됐다: {line!r}"
