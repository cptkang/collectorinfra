"""서버 식별 역조회(hostname → 폴스타 등록 서버명·IP) + 승격 규칙 + 경로 대칭 (D-188).

배경: 폴스타 템플릿이 `${platformName}`·`${ipAddress}`를 지원하지 않아(EL1008E) 운영 템플릿이
`serverName=${hostname}`로 바뀌었다. 공동존(name ≠ hostname)에서 UI 식별·이력 매칭이 깨지지
않도록 `cmm_resource`를 hostname으로 역조회해 등록명·IP를 붙이고 보수적으로 승격한다.

검증 항목:
    A. SQL 조립 — hostname 완전 일치·server.Server·DTIME IS NULL·LIMIT 2(모호 판별)·D-022 금지 패턴 없음·DB2 방언
    B. lookup_identity — 0건/미등록/실패 None, 1건 dict, 2건 ambiguous
    C. attach_server_identity — 승격 규칙(hostname일 때만 name 승격, ip는 빈 경우만, 모호 시 생략),
       리졸버 None → 존 라벨만, 타임아웃/예외 graceful, Redis 캐시 hit/set
    D. 워커 `_process` — 그래프 도달 전에 server_name이 등록명으로 승격
    E. notifier — `_tier_sse_payload`/`_incident_open_payload`에 server_identity, 본문 존 라인(미부착 시 비트 동일)
    F. API 라우트 — `_attach_server_identity`가 워커와 같은 승격 결과
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_notifier import (
    _incident_open_payload,
    _tier_sse_payload,
    build_workb_body,
)
from noise_gate.application.server_identity import (
    SOURCE_CACHE,
    SOURCE_DB,
    SOURCE_EVENT,
    attach_server_identity,
    source_labels_for,
    zone_labels_for,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent, ServerIdentity
from noise_gate.domain.notification_policy import NotificationDecision, TIER_DASHBOARD
from noise_gate.infrastructure.polestar_hostname_resolver import (
    PolestarHostnameResolver,
    build_server_identity_sql,
)

REF = datetime(2026, 8, 26, 10, 0, 0)


# ─── 공용 페이크 ─────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, rows, error=None):
        self.rows = rows
        self.error = error
        self.executed_sql = None

    async def execute_sql(self, sql):
        self.executed_sql = sql
        if self.error:
            raise self.error
        return SimpleNamespace(rows=self.rows, row_count=len(self.rows))


class _FakeRegistry:
    def __init__(self, client, registered=True):
        self._client = client
        self._registered = registered

    def is_registered(self, db_id):
        return self._registered

    @asynccontextmanager
    async def get_client(self, db_id):
        yield self._client


class _FakeResolver:
    """lookup_identity 덕 타이핑 — 고정 응답/지연/예외."""

    def __init__(self, row=None, delay=0.0, error=None):
        self.row = row
        self.delay = delay
        self.error = error
        self.calls = 0

    async def lookup_identity(self, db_id, hostname):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.row


class _FakeRedis:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.sets = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.sets.append((key, value, ex))


def make_event(**kwargs) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp", server_name="fdgaapd2", hostname="fdgaapd2", ip_address="",
        resource_ancestry="", alarm_id="A-1", severity=2, alarm_status="발생",
        resource_type="server.Cpus", resource_name="CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="", is_clear=False,
    )
    defaults.update(kwargs)
    return AlarmEvent(**defaults)


ROW = {"name": "AP-WEB-01 (WEB AP서버)", "hostname": "fdgaapd2", "ip_address": "10.1.2.3",
       "os_type": "LINUX", "os_version": "Red Hat Enterprise Linux 8.10 (Ootpa)", "ambiguous": False}


# ─── A. SQL 조립 ─────────────────────────────────────────────────────────────

class TestBuildServerIdentitySql:
    def test_postgres_dialect(self):
        sql = build_server_identity_sql("polestar_cm_gp", "fdgaapd2")
        assert "polestar.cmm_resource" in sql
        assert "r.resource_type = 'server.Server'" in sql
        assert "r.dtime IS NULL" in sql
        assert "r.hostname = 'fdgaapd2'" in sql
        assert "r.ipaddress AS ipaddress" in sql
        # OSType: 스칼라 서브쿼리(MAX) — LEFT JOIN 행 증식 없이 단일 값
        assert "(SELECT MAX(cc.stringvalue_short) FROM polestar.core_config_prop cc" in sql
        assert "cc.configuration_id = r.resource_conf_id AND cc.name = 'OSType') AS ostype," in sql
        # OSVerson(폴스타 EAV 원본 철자): 별도 스칼라 서브쿼리 — 배지 툴팁 상세 버전
        assert "(SELECT MAX(cv.stringvalue_short) FROM polestar.core_config_prop cv" in sql
        assert "cv.configuration_id = r.resource_conf_id AND cv.name = 'OSVerson') AS osversion" in sql
        assert "LEFT JOIN" not in sql
        assert sql.rstrip().endswith("LIMIT 2")  # 모호 판별용 2행
        assert "resource_conf_id = " not in sql  # D-022 원 규칙의 조인 형태(r JOIN cc ON …)가 아니라 서브쿼리
        assert "r.name = 'fdgaapd2'" not in sql  # hostname 완전 일치만(이름 매칭 아님)

    def test_db2_dialect(self):
        sql = build_server_identity_sql("polestar_b0", "AAA", db_engine="db2")
        assert "FETCH FIRST 2 ROWS ONLY" in sql
        assert "LIMIT" not in sql

    def test_sql_literal_escaped(self):
        sql = build_server_identity_sql("polestar_cm_gp", "x'y")
        assert "r.hostname = 'x''y'" in sql


# ─── B. lookup_identity ──────────────────────────────────────────────────────

class TestLookupIdentity:
    async def test_single_row(self):
        client = _FakeClient([{"NAME": "AP-WEB-01", "HOSTNAME": "fdgaapd2", "IPADDRESS": "10.1.2.3", "OSTYPE": "LINUX",
                               "OSVERSION": "Red Hat Enterprise Linux 8.10 (Ootpa)"}])
        r = PolestarHostnameResolver(_FakeRegistry(client))
        found = await r.lookup_identity("polestar_cm_gp", "fdgaapd2")
        assert found == {"name": "AP-WEB-01", "hostname": "fdgaapd2", "ip_address": "10.1.2.3",
                         "os_type": "LINUX", "os_version": "Red Hat Enterprise Linux 8.10 (Ootpa)",
                         "ambiguous": False}

    async def test_os_null_is_blank(self):
        client = _FakeClient([{"name": "A", "hostname": "h", "ipaddress": "1.1.1.1", "ostype": None}])
        found = await PolestarHostnameResolver(_FakeRegistry(client)).lookup_identity("polestar_cm_gp", "h")
        assert found["os_type"] == "" and found["os_version"] == ""  # osversion 칼럼 부재도 빈 문자열

    async def test_two_rows_ambiguous(self):
        client = _FakeClient([
            {"name": "A", "hostname": "h", "ipaddress": "1.1.1.1"},
            {"name": "B", "hostname": "h", "ipaddress": "2.2.2.2"},
        ])
        found = await PolestarHostnameResolver(_FakeRegistry(client)).lookup_identity("polestar_cm_gp", "h")
        assert found["ambiguous"] is True and found["name"] == "A"

    async def test_no_rows_none(self):
        found = await PolestarHostnameResolver(_FakeRegistry(_FakeClient([]))).lookup_identity("polestar_cm_gp", "h")
        assert found is None

    async def test_unregistered_db_none(self):
        found = await PolestarHostnameResolver(_FakeRegistry(_FakeClient([ROW]), registered=False)).lookup_identity("x", "h")
        assert found is None

    async def test_query_error_none(self):
        client = _FakeClient([], error=RuntimeError("db down"))
        found = await PolestarHostnameResolver(_FakeRegistry(client)).lookup_identity("polestar_cm_gp", "h")
        assert found is None

    async def test_empty_hostname_none(self):
        found = await PolestarHostnameResolver(_FakeRegistry(_FakeClient([ROW]))).lookup_identity("polestar_cm_gp", "")
        assert found is None


# ─── C. attach_server_identity ───────────────────────────────────────────────

class TestZoneLabels:
    def test_gongjon_gimpo(self):
        zone, zone_label, site = zone_labels_for("polestar_cm_gp")
        assert zone == "gongjon"
        assert "공동존" in zone_label
        assert site == "김포"

    def test_gongjon_yeouido(self):
        assert zone_labels_for("polestar_cm_yd")[2] == "여의도"

    def test_bankjon_prefers_zone_suffix_term(self):
        zone, _, site = zone_labels_for("polestar_b0")
        assert zone == "bankjon" and site == "은행존"

    def test_unknown_db_blank(self):
        assert zone_labels_for("nope") == ("", "", "")


class TestSourceLabels:
    """(D-188 부기) 소스 배지: 제품명 표시 + 위치·db_id 툴팁 — 레지스트리 family 파생."""

    def test_gongjon_gimpo(self):
        label, detail = source_labels_for("polestar_cm_gp", *zone_labels_for("polestar_cm_gp")[1:])
        assert label == "폴스타"
        assert detail == "폴스타 — 공동존 김포; polestar_cm_gp"

    def test_gongjon_yeouido(self):
        detail = source_labels_for("polestar_cm_yd", *zone_labels_for("polestar_cm_yd")[1:])[1]
        assert detail == "폴스타 — 공동존 여의도; polestar_cm_yd"

    def test_bankjon_no_duplicate_zone_word(self):
        # 사이트 라벨(은행존)이 존 약칭(은행존)과 같으면 한 번만
        assert source_labels_for("polestar_b0", *zone_labels_for("polestar_b0")[1:]) == (
            "폴스타", "폴스타 — 은행존; polestar_b0"
        )

    def test_unknown_db_falls_back_to_pieces(self):
        assert source_labels_for("nope") == ("", "nope")  # 라벨 없음 → UI는 사이트 라벨 폴백, 툴팁은 db_id만
        assert source_labels_for("", "", "") == ("", "")


class TestAttach:
    async def test_promotes_name_and_ip_when_template_gave_hostname(self):
        ev = make_event()  # server_name == hostname, ip 비어 있음
        identity = await attach_server_identity(ev, _FakeResolver(ROW))
        assert ev.server_name == ROW["name"]
        assert ev.ip_address == "10.1.2.3"
        assert identity is ev.server_identity
        assert identity.name == ROW["name"] and identity.source == SOURCE_DB
        assert identity.os_type == "LINUX"  # UI 배지 렌더 입력
        assert identity.os_version == "Red Hat Enterprise Linux 8.10 (Ootpa)"  # 배지 툴팁
        assert identity.source_label == "폴스타" and identity.source_detail == "폴스타 — 공동존 김포; polestar_cm_gp"
        assert identity.zone == "gongjon" and identity.site_label == "김포"

    async def test_keeps_explicit_server_name(self):
        ev = make_event(server_name="운영자가 준 이름")
        await attach_server_identity(ev, _FakeResolver(ROW))
        assert ev.server_name == "운영자가 준 이름"  # 템플릿이 별도 서버명을 준 경우 존중
        assert ev.server_identity.name == ROW["name"]  # 식별 정보에는 등록명 보존

    async def test_keeps_existing_ip(self):
        ev = make_event(ip_address="9.9.9.9")
        await attach_server_identity(ev, _FakeResolver(ROW))
        assert ev.ip_address == "9.9.9.9"

    async def test_ambiguous_skips_promotion(self):
        ev = make_event()
        await attach_server_identity(ev, _FakeResolver({**ROW, "ambiguous": True}))
        assert ev.server_name == "fdgaapd2" and ev.ip_address == ""
        assert ev.server_identity.ambiguous is True

    async def test_resolver_none_gives_zone_only(self):
        ev = make_event()
        identity = await attach_server_identity(ev, None)
        assert identity.source == SOURCE_EVENT and identity.name == ""
        assert identity.site_label == "김포"
        assert ev.server_name == "fdgaapd2"

    async def test_empty_hostname_noop(self):
        ev = make_event(hostname="", server_name="")
        assert await attach_server_identity(ev, _FakeResolver(ROW)) is None
        assert ev.server_identity is None

    async def test_timeout_graceful(self):
        ev = make_event()
        identity = await attach_server_identity(ev, _FakeResolver(ROW, delay=0.2), timeout=0.01)
        assert identity.source == SOURCE_EVENT and ev.server_name == "fdgaapd2"

    async def test_exception_graceful(self):
        ev = make_event()
        identity = await attach_server_identity(ev, _FakeResolver(error=RuntimeError("x")))
        assert identity.source == SOURCE_EVENT

    async def test_cache_hit_skips_resolver(self):
        key = "alarm:identity:v2:polestar_cm_gp:fdgaapd2"
        redis = _FakeRedis({key: json.dumps(ROW, ensure_ascii=False).encode("utf-8")})
        resolver = _FakeResolver(ROW)
        ev = make_event()
        identity = await attach_server_identity(ev, resolver, redis=redis)
        assert resolver.calls == 0
        assert identity.source == SOURCE_CACHE and ev.server_name == ROW["name"]

    async def test_cache_set_on_db_hit(self):
        redis = _FakeRedis()
        ev = make_event()
        await attach_server_identity(ev, _FakeResolver(ROW), redis=redis, cache_ttl=123)
        assert len(redis.sets) == 1
        key, value, ex = redis.sets[0]
        assert key == "alarm:identity:v2:polestar_cm_gp:fdgaapd2" and ex == 123
        assert json.loads(value)["name"] == ROW["name"]

    async def test_cache_disabled_when_ttl_zero(self):
        redis = _FakeRedis()
        await attach_server_identity(make_event(), _FakeResolver(ROW), redis=redis, cache_ttl=0)
        assert redis.sets == []

    def test_identity_to_dict_roundtrip(self):
        d = ServerIdentity(name="n", hostname="h", ip_address="i", zone="gongjon").to_dict()
        assert d["name"] == "n" and d["ambiguous"] is False and "os_type" in d
        assert "os_version" in d and "source_label" in d and "source_detail" in d  # SSE/JSON에 실려 UI가 읽는다


# ─── D. 워커 통합 ────────────────────────────────────────────────────────────

class _WorkerRedis:
    async def xack(self, *a, **k):
        return 1

    async def xadd(self, *a, **k):
        return b"1-0"

    async def get(self, key):
        return None

    async def set(self, *a, **k):
        return True


class _FakeGraph:
    def __init__(self):
        self.states = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _worker(resolver) -> tuple[AlarmWorker, _FakeGraph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True, repeat_interval_seconds=14400,
            suppress_max_severity=2, self_heal_window_seconds=300,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _FakeGraph()
    w._graph = g
    w._identity_resolver = resolver
    return w, g


async def test_worker_promotes_server_name_before_graph():
    w, g = _worker(_FakeResolver(ROW))
    payload = {"dbId": "polestar_cm_gp", "serverName": "fdgaapd2", "hostname": "fdgaapd2",
               "alarmId": "A-7", "severity": "경고", "alarmName": "CPU 임계"}
    fields = {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")}
    await w._process(_WorkerRedis(), "alarm:raw", "g", b"1-1", fields, {})
    ev = g.states[0]["alarm_event"]
    assert ev.server_name == ROW["name"]
    assert ev.ip_address == "10.1.2.3"
    assert ev.server_identity.site_label == "김포"
    assert ev.received_at is not None  # 워커 구성 시각(UI '수신' 표시용)


async def test_worker_without_resolver_keeps_hostname():
    w, g = _worker(None)
    payload = {"dbId": "polestar_cm_gp", "serverName": "fdgaapd2", "hostname": "fdgaapd2",
               "alarmId": "A-8", "severity": "2", "alarmName": "x"}
    await w._process(_WorkerRedis(), "alarm:raw", "g", b"1-1",
                     {b"data": json.dumps(payload).encode()}, {})
    ev = g.states[0]["alarm_event"]
    assert ev.server_name == "fdgaapd2" and ev.server_identity.source == SOURCE_EVENT


def test_build_identity_resolver_failure_returns_none(monkeypatch):
    # 상시 동작(플래그 없음) — 생성 실패만 None(graceful), 역조회 없이 존 라벨만 부착된다.
    def _boom(cfg):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("src.routing.db_registry.DBRegistry", _boom)
    w = AlarmWorker(SimpleNamespace(alarm=SimpleNamespace()))
    assert w._build_identity_resolver() is None


# ─── E. notifier payload / 본문 ──────────────────────────────────────────────

def _result(event: AlarmEvent) -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=["workb"],
    )


def _decision() -> NotificationDecision:
    return NotificationDecision(tier=TIER_DASHBOARD, reason="t", priority=300,
                                signals={}, fingerprint="fp")


def test_sse_payload_includes_identity():
    ev = make_event()
    ev.server_identity = ServerIdentity(name="AP-WEB-01", hostname="fdgaapd2", ip_address="10.1.2.3",
                                        zone="gongjon", site_label="김포", source=SOURCE_DB)
    payload = _tier_sse_payload(_result(ev), _decision())
    assert payload["server_identity"]["name"] == "AP-WEB-01"
    assert payload["server_identity"]["site_label"] == "김포"
    incident = _incident_open_payload(_result(ev), _decision())
    assert incident["server_identity"]["ip_address"] == "10.1.2.3"
    # (D-188 부기) 발생·수신 시각 — 워커 SSE·incident 재발행 모두 API 경로와 대칭
    assert payload["alarm_time"] == REF.isoformat() and payload["received_at"] is None
    ev.received_at = datetime(2026, 8, 26, 10, 0, 5)
    assert _tier_sse_payload(_result(ev), _decision())["received_at"] == "2026-08-26T10:00:05"
    assert incident["alarm_time"] == REF.isoformat()


def test_sse_payload_identity_none_when_unattached():
    payload = _tier_sse_payload(_result(make_event()), _decision())
    assert payload["server_identity"] is None


def test_workb_body_zone_line_only_when_identity():
    ev = make_event()
    body_plain = build_workb_body(_result(ev))
    assert "<b>존:</b>" not in body_plain
    ev.server_identity = ServerIdentity(hostname="fdgaapd2", zone="gongjon", site_label="김포")
    body = build_workb_body(_result(ev))
    assert "<b>존:</b> 김포<br>" in body
    # 존 라인 외 본문은 동일
    assert body.replace("<b>존:</b> 김포<br>", "") == body_plain


# ─── F. API 라우트 대칭 ──────────────────────────────────────────────────────

async def test_route_attach_symmetric_with_worker(monkeypatch):
    from src.api.routes import alarm as route_mod

    class _R:
        def __init__(self, registry):
            pass

        async def lookup_identity(self, db_id, hostname):
            return ROW

    monkeypatch.setattr(
        "noise_gate.infrastructure.polestar_hostname_resolver.PolestarHostnameResolver", _R
    )
    monkeypatch.setattr("src.routing.db_registry.DBRegistry", lambda cfg: object())
    cfg = SimpleNamespace(alarm=SimpleNamespace(server_identity_timeout_seconds=1.0))
    ev = make_event()
    await route_mod._attach_server_identity(cfg, ev)
    assert ev.server_name == ROW["name"] and ev.ip_address == "10.1.2.3"
    assert route_mod._identity_dict(ev)["name"] == ROW["name"]


async def test_route_attach_resolver_failure_zone_only(monkeypatch):
    from src.api.routes import alarm as route_mod

    def _boom(cfg):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("src.routing.db_registry.DBRegistry", _boom)
    cfg = SimpleNamespace(alarm=SimpleNamespace())
    ev = make_event()
    await route_mod._attach_server_identity(cfg, ev)
    assert ev.server_name == "fdgaapd2"
    assert ev.server_identity.site_label == "김포" and ev.server_identity.source == SOURCE_EVENT
