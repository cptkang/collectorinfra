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

    # 구획 내 상대 순서 보존(안정 정렬): E1 기본의 대표 2필드가 정의 순서대로
    e1_keys = [s.env_key for s in noise.settings if s.section == "E1 기본"]
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
