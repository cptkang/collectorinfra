"""노이즈 캔슬링 목업 이벤트 생성기 (Mock Polestar Event Generator).

Plan 65(`plans/65-noise-cancellation-mock-event-generator.md`) R1 구현.

폴스타 실계 없이 **사전 정의된 알람 이벤트를 번호 입력만으로 생성·주입**하여,
collectorinfra 노이즈 캔슬링 구조(alarm_server → Redis Stream → AlarmWorker → 노이즈 게이트)가
해당 이벤트를 캔슬링(SUPPRESS/DASHBOARD 등)하는지 사용자가 직접 테스트하는 대화형 도구다.

구성(§3.1):
    - 시나리오 카탈로그(SCENARIOS): 번호 고정, 사전 정의 이벤트(단건/시퀀스) + 기대 티어 + 필요 플래그.
    - 이벤트 빌더(make_payload): §2.2 스키마의 단일행 JSON 페이로드 생성(실측:
      `src/api/routes/alarm.py::_build_alarm_event_from_payload`의 실제 키).
    - 전송기: TcpSender(기본, socket→localhost:9100) / RedisSender(폴백, XADD alarm:raw).
    - 판정기(Judge): `logs/alarm_decisions.jsonl` 폴링·alarm_id별 tier/감사 필드 대조
      (실측: `noise_gate/infrastructure/decision_store.py`가 실제로 적재하는 필드).
    - 메뉴 루프: 기동 점검 → 번호 입력 → 주입·판정·결과 출력 → 메뉴 복귀(stdlib input()).

사용:
    python scripts/mock_polestar_events.py                     # 대화형 메뉴(기본)
    python scripts/mock_polestar_events.py --send dup-suppress # 단발 주입 후 종료(자동화/e2e)
    python scripts/mock_polestar_events.py --path redis        # Redis 직주입 폴백

기동 옵션(§5): --host/--port/--path tcp|redis/--redis-url/--decision-log/--timeout/--db-id/--send.

원칙: 파이프라인(src/) 무변경 — 본 도구는 **주입·관찰만** 한다(읽기전용 D-003). 전제 미충족
(서버 미기동·플래그 off·모델 부재) 시 침묵 실패 없이 원인과 해결 방법을 출력한다(Known Mistakes).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# ─── 기본 상수 (기동 옵션 기본값 · §5) ────────────────────────────────────────

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9100                              # ALARM_SERVER_SOCKET_PORT 기본
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_DECISION_LOG = "logs/alarm_decisions.jsonl"
DEFAULT_DB_ID = "polestar_pg"                    # 도커 폴스타 픽스처 프로필(testdata/pg)
DEFAULT_TIMEOUT = 30.0                           # 판정 대기(초)
STREAM_KEY = "alarm:raw"                         # base_receiver.py XADD 대상

# 도커 폴스타 픽스처 서버(testdata/pg/init/06_plan52_noise_fixtures.sql) — DB 연동 신호 필요 시나리오용.
SRV_LOW = "noise-test-low"      # importance_id=1(낮음)
SRV_MED = "noise-test-med"      # importance_id=2(보통)
SRV_HIGH = "noise-test-high"    # importance_id=3(높음)
SRV_MAINT = "noise-test-maint"  # is_maintenance=1

# §2.4 severity 매핑 가정(가용성 DOWN=2, UP=0 클리어, 사용률 임계=1, 설비 단락 테스트=3).
SEV_CLEAR = 0
SEV_THRESHOLD = 1
SEV_AVAIL_DOWN = 2
SEV_FACILITY_PAGE = 3

# §2.2 이벤트 페이로드 스키마의 확정 키 집합(실측: _build_alarm_event_from_payload).
REQUIRED_PAYLOAD_KEYS = frozenset({
    "dbId", "serverName", "hostname", "ipAddress", "resourceAncestry", "alarmId",
    "severity", "alarmStatus", "resourceType", "resourceName", "alarmName",
    "alarmTime", "conditions", "conditionLog",
})

# 메뉴 보조 키(§3.1-4).
KEY_LIST = "l"
KEY_TOGGLE = "v"
KEY_QUIT = "q"

# 결정 감사 tier 값(실측: notification_policy.TIER_*).
TIER_PAGE = "page"
TIER_TICKET = "ticket"
TIER_DASHBOARD = "dashboard"
TIER_SUPPRESS = "suppress"


# ─── 이벤트 빌더 (§2.2 스키마 · §2.4 매핑 규칙) ────────────────────────────────

def mock_alarm_id(run_id: str, seq: int) -> str:
    """목업 알람 식별자를 `MOCK-<run_id>-<seq>` 형식으로 생성한다(판정 추적 키, §2.2)."""
    return f"MOCK-{run_id}-{seq}"


def make_payload(
    *,
    db_id: str,
    server_name: str,
    severity: int,
    alarm_name: str,
    alarm_id: str,
    hostname: Optional[str] = None,
    ip_address: str = "10.0.0.1",
    resource_type: str = "server.Server",
    resource_name: str = "",
    resource_ancestry: str = "",
    conditions: str = "",
    condition_log: str = "",
    alarm_status: str = "NOT_ACK",
    alarm_time: Optional[str] = None,
) -> dict:
    """폴스타 단일행 JSON 템플릿과 동일한 키의 알람 페이로드를 생성한다(§2.2 스키마).

    키 집합은 REQUIRED_PAYLOAD_KEYS와 정확히 일치한다(실측:
    `_build_alarm_event_from_payload`가 소비하는 필드). alarmTime 미지정 시 현재 시각을
    `%Y%m%d%H%M%S`로 채운다. hostname 미지정 시 server_name과 동일하게 채운다.
    """
    return {
        "dbId": db_id,
        "serverName": server_name,
        "hostname": server_name if hostname is None else hostname,
        "ipAddress": ip_address,
        "resourceAncestry": resource_ancestry,
        "alarmId": alarm_id,
        "severity": severity,
        "alarmStatus": alarm_status,
        "resourceType": resource_type,
        "resourceName": resource_name,
        "alarmName": alarm_name,
        "alarmTime": alarm_time or datetime.now().strftime("%Y%m%d%H%M%S"),
        "conditions": conditions,
        "conditionLog": condition_log,
    }


# ─── 시나리오 자료구조 (번호 고정 카탈로그) ────────────────────────────────────

@dataclass
class Step:
    """시나리오의 단일 주입 단위(페이로드 + 기대 판정 + 시퀀스 간격)."""

    payload: dict
    label: str                                       # 진행 표시용 라벨
    interval_after: float = 0.0                      # 다음 스텝 전 대기(초, 시퀀스)
    expect_tiers: Optional[frozenset] = None         # 기대 tier 집합(None=결정 레코드면 통과)
    expect_recurrence: bool = False                  # dedup 억제(type=recurrence) 기대
    expect_no_dedup: bool = False                    # 독립 판정(dedup 아님) 기대 — 음성 대조군
    show_audit: bool = False                         # Plan 60 감사 필드 표시 여부
    expect_investigation: bool = False               # 자동 조사 트리거 감사 레코드 조회 기대(§4.3)

    @property
    def alarm_id(self) -> str:
        """이 스텝 페이로드의 alarmId."""
        return str(self.payload["alarmId"])


@dataclass(frozen=True)
class Scenario:
    """번호 고정 사전 정의 시나리오(단건 또는 시퀀스)."""

    number: int
    name: str
    title: str                                       # 메뉴 한 줄 설명
    expect_label: str                                # 기대 결과 표시
    category: str                                    # "basic" | "plan60" | "sre"
    build: Callable[[str, str], list]                # (run_id, db_id) -> list[Step]
    required_flags: tuple = ()                        # ((config_attr, env_key), ...)
    requires_model: bool = False                     # 로컬 임베딩 모델 필요(B-7)
    requires_investigation_service: bool = False     # sre_agent 조사 서비스 도달성 점검(§4.3)
    flag_note: str = ""                              # 메뉴의 ⚑ 플래그 표기
    stub: bool = False                               # 미구현 보류(현재 미사용 — 확장 여지)
    stub_reason: str = ""


# ─── 시나리오 빌더 (§4.1 기본 6종 · §4.2 Plan 60 5종 · §4.3 invest-trigger) ────

def _build_sev3_page(run_id: str, db_id: str) -> list:
    """[1] sev3-page — 설비 UPS 경고 severity 3 단건(S8). 심각도3 단락 → PAGE."""
    return [
        Step(
            payload=make_payload(
                db_id=db_id, server_name="SAE 0011649", severity=SEV_FACILITY_PAGE,
                alarm_name="UPS 출력 전압 하한 경고", resource_type="facility.UPS",
                resource_name="자본시장추진부 UPS", conditions="< 하한 임계치",
                condition_log="[출력 전압 하한 경고 임계치 미만]",
                alarm_id=mock_alarm_id(run_id, 1),
            ),
            label="sev3 단건",
            expect_tiers=frozenset({TIER_PAGE}),
        )
    ]


def _build_low_suppress(run_id: str, db_id: str) -> list:
    """[2] low-suppress — 중요도 낮음 서버 /fsapp 사용률 sev1(S2). 매트릭스 억제/강등."""
    return [
        Step(
            payload=make_payload(
                db_id=db_id, server_name=SRV_LOW, severity=SEV_THRESHOLD,
                alarm_name="파일시스템 사용률", resource_name="/fsapp",
                conditions="> 90 %, 2회 연속", condition_log="[90 % (> 90 %, 2회 연속)]",
                alarm_id=mock_alarm_id(run_id, 1),
            ),
            label="sev1 중요도 낮음",
            expect_tiers=frozenset({TIER_SUPPRESS, TIER_DASHBOARD, TIER_TICKET}),
        )
    ]


def _build_maint_suppress(run_id: str, db_id: str) -> list:
    """[3] maint-suppress — 유지보수 중 서버 가용성 DOWN sev2(S4). SUPPRESS."""
    return [
        Step(
            payload=make_payload(
                db_id=db_id, server_name=SRV_MAINT, severity=SEV_AVAIL_DOWN,
                alarm_name="가용성", resource_name="가용성", conditions="= DOWN, 2회 연속",
                condition_log="[DOWN (= DOWN, 2회 연속)]",
                alarm_id=mock_alarm_id(run_id, 1),
            ),
            label="sev2 유지보수",
            expect_tiers=frozenset({TIER_SUPPRESS}),
        )
    ]


def _build_dup_suppress(run_id: str, db_id: str) -> list:
    """[4] dup-suppress — 동일 알람 2연속(S6 FAX#1 재발). 2건째 dedup 억제."""
    common = dict(
        db_id=db_id, server_name="stafxo01", severity=SEV_AVAIL_DOWN, alarm_name="가용성",
        resource_name="FAX#1", conditions="= DOWN, 2회 연속",
        condition_log="[DOWN (= DOWN, 2회 연속)]",
    )
    return [
        Step(make_payload(**common, alarm_id=mock_alarm_id(run_id, 1)),
             label="1건째", expect_tiers=None, interval_after=3.0),
        Step(make_payload(**common, alarm_id=mock_alarm_id(run_id, 2)),
             label="2건째(dedup)", expect_recurrence=True),
    ]


def _build_clear(run_id: str, db_id: str) -> list:
    """[5] clear — DOWN(sev2) → UP(sev0) 순단(S1). 2건째 클리어 처리(통보 아님)."""
    common = dict(
        db_id=db_id, server_name="nspist02", alarm_name="가용성",
        resource_name="일체형-망연계스트리밍(내부)#2",
    )
    return [
        Step(make_payload(**common, severity=SEV_AVAIL_DOWN, conditions="= DOWN, 2회 연속",
                          condition_log="[DOWN (= DOWN, 2회 연속)]",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="DOWN", expect_tiers=None, interval_after=3.0),
        Step(make_payload(**common, severity=SEV_CLEAR, conditions="= UP",
                          condition_log="[UP (= UP)]", alarm_id=mock_alarm_id(run_id, 2)),
             label="UP(클리어)", expect_tiers=frozenset({TIER_SUPPRESS, TIER_DASHBOARD})),
    ]


def _build_distinct_pair(run_id: str, db_id: str) -> list:
    """[6] distinct-pair — 같은 호스트 상이 알람 2건(S7). 음성 대조군: 각각 독립 판정."""
    return [
        Step(make_payload(db_id=db_id, server_name="nipdbr36", severity=SEV_AVAIL_DOWN,
                          alarm_name="가용성", resource_name="운영-MCIDDB#2",
                          conditions="= DOWN, 2회 연속",
                          condition_log="[DOWN (= DOWN, 2회 연속)]",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="가용성 DOWN", expect_tiers=None, interval_after=2.0),
        Step(make_payload(db_id=db_id, server_name="nipdbr36", severity=SEV_AVAIL_DOWN,
                          alarm_name="서버 기동 지속시간", resource_name="운영-MCIDDB#2",
                          conditions="< 1.5 min", condition_log="[7 sec (< 1.5 min)]",
                          alarm_id=mock_alarm_id(run_id, 2)),
             label="서버 기동 지속시간", expect_no_dedup=True),
    ]


def _build_recur_audit(run_id: str, db_id: str) -> list:
    """[7] recur-audit (E1) — 동일 알람 3회 반복(S6). 재발 count 감사(recurrence)."""
    common = dict(
        db_id=db_id, server_name="stafxo01", severity=SEV_AVAIL_DOWN, alarm_name="가용성",
        resource_name="FAX#1", conditions="= DOWN, 2회 연속",
        condition_log="[DOWN (= DOWN, 2회 연속)]",
    )
    return [
        Step(make_payload(**common, alarm_id=mock_alarm_id(run_id, 1)),
             label="1건째", expect_tiers=None, interval_after=2.0, show_audit=True),
        Step(make_payload(**common, alarm_id=mock_alarm_id(run_id, 2)),
             label="2건째(재발)", expect_recurrence=True, interval_after=2.0, show_audit=True),
        Step(make_payload(**common, alarm_id=mock_alarm_id(run_id, 3)),
             label="3건째(재발)", expect_recurrence=True, show_audit=True),
    ]


def _build_cross_host(run_id: str, db_id: str) -> list:
    """[8] cross-host (E2) — 유사 역할 3호스트 동시 가용성 DOWN(S4 IPL 축소판). 대표 외 억제."""
    hosts = ["staapos1", "staapos2", "stadbos1"]
    steps: list = []
    for i, host in enumerate(hosts):
        steps.append(Step(
            make_payload(db_id=db_id, server_name=host, severity=SEV_AVAIL_DOWN,
                         alarm_name="가용성", resource_name="직판AP/DB",
                         conditions="= DOWN, 2회 연속",
                         condition_log="[DOWN (= DOWN, 2회 연속)]",
                         alarm_id=mock_alarm_id(run_id, i + 1)),
            label=host,
            expect_tiers=(None if i == 0 else frozenset({TIER_SUPPRESS})),
            interval_after=(2.0 if i < len(hosts) - 1 else 0.0),
            show_audit=True,
        ))
    return steps


def _build_cascade(run_id: str, db_id: str) -> list:
    """[9] cascade (E4) — 상위(root) 자원 → 하위 자원 연쇄(S5 전기작업→네트워크→장비).

    ⚠ AVAIL_DEPEND 토폴로지 픽스처가 있어야 하위 알람의 cascaded=true가 관측된다
    (현 픽스처 부재 시 cascaded=null — Plan 65 §7 G-3, testdata 확장은 본 범위 밖).
    """
    return [
        Step(make_payload(db_id=db_id, server_name=SRV_HIGH, severity=SEV_AVAIL_DOWN,
                          alarm_name="가용성", resource_name="상위-네트워크",
                          resource_type="network.Switch", conditions="= 장애",
                          condition_log="[장애 (= DOWN)] 세종대 네트워크 3등급 장애",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="상위(root) 자원", expect_tiers=None, interval_after=3.0, show_audit=True),
        Step(make_payload(db_id=db_id, server_name=SRV_MED, severity=SEV_AVAIL_DOWN,
                          alarm_name="가용성", resource_name="하위-서버",
                          conditions="= DOWN, 2회 연속",
                          condition_log="[DOWN (= DOWN, 2회 연속)]",
                          alarm_id=mock_alarm_id(run_id, 2)),
             label="하위 자원(연쇄)",
             expect_tiers=frozenset({TIER_SUPPRESS, TIER_DASHBOARD}), show_audit=True),
    ]


def _build_change_corr(run_id: str, db_id: str) -> list:
    """[10] change-corr (E5) — 변경이력 근접 가용성 DOWN(S4 예정된 IPL 작업). 변경 근접 오버레이.

    ⚠ cmm_resource_lifecycle_history 변경이력 픽스처가 있어야 change_nearby가 관측된다.
    change 상관은 억제가 아니라 승격(promote) 신호 — 결정 레코드는 정상 통보되고 변경 근접이
    priority/worker 로그로 관측된다(현 픽스처 부재 시 change_nearby 미표기 — §7 G-3).
    """
    return [
        Step(make_payload(db_id=db_id, server_name=SRV_MED, severity=SEV_AVAIL_DOWN,
                          alarm_name="가용성", resource_name="직판DB#1",
                          conditions="= DOWN, 2회 연속",
                          condition_log="[DOWN (= DOWN, 2회 연속)] 예정된 IPL 작업으로 발생",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="변경 근접 알람", expect_tiers=None, show_audit=True),
    ]


def _build_semantic_dup(run_id: str, db_id: str) -> list:
    """[11] semantic-dup (B-7) — 표현만 다른 유사 텍스트 쌍(S3 운영자 주석 재발신).

    의미적 근접중복 주석(semantic_near_dup)은 **지문이 다르지만 의미가 유사**할 때만 붙는다
    (실측: alarm_worker._annotate_semantic_dedup — score>=임계 AND matched_fp!=fingerprint).
    지문 = f(db_id, server, alarm_name, resource_name)이므로, 동일 지문 재발신은 결정적 dedup으로
    처리되어 주석 경로에 도달하지 않는다. 따라서 서버명을 달리해(szaaso01/szaaso02) 지문을
    분리하되 텍스트(alarm_name+resource_type+condition_log)는 유사하게 유지한다.
    """
    return [
        Step(make_payload(db_id=db_id, server_name="szaaso01", severity=SEV_THRESHOLD,
                          alarm_name="파일시스템 사용률", resource_name="/fsapp",
                          conditions="> 95 %", condition_log="[95 % (> 95 %)]",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="원 알람", expect_tiers=None, interval_after=3.0, show_audit=True),
        Step(make_payload(db_id=db_id, server_name="szaaso02", severity=SEV_THRESHOLD,
                          alarm_name="파일시스템 사용률", resource_name="/fsapp",
                          conditions="> 95 %",
                          condition_log="[95 % (> 95 %)] => 담당자 ○○○ 수석차장. 서비스 영향 없음.",
                          alarm_id=mock_alarm_id(run_id, 2)),
             label="표현만 다른 유사 알람", expect_tiers=None, show_audit=True),
    ]


def _build_invest_trigger(run_id: str, db_id: str) -> list:
    """[12] invest-trigger (§4.3 · Plan 66 R9) — sev3 PAGE 단건 → 게이트 훅 자동 조사 submit.

    S8 변형 설비 UPS 경고 severity 3 단건(PAGE 단락 확정)을 주입하면, notification_gate 직후
    investigation_trigger 노드(Plan 64 CW-A, 3-A 배선)가 `sre_investigate_alarm`을 submit→poll하고
    그 결과(investigation_id·최종 status·verdict)를 decision_store.record_investigation으로
    `logs/alarm_decisions.jsonl`에 `type="investigation"` 레코드로 감사한다(실측: decision_store
    §183·investigation_trigger_node §112). 판정기는 이 investigation 감사 레코드를 조회해
    accepted(신규 investigation_id) / duplicate(기존 id 재사용·dispatcher dedup) / submit 실패
    (investigation_id 없음 — 서비스 미기동 graceful)를 표시한다(classify_investigation).

    ※ 기본(스텁 서비스·LLM 키 부재)은 submit 수용·investigation_id 확인까지 검증한다. 실 HolmesGPT
    조사 완주·브리핑 수신 대조는 LLM 비용이 발생하므로 RUN_E2E=1 옵트인에서만 한다(Plan 65 §4.3).
    조사 서비스 미기동이어도 게이트 판정·통보는 정상 완료되고 트리거만 graceful 실패한다(비차단 계약).
    """
    return [
        Step(make_payload(db_id=db_id, server_name="SAE 0011649", severity=SEV_FACILITY_PAGE,
                          alarm_name="UPS 출력 전압 하한 경고", resource_type="facility.UPS",
                          resource_name="자본시장추진부 UPS", conditions="< 하한 임계치",
                          condition_log="[출력 전압 하한 경고 임계치 미만]",
                          alarm_id=mock_alarm_id(run_id, 1)),
             label="sev3 PAGE(조사 트리거)", expect_tiers=frozenset({TIER_PAGE}),
             expect_investigation=True),
    ]


def _build_non_alarm(run_id: str, db_id: str) -> list:
    """[13] non-alarm (E7-b, S8) — 승인/안내성 비알람 단건(마커 사전 억제 대상).

    Plan 65 §2.4 S8 실측: `내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다`(승인요청·비알람).
    알람 마커(가용성/사용률/장애/DOWN/경고 등)가 **부재**하고 비알람 마커(승인/요청/바랍니다)가
    **존재**하므로 is_operational_alarm=False → non_alarm_filter_enabled on 시 게이트 step0.5에서
    SUPPRESS("비운영 알람")로 사전 억제된다(실측: notification_policy.is_operational_alarm은
    alarm_name·condition_log·conditions·resource_name·resource_type 텍스트를 마커로 판정하고,
    decide_notification step0.5가 심각도3 단락보다 앞서 억제). 담당자는 가명화(○○○)한다.
    """
    return [
        Step(
            payload=make_payload(
                db_id=db_id, server_name="내부Cloud", severity=SEV_THRESHOLD,
                alarm_name="Cloud PC 사양변경 승인 요청", resource_type="notice.Approval",
                resource_name="Cloud PC",
                condition_log="내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다",
                alarm_id=mock_alarm_id(run_id, 1),
            ),
            label="비알람(승인요청)",
            expect_tiers=frozenset({TIER_SUPPRESS}),
            show_audit=True,
        )
    ]


def _build_net_site_cascade(run_id: str, db_id: str) -> list:
    """[14] net-site-cascade (E7-c/d, S5) — 동일 사이트 네트워크 장비 다발 장애(사이트 상관).

    Plan 65 §2.4 S5 실측: 네트워크 장비는 `<장비ID>.<도메인>||(장애) <사이트명>` 별도 형식으로
    수신된다(예: `S8530JUM-4331-1.sotori.com||(장애) 세종대`, 페어 `K8530JUM-4331-2 …`).
    이 마커를 **serverName**에 실어(실측: `_build_alarm_event_from_payload`는 serverName/resourceName
    에서 `extract_site_token`으로 사이트 토큰을 파싱) format_tolerant_parsing_enabled(E7-c) 파싱 시
    raw_payload._site_token='세종대'가 노출되고, correlation_site_dimension_enabled(E7-d) 시 워커가
    이 site 토큰을 signature_tokens의 extra로 주입해 E2 상관에 사이트 차원을 더한다. 두 장비는 동일
    사이트 '세종대'로 상관된다(기존 [9] cascade는 conditionLog에만 사이트명을 실어 추출 0 — 별도 신규).

    ※ 실제 E2 억제(대표 외 SUPPRESS)까지 관측하려면 cross_host_correlation_enabled도 함께 켜야 한다
    (실측: alarm_worker._correlate_and_maybe_suppress는 cross_host_correlation_enabled 게이트 하에서만
    site_extra를 산출 — 사이트 차원은 상관을 강화할 뿐). 본 시나리오의 1차 관측은 site 토큰 추출·
    차원 반영이며, 각 스텝은 결정 레코드 존재를 확인한다(expect_tiers=None, 감사 필드 표시).
    """
    devices = [
        "S8530JUM-4331-1.sotori.com||(장애) 세종대",
        "K8530JUM-4331-2.sotori.com||(장애) 세종대",
    ]
    steps: list = []
    for i, device in enumerate(devices):
        steps.append(Step(
            make_payload(
                db_id=db_id, server_name=device, severity=SEV_AVAIL_DOWN,
                alarm_name="가용성", resource_type="network.Switch",
                resource_name="네트워크 장비", conditions="= DOWN, 2회 연속",
                condition_log="[DOWN (= DOWN, 2회 연속)] 영업점 세종대 네트워크 3등급 장애",
                alarm_id=mock_alarm_id(run_id, i + 1),
            ),
            label=f"세종대 네트워크 장비 {i + 1}",
            expect_tiers=None,
            interval_after=(2.0 if i < len(devices) - 1 else 0.0),
            show_audit=True,
        ))
    return steps


# 번호 고정 카탈로그 — 순서·번호를 변경하면 단위 테스트가 실패한다(고정 계약).
SCENARIOS: tuple = (
    Scenario(1, "sev3-page", "UPS 출력 전압 하한 sev3", "PAGE",
             "basic", _build_sev3_page),
    Scenario(2, "low-suppress", "중요도 낮음 /fsapp 사용률 sev1", "SUPPRESS/TICKET",
             "basic", _build_low_suppress),
    Scenario(3, "maint-suppress", "유지보수 중 가용성 DOWN sev2", "SUPPRESS",
             "basic", _build_maint_suppress),
    Scenario(4, "dup-suppress", "FAX#1 가용성 DOWN 2연속", "2건째 SUPPRESS(dedup)",
             "basic", _build_dup_suppress),
    Scenario(5, "clear", "가용성 DOWN → UP 순단", "2건째 통보 없음",
             "basic", _build_clear),
    Scenario(6, "distinct-pair", "같은 호스트 상이 알람 2건", "각각 독립 판정",
             "basic", _build_distinct_pair),
    Scenario(7, "recur-audit", "동일 알람 3회 반복 (E1)", "recurrence 감사",
             "plan60", _build_recur_audit),
    Scenario(8, "cross-host", "3개 호스트 동시 DOWN (E2)", "correlation_meta",
             "plan60", _build_cross_host,
             required_flags=(("cross_host_correlation_enabled",
                              "NOISE_CROSS_HOST_CORRELATION_ENABLED"),),
             flag_note="⚑ NOISE_CROSS_HOST_CORRELATION_ENABLED"),
    Scenario(9, "cascade", "상위→하위 자원 연쇄 (E4)", "noise_ctx.cascaded",
             "plan60", _build_cascade,
             required_flags=(("multi_hop_cascade_enabled", "NOISE_MULTI_HOP_CASCADE_ENABLED"),
                             ("dependency_suppression", "NOISE_DEPENDENCY_SUPPRESSION")),
             flag_note="⚑ NOISE_MULTI_HOP_CASCADE_ENABLED (+DEPENDENCY_SUPPRESSION, 토폴로지 픽스처)"),
    Scenario(10, "change-corr", "IPL/변경이력 근접 알람 (E5)", "noise_ctx.change_nearby",
             "plan60", _build_change_corr,
             required_flags=(("change_correlation_enabled", "NOISE_CHANGE_CORRELATION_ENABLED"),),
             flag_note="⚑ NOISE_CHANGE_CORRELATION_ENABLED (+변경이력 픽스처)"),
    Scenario(11, "semantic-dup", "운영자 주석 재발신 쌍 (B-7)", "semantic_annotation",
             "plan60", _build_semantic_dup,
             required_flags=(("semantic_dedup_annotation_enabled",
                              "NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED"),),
             requires_model=True,
             flag_note="⚑ NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED + 로컬 모델(docs/19)"),
    Scenario(12, "invest-trigger", "sev3 PAGE→자동 조사 submit", "PAGE + submit accepted/duplicate",
             "sre", _build_invest_trigger,
             required_flags=(("investigation_trigger_enabled",
                              "NOISE_INVESTIGATION_TRIGGER_ENABLED"),),
             requires_investigation_service=True,
             flag_note="⚑ NOISE_INVESTIGATION_TRIGGER_ENABLED + sre_agent 조사 서비스(RUN_E2E=1 완주)"),
    Scenario(13, "non-alarm", "승인/안내성 비알람 (E7-b)", "SUPPRESS(비운영)",
             "plan60e7", _build_non_alarm,
             required_flags=(("non_alarm_filter_enabled",
                              "NOISE_NON_ALARM_FILTER_ENABLED"),),
             flag_note="⚑ NOISE_NON_ALARM_FILTER_ENABLED"),
    Scenario(14, "net-site-cascade", "동일 사이트 네트워크 장비 다발 (E7-c/d)",
             "site 토큰 추출 + 상관 차원", "plan60e7", _build_net_site_cascade,
             required_flags=(("format_tolerant_parsing_enabled",
                              "NOISE_FORMAT_TOLERANT_PARSING_ENABLED"),
                             ("correlation_site_dimension_enabled",
                              "NOISE_CORRELATION_SITE_DIMENSION_ENABLED")),
             flag_note=("⚑ NOISE_FORMAT_TOLERANT_PARSING_ENABLED "
                        "+ NOISE_CORRELATION_SITE_DIMENSION_ENABLED (E2 억제 관측 시 +CROSS_HOST)")),
)

SCENARIOS_BY_NUMBER: dict = {s.number: s for s in SCENARIOS}
SCENARIOS_BY_NAME: dict = {s.name: s for s in SCENARIOS}


# ─── 전송기 (TcpSender 기본 / RedisSender 폴백) ────────────────────────────────

class TcpSender:
    """폴스타 실경로(alarm_server TcpReceiver)로 단일행 JSON을 전송한다(기본 경로)."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def label(self) -> str:
        """전송 대상 표시 라벨."""
        return f"TCP {self.host}:{self.port}"

    def check(self) -> tuple:
        """전송 경로 도달성을 점검한다 → (ok, 사유)."""
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                return True, f"{self.label} 도달"
        except OSError as exc:
            return False, (
                f"{self.label} 도달 실패 ({exc}) — 'python -m noise_gate.alarm_server' 기동 후 재시도"
                " 하거나 '--path redis'로 폴백하세요"
            )

    def send(self, payload: dict) -> None:
        """페이로드를 개행 종단 단일행 JSON으로 소켓 전송한다(TcpReceiver.readline 대응)."""
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(data)


class RedisSender:
    """Redis Stream(alarm:raw)에 직접 XADD한다(수신부 제외 폴백 경로)."""

    def __init__(self, url: str, timeout: float) -> None:
        self.url = url
        self.timeout = timeout

    @property
    def label(self) -> str:
        """전송 대상 표시 라벨."""
        return f"Redis {self.url} (XADD {STREAM_KEY})"

    def _client(self):  # noqa: ANN202 - redis 타입은 지연 임포트
        """redis 클라이언트를 생성한다(지연 임포트 — 기존 프로젝트 의존성)."""
        import redis

        return redis.Redis.from_url(self.url, socket_timeout=self.timeout)

    def check(self) -> tuple:
        """Redis 도달성을 ping으로 점검한다 → (ok, 사유)."""
        try:
            client = self._client()
            client.ping()
            client.close()
            return True, f"{self.label} 도달"
        except Exception as exc:  # redis.ConnectionError 등 — 폴백 경로 사유 노출
            return False, f"{self.label} 도달 실패 ({exc})"

    def send(self, payload: dict) -> None:
        """페이로드를 base_receiver와 동일 포맷 {\"data\": <json>}으로 XADD한다."""
        client = self._client()
        client.xadd(STREAM_KEY, {"data": json.dumps(payload, ensure_ascii=False)})
        client.close()


def build_sender(args: argparse.Namespace):  # noqa: ANN201
    """--path 옵션에 따라 전송기를 생성한다(tcp 기본 / redis 폴백)."""
    if args.path == "redis":
        return RedisSender(args.redis_url, args.timeout)
    return TcpSender(args.host, args.port, args.timeout)


# ─── 판정기 (결정 JSONL 폴링·대조 · §3.3) ──────────────────────────────────────

class Judge:
    """`logs/alarm_decisions.jsonl`을 폴링해 alarm_id별 결정/재발 레코드를 조회한다."""

    def __init__(self, path: str, poll_interval: float = 1.0) -> None:
        self.path = Path(path)
        self.poll_interval = poll_interval

    def current_offset(self) -> int:
        """현재 파일 크기(바이트)를 반환한다(주입 후 append분만 조회하기 위한 기준)."""
        return self.path.stat().st_size if self.path.exists() else 0

    def wait_for(self, alarm_id: str, offset: int, timeout: float) -> Optional[dict]:
        """offset 이후 append된 JSONL에서 alarm_id 레코드를 timeout까지 대기한다.

        결정 레코드(tier 보유)와 재발 레코드(type=recurrence)를 모두 매칭한다
        (실측: decision_store.record / record_recurrence 모두 alarm_id 필드를 남긴다).
        미검출 시 None을 반환한다(전제 미충족·타임아웃 — 침묵 실패 아님).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.path.exists():
                with self.path.open(encoding="utf-8") as fh:
                    fh.seek(offset)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("alarm_id") == alarm_id:
                            return rec
            time.sleep(self.poll_interval)
        return None

    def wait_for_investigation(
        self, alarm_id: str, offset: int, timeout: float
    ) -> Optional[dict]:
        """offset 이후 JSONL에서 `type="investigation"`·alarm_id 일치 레코드를 대기한다(§4.3).

        investigation_trigger 노드(Plan 64 CW-A)가 record_investigation으로 남긴 조사 감사
        레코드(type="investigation"·investigation_id·status·verdict)를 조회한다. 이 레코드는
        게이트 결정 레코드(tier)보다 뒤에 append되므로, 결정 레코드를 건너뛰고 investigation
        레코드만 매칭한다. 미검출 시 None(트리거 노드 미배선·클라이언트 미주입·타임아웃 —
        침묵 실패 아님, 게이트 PAGE 판정 자체는 정상).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.path.exists():
                with self.path.open(encoding="utf-8") as fh:
                    fh.seek(offset)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("type") == "investigation" and rec.get("alarm_id") == alarm_id:
                            return rec
            time.sleep(self.poll_interval)
        return None

    def known_investigation_ids(self) -> set:
        """기존 로그의 investigation 레코드 investigation_id 집합을 반환한다(dedup 시드).

        별도 프로세스(`--send`)로 동일 시나리오를 연속 주입해도 2회째 duplicate(기존 id 재사용)를
        감지하도록, 세션 시작 시 로그에 이미 존재하는 investigation_id를 '관측됨'으로 시드한다
        (investigation_id는 uuid4 hex이므로 재사용은 dispatcher/JobStore dedup의 확정 신호다).
        """
        ids: set = set()
        if not self.path.exists():
            return ids
        try:
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "investigation":
                        inv_id = rec.get("investigation_id")
                        if inv_id:
                            ids.add(inv_id)
        except OSError:
            return ids
        return ids


# ─── 판정 로직 (기대치 대조 · 감사 필드 표시) ──────────────────────────────────

def evaluate_step(step: Step, rec: Optional[dict]) -> tuple:
    """스텝의 기대 판정과 결정 레코드를 대조한다 → (state, detail).

    state: True(✔ 일치) / False(✘ 불일치) / None(? 레코드 없음 — 전제 미충족 가능).
    """
    if rec is None:
        return None, "결정 레코드 없음(타임아웃) — 서버 기동·플래그·ALARM_MIN_SEVERITY 확인"

    is_recurrence = rec.get("type") == "recurrence"

    if step.expect_recurrence:
        if is_recurrence:
            return True, f"SUPPRESS(dedup) count={rec.get('count')}"
        return False, f"dedup 안 됨 — tier={rec.get('tier')}"

    if step.expect_no_dedup:
        if is_recurrence:
            return False, f"의도치 않은 dedup(count={rec.get('count')})"
        return True, f"독립 판정 tier={rec.get('tier')}"

    # 통상 결정 기대
    if is_recurrence:
        return False, f"결정 기대인데 dedup됨(count={rec.get('count')})"
    tier = rec.get("tier")
    if step.expect_tiers is None:
        return True, f"tier={tier}"
    return (tier in step.expect_tiers), f"tier={tier} (기대 {sorted(step.expect_tiers)})"


def format_audit(rec: dict) -> str:
    """Plan 60 감사 필드를 한 줄로 요약한다(recurrence/correlation_meta/semantic/cascaded 등)."""
    parts: list = []
    for key in ("recurrence", "correlation_meta", "semantic_annotation"):
        val = rec.get(key)
        if val is not None:
            parts.append(f"{key}={json.dumps(val, ensure_ascii=False)}")
    signals = rec.get("signals") or {}
    for key in ("correlated", "cascaded", "root_resource"):
        val = signals.get(key)
        if val not in (None, False):
            parts.append(f"signals.{key}={val}")
    return "  ".join(parts) if parts else "(Plan 60 감사 필드 없음 — 플래그/픽스처 확인)"


def classify_investigation(rec: Optional[dict], seen_ids: set) -> tuple:
    """조사 트리거 감사 레코드를 accepted/duplicate/실패로 분류한다 → (state, detail) (§4.3).

    게이트 훅(investigation_trigger_node)이 남기는 investigation 레코드의 `status`는 poll 최종
    상태(stub/done/down/timeout 등)이므로, submit의 accepted/duplicate는 **investigation_id 재사용
    여부**로 판정한다(duplicate = dispatcher/JobStore가 기존 조사 id를 재반환 — sre-agent/05 §3).

    state: True(✔ 트리거 관측) / None(? 미관측·submit 실패 — 침묵 실패 아님, 게이트 판정은 정상).
        - rec None: 트리거 레코드 미관측(노드 미배선/클라이언트 미주입/타임아웃).
        - investigation_id 없음: submit 실패(서비스 미기동 graceful) — status를 사유로 노출.
        - investigation_id ∈ seen_ids: duplicate(기존 id 재사용 dedup).
        - 그 외: accepted(신규 조사).
    """
    if rec is None:
        return None, (
            "조사 트리거 레코드 미관측 — 트리거 노드 미배선/클라이언트 미주입 가능"
            "(게이트 PAGE 판정·통보는 정상)"
        )
    inv_id = rec.get("investigation_id")
    status = rec.get("status")
    if not inv_id:
        return None, (
            f"submit 실패(status={status}) — 조사 서비스 미기동 graceful, "
            "게이트 PAGE 판정·통보는 정상 완료"
        )
    if inv_id in seen_ids:
        return True, f"duplicate — 기존 investigation_id 재사용(dedup) id={inv_id} status={status}"
    return True, f"accepted — 신규 조사 id={inv_id} status={status}"


def parse_service_hostport(url: str) -> Optional[tuple]:
    """SSE URL에서 (host, port)를 파싱한다(조사 서비스 도달성 TCP 프로브용). 실패 시 None."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return None
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


def check_investigation_service(noise_config, timeout: float = 2.0) -> tuple:  # noqa: ANN001
    """조사 서비스 URL(investigation_service_url)의 TCP 도달성을 점검한다 → (ok, detail).

    비차단 정보용 — 미도달이어도 게이트 PAGE 판정·통보는 정상 완료되고 트리거만 graceful
    실패한다(§4.3 비차단 계약). 그 사유를 명시해 침묵 실패를 막는다(Known Mistakes).
    """
    url = getattr(noise_config, "investigation_service_url", "") or ""
    if not url:
        return False, "investigation_service_url 미설정 — 조사 서비스 URL 확인(.env)"
    hostport = parse_service_hostport(url)
    if hostport is None:
        return False, f"조사 서비스 URL 파싱 실패: {url}"
    host, port = hostport
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"조사 서비스 {host}:{port} 도달"
    except OSError as exc:
        return False, (
            f"조사 서비스 {host}:{port} 미도달 ({exc}) — 트리거는 graceful 실패, "
            "게이트 PAGE 판정·통보는 정상"
        )


# ─── 전제 조건 점검 (플래그·모델 · §4.2) ───────────────────────────────────────

def load_noise_config():  # noqa: ANN201
    """NoiseGateConfig를 로드한다(실측: NOISE_ prefix .env, os.getenv 판정 금지 — Known Mistakes)."""
    from src.config import NoiseGateConfig

    return NoiseGateConfig()


def check_preconditions(scenario: Scenario, noise_config) -> tuple:  # noqa: ANN001
    """시나리오의 전제(게이트·플래그·모델)를 점검한다 → (ok, [사유, ...]).

    미충족이면 주입 전에 사유를 출력하고 중단하기 위한 판정이다(침묵 실패 금지, §4.2).
    stub 시나리오(invest-trigger)는 항상 미충족으로 보류 사유를 반환한다.
    """
    reasons: list = []
    if scenario.stub:
        return False, [scenario.stub_reason]

    if noise_config is None:
        return False, ["NoiseGateConfig 로드 실패 — 리포지토리 루트에서 실행하세요"]

    if not getattr(noise_config, "enable_noise_gate", False):
        reasons.append("NOISE_ENABLE_NOISE_GATE=false — 노이즈 게이트 비활성(.env)")

    for attr, env_key in scenario.required_flags:
        if not getattr(noise_config, attr, False):
            reasons.append(f"{env_key}=false — 미활성(.env)")

    if scenario.requires_model:
        path = getattr(noise_config, "embedding_model_path", "") or ""
        if not path or not Path(path).is_dir():
            reasons.append(
                "NOISE_EMBEDDING_MODEL_PATH 미설정/비디렉토리 — 로컬 모델 필요(docs/19)"
            )

    return (len(reasons) == 0), reasons


# ─── 시나리오 실행 (주입·판정) ─────────────────────────────────────────────────

@dataclass
class RunContext:
    """시나리오 실행에 필요한 런타임 컨텍스트."""

    sender: object
    judge: Judge
    noise_config: object
    db_id: str
    timeout: float
    wait_enabled: bool = True
    seen_investigation_ids: set = field(default_factory=set)  # 세션 내 관측 investigation_id(dedup 판정)


def dispatch_scenario(scenario: Scenario, ctx: RunContext) -> None:
    """시나리오를 전제 점검 → 주입 → 판정 순으로 실행한다(미충족 시 주입 없이 사유 출력)."""
    ok, reasons = check_preconditions(scenario, ctx.noise_config)
    if not ok:
        print(f"[전제 미충족] [{scenario.number}] {scenario.name} — 주입하지 않음:")
        for reason in reasons:
            print(f"  · {reason}")
        return

    # 조사 서비스 도달성 사전 표기(비차단 — 미도달이어도 게이트는 정상, 트리거만 graceful 실패).
    if scenario.requires_investigation_service and ctx.noise_config is not None:
        svc_ok, svc_detail = check_investigation_service(ctx.noise_config)
        print(f"  [조사 서비스] {'✔' if svc_ok else '✘'} {svc_detail}")

    run_id = uuid.uuid4().hex[:8]
    steps = scenario.build(run_id, ctx.db_id)
    print(f"[{scenario.number}] {scenario.name} — {len(steps)}개 이벤트 주입 (run_id={run_id})")

    per_step_timeout = ctx.timeout
    for idx, step in enumerate(steps, start=1):
        offset = ctx.judge.current_offset()
        try:
            ctx.sender.send(step.payload)
        except Exception as exc:  # 소켓/Redis 실패 — 침묵 폴백 금지, 사유 노출 후 중단
            print(f"  [오류] 전송 실패({ctx.sender.label}): {exc}")
            print("  → alarm_server 미기동일 수 있습니다. 'python -m noise_gate.alarm_server' 또는 '--path redis'.")
            return
        print(f"  [주입 {idx}/{len(steps)}] {step.label} alarm_id={step.alarm_id}")

        if ctx.wait_enabled:
            rec = ctx.judge.wait_for(step.alarm_id, offset, per_step_timeout)
            state, detail = evaluate_step(step, rec)
            mark = {True: "✔", False: "✘", None: "?"}[state]
            print(f"  [판정 {idx}] {mark} {detail}")
            if step.show_audit and rec is not None:
                print(f"           {format_audit(rec)}")
            if step.expect_investigation:
                # 게이트 훅(investigation_trigger 노드)이 남긴 investigation 감사 레코드 조회 →
                # accepted(신규 id)/duplicate(기존 id 재사용)/submit 실패(id 없음·graceful) 표기.
                inv_rec = ctx.judge.wait_for_investigation(
                    step.alarm_id, offset, per_step_timeout
                )
                inv_state, inv_detail = classify_investigation(
                    inv_rec, ctx.seen_investigation_ids
                )
                inv_mark = {True: "✔", False: "✘", None: "?"}[inv_state]
                print(f"  [조사 {idx}] {inv_mark} {inv_detail}")
                if inv_rec is not None and inv_rec.get("investigation_id"):
                    ctx.seen_investigation_ids.add(inv_rec["investigation_id"])
        if step.interval_after > 0 and idx < len(steps):
            time.sleep(step.interval_after)

    if not ctx.wait_enabled:
        print("  (판정 대기 off — 주입만 완료. UI/로그로 직접 확인하세요)")


# ─── 메뉴 (대화형 상주 루프 · §3.1) ────────────────────────────────────────────

def _category_label(category: str) -> str:
    """카테고리 코드 → 섹션 헤더 라벨."""
    return {
        "basic": "─ 기본 (Plan 52 게이트) ───────────────────────",
        "plan60": "─ Plan 60 (플래그 필요 — off면 선택 시 안내) ──",
        "sre": "─ SRE-Agent 연동 (§4.3 — 자동 조사 트리거) ─────",
        "plan60e7": "─ Plan 60 E7 (실측 텍스트·사이트 — 플래그 필요) ─",
    }.get(category, category)


def build_menu_text(sender_label: str, decision_log: str, wait_enabled: bool) -> str:
    """번호 메뉴 텍스트를 구성한다(카테고리별 그룹 + 보조 키)."""
    lines: list = [f"══ 노이즈 캔슬링 목업 이벤트 생성기 ({sender_label}) ══"]
    last_category = None
    for scen in SCENARIOS:
        if scen.category != last_category:
            lines.append(_category_label(scen.category))
            last_category = scen.category
        note = f"  {scen.flag_note}" if scen.flag_note else ""
        lines.append(
            f" [{scen.number:>2}] {scen.name:<14} {scen.title} → 기대: {scen.expect_label}{note}"
        )
    toggle_state = "on" if wait_enabled else "off"
    lines.append("──────────────────────────────────────────────")
    lines.append(
        f" [{KEY_LIST}] 메뉴 다시 표시   [{KEY_TOGGLE}] 판정 대기 토글(현재 {toggle_state})"
        f"   [{KEY_QUIT}] 종료"
    )
    lines.append(f" 결정 로그: {decision_log}")
    return "\n".join(lines)


def parse_menu_input(raw: str) -> tuple:
    """사용자 입력을 액션으로 해석한다 → (action, scenario|None).

    action: "scenario" | "list" | "toggle" | "quit" | "empty" | "invalid".
    번호는 SCENARIOS_BY_NUMBER로, 보조 키(l/v/q)는 소문자 매칭한다(순수 함수 — 단위 테스트 대상).
    """
    token = raw.strip().lower()
    if token == "":
        return "empty", None
    if token == KEY_QUIT:
        return "quit", None
    if token == KEY_LIST:
        return "list", None
    if token == KEY_TOGGLE:
        return "toggle", None
    if token.isdigit():
        scen = SCENARIOS_BY_NUMBER.get(int(token))
        if scen is not None:
            return "scenario", scen
    return "invalid", None


def run_menu(ctx: RunContext, sender_label: str, decision_log: str) -> int:
    """대화형 메뉴 루프를 실행한다(번호 입력 → 주입·판정 → 복귀, q 종료)."""
    print(build_menu_text(sender_label, decision_log, ctx.wait_enabled))
    while True:
        try:
            raw = input("번호 입력> ")
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            return 0

        action, scenario = parse_menu_input(raw)
        if action == "quit":
            print("종료합니다.")
            return 0
        if action == "empty":
            continue
        if action == "list":
            print(build_menu_text(sender_label, decision_log, ctx.wait_enabled))
            continue
        if action == "toggle":
            ctx.wait_enabled = not ctx.wait_enabled
            print(f"판정 대기: {'on' if ctx.wait_enabled else 'off'}")
            continue
        if action == "scenario" and scenario is not None:
            dispatch_scenario(scenario, ctx)
            continue
        print(f"알 수 없는 입력: {raw!r} (번호 또는 l/v/q)")


# ─── 진입점 ────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 인자 파서를 구성한다(§5 기동 옵션)."""
    parser = argparse.ArgumentParser(
        description="노이즈 캔슬링 목업 이벤트 생성기 (Plan 65 R1)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="TCP 대상 호스트(기본 localhost)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP 대상 포트(기본 9100)")
    parser.add_argument("--path", choices=("tcp", "redis"), default="tcp",
                        help="주입 경로(기본 tcp 실경로 / redis 폴백)")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS_URL,
                        help="Redis URL(--path redis일 때, 기본 redis://localhost:6379/0)")
    parser.add_argument("--decision-log", default=DEFAULT_DECISION_LOG,
                        help="결정 감사 JSONL 경로(기본 logs/alarm_decisions.jsonl)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="판정 대기 타임아웃(초, 기본 30)")
    parser.add_argument("--db-id", default=DEFAULT_DB_ID,
                        help="대상 DB 프로필(기본 polestar_pg)")
    parser.add_argument("--send", metavar="NAME",
                        help="비대화형 단발 주입 후 종료(시나리오 이름, 자동화/e2e용)")
    return parser


def main(argv: Optional[list] = None) -> int:
    """진입점 — --send면 단발 주입, 아니면 대화형 메뉴를 실행한다."""
    args = build_arg_parser().parse_args(argv)
    sender = build_sender(args)
    judge = Judge(args.decision_log)

    try:
        noise_config = load_noise_config()
    except Exception as exc:  # config 로드 실패 — 사유 노출(침묵 실패 금지)
        print(f"[경고] NoiseGateConfig 로드 실패: {exc}")
        print("       전제 점검 없이 진행합니다(리포지토리 루트에서 실행 권장).")
        noise_config = None

    # 기동 점검(전송 경로 도달성 · 결정 로그 존재).
    ok, detail = sender.check()
    print(f"[점검] {detail}")
    log_exists = Path(args.decision_log).exists()
    print(f"[점검] 결정 로그 {args.decision_log} {'존재' if log_exists else '부재(주입 후 생성됨)'}")

    ctx = RunContext(
        sender=sender, judge=judge, noise_config=noise_config,
        db_id=args.db_id, timeout=args.timeout, wait_enabled=True,
        # 기존 로그의 investigation_id를 시드 → 별도 프로세스 연속 주입에서도 duplicate 감지(§4.3).
        seen_investigation_ids=judge.known_investigation_ids(),
    )

    if args.send:
        scenario = SCENARIOS_BY_NAME.get(args.send)
        if scenario is None:
            names = ", ".join(s.name for s in SCENARIOS)
            print(f"[오류] 알 수 없는 시나리오: {args.send!r}\n사용 가능: {names}")
            return 2
        dispatch_scenario(scenario, ctx)
        return 0

    return run_menu(ctx, sender.label, args.decision_log)


if __name__ == "__main__":
    sys.exit(main())
