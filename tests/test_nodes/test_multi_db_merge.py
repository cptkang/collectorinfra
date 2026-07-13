"""멀티 DB 결과 병합(_merge_results)의 칼럼명 통일 회귀 테스트 (D-068 후속).

버그(2026-07-13): 이종 엔진 폼필(공동존 gp/yd + 은행존 b0)의 CSV 다운로드에서
`IP주소`/`ip주소`, `OS 종류`/`os 종류`, `CPU 평균`/`cpu 평균`처럼 **라틴 문자 대소문자만 다른
중복 칼럼**이 생기고 각 존 데이터가 자기 표기 칼럼에만 채워졌다. 원인: DB2가 결과 칼럼의 라틴
문자를 소문자로 반환 → gp="IP주소" vs b0="ip주소" → 원본 병합·CSV가 별개 칼럼으로 분리
(Excel writer는 자체 정규화 매칭으로 흡수해 정상). 순수 한글 칼럼(서버 이름/메모리 용량 등)은
소문자화 대상이 없어 중복 안 됨. 수정: 병합 시 정규화 기준 canonical(양식 필드) 이름으로 통일.
"""

from __future__ import annotations

from src.nodes.multi_db_executor import _merge_results


def test_merge_unifies_db2_lowercased_columns_to_canonical():
    db_results = {
        "polestar_cm_gp": [
            {"서버 이름": "gp1", "IP주소": "10.0.0.1", "CPU 평균": 6.5, "OS 종류": "Linux"}
        ],
        "polestar_b0": [
            {"서버 이름": "b01", "ip주소": "10.0.1.1", "cpu 평균": 7.2, "os 종류": "AIX"}
        ],
    }
    canonical = ["서버 이름", "IP주소", "CPU 평균", "OS 종류"]
    merged = _merge_results(db_results, canonical_fields=canonical)

    keys = set()
    for row in merged:
        keys |= set(row.keys())
    keys.discard("_source_db")
    # 중복 칼럼 없이 양식 표기로 통일
    assert keys == {"서버 이름", "IP주소", "CPU 평균", "OS 종류"}
    assert "ip주소" not in keys and "cpu 평균" not in keys and "os 종류" not in keys

    by_db = {r["_source_db"]: r for r in merged}
    assert by_db["polestar_b0"]["IP주소"] == "10.0.1.1"  # b0 값이 canonical 칼럼에 실림
    assert by_db["polestar_b0"]["CPU 평균"] == 7.2
    assert by_db["polestar_cm_gp"]["IP주소"] == "10.0.0.1"


def test_merge_without_canonical_uses_first_seen_representative():
    """양식 필드가 없어도 정규형이 같으면 첫 등장 표기로 통일한다(비폼필 멀티 DB도 이득)."""
    db_results = {
        "gp": [{"IP주소": "a"}],
        "b0": [{"ip주소": "b"}],
    }
    merged = _merge_results(db_results)  # canonical_fields 미전달
    keys = set()
    for row in merged:
        keys |= set(row.keys())
    keys.discard("_source_db")
    assert keys == {"IP주소"}  # 첫 등장(gp)의 "IP주소"로 통일


def test_merge_tags_source_db_and_preserves_pure_korean_columns():
    db_results = {
        "gp": [{"서버 이름": "s1", "메모리 용량": "16GB"}],
        "b0": [{"서버 이름": "s2", "메모리 용량": "32GB"}],
    }
    merged = _merge_results(db_results, canonical_fields=["서버 이름", "메모리 용량"])
    assert {r["_source_db"] for r in merged} == {"gp", "b0"}
    # 순수 한글 칼럼은 그대로 통일 유지
    assert all("메모리 용량" in r for r in merged)
