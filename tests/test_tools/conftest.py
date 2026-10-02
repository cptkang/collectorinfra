"""fine-grained 도구 계층 테스트 공용 픽스처 (Plan 67 Phase S1).

도구는 의존성을 주입받는 순수 함수라, 실 DB·LLM·캐시 없이 목 데이터만으로 검증한다.
픽스처의 시맨틱 모델은 **특정 DB의 실제 스키마가 아닌 가공 값**이다(공용 계층 DB-agnostic
검증이 목적 — D-088).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def semantic_model() -> dict:
    """검증용 최소 시맨틱 모델(패턴 A/B/C 각 소수 항목)."""
    return {
        "pattern_a": {
            "dimensions": [
                {
                    "name": "hostname",
                    "resource_type": "host.entity",
                    "aliases": ["호스트명", "서버명"],
                    "source": "direct",
                    "column": "hostname",
                },
                {
                    "name": "OSName",
                    "resource_type": "host.os",
                    "aliases": ["운영체제", "OS"],
                },
                {
                    "name": "OSParameter",
                    "resource_type": "host.os",
                    "aliases": ["커널 파라미터"],
                    "lob": True,
                },
            ]
        },
        "pattern_b": {
            "measures": [
                {
                    "resource_type": "host.cpu",
                    "definition_name": "Utilization",
                    "aliases": ["CPU 사용률", "사용률"],
                }
            ],
            "metric_tables": {"month": "metric_month"},
        },
        "pattern_c": {
            "entities": {"ALARM": {}},
            "dimensions": {"ALARMSEVERITY": {}},
            "severity_map": {"critical": 5},
        },
    }


@pytest.fixture
def synonyms() -> dict[str, list[str]]:
    """검증용 유사어 사전({테이블.컬럼: [유사어]})."""
    return {
        "host.hostname": ["서버명", "호스트명"],
        "host.os_name": ["운영체제"],
    }


@pytest.fixture
def schema_info() -> dict:
    """검증용 스키마 정보(테이블 1개)."""
    return {
        "tables": {
            "host": {
                "columns": [
                    {"name": "hostname", "data_type": "varchar"},
                    {"name": "os_name", "data_type": "varchar"},
                ]
            }
        }
    }
