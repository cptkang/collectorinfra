"""`run_apm_query` 단계 간 상태(`_RunCtx`).

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config import AppConfig
from src.domain import disclosure as disc
from src.domain.query_time import QueryTime
from src.orchestration.apm.calls import _Call, _JobScope
from src.orchestration.apm.reference import _RefPick
from src.orchestration.apm.targets import _TargetText
from src.orchestration.apm.window import WindowPlan
from src.orchestration.entity_link import LinkEntry
from src.routing import apm_source_select as source_select
from src.routing.registry import ViewSpec


@dataclass
class _RunCtx:
    """`run_apm_query` 단계 간 상태(plans/172 — 단계 함수가 차례로 읽고 채운다).

    `task`는 단계 중에 재바인딩된다(선택 조건 `view_args` · 승계 `targets`) — 뒤 단계는 늘 이
    필드의 최신 값을 읽는다. `notices`·`ledger`·`blocked`는 같은 객체를 제자리에서 확장한다.
    """

    task: dict[str, Any]
    isolated: dict[str, Any]
    app_config: AppConfig
    query_time: QueryTime | None
    now: datetime
    label: str
    by_id: dict[str, ViewSpec]
    meta: dict[str, Any]
    source: str = "task"
    views: list[str] = field(default_factory=list)
    notices: list[disc.Disclosure] = field(default_factory=list)
    source_ids: tuple[str, ...] = ()
    decision: source_select.SourceDecision = field(init=False)
    ledger: list[LinkEntry] = field(default_factory=list)
    #: 같은 대상 묶음 1회 변환 캐시(`_hosts_of`)
    linked: dict[str, list[str]] = field(default_factory=dict)
    hostnames: list[str] = field(default_factory=list)
    scoped_hosts: list[str] = field(default_factory=list)
    explicit_hosts: list[str] = field(default_factory=list)
    parsed: dict[str, Any] = field(default_factory=dict)
    windows: dict[str, WindowPlan] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)
    deferred: dict[str, list[str]] = field(default_factory=dict)
    bad_refs: dict[str, Any] = field(default_factory=dict)
    view_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    picks: dict[str, _RefPick] = field(default_factory=dict)
    live: list[str] = field(default_factory=list)
    texts: list[dict[str, str]] = field(default_factory=list)
    resolved: list[_TargetText] = field(default_factory=list)
    instances: list[dict[str, Any]] = field(default_factory=list)
    hostless: list[dict[str, Any]] = field(default_factory=list)
    answers: list[_Call] = field(default_factory=list)
    #: 첫 홉 부하 순위가 정한 호스트별 소스 · 첫 홉 단계와 그 범위 고지 위치(V6-3)
    host_sources: dict[str, list[str]] = field(default_factory=dict)
    first_hop: dict[str, Any] | None = None
    first_hop_failed: dict[str, str] = field(default_factory=dict)
    first_hop_notice: int | None = None
    named_given: list[str] = field(default_factory=list)
    named_texts: list[str] = field(default_factory=list)
    concurrency: int = 0
    call_timeout: float = 0.0
    scope: _JobScope = field(init=False)
    calls: list[_Call] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[_TargetText] = field(default_factory=list)
    missing_named: list[_Call] = field(default_factory=list)
    disclosures: list[disc.Disclosure] = field(default_factory=list)
    accepted: list[_Call] = field(default_factory=list)
