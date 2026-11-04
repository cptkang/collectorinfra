"""알람 워커 동등성 하네스 (plans/208 트랙 B W1 · C-1 — 테스트 유틸 · 제품 코드 무변경).

같은 입력 알람 순서를 워커(`AlarmWorker._process`)로 처리해 **판정 감사 레코드 집합**과 그래프
입력·그래프 뒤 상태 요약을 정규화해 돌려준다. 두 실행의 결과가 같은지로 동등성을 판정한다
(§4 수용 기준 1·2). 실 LLM·실 Redis·DB·네트워크를 쓰지 않는다.

구성:
    - `FakeStreamRedis` — 메모리 스트림(`xreadgroup`·`xack`·`xadd`·`get`·`set`). `read_messages`
      헬퍼가 그대로 읽는다. dead-letter(`xadd`)와 ACK를 모아 둔다.
    - `FakeAlarmGraph` — `graph.ainvoke` 대역. 알람마다 **시드 고정 무작위 지연**(알람 id로 RNG를
      만든다 — 호출 순서와 무관하게 같은 알람은 같은 지연)을 준 뒤, 가짜 enricher가 워커의
      `active_firings`로 `root_notified`를 읽고(실 `_compute_root_notified`), **실 게이트 노드**
      (`notification_gate_node` — `decide_notification` · `review_page` · 감사 기록)를 부른다.
    - `EventClock` — 워커 모듈의 `time`만 바꿔 끼우는 가상 시계. P3의 `now = time.time()`이
      **처리 중인 알람의 발생 시각(alarmTime)** 을 받는다(호출 프레임의 `AlarmEvent` 지역 변수로
      찾는다 — 실행 순서·동시성과 무관하게 같은 알람은 같은 `now`).
    - 실 게이트 상태 — 워커 인스턴스 필드(dedup·자가복구·인히비션·플래핑·스톰·상관·사건 추적기·
      PAGE 레지스트리)를 그대로 쓴다. 판정 감사는 실 `DecisionStore`(임시 jsonl)다.

실행기(W2가 꽂는 자리):
    `Executor = Callable[[AlarmWorker, FakeStreamRedis, str, str], Awaitable[None]]`.
    스트림에 입력이 모두 들어간 상태에서 `(worker, redis, stream_key, group)`을 받아 **전부 처리하고
    돌아오면** 된다. 현행은 `serial_executor`(run() 소비 루프 `alarm_worker.py:774-784`와 같은
    순서 — 10건씩 읽어 한 건씩 `await _process`). W2는 예컨대 다음처럼 N=k 실행기를 꽂는다::

        async def concurrent_executor(worker, r, stream_key, group):
            await worker._consume(r, stream_key, group, {}, max_concurrency=4)  # W2가 만드는 경로

        base = await run_sequence(alarms, work_dir=tmp_path / "n1")
        got = await run_sequence(alarms, work_dir=tmp_path / "n4",
                                 executor=concurrent_executor, seed=7, max_delay=0.02)
        assert_equivalent(base, got)

    무한 소비 루프를 그대로 쓰는 실행기는 `FakeStreamRedis(cancel_when_drained=True)`로 빈 읽기에서
    `CancelledError`를 받게 할 수 있다(`run_sequence(..., cancel_when_drained=True)`).

정규화(`normalize_record`): 타임스탬프·소요 시간 키(`VOLATILE_KEYS` · 접미사 `_ms`)를 모든 깊이에서
뺀다. 가상 시계에서 나온 값(자가복구 소요 초·창 경과 초·`page_review_signals.now` 등)은 실행
방식과 무관하게 같아야 하므로 남긴다. 레코드 순서는 비교하지 않는다(정준 JSON 정렬 목록 =
다중집합).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import tempfile
import time as _real_time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from src.config import AlarmConfig, NoiseGateConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import _compute_root_notified
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.redis_queue import read_messages

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "noise_gate" / "testdata" / "cross_source" / "mock_events.jsonl"
SHIPPED_RULES = REPO / "config" / "cross_source_rules.yaml"

STREAM_KEY = "alarm:raw"
GROUP = "noise-gate-harness"
CONSUMER = "harness-1"

# 정규화에서 빼는 키 — 실행 시각·처리 소요(W0 `timing` 포함). 접미사 `_ms`도 뺀다.
VOLATILE_KEYS = frozenset({"ts", "timing", "received_at", "failed_at", "elapsed_seconds"})

_POLESTAR_CTX = {"importance_id": "3", "maintenance": False, "noti_policy": None,
                 "source": "fixture"}
_APM_CTX = {"importance_id": "3", "maintenance": None, "noti_policy": "notify",
            "source": "apm_policy"}

Executor = Callable[[AlarmWorker, "FakeStreamRedis", str, str], Awaitable[None]]


# ═════════════════════════════════════════════════════════════════════════════
# 입력
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HarnessAlarm:
    """입력 알람 1건 — 스트림 페이로드 + 가짜 그래프가 쓸 수집 결과."""

    payload: dict[str, Any]
    # 가짜 enricher의 노이즈 컨텍스트(없으면 소스별 기본값 — 중요도 높음).
    noise_ctx: dict[str, Any] | None = None
    # 다홉 근본원인 자원명 — 주면 가짜 enricher가 cascaded=True로 두고 워커 `active_firings`에서
    # `root_notified`를 읽는다(P4가 워커 상태를 읽는 유일한 경로의 재현).
    root: str | None = None

    @property
    def alarm_id(self) -> str:
        return str(self.payload["alarmId"])


def polestar_payload(
    alarm_id: str,
    at: datetime,
    *,
    host: str,
    name: str,
    severity: int = 2,
    resource: str = "",
    db_id: str = "polestar_cm_gp",
) -> dict[str, Any]:
    """폴스타 단일행 페이로드(TcpReceiver가 넣는 camelCase 키)."""
    return {
        "dbId": db_id, "serverName": host, "hostname": host, "ipAddress": "",
        "resourceAncestry": "", "alarmId": alarm_id, "severity": severity,
        "alarmStatus": "NOT_ACK", "resourceType": "server.Server",
        "resourceName": resource or name, "alarmName": name,
        "alarmTime": at.strftime("%Y%m%d%H%M%S"), "conditions": "> 90 %",
        "conditionLog": "[95 % (> 90 %)]",
    }


def jennifer_payload(
    alarm_id: str,
    at: datetime,
    *,
    host: str,
    name: str = "SERVICE_QUEUING",
    severity: int = 2,
    kind: str = "was_service_queuing",
) -> dict[str, Any]:
    """제니퍼 이벤트 페이로드(게이트웨이가 넣는 `apm` 블록 — host_name 정합 = strong)."""
    return {
        "dbId": "jennifer_cm_prd", "source": "jennifer", "serverName": host, "hostname": host,
        "ipAddress": "", "resourceAncestry": f"제니퍼 > cm_prd > 주문 > {host}_was01",
        "alarmId": alarm_id, "severity": severity, "alarmStatus": "",
        "resourceType": "apm.Instance", "resourceName": f"{host}_was01", "alarmName": name,
        "alarmTime": at.strftime("%Y%m%d%H%M%S"), "conditions": "", "conditionLog": "",
        "apm": {"source": "jennifer", "source_id": "cm_prd", "match_reason": "host_name",
                "event_type": name, "event_type_norm": name, "event_kind": "error",
                "level": "warning", "was_signals": [{"kind": kind}]},
    }


def fixture_alarms(path: Path = FIXTURE) -> list[HarnessAlarm]:
    """기존 픽스처(`mock_events.jsonl`)를 입력으로 읽는다 — 줄 순서 그대로."""
    out: list[HarnessAlarm] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        payload = rec.get("payload", rec)
        out.append(HarnessAlarm(payload=payload, noise_ctx=rec.get("noise_ctx")))
    return out


def synthetic_alarms(base: datetime = datetime(2026, 11, 2, 9, 0, 0)) -> list[HarnessAlarm]:
    """합성 순서 — 같은 호스트 폭주 · 다른 호스트 혼합 · 자가복구 짝 · 플래핑 · 상관 클러스터 ·
    의존성 근본원인 · PAGE 재판정(지문 반복·같은 호스트·동시발생) · 크로스소스 사건.

    시나리오를 같은 10분 창에 섞어 넣는다 — 동시 실행기에서 서로 다른 호스트가 겹치게 하려는 것.
    발생 시각 오름차순으로 돌려준다(입력 순서 = 스트림 순서).
    """

    def t(sec: int) -> datetime:
        return base + timedelta(seconds=sec)

    items: list[tuple[int, HarnessAlarm]] = []

    def add(sec: int, payload: dict[str, Any], **kw: Any) -> None:
        items.append((sec, HarnessAlarm(payload=payload, **kw)))

    # ① 같은 호스트 폭주 — 서로 다른 알람 8건(스톰 임계 5 초과) + 같은 지문 반복(지문 dedup).
    for i in range(8):
        add(i * 4, polestar_payload(f"BURST-{i}", t(i * 4), host="eqapp01",
                                    name=f"프로세스 {i} 다운", resource=f"proc{i}"))
    add(35, polestar_payload("BURST-dup", t(35), host="eqapp01", name="프로세스 0 다운",
                             resource="proc0"))
    # ② 다른 호스트 혼합 — 호스트 4대에 서로 다른 알람이 번갈아(심각도 섞음).
    for i in range(8):
        host = f"eqmix0{i % 4}"
        add(2 + i * 5, polestar_payload(f"MIX-{i}", t(2 + i * 5), host=host,
                                        name=f"응답 지연 {i}", severity=1 + i % 3,
                                        resource=f"svc{i}"))
    # ③ 자가복구 짝 — 심각도1 발생 → 창(300초) 안 해소.
    add(10, polestar_payload("HEAL-1", t(10), host="eqheal01", name="스왑 사용률", severity=1))
    add(70, polestar_payload("HEAL-0", t(70), host="eqheal01", name="스왑 사용률", severity=0))
    # ④ 플래핑 — 같은 지문 발생/해소 교대(지문 dedup 간격 60초보다 넓게 발생).
    for i in range(4):
        add(100 + i * 70, polestar_payload(f"FLAP-F{i}", t(100 + i * 70), host="eqflap01",
                                           name="링크 상태", resource="eth0"))
        add(135 + i * 70, polestar_payload(f"FLAP-C{i}", t(135 + i * 70), host="eqflap01",
                                           name="링크 상태", resource="eth0", severity=0))
    # ⑤ 상관 클러스터 — 같은 알람·자원이 같은 존의 여러 호스트에(창 120초 안).
    for i in range(5):
        add(40 + i * 6, polestar_payload(f"CORR-{i}", t(40 + i * 6), host=f"eqweb0{i}",
                                         name="디스크 사용률", resource="disk0"))
    # ⑥ 의존성 근본원인 — 자식 알람 일부는 근본원인보다 먼저, 일부는 뒤에(root_notified가 순서에
    #    따라 갈린다 · 인히비션 상태 `active_firings`가 근본원인 스코프를 기록).
    add(60, polestar_payload("DEP-C1", t(60), host="eqsvc01", name="API 오류율",
                             resource="api"), root="eqdb01")
    add(64, polestar_payload("DEP-ROOT", t(64), host="eqdb01", name="DB 가용성",
                             severity=3, resource="db"))
    add(66, polestar_payload("DEP-C2", t(66), host="eqsvc02", name="API 오류율",
                             resource="api"), root="eqdb01")
    add(68, polestar_payload("DEP-C3", t(68), host="eqsvc03", name="API 오류율",
                             resource="api"), root="eqdb01")
    # ⑦ PAGE 재판정 — 같은 지문 재PAGE(dedup 뒤) · 같은 호스트 다른 알람 · 동시발생(같은 알람명).
    add(20, polestar_payload("PR-1", t(20), host="eqpg01", name="파일시스템 읽기전용",
                             severity=3, resource="/data"))
    add(95, polestar_payload("PR-2", t(95), host="eqpg01", name="파일시스템 읽기전용",
                             severity=3, resource="/data"))
    add(97, polestar_payload("PR-3", t(97), host="eqpg01", name="NTP 동기화 실패", severity=3))
    for i in range(3):
        add(150 + i * 3, polestar_payload(f"FAN-{i}", t(150 + i * 3), host=f"eqfan0{i}",
                                          name="인증서 만료", severity=3, resource=f"cert{i}"))
    # ⑧ 크로스소스 사건 — 폴스타 원인(CPU) 뒤 제니퍼 증상(큐잉) · 같은 호스트 키.
    add(30, polestar_payload("XS-CAUSE", t(30), host="eqwas01", name="CPU 사용률",
                             resource="CPU"))
    add(90, jennifer_payload("XS-EFF-1", t(90), host="eqwas01"))
    add(120, jennifer_payload("XS-EFF-2", t(120), host="eqwas01", name="SERVICE_QUEUING_2"))
    add(400, polestar_payload("XS-CAUSE-0", t(400), host="eqwas01", name="CPU 사용률",
                              resource="CPU", severity=0))

    items.sort(key=lambda it: it[0])  # 같은 초는 넣은 순서 유지(안정 정렬)
    return [a for _, a in items]


# ═════════════════════════════════════════════════════════════════════════════
# 가짜 Redis · 가짜 그래프 · 가상 시계
# ═════════════════════════════════════════════════════════════════════════════


class FakeStreamRedis:
    """메모리 스트림 — 워커와 `redis_queue` 헬퍼가 부르는 메서드만 흉내 낸다."""

    def __init__(self, *, cancel_when_drained: bool = False) -> None:
        self._streams: dict[str, list[tuple[bytes, dict[bytes, bytes]]]] = {}
        self._cursor: dict[str, int] = {}
        self._seq = 0
        self.cancel_when_drained = cancel_when_drained
        self.acked: list[bytes] = []
        self.dead_letters: list[dict[str, Any]] = []
        self.kv: dict[str, Any] = {}

    def add(self, stream_key: str, fields: dict[bytes, bytes]) -> bytes:
        """입력 적재(하네스 전용) — id는 `{순번}-0`."""
        self._seq += 1
        msg_id = f"{self._seq}-0".encode()
        self._streams.setdefault(stream_key, []).append((msg_id, fields))
        return msg_id

    async def xgroup_create(self, *a: Any, **k: Any) -> bool:
        return True

    async def xreadgroup(
        self, groupname: str, consumername: str, streams: dict[str, str],
        count: int | None = None, block: int | None = None,
    ) -> list[list[Any]]:
        if any(str(v) != ">" for v in streams.values()):
            return self._read_pending(streams, count)
        out: list[list[Any]] = []
        for key in streams:
            msgs = self._streams.get(key, [])
            start = self._cursor.get(key, 0)
            batch = msgs[start:start + (count or len(msgs))]
            self._cursor[key] = start + len(batch)
            if batch:
                out.append([key.encode(), batch])
        if not out and self.cancel_when_drained:
            raise asyncio.CancelledError
        return out

    def pending_ids(self, stream_key: str) -> list[bytes]:
        """(plans/208 W3) 배달됐고 ACK되지 않은 id — 소비자 1개(워커)의 PEL 흉내."""
        acked = set(self.acked)
        delivered = self._streams.get(stream_key, [])[: self._cursor.get(stream_key, 0)]
        return [msg_id for msg_id, _ in delivered if msg_id not in acked]

    def _read_pending(self, streams: dict[str, Any], count: int | None) -> list[list[Any]]:
        """(plans/208 W3) `XREADGROUP id≠">"` 흉내 — pending 중 id가 주어진 id보다 큰 것.

        블로킹하지 않고, 실 Redis처럼 더 없으면 스트림 키와 빈 목록을 돌려준다. 소비자 구분은
        하지 않는다(워커 1개).
        """
        out: list[list[Any]] = []
        for key, after in streams.items():
            raw = after.decode() if isinstance(after, bytes) else str(after)
            floor = _stream_id_key(raw)
            pending = set(self.pending_ids(key))
            batch = [
                (msg_id, fields)
                for msg_id, fields in self._streams.get(key, [])[: self._cursor.get(key, 0)]
                if msg_id in pending and _stream_id_key(msg_id.decode()) > floor
            ]
            out.append([key.encode(), batch[: count or len(batch)]])
        return out

    async def xack(self, stream_key: str, group: str, *ids: bytes) -> int:
        self.acked.extend(ids)
        return len(ids)

    async def xadd(self, key: str, fields: dict[str, Any], **kw: Any) -> bytes:
        self.dead_letters.append({"stream": key, **fields})
        return b"0-1"

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, *a: Any, **k: Any) -> bool:
        self.kv[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: Any) -> bool:
        self.kv[key] = value
        return True


def _stream_id_key(raw: str) -> tuple[int, int]:
    """스트림 id `ms-seq`(또는 `0`) → 비교용 정수 쌍."""
    ms, _, seq = raw.partition("-")
    return int(ms), int(seq or 0)


class EventClock:
    """워커 모듈 `time` 대역 — `time()`은 호출 프레임의 `AlarmEvent` 발생 시각을 돌려준다.

    P3는 `now = time.time()`을 알람 1건당 1회 부른다(`alarm_worker.py:913`). 같은 알람은 실행 순서와
    무관하게 같은 `now`를 받는다. 프레임에서 알람을 못 찾으면(기동 복원 등) 마지막 값을 준다.
    그 밖 속성(`monotonic`·`perf_counter` 등 — W0 계측)은 실 `time` 모듈로 넘긴다.
    """

    def __init__(self, start: float, *, max_depth: int = 4) -> None:
        self._last = start
        self._max_depth = max_depth
        self.fallbacks = 0

    def time(self) -> float:
        frame = inspect.currentframe()
        try:
            cur = frame.f_back if frame is not None else None
            for _ in range(self._max_depth):
                if cur is None:
                    break
                for value in cur.f_locals.values():
                    if isinstance(value, AlarmEvent):
                        self._last = value.alarm_time.timestamp()
                        return self._last
                cur = cur.f_back
        finally:
            del frame
        self.fallbacks += 1
        return self._last

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)


class FakeAlarmGraph:
    """`graph.ainvoke` 대역 — 시드 고정 지연 → 가짜 enricher(root_notified) → 실 게이트 노드."""

    def __init__(
        self, specs: dict[str, HarnessAlarm], *, seed: int = 0, max_delay: float = 0.0
    ) -> None:
        self._specs = specs
        self.seed = seed
        self.max_delay = max_delay
        self.inputs: dict[str, dict[str, Any]] = {}
        self.completed: list[str] = []

    def delay_for(self, alarm_id: str) -> float:
        """알람별 지연(초) — RNG를 알람 id로 만들어 호출 순서와 무관하게 결정적이다."""
        if self.max_delay <= 0:
            return 0.0
        return random.Random(f"{self.seed}:{alarm_id}").uniform(0.0, self.max_delay)

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        event: AlarmEvent = state["alarm_event"]
        spec = self._specs.get(event.alarm_id)
        delay = self.delay_for(event.alarm_id)
        if delay > 0:
            await asyncio.sleep(delay)
        configurable = (config or {}).get("configurable", {})
        gate_cfg = configurable["app_config"].noise_gate
        default_ctx = _APM_CTX if is_apm_source(event) else _POLESTAR_CTX
        ctx = dict((spec.noise_ctx if spec and spec.noise_ctx else None) or default_ctx)
        if spec is not None and spec.root:
            # 가짜 enricher — 실 enricher처럼 **그래프 실행 중에** 워커 인히비션 상태를 읽는다.
            ctx.update(cascaded=True, root_resource_name=spec.root)
            ctx["root_notified"] = _compute_root_notified(
                event, ctx, configurable.get("active_firings"),
                int(gate_cfg.inhibition_window_seconds), now=event.alarm_time.timestamp(),
            )
        result = AlarmAnalysisResult(
            alarm_event=event, severity_label=str(event.severity), summary="가짜 분석",
            probable_cause="", recommended_action="", notification_channels=[],
        )
        gate_state = {**state, "analysis_result": result, "noise_context": ctx}
        out = await notification_gate_node(gate_state, config)
        self.inputs[event.alarm_id] = _graph_input_view(gate_state)
        self.completed.append(event.alarm_id)
        return {**gate_state, **out}


def _graph_input_view(state: dict[str, Any]) -> dict[str, Any]:
    """그래프 입력 중 워커 상태가 만든 것 — P3 신호 · 사건 · PAGE 재판정 재료 · P4 root 판독."""
    keys = (
        "self_heal", "inhibited", "flapping", "storm", "correlated", "correlation_meta",
        "recurrence", "detection_evidence", "silence_rules", "apm_policy", "cross_source",
        "episode_app_impact", "episode_investigation", "episode_summary", "episode_snapshot",
        "page_review_signals",
    )
    view = {k: state[k] for k in keys if k in state}
    ctx = state.get("noise_context") or {}
    if "root_notified" in ctx:
        view["root_notified"] = ctx["root_notified"]
    return view


# ═════════════════════════════════════════════════════════════════════════════
# 설정 · 실행
# ═════════════════════════════════════════════════════════════════════════════


def write_enforce_rules(work_dir: Path) -> Path:
    """출하 규칙 표에서 첫 활성 규칙만 enforce로 바꾼 사본.

    사건 멤버 티어(P5 → 다음 P3)가 실제 판정(크로스소스 강등)에 쓰이게 한다.
    """
    table = yaml.safe_load(SHIPPED_RULES.read_text(encoding="utf-8"))
    for row in table.get("rules", []):
        if row.get("enabled", True):
            row["enforce"] = True
            break
    path = work_dir / "cross_source_rules.json"
    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    return path


def harness_config(rules_path: Path, **noise_gate_overrides: Any) -> SimpleNamespace:
    """게이트 상태 전부를 켠 워커 설정 — `.env`를 읽지 않는다(`model_construct` = 코드 기본값)."""
    ng: dict[str, Any] = dict(
        enable_noise_gate=True,
        repeat_interval_seconds=60,
        sev3_repeat_interval_seconds=60,
        importance_value_map_csv="1=낮음,2=보통,3=높음",
        inhibition_enabled=True,
        flapping_enabled=True,
        storm_grouping_enabled=True,
        cross_host_correlation_enabled=True,
        # 기본 0.5면 합성 순서의 다른 시나리오(혼합·사건 원인)까지 상관 억제에 먹힌다 — 같은
        # 알람·자원 묶음(⑤)만 군집되게 높인다(단계 다양성 확보).
        correlation_sim_threshold=0.9,
        dependency_suppression=True,
        page_review_mode="enforce",
        cross_source_mode="enforce",
        cross_source_rules_path=str(rules_path),
    )
    ng.update(noise_gate_overrides)
    return SimpleNamespace(
        noise_gate=NoiseGateConfig.model_construct(**ng),
        alarm=AlarmConfig.model_construct(min_severity=1),
    )


async def serial_executor(
    worker: AlarmWorker, r: FakeStreamRedis, stream_key: str, group: str
) -> None:
    """현행 직렬 경로 — run() 소비 루프와 같다(10건씩 읽어 한 건씩 `await _process`)."""
    dedup: dict[str, float] = {}
    while True:
        messages = await read_messages(r, stream_key, group, CONSUMER, count=10, block_ms=0)
        if not messages:
            return
        for msg_id, fields in messages:
            await worker._process(r, stream_key, group, msg_id, fields, dedup)  # type: ignore[arg-type]


def concurrent_executor(max_concurrency: int) -> Executor:
    """(plans/208 W2) N=k 실행기 — `run()`의 N≥2 분기와 같은 소비 루프(`_consume_concurrent`)를
    빈 읽기(`block_ms=0`)에서 진행 중을 마저 끝내고 돌아오게 부른다."""

    async def executor(
        worker: AlarmWorker, r: FakeStreamRedis, stream_key: str, group: str
    ) -> None:
        await worker._consume_concurrent(
            r,  # type: ignore[arg-type]
            stream_key, group, {}, max_concurrency=max_concurrency, block_ms=0,
            consumer=CONSUMER, stop_when_drained=True,
        )

    return executor


@dataclass
class HarnessResult:
    """정규화한 실행 결과 — `signature()`가 같으면 동등하다."""

    records: list[str]
    graph_inputs: dict[str, str]
    final_state: dict[str, Any]
    acked: list[str]
    dead_letters: list[dict[str, Any]]
    completion_order: list[str] = field(default_factory=list)
    clock_fallbacks: int = 0

    def signature(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "graph_inputs": self.graph_inputs,
            "final_state": self.final_state,
            "acked": self.acked,
            "dead_letters": len(self.dead_letters),
        }


async def run_sequence(
    alarms: Iterable[HarnessAlarm],
    *,
    work_dir: Path,
    executor: Executor = serial_executor,
    seed: int = 0,
    max_delay: float = 0.0,
    cancel_when_drained: bool = False,
    **noise_gate_overrides: Any,
) -> HarnessResult:
    """입력 순서대로 스트림에 넣고 `executor`로 처리한 뒤 정규화 결과를 돌려준다.

    `work_dir` 아래에 실행마다 새 디렉터리를 만들어 판정 감사 jsonl·규칙 사본을 둔다.
    """
    alarms = list(alarms)
    work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=work_dir))
    cfg = harness_config(write_enforce_rules(run_dir), **noise_gate_overrides)

    worker = AlarmWorker(cfg)
    specs = {a.alarm_id: a for a in alarms}
    graph = FakeAlarmGraph(specs, seed=seed, max_delay=max_delay)
    r = FakeStreamRedis(cancel_when_drained=cancel_when_drained)
    worker._graph = graph
    worker._redis = r
    worker._decision_store = DecisionStore(str(run_dir / "decisions.jsonl"))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()

    for a in alarms:
        r.add(STREAM_KEY, {b"data": json.dumps(a.payload, ensure_ascii=False).encode("utf-8")})

    start = _event_epoch(alarms[0].payload) if alarms else 0.0
    clock = EventClock(start)
    saved_time = worker_mod.time
    worker_mod.time = clock  # type: ignore[assignment]
    try:
        try:
            await executor(worker, r, STREAM_KEY, GROUP)
        except asyncio.CancelledError:
            if not cancel_when_drained:
                raise
    finally:
        worker_mod.time = saved_time

    path = run_dir / "decisions.jsonl"
    raw = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return HarnessResult(
        records=sorted(canonical(json.loads(line)) for line in raw if line.strip()),
        graph_inputs={k: canonical(v) for k, v in sorted(graph.inputs.items())},
        final_state=state_digest(worker),
        acked=sorted(m.decode() for m in r.acked),
        dead_letters=list(r.dead_letters),
        completion_order=list(graph.completed),
        clock_fallbacks=clock.fallbacks,
    )


def _event_epoch(payload: dict[str, Any]) -> float:
    return datetime.strptime(str(payload["alarmTime"]), "%Y%m%d%H%M%S").timestamp()


# ═════════════════════════════════════════════════════════════════════════════
# 정규화 · 비교
# ═════════════════════════════════════════════════════════════════════════════


def normalize_record(value: Any) -> Any:
    """타임스탬프·소요 시간 키를 모든 깊이에서 뺀다(그 밖은 그대로)."""
    if isinstance(value, dict):
        return {
            k: normalize_record(v) for k, v in value.items()
            if k not in VOLATILE_KEYS and not str(k).endswith("_ms")
        }
    if isinstance(value, (list, tuple)):
        return [normalize_record(v) for v in value]
    return value


def canonical(value: Any) -> str:
    """정규화 뒤 정준 JSON(키 정렬) — 비교·정렬 단위."""
    return json.dumps(
        normalize_record(_jsonable(value)), sort_keys=True, ensure_ascii=False, default=str
    )


def _jsonable(value: Any) -> Any:
    """데이터클래스·튜플·집합을 JSON 친화형으로(그래프 입력 비교용)."""
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    return value


def state_digest(worker: AlarmWorker) -> dict[str, Any]:
    """그래프 뒤(P5)에 쓰이는 상태의 요약 — 사건 멤버 티어 · PAGE 레지스트리 대표."""
    digest: dict[str, Any] = {}
    tracker = worker._episodes
    if tracker is not None:
        digest["episodes"] = sorted(
            (
                {
                    "id": ep.id,
                    "members": [
                        [m.alarm.alarm_id, m.tier, m.resolved] for m in ep.members
                    ],
                    "cause_ids": list(ep.cause_ids),
                    "representative_id": ep.representative_id,
                }
                for ep in tracker.episodes()
            ),
            key=lambda d: d["id"],
        )
    registry = worker._page_registry
    if registry is not None:
        pages = getattr(registry, "_pages", {})  # 테스트 요약 전용 — 공개 조회가 없다
        digest["page_registry"] = sorted(
            [fp[:12], e.alarm_id, e.last_alarm_id, e.repeat_count, e.resolved_ts is None]
            for fp, e in pages.items()
        )
    return digest


def diff(a: HarnessResult, b: HarnessResult) -> list[str]:
    """두 결과의 차이를 사람이 읽는 줄로(같으면 빈 목록)."""
    out: list[str] = []
    ra, rb = set(a.records), set(b.records)
    out += [f"- 레코드(앞에만): {x}" for x in sorted(ra - rb)]
    out += [f"+ 레코드(뒤에만): {x}" for x in sorted(rb - ra)]
    if a.records != b.records and not out:
        out.append("레코드 다중집합 개수 차이")
    for k in sorted(set(a.graph_inputs) | set(b.graph_inputs)):
        if a.graph_inputs.get(k) != b.graph_inputs.get(k):
            out.append(f"그래프 입력 {k}: {a.graph_inputs.get(k)} != {b.graph_inputs.get(k)}")
    if a.final_state != b.final_state:
        out.append(f"최종 상태: {a.final_state} != {b.final_state}")
    if a.acked != b.acked:
        out.append(f"ACK: {a.acked} != {b.acked}")
    if len(a.dead_letters) != len(b.dead_letters):
        out.append(f"dead-letter: {len(a.dead_letters)} != {len(b.dead_letters)}")
    return out


def assert_equivalent(a: HarnessResult, b: HarnessResult) -> None:
    """동등하지 않으면 차이 목록과 함께 실패한다."""
    lines = diff(a, b)
    assert not lines, "동등성 위반:\n" + "\n".join(lines)
