"""답변 영역 소유 — 가이드(프롬프트) 렌더 (plans/102 X-8 · X-T4).

단언하는 것:
    P1 off — 라우터 프롬프트가 골든과 바이트 동일(fault_diagnosis 두 상태)
    P2 on  — 골든 대비 **줄 단위 삽입만** 있다(기존 줄 변경·삭제 0) · 단일 호출·2단 DB 선택 대칭
    P3 on 삽입분 — 소유표 행은 레지스트리 파생(활성 DB가 있는 시스템만) · 출력 필드 정의 ·
         교차 예시 2건
         (예시 JSON이 파싱되고 코드가 카탈로그 안에 있다) · 소유 시스템이 하나뿐이면 교차 예시 없음
    P4 렌더는 활성 DB 목록 단위 캐시(기동 시 1회 — 프롬프트 접두 고정)
    P5 분해 프롬프트 — off 동일 · on 삽입만 · 앵커 가드
    P6 DB 설명 생성 프롬프트(X-T4) — off 동일 · on에서 다른 시스템 소유 영역을 알린다

실 LLM 호출 0(D-127).
"""

from __future__ import annotations

import difflib
import importlib
import json
import pathlib
import re

import pytest

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.prompts import intent_planner as planner_prompts
from src.prompts.semantic_router import (
    SEMANTIC_ROUTER_CAPABILITY_CHAIN_LINE,
    SEMANTIC_ROUTER_CAPABILITY_FIELD_LINE,
    SEMANTIC_ROUTER_OWNERSHIP_EXAMPLES,
    SEMANTIC_ROUTER_OWNERSHIP_HEADING,
    SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE,
)
from src.routing import capability_ownership as own
from src.routing.domain_config import DB_DOMAINS
from src.routing.registry import get_registry

sr = importlib.import_module("src.routing.semantic_router")
ip = importlib.import_module("src.orchestration.intent_planner")

GOLDEN_DIR = pathlib.Path(__file__).parents[1] / "test_semantic_routing" / "goldens"
_POLESTAR_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _cfg(*, ownership: bool, unknown: bool = False) -> AppConfig:
    return AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv=",".join([*_POLESTAR_ZONES, "itam"])),
        router=RouterConfig(
            capability_ownership_enabled=ownership, unknown_enabled=unknown, two_stage_enabled=False
        ),
    )


@pytest.fixture
def flag(monkeypatch):
    def _set(ownership: bool, unknown: bool = False) -> AppConfig:
        cfg = _cfg(ownership=ownership, unknown=unknown)
        monkeypatch.setattr(sr, "load_config", lambda: cfg)
        return cfg

    return _set


def _all_domains():
    return list(DB_DOMAINS)


def _opcodes(before: str, after: str) -> set[str]:
    sm = difflib.SequenceMatcher(
        None, before.splitlines(True), after.splitlines(True), autojunk=False
    )
    return {op for op, *_ in sm.get_opcodes()}


def _inserted(before: str, after: str) -> str:
    sm = difflib.SequenceMatcher(
        None, before.splitlines(True), after.splitlines(True), autojunk=False
    )
    lines = after.splitlines(True)
    return "".join(
        "".join(lines[j1:j2]) for op, _i1, _i2, j1, j2 in sm.get_opcodes() if op == "insert"
    )


# ──────────────────────────────────────────────
# P1 · P2 — 라우터 프롬프트
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "fault,golden", [(False, "router_prompt_fd_off.txt"), (True, "router_prompt_fd_on.txt")]
)
def test_off_render_is_byte_identical_to_golden(flag, fault, golden):
    flag(False)
    rendered = sr._build_router_prompt(_all_domains(), fault_diagnosis_enabled=fault)
    assert rendered == (GOLDEN_DIR / golden).read_text()


@pytest.mark.parametrize(
    "fault,golden", [(False, "router_prompt_fd_off.txt"), (True, "router_prompt_fd_on.txt")]
)
def test_on_render_is_insertion_only_against_golden(flag, fault, golden):
    """★ on 렌더 = 골든 + 삽입 줄. 기존 줄을 한 글자도 고치지 않는다."""
    flag(True)
    on = sr._build_router_prompt(_all_domains(), fault_diagnosis_enabled=fault)
    off = (GOLDEN_DIR / golden).read_text()
    assert _opcodes(off, on) == {"equal", "insert"}
    assert on.count("\n\n\n") == off.count("\n\n\n"), "삽입이 빈 줄 겹침을 새로 만들면 안 된다"
    added = _inserted(off, on)
    assert SEMANTIC_ROUTER_OWNERSHIP_HEADING in added
    assert SEMANTIC_ROUTER_CAPABILITY_CHAIN_LINE.strip() in added
    assert SEMANTIC_ROUTER_CAPABILITY_FIELD_LINE.strip() in added
    assert "CPU 사용률 90% 넘는 서버들의 유지보수 계약 만료일" in added
    assert "HW 지원 종료가 6개월 안 남은 서버의 현재 알람" in added


def test_on_render_insertion_only_with_unknown_class_on(flag):
    """옵트인 슬롯끼리 겹쳐도(unknown 예시 뒤에 교차 예시) 삽입만이다."""
    flag(False, unknown=True)
    off = sr._build_router_prompt(_all_domains())
    flag(True, unknown=True)
    on = sr._build_router_prompt(_all_domains())
    assert _opcodes(off, on) == {"equal", "insert"}
    assert on.count("\n\n\n") == off.count("\n\n\n")


def test_stage2_database_prompt_is_symmetric(flag):
    """2단 DB 선택 템플릿도 같은 슬롯을 같은 값으로 받는다 — 삽입만."""

    def _render(ownership: bool) -> str:
        flag(ownership)
        return SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE.format(
            db_list=sr._render_db_list(_all_domains()),
            location_vocab="V",
            confirmed_intent="data_query",
            intent_section="",
            unknown_example="",
            **sr._ownership_prompt_slots(_all_domains()),
        )

    off, on = _render(False), _render(True)
    assert _opcodes(off, on) == {"equal", "insert"}
    assert SEMANTIC_ROUTER_OWNERSHIP_HEADING in on
    # 기존 템플릿에 이미 있는 빈 줄 겹침(빈 intent_section)은 그대로 두고, 새로 만들지 않는다
    assert on.count("\n\n\n") == off.count("\n\n\n")


# ──────────────────────────────────────────────
# P3 — 삽입분 내용
# ──────────────────────────────────────────────


def test_ownership_rows_are_registry_derived(flag):
    flag(True)
    on = sr._build_router_prompt(_all_domains())
    reg = get_registry()
    section = on.split(SEMANTIC_ROUTER_OWNERSHIP_HEADING, 1)[1].split("### 답변 영역 출력 필드", 1)[
        0
    ]
    table_codes = re.findall(r"^\| ([a-z_]+) \|", section, re.MULTILINE)
    assert table_codes == [s.code for s in reg.capability_specs() if reg.capability_owners(s.code)]
    for code in table_codes:
        assert reg.system_label(reg.capability_owners(code)[0]) in section


def test_output_field_lines_do_not_look_like_intent_classes(flag):
    """「출력 형식」 클래스 정의 판독(`- <class>: `)에 새 필드가 섞이지 않는다.

    S4 구조 테스트(정의 클래스 == 예시 클래스)를 보호한다.
    """
    flag(True)
    on = sr._build_router_prompt(_all_domains())
    added = _inserted((GOLDEN_DIR / "router_prompt_fd_off.txt").read_text(), on)
    assert not re.findall(r"^- ([a-z_]+):\s", added, re.MULTILINE)


def test_cross_examples_parse_and_use_catalog_codes():
    known = own.known_capability_codes()
    blocks = re.findall(r"```json\s*(.*?)```", SEMANTIC_ROUTER_OWNERSHIP_EXAMPLES, re.DOTALL)
    assert len(blocks) == 2
    chains = []
    for block in blocks:
        obj = json.loads(block)
        assert set(obj["chain"]) <= known and len(obj["chain"]) == 2
        chains.append(obj["chain"])
        for db in obj["databases"]:
            assert set(db["capabilities"]) <= known
            owners = get_registry().capability_owners(db["capabilities"][0])
            assert get_registry().system_of(db["db_id"]) in owners, (
                "예시가 소유 규칙을 어기면 안 된다"
            )
    # 양방향 — 한 예시는 폴스타 영역이 앞, 다른 예시는 자산 영역이 앞
    first_owners = {get_registry().capability_owners(c[0])[0] for c in chains}
    assert len(first_owners) == 2


def test_single_owner_system_renders_no_cross_examples(flag):
    flag(True)
    only_polestar = [d for d in DB_DOMAINS if d.db_id in _POLESTAR_ZONES]
    on = sr._build_router_prompt(only_polestar)
    assert SEMANTIC_ROUTER_OWNERSHIP_HEADING in on
    assert "asset_contract" not in on, "활성 DB가 없는 시스템의 영역은 소유표에 싣지 않는다"
    assert "유지보수 계약 만료일" not in on


# ──────────────────────────────────────────────
# P4 — 캐시(기동 시 1회)
# ──────────────────────────────────────────────


def test_slot_render_is_cached_per_db_list(flag):
    flag(True)
    sr._render_ownership_slots.cache_clear()
    a = sr._ownership_prompt_slots(_all_domains())
    b = sr._ownership_prompt_slots(_all_domains())
    assert a == b
    info = sr._render_ownership_slots.cache_info()
    assert info.misses == 1 and info.hits >= 1


def test_off_does_not_touch_render_cache(flag):
    flag(False)
    sr._render_ownership_slots.cache_clear()
    assert set(sr._ownership_prompt_slots(_all_domains()).values()) == {""}
    assert sr._render_ownership_slots.cache_info().misses == 0


# ──────────────────────────────────────────────
# P5 — 분해 프롬프트
# ──────────────────────────────────────────────


def test_planner_off_is_base_template():
    assert (
        ip._planner_system_prompt(_cfg(ownership=False))
        is planner_prompts.INTENT_PLANNER_SYSTEM_TEMPLATE
    )


def test_planner_on_is_insertion_only():
    ip._render_planner_ownership_prompt.cache_clear()
    on = ip._planner_system_prompt(_cfg(ownership=True))
    base = planner_prompts.INTENT_PLANNER_SYSTEM_TEMPLATE
    assert _opcodes(base, on) == {"equal", "insert"}
    added = _inserted(base, on)
    assert "## 답변 영역(capability)" in added and "### 예시 3-2" in added
    assert "\n\n\n" not in on


def test_planner_single_owner_system_has_section_without_examples():
    cfg = AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(_POLESTAR_ZONES)),
        router=RouterConfig(capability_ownership_enabled=True),
    )
    ip._render_planner_ownership_prompt.cache_clear()
    on = ip._planner_system_prompt(cfg)
    assert "## 답변 영역(capability)" in on and "### 예시 3-2" not in on


def test_planner_anchor_guard(monkeypatch):
    monkeypatch.setattr(planner_prompts, "INTENT_PLANNER_SYSTEM_TEMPLATE", "앵커 없음")
    with pytest.raises(RuntimeError):
        planner_prompts.render_intent_planner_ownership_template("| a | b | c |")


# ──────────────────────────────────────────────
# P6 — DB 설명 생성 프롬프트 (X-T4)
# ──────────────────────────────────────────────


class _CaptureLLM:
    def __init__(self):
        self.user_prompts: list[str] = []

    async def ainvoke(self, messages):
        self.user_prompts.append(messages[-1].content)
        return type("R", (), {"content": "설명"})()


_SCHEMA = {"tables": {"t": {"columns": [{"name": "c1"}, {"name": "cpu_usage"}], "sample_data": []}}}


async def _generate(monkeypatch, db_id: str, *, ownership: bool) -> str:
    import src.schema_cache.description_generator as dg

    monkeypatch.setattr(dg, "load_config", lambda: _cfg(ownership=ownership))
    llm = _CaptureLLM()
    await dg.DescriptionGenerator(llm).generate_db_description(db_id, _SCHEMA)
    return llm.user_prompts[0]


async def test_description_prompt_unchanged_when_off(monkeypatch):
    from src.prompts.schema_description import DB_DESCRIPTION_USER_TEMPLATE

    prompt = await _generate(monkeypatch, "itam", ownership=False)
    assert prompt.startswith(DB_DESCRIPTION_USER_TEMPLATE.split("{db_id}")[0])
    assert "답변 영역 소유" not in prompt
    assert prompt.endswith("한국어 1문장으로 설명하세요.\n")


async def test_description_prompt_names_other_system_areas_when_on(monkeypatch):
    prompt = await _generate(monkeypatch, "itam", ownership=True)
    assert "## 답변 영역 소유" in prompt
    own_part, other_part = prompt.split("다른 시스템이 정본인 답변 영역", 1)
    assert "- asset_contract:" in own_part and "- server_usage:" not in own_part
    assert "- server_usage:" in other_part and "(정본: " in other_part


async def test_description_prompt_skips_db_without_ownership(monkeypatch):
    prompt = await _generate(monkeypatch, "cloud_portal", ownership=True)
    assert "답변 영역 소유" not in prompt
