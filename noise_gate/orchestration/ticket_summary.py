"""TICKET 일배치 요약 발송 — 큐의 소비 측 (결함 ⑬ · D-048.9 「일배치 요약」 · Plan 52 §7).

TICKET 티어는 즉시 발송하지 않고 `TicketBatchQueue`에 쌓인다. 이 모듈의 주기 루프(워커가 띄운다)가
하루 1회 지정 시각(로컬 `NOISE_TICKET_BATCH_SUMMARY_HOUR`시)에 큐를 읽어 worKB 요약 1통을 보내고,
보낸 항목은 큐에서 지운다(다시 보내지 않는다). 발송은 밖으로 나가는 새 동작이라 기본 꺼짐
(`NOISE_TICKET_BATCH_SUMMARY_ENABLED=false`)이다. 보존 한도 정리
(`NOISE_TICKET_BATCH_QUEUE_MAX_LINES`)는 발송 여부와 무관하게 기동 시 1회 + 매 주기 돈다 —
큐는 감사 정본이 아니다(감사는 decision_store).

- worKB 미설정(WORKB_BASE_URL 없음)이면 D-243 규칙대로 traceback 없는 한 줄 경고로 발송을 생략한다.
  항목은 남긴다(연동 뒤 첫 요약에 실린다 · 상한은 보존 한도).
- 발송 실패는 원인(traceback)과 함께 경고로 남기고 항목을 남긴다 — 다음 주기에 다시 보낸다.
  어떤 실패도 워커를 멈추지 않는다.
- 발송 성공 직후 프로세스가 죽으면 같은 항목이 한 번 더 나갈 수 있다(최소 1회 전달).
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

from noise_gate.application.nodes.alarm_notifier import WorkbNotConfiguredError

logger = logging.getLogger(__name__)

# 요약 본문에 이름을 나열할 알람 종류 수 상한 — 나머지는 「외 N종 M건」 한 줄로 접는다.
_SUMMARY_TOP_N = 20
_DEFAULT_HOUR = 9


def _label(rec: dict) -> str:
    """항목의 사람이 읽는 이름(알람명 — 서버명). 이름 없는 옛 항목은 지문 앞자리."""
    parts = [str(rec.get(key) or "") for key in ("alarm_name", "server_name")]
    if any(parts):
        return " — ".join(p for p in parts if p)
    return f"지문 {str(rec.get('fingerprint') or '')[:12]}"


def _local_time(raw: Any) -> datetime | None:
    """적재 시각(ISO8601)을 로컬 시각으로 바꾼다(파싱 실패 시 None)."""
    try:
        return datetime.fromisoformat(str(raw)).astimezone()
    except (TypeError, ValueError):
        return None


def build_ticket_summary(records: list[dict]) -> tuple[str, str]:
    """TICKET 항목을 worKB 요약 (제목, HTML 본문)으로 만든다.

    `TicketBatchQueue.summarize`와 같은 기준(지문)으로 묶어 건수가 많은 순으로 상위
    `_SUMMARY_TOP_N`종을 나열한다. 이름은 가장 최근 항목의 알람명·서버명이다.
    외부에서 온 문자열은 전부 escape한다.
    """
    groups: dict[str, dict[str, Any]] = {}
    for rec in records:
        group = groups.setdefault(str(rec.get("fingerprint", "")), {"count": 0})
        group["count"] += 1
        group["label"] = _label(rec)
    ordered = sorted(groups.values(), key=lambda g: -g["count"])  # 안정 정렬 — 동률은 먼저 온 순

    total = len(records)
    title = f"[TICKET 요약] {total}건 · 알람 {len(ordered)}종"
    head = ["<b>TICKET 일배치 요약</b>"]
    times = [t for t in (_local_time(r.get("ts")) for r in records) if t is not None]
    if times:
        head.append(f"<b>기간:</b> {min(times):%Y-%m-%d %H:%M} ~ {max(times):%Y-%m-%d %H:%M}")
    head.append(f"<b>건수:</b> {total}건 (알람 {len(ordered)}종)")
    rows = "".join(
        f"<tr><td>{i}. {html.escape(g['label'])}</td><td>{g['count']}건</td></tr>"
        for i, g in enumerate(ordered[:_SUMMARY_TOP_N], start=1)
    )
    body = "<br>".join(head) + f"<hr><table>{rows}</table>"
    rest = ordered[_SUMMARY_TOP_N:]
    if rest:
        body += f"<br>외 {len(rest)}종 {sum(g['count'] for g in rest)}건"
    body += "<br><br>상세: 노이즈 관제 결정 이력에서 티어 TICKET으로 조회"
    return title, body


async def _send_workb_summary(workb_cfg, title: str, body: str) -> None:  # noqa: ANN001
    """요약을 worKB 쪽지로 보낸다 (`_send_workb_followup` 전송 규약 동형 · 기본 수신자).

    수신자는 기본 수신자(`WORKB_USER_IDS_CSV`)다 — 요약은 심각도 1·2가 섞여 한 심각도의
    수신자 오버라이드를 고를 수 없다. 미설정 판정은 전송 함수 한 곳에서 한다(D-243).
    """
    if not workb_cfg.base_url:
        raise WorkbNotConfiguredError("WORKB_BASE_URL이 설정되지 않았습니다.")
    payload = {
        "systemDiv": workb_cfg.system_div,
        "msgTitle": title,
        "msgBody": body,
        "sendId": workb_cfg.send_id,
        "userIds": workb_cfg.user_ids_csv,
        "alias": workb_cfg.alias,
    }
    headers = {
        "Authorization": f"Bearer {workb_cfg.bearer_token}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    url = f"{workb_cfg.base_url.rstrip('/')}/api/sendWorkbMsg"
    async with httpx.AsyncClient(timeout=workb_cfg.timeout_seconds) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()


async def send_ticket_summary_once(queue, workb_cfg) -> bool:  # noqa: ANN001
    """쌓인 TICKET을 요약 1통으로 보내고, 성공하면 보낸 항목만 큐에서 지운다.

    발송했으면 True. 보낼 것이 없거나 worKB 미설정·발송 실패면 False이고 항목은 남는다.
    예외를 올리지 않는다(워커를 멈추지 않는다).
    """
    records, end = queue.read_batch()
    if not records:
        logger.info("TICKET 요약: 대기 항목 없음 — 발송 생략")
        return False
    title, body = build_ticket_summary(records)
    try:
        await _send_workb_summary(workb_cfg, title, body)
    except WorkbNotConfiguredError:
        logger.warning(
            "worKB 미설정(WORKB_BASE_URL 없음) — TICKET 요약 발송 생략: 대기 %d건 유지",
            len(records),
        )
        return False
    except Exception:  # noqa: BLE001 — 발송 실패가 워커를 멈추지 않는다(원인은 남긴다)
        logger.warning(
            "TICKET 요약 발송 실패 — 다음 주기에 다시 보낸다: 대기 %d건",
            len(records),
            exc_info=True,
        )
        return False
    queue.consume(end)
    logger.info("TICKET 요약 발송 완료: %s", title)
    return True


def seconds_until_hour(hour: int, now: datetime) -> float:
    """now 뒤 처음 오는 hour시 정각까지 남은 초(정각이면 다음 날)."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _summary_hour(gate) -> int:  # noqa: ANN001
    """발송 시각(0~23시). 범위 밖이면 경고하고 기본 9시로 보낸다."""
    raw = getattr(gate, "ticket_batch_summary_hour", _DEFAULT_HOUR)
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 23:
        return raw
    logger.warning(
        "NOISE_TICKET_BATCH_SUMMARY_HOUR=%r 는 0~23 밖이다 — %d시로 보낸다", raw, _DEFAULT_HOUR
    )
    return _DEFAULT_HOUR


async def run_ticket_summary_loop(
    queue,  # noqa: ANN001 — TicketBatchQueue (덕 타이핑)
    config,  # noqa: ANN001 — AppConfig (덕 타이핑)
    *,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """기동 시 보존 정리 → 매일 지정 시각에 (켜져 있으면) 요약 발송 → 보존 정리를 반복한다.

    워커 종료(취소)로 끝난다. 한 주기의 어떤 실패도 루프를 끝내지 않는다. now·sleep은 테스트 주입용.
    """
    gate = config.noise_gate
    enabled = bool(getattr(gate, "ticket_batch_summary_enabled", False))
    hour = _summary_hour(gate)
    max_lines = int(getattr(gate, "ticket_batch_queue_max_lines", 20000))
    logger.info(
        "TICKET 일배치 루프 시작: 요약 발송=%s · 매일 %02d시 · 보존 한도 %s줄",
        "on" if enabled else "off", hour, max_lines if max_lines > 0 else "무제한",
    )
    queue.trim(max_lines)  # 기동 시 1회 — 이미 커진 파일을 바로 줄인다
    while True:
        try:
            await sleep(seconds_until_hour(hour, now()))
        except asyncio.CancelledError:
            break
        if enabled:
            try:
                await send_ticket_summary_once(queue, config.workb)
            except Exception:  # noqa: BLE001 — 루프가 죽으면 이후 요약·정리가 모두 멈춘다
                logger.warning("TICKET 일배치 주기 작업 실패(다음 주기 계속)", exc_info=True)
        queue.trim(max_lines)
