"""시맨틱 카탈로그 → 프롬프트 텍스트 렌더.

``src/nodes/semantic_compiler.py``에서 분리했다(Plan 69 P5-1) — 시맨틱 모델 dict만 보는
순수 렌더라 nodes 밖에 두어 ``src.tools``가 nodes를 거치지 않고 참조하게 한다(순환 해소).
"""

from __future__ import annotations

from src.semantic.ir import _AGG_FN


def render_catalog(model: dict) -> str:
    """시맨틱 모델을 NL→SMQ 프롬프트용 카탈로그 텍스트로 렌더한다(선택 가능 항목만 제시)."""
    lines: list[str] = []
    pattern_a = model.get("pattern_a") or {}
    dims = pattern_a.get("dimensions") or []
    if dims:
        lines.append("■ 패턴 A 서버설정 dimensions (name — resource_type — 별칭):")
        for d in dims:
            aliases = ", ".join(d.get("aliases", []) or [])
            lob = " (LOB — 미지원)" if d.get("lob") else ""
            lines.append(f"  - {d.get('name')} [{d.get('resource_type')}]{lob}"
                         + (f" ← {aliases}" if aliases else ""))
        # S-IR4: 선언된 filterable을 노출해야 LLM이 서버명·가용성 필터를 커버리지 안으로 낸다
        filterable = pattern_a.get("filterable") or []
        if filterable:
            lines.append("  필터 가능 필드(filterable): " + ", ".join(map(str, filterable)))
    pattern_b = model.get("pattern_b") or {}
    measures = pattern_b.get("measures") or []
    if measures:
        lines.append("■ 패턴 B 성능지표 measures (resource_type/definition_name — 별칭):")
        for m in measures:
            aliases = ", ".join(m.get("aliases", []) or [])
            lines.append(f"  - {m.get('resource_type')} / {m.get('definition_name')}"
                         + (f" ← {aliases}" if aliases else ""))
        grains = ", ".join((pattern_b.get("metric_tables") or {}).keys())
        lines.append(f"  time_grain 옵션: {grains} (기본 month)")
        # 지원 집계를 _AGG_FN에서 파생 — S-IR1 확장(count/sum)이 안내에서 빠지는
        # 하드코딩 드리프트 재발 차단 (Plan 69 P0-⑨)
        lines.append("  집계(agg): " + ", ".join(_AGG_FN))
    pattern_c = model.get("pattern_c") or {}
    ents = pattern_c.get("entities") or {}
    if ents:
        lines.append("■ 패턴 C 알람 엔터티: " + ", ".join(ents.keys()))
        cdims = pattern_c.get("dimensions") or {}
        lines.append("  알람 dimensions: " + ", ".join(cdims.keys()))
        sev = pattern_c.get("severity_map") or {}
        lines.append("  severity: " + ", ".join(f"{k}={v}" for k, v in sev.items()))
    return "\n".join(lines)
