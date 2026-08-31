"""카탈로그 구획(section) 클러스터링 검증.

config.py 정의 순서가 구획을 오가며 섞여 있어도, 스키마 응답에서는 같은 구획의
항목이 연속 블록으로 묶여야 한다(UI 소제목 반복 방지 — 그룹핑 형식 표시의 전제).
"""

from __future__ import annotations

from src.api.settings_catalog import build_catalog


def _schema_groups():
    response = build_catalog(
        file_values={}, config=None, os_environ={}, env_file_path="/tmp/none.env",
    )
    return response.groups


def test_sections_are_contiguous_in_every_group():
    """모든 그룹에서 각 구획은 정확히 한 번의 연속 블록으로만 등장한다."""
    for group in _schema_groups():
        seen_done: set[object] = set()
        current: object = object()  # 어떤 section 값과도 다른 초기 센티널
        for item in group.settings:
            if item.section != current:
                assert item.section not in seen_done, (
                    f"{group.group_key}: 구획 '{item.section}'이 비연속으로 재등장 — "
                    "_cluster_by_section 클러스터링 회귀"
                )
                seen_done.add(current)
                current = item.section
        # 마지막 블록도 종료 집합에 반영될 필요는 없음(재등장 검사만 목적)


def test_sectioned_groups_have_expected_shape():
    """구획을 쓰는 그룹(알람·노이즈 게이트)은 복수 구획을 유지하고, 구획 내 정의 순서가 보존된다."""
    groups = {g.group_key: g for g in _schema_groups()}

    noise = groups["noise_gate"]
    noise_sections = [s.section for s in noise.settings if s.section]
    assert len(set(noise_sections)) >= 10  # 세부 개수는 config 진화에 따라 변동 — 하한만 고정

    # 구획 내 상대 순서 보존(안정 정렬): '기본 동작' 구획의 대표 2필드가 정의 순서대로
    e1_keys = [s.env_key for s in noise.settings if s.section == "기본 동작"]
    assert e1_keys.index("NOISE_ENABLE_NOISE_GATE") < e1_keys.index(
        "NOISE_SELF_HEAL_WINDOW_SECONDS"
    )

    alarm = groups["alarm"]
    alarm_sections = [s.section for s in alarm.settings if s.section]
    assert len(set(alarm_sections)) >= 2
    # 무구획(기본) 항목이 구획 블록 앞에 하나의 묶음으로 온다
    first_sectioned = next(
        i for i, s in enumerate(alarm.settings) if s.section is not None
    )
    assert all(s.section is not None for s in alarm.settings[first_sectioned:])


# ─── Plan 83 T1: 구획 누락 감지 · 파일 키 커버리지 ────────────────────────────


def test_every_noise_key_has_section():
    """모든 `NOISE_` 키가 구획을 갖는다 — 미분류는 UI에서 소제목 없이 섞여 발견성을 떨어뜨린다.

    이 테스트는 **신규 플래그가 늘 때 자동으로 실패**하는 그물이다. `NOISE_` 키를 추가하면서
    `SECTION_BY_KEY` 등재를 잊으면 여기서 잡힌다(Plan 83 §3.2 C-1 — 실제 누락 8건에서 출발).
    """
    from src.api.settings_catalog import SECTION_BY_KEY, field_index

    missing = sorted(
        k for k in field_index() if k.startswith("NOISE_") and k not in SECTION_BY_KEY
    )
    assert missing == [], (
        f"구획 미분류 NOISE 키 {len(missing)}건: {missing} — "
        "SECTION_BY_KEY에 인접 키와 같은 구획명으로 등재할 것"
    )


def test_env_files_fully_covered_by_catalog():
    """`.env`·`.env.example`의 모든 키가 카탈로그에 존재한다(관리자 UI에서 정의 가능).

    Plan 68의 인트로스펙션 SSOT가 유지되는지 고정한다 — 파일에만 있고 config에 없는 키가
    생기면 그 옵션은 웹UI에서 보이지 않는다(2026-08-28 실측 기준선: 양쪽 모두 누락 0건).
    """
    from src.api.settings_catalog import _PROJECT_ROOT, _parse_env_keys, field_index

    catalog = set(field_index())
    for name in (".env", ".env.example"):
        path = _PROJECT_ROOT / name
        if not path.exists():
            continue  # 배포 환경에 따라 부재 가능 — 있을 때만 검사
        missing = sorted(_parse_env_keys(path) - catalog)
        assert missing == [], f"{name}: 카탈로그 미포함 {len(missing)}건 — {missing}"
