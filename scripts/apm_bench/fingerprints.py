"""APM few-shot 지문 · 분해 프롬프트 APM 절 크기 게이트 (plans/152 §3.9 · W5 · D-323).

지문은 「같은 SHA에서 F1~F4 중 무엇이 바뀌었나」를 run 사이에 대조하려고 남긴다. 계산은
`scripts.itam_bench._serve.fingerprint`(문자열은 그대로 · 그 밖은 키 정렬 JSON → sha256 앞 12자)를
그대로 쓴다 — 벤치 사이에 지문 정의가 갈라지지 않게 한다.

| 키 | 대상 |
|---|---|
| `F1` | 레지스트리 `solutions[apm].views`의 `[{id, label, examples, args: [{name, label}]}]` |
| `F1_render` | `apm_query.render_view_rows()` 렌더(보기 표 — 분해·재선택 프롬프트에 실리는 글) |
| `F2` | 분해 프롬프트 APM 상수 5종(`INTENT_PLANNER_APM_SECTION` 등)을 `"\\n"`으로 이은 것 |
| `F3` | `INTENT_PLANNER_OWNERSHIP_EXAMPLES`(교차 시스템 예시) |
| `F4` | `apm_query._SELECTION_SYSTEM`(보기 재선택 프롬프트 — 비공개 이름이라 못 읽으면 `None`) |
| `registry` | `config/db_registry.yaml` 파일 전체 |

크기 게이트: APM 절은 APM 활성 배포에서 **모든 질의**의 분해 프롬프트에 실린다. 예문을 늘리면 전
질의의 접두가 길어지므로 W0 실측(2026-10-12 · `ee13b47`) 대비 약 15%까지만 허용한다.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.itam_bench._serve import fingerprint

logger = logging.getLogger(__name__)

#: APM 절 렌더(템플릿 + `render_view_rows()` + `render_source_line()`) 상한(자).
#: 근거: W0 실측 12,344자(`ee13b47`) + 약 15% 여유(예문 10여 건 추가 분량).
APM_SECTION_MAX_CHARS = 14_200
#: 분해 시스템 프롬프트의 APM 증분(APM 활성 − 비활성) 상한(자).
#: 근거: W0 실측 13,323자(소유 플래그 on/off 모두 같음) + 약 15% 여유.
APM_INCREMENT_MAX_CHARS = 15_300

#: 크기 측정용 활성 DB(W0 측정과 같은 4종 — 소유표 렌더 입력일 뿐 증분에는 영향 없다).
_MEASURE_DB_IDS: tuple[str, ...] = ("polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam")
#: 크기 측정용 가짜 APM 엔드포인트(활성 판정만 — 연결하지 않는다).
_MEASURE_ENDPOINT = ("http://127.0.0.1:9/sse", None)

#: F2 상수 이름(이 순서로 `"\n"` 결합).
F2_NAMES: tuple[str, ...] = (
    "INTENT_PLANNER_APM_SECTION",
    "APM_OUTPUT_RULE",
    "APM_SKELETON_TAIL_WITH_KEYS",
    "APM_SKELETON_TAIL_WITH_SOURCES",
    "APM_SOURCES_RULE",
)


def planner_module() -> Any:
    """`src.orchestration.intent_planner` 모듈.

    패키지가 같은 이름의 함수를 재노출해 속성 접근으로는 모듈을 얻지 못한다.
    """
    return importlib.import_module("src.orchestration.intent_planner")


def f1_payload(views: Any) -> list[dict[str, Any]]:
    """F1 지문 대상 — 보기마다 id·label·examples·args(name·label)."""
    return [
        {"id": v.id, "label": v.label, "examples": list(v.examples),
         "args": [{"name": a.name, "label": a.label} for a in v.args]}
        for v in views
    ]


def f2_text() -> str:
    """F2 — 분해 프롬프트 APM 상수 5종을 `"\\n"`으로 이은 글."""
    from src.prompts import intent_planner as prompts

    return "\n".join(getattr(prompts, name) for name in F2_NAMES)


def f3_text() -> str:
    """F3 — 교차 시스템 예시(`INTENT_PLANNER_OWNERSHIP_EXAMPLES`)."""
    from src.prompts import intent_planner as prompts

    return str(prompts.INTENT_PLANNER_OWNERSHIP_EXAMPLES)


def f4_text() -> str | None:
    """F4 — 보기 재선택 시스템 프롬프트(비공개 이름 · 못 읽으면 None + 경고)."""
    try:
        from src.orchestration import apm_query

        return str(apm_query._SELECTION_SYSTEM)
    except (ImportError, AttributeError) as e:
        logger.warning("F4 지문 생략 — apm_query._SELECTION_SYSTEM 을 읽지 못했다: %s", e)
        return None


def fewshot_fingerprints(registry: Any = None) -> dict[str, str | None]:
    """F1~F4·보기 표 렌더·레지스트리 파일 지문.

    Args:
        registry: `RegistryInfo`(주면 F1을 그 보기로 계산) · None이면 런타임 레지스트리
            (`get_registry`)

    Returns:
        `{"F1", "F1_render", "F2", "F3", "F4", "registry"}` → 지문(앞 12자) · F4는 못 읽으면 None
    """
    from src.orchestration import apm_query
    from src.routing.registry import REGISTRY_PATH

    views = registry.views.values() if registry is not None else apm_query.apm_views()
    f4 = f4_text()
    return {
        "F1": fingerprint(f1_payload(views)),
        "F1_render": fingerprint(apm_query.render_view_rows()),
        "F2": fingerprint(f2_text()),
        "F3": fingerprint(f3_text()),
        "F4": fingerprint(f4) if f4 is not None else None,
        "registry": fingerprint(Path(REGISTRY_PATH).read_text(encoding="utf-8")),
    }


def _measure_config(apm_on: bool) -> Any:
    """크기 측정용 설정 대역 — 소유 on · task 프레임 off · APM 활성 여부만 바꾼다."""
    def endpoint(system: str) -> tuple[str, None] | None:
        return _MEASURE_ENDPOINT if apm_on and system == "apm" else None

    return SimpleNamespace(
        router=SimpleNamespace(capability_ownership_enabled=True),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(_MEASURE_DB_IDS)),
        composite=SimpleNamespace(task_frame_enabled=False),
        dbhub=SimpleNamespace(source_endpoint=endpoint),
        rag=None,
    )


def _section_text() -> str:
    """`INTENT_PLANNER_APM_SECTION`에 보기 표·소스 줄을 채운 렌더(분해 프롬프트에 실리는 APM 절)."""
    from src.orchestration import apm_query
    from src.prompts import intent_planner as prompts

    return (prompts.INTENT_PLANNER_APM_SECTION
            .replace(prompts.APM_VIEW_ROWS_SLOT, apm_query.render_view_rows())
            .replace(prompts.APM_SOURCES_SLOT, apm_query.render_source_line()))


def apm_section_evidence() -> dict[str, Any]:
    """근거 묶음 `versions.apm_section`(§3.8) — APM 절 렌더 지문 · 문자 수 · 실린 보기 행 id.

    보기 행은 `render_view_rows()`가 도는 순서(`apm_views()` — 레지스트리 선언 순)다.
    """
    from src.orchestration import apm_query

    text = _section_text()
    return {"fingerprint": fingerprint(text), "chars": len(text),
            "view_rows": [v.id for v in apm_query.apm_views()]}


def apm_section_size() -> dict[str, int]:
    """분해 프롬프트 APM 절 크기(자).

    - `section_chars`: `INTENT_PLANNER_APM_SECTION`에 보기 표·소스 줄을 채운 렌더
    - `increment_chars`: 분해 시스템 프롬프트(`_planner_system_prompt`) APM 활성 − 비활성
    - `view_rows_chars`·`template_chars`·`selection_chars`(F4 + 보기 표 · 영역 자리 비움): 참고
    """
    from src.orchestration import apm_query
    from src.prompts import intent_planner as prompts

    rows = apm_query.render_view_rows()
    section = _section_text()
    planner = planner_module()
    with_apm = planner._planner_system_prompt(_measure_config(True))
    without = planner._planner_system_prompt(_measure_config(False))
    f4 = f4_text() or ""
    return {
        "section_chars": len(section),
        "increment_chars": len(with_apm) - len(without),
        "view_rows_chars": len(rows),
        "template_chars": len(prompts.INTENT_PLANNER_APM_SECTION),
        "selection_chars": len(f4.replace("<areas>", "").replace("<rows>", rows)),
    }


def size_gate(sizes: dict[str, int] | None = None) -> list[str]:
    """크기 상한 위반 문구(없으면 빈 목록)."""
    sizes = sizes if sizes is not None else apm_section_size()
    out: list[str] = []
    if sizes["section_chars"] > APM_SECTION_MAX_CHARS:
        out.append(f"APM 절 {sizes['section_chars']:,}자 > 상한 {APM_SECTION_MAX_CHARS:,}자"
                   " — 예문을 줄이거나 다른 예문과 합친다")
    if sizes["increment_chars"] > APM_INCREMENT_MAX_CHARS:
        out.append(f"분해 프롬프트 APM 증분 {sizes['increment_chars']:,}자 > 상한 "
                   f"{APM_INCREMENT_MAX_CHARS:,}자")
    return out


__all__ = [
    "APM_SECTION_MAX_CHARS",
    "APM_INCREMENT_MAX_CHARS",
    "F2_NAMES",
    "fingerprint",
    "planner_module",
    "f1_payload",
    "f2_text",
    "f3_text",
    "f4_text",
    "fewshot_fingerprints",
    "apm_section_evidence",
    "apm_section_size",
    "size_gate",
]
