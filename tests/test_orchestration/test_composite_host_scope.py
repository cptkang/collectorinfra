"""복합 질의 조사 대상 갭 — 재현에서 **회귀 고정**으로 (Plan 78 W0 · SPEC-composite-gap-tests).

> **2026-08-27 — 두 갭 모두 해소됐다**(W1 = WU-11 · W2 = WU-12). xfail(strict) 마커를
> 전부 제거했고, 이 파일은 이제 *갭 재현*이 아니라 **해소 상태의 회귀 고정**이다.
> 아래 갭 설명은 "무엇이 문제였는지"의 기록으로 남긴다.

사용자 예시를 **실패하는 테스트로 먼저 고정**한다 —
*"CPU 사용량이 80% 이상인 서버를 조회하고 해당 서버에서 동작 중인 프로세스를 보여줘"*

두 갭이 실측됐다(`plans/78` §2.2·§2.3):

    G2  선행 결과(prior_rows) → 조사 대상 전달이 **없다**.
        `_resolve_hostname`은 이번 턴 filter_conditions와 previous_entities만 본다 —
        선행 task가 서버 3행을 반환해도 **대상이 잡히지 않는다**.
    G3  대상이 여럿이어도 **1개로 절단**된다.
        반환 타입이 `Optional[str]`이라 N-대상 fan-out이 구조적으로 불가능하다.

## 왜 xfail(strict=True)인가

해소(W1·W2)는 `plans/80` **WU-11 이후**이고, 그건 **G-BILL(D-127 승인) 뒤**에 있다.
단순 실패로 두면 **무기한 실패 테스트**가 스위트에 남아 회귀 판정을 오염시킨다.

    갭이 살아 있으면  → xfail  (스위트 초록 · 갭은 추적됨)
    W1·W2가 해소하면  → XPASS → **strict라서 실패** → "고쳤으니 마커를 떼라"고 알린다

단순 실패는 무시되지만 xfail-strict는 **해소 시점에 반드시 손대게 만든다** — W0의 의도
("실패로 고정")를 더 잘 만족한다.

## 제약 (`plans/80` §5.2 WU-04 단서)

**라우팅 결과·relevance_score·의도 분류에 단언하지 않는다** — 그 영역은 WU-05(S-1 골든셋
회귀)가 아직 검증하지 않았다. 여기서는 **대상 해소와 대상 수**만 본다.
"""

from __future__ import annotations

import pytest

from src.config import load_config
from src.orchestration.process_query import (
    _resolve_hostname,
    resolve_investigation_targets,
)

# 선행 task t1(data_query)이 반환한 서버 3행 — "CPU 80% 이상인 서버"의 결과에 해당한다.
PRIOR_ROWS = {
    "t1": [
        {"hostname": "svweb001"},
        {"hostname": "svweb002"},
        {"hostname": "svbatch009"},
    ]
}


@pytest.fixture
def targets_on(monkeypatch):
    """W1 대상 전달 경로를 켠다.

    `COMPOSITE_PRIOR_TARGETS_ENABLED`는 **기본 off**다 — 미설정 시 현행 동작과 비트 동일해야
    하기 때문이다(`plans/80` §5.4-③). 갭 해소를 확인하려면 그 기능을 켜야 한다.
    """
    monkeypatch.setenv("COMPOSITE_PRIOR_TARGETS_ENABLED", "true")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


def _isolated(*, prior_rows=None, filters=None) -> dict:
    """후속 task t2(process_query)의 격리 입력."""
    return {
        "parsed_requirements": {"filter_conditions": list(filters or [])},
        "conversation_context": {},
        "prior_rows": prior_rows or {},
    }


def _as_targets(resolved) -> list:
    """단일/복수 반환을 모두 받아 대상 목록으로 정규화한다.

    현행은 `Optional[str]`을 돌려주지만, W2(fan-out) 이후 목록이 되어도 이 테스트가
    그대로 의미를 갖도록 한다.
    """
    if resolved is None:
        return []
    if isinstance(resolved, (list, tuple, set)):
        return list(resolved)
    return [resolved]


def test_g2_prior_rows_become_investigation_targets(targets_on):
    """T-G2 — 선행 task가 서버 3행을 반환했으면 후속 조사 대상이 잡혀야 한다. **해소됨**.

    갭이던 시절: `_resolve_hostname`이 `prior_rows`를 **읽지 않아** 이번 턴 filter도
    previous_entities도 없으면 `None`이었다 — 사용자 목표 ②(조회된 자원의 OS 정보 조회)가
    RCA 경로에서 불성립했다.

    해소: `plans/78` W1 / `plans/80` WU-11 — `resolve_investigation_targets`가 공통 모듈
    (`src.utils.prior_targets`)로 `prior_rows`를 대상으로 해소한다. xfail 마커 제거(2026-08-27).
    """
    targets = [
        t.hostname or t.server_name
        for t in resolve_investigation_targets(_isolated(prior_rows=PRIOR_ROWS)).targets
    ]
    assert targets, (
        "선행 결과 3행이 있는데 조사 대상이 하나도 잡히지 않았다 — "
        "prior_rows → 대상 전달 경로가 없다(G2)."
    )
    assert set(targets) == {"svweb001", "svweb002", "svbatch009"}


def test_g3_all_targets_survive_without_truncation():
    """T-G3 — 대상 3개를 지목하면 3개가 모두 조사 대상이어야 한다. **해소됨**.

    갭이던 시절: `_resolve_hostname`이 첫 조건에서 `return`해 **1개만** 남았다. 부분 결과가
    전체로 오인되고, 그 사실이 사용자에게 드러나지도 않았다(절단 사실 미노출).

    해소: `plans/78` W2 / `plans/80` WU-12 — `resolve_investigation_targets`가 대상 목록을
    돌려주고 `run_process_query`가 fan-out한다. xfail 마커 제거(2026-08-27).

    `_resolve_hostname`은 **단일 대상 호환 래퍼로 남아** 여전히 하나만 돌려준다 —
    N-대상은 목록 API로 본다.
    """
    filters = [
        {"field": "hostname", "value": "svweb001"},
        {"field": "hostname", "value": "svweb002"},
        {"field": "hostname", "value": "svbatch009"},
    ]
    targets = _as_targets([
        t.hostname or t.server_name
        for t in resolve_investigation_targets(_isolated(filters=filters)).targets
    ])
    assert set(targets) == {"svweb001", "svweb002", "svbatch009"}, (
        f"대상 3개 중 {len(targets)}개만 남았다: {targets} — N-대상 fan-out 부재(G3)."
    )


def test_flag_off_keeps_legacy_behavior():
    """★ 회귀 0 — 플래그 미설정(기본)이면 **종전 동작 그대로**다(비트동일).

    W1은 옵트인이다. 켜지 않은 환경에서 `prior_rows`는 여전히 무시된다 —
    이것이 `plans/80` §5.4-③("플래그 신설 시 기본값은 현행 동작")의 실측 근거다.
    """
    load_config.cache_clear()
    assert _resolve_hostname(_isolated(prior_rows=PRIOR_ROWS)) is None

    # G3: 조건이 3개여도 첫 번째만 반환된다
    filters = [
        {"field": "hostname", "value": "svweb001"},
        {"field": "hostname", "value": "svweb002"},
    ]
    assert _resolve_hostname(_isolated(filters=filters)) == "svweb001"
