# -*- coding: utf-8 -*-
"""위치 표면어 값 사본 동기 가드 (ux_improvement 병합 해소분).

선언 정본은 config/db_registry.yaml locations(D-131)이지만, 존 역질문 후단
게이트(D-150)가 infrastructure(routing) 계층에서도 소비하므로 값은
utils.query_gen_common.LOCATION_HINT_TERMS에 둔다(utils는 registry 임포트 불가).
두 출처가 어긋나면 이 테스트가 깨진다 — 항목 추가 시 양쪽을 함께 갱신할 것.
"""

from src.routing.registry import get_registry
from src.utils.query_gen_common import LOCATION_HINT_TERMS


def test_location_hint_terms_match_registry() -> None:
    assert tuple(get_registry().location_terms()) == LOCATION_HINT_TERMS
