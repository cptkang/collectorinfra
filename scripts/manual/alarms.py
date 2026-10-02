"""알람·노이즈 화면용 녹화·재생 (plans/116 §4.4).

``--record`` : 알람 녹화 서버(스냅샷 ``alarm`` 프로필 — 알람 워커 · 노이즈 게이트 · 로컬 MLX)에 모의 폴스타
               이벤트를 넣고, 워커가 내보내는 SSE·사건 이벤트(Redis pub/sub)와 결정 기록을 저장한다.
``--replay`` : 캡처 서버가 구독 중인 채널에 녹화한 이벤트를 다시 발행한다(LLM 호출 0).
``seed_decisions(app_dir)`` : 녹화한 결정 기록을 캡처 서버의 결정 저장소로 복사한다 — 간격은 두고
               마지막 기록이 지금이 되도록 시각만 옮긴다(노이즈 관제는 기간을 현재 기준으로 거른다).

이벤트는 ``noise_gate/scripts/mock_polestar_events.py`` 의 시나리오 카탈로그를 그대로 쓴다.
스트림·채널 이름은 캡처 전용(snapshot.py)이라 다른 세션의 Redis 사용과 섞이지 않는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import redis
from noise_gate.scripts.mock_polestar_events import SCENARIOS, make_payload
from scripts.manual.snapshot import APP_DIRS, FIXTURES, PROFILES

OUT = FIXTURES / "alarms"
_ENV = PROFILES["capture"]
CH_SSE = _ENV["NOISE_SSE_BRIDGE_CHANNEL"]
CH_INC = _ENV["NOISE_INCIDENT_EVENT_CHANNEL"]
STREAM = _ENV["ALARM_REDIS_STREAM_KEY"]
REDIS_URL = f"redis://{_ENV['REDIS_HOST']}:{_ENV['REDIS_PORT']}/{_ENV['REDIS_DB']}"

# 매뉴얼에 싣는 장면: 즉시 통보(PAGE) · 억제(중요도 낮음·유지보수·중복) · 여러 서버 동시 장애
RECORD_SET = (
    "sev3-page",
    "low-suppress",
    "maint-suppress",
    "dup-suppress",
    "distinct-pair",
    "cross-host",
)
DB_ID = "polestar"


def record(wait_s: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    r = redis.Redis.from_url(REDIS_URL)
    got: dict[str, list] = {CH_SSE: [], CH_INC: []}
    stop = threading.Event()

    def listen() -> None:
        ps = r.pubsub()
        ps.subscribe(CH_SSE, CH_INC)
        while not stop.is_set():
            msg = ps.get_message(timeout=1.0)
            if msg and msg.get("type") == "message":
                ch = msg["channel"].decode()
                got[ch].append(json.loads(msg["data"]))
        ps.close()

    th = threading.Thread(target=listen, daemon=True)
    th.start()
    run_id = uuid.uuid4().hex[:6]
    by_name = {s.name: s for s in SCENARIOS}
    for name in RECORD_SET:
        for step in by_name[name].build(run_id, DB_ID):
            r.xadd(STREAM, {"data": json.dumps(step.payload, ensure_ascii=False)})
            print(f"보냄 {name}: {step.label}", flush=True)
            time.sleep(max(1.0, step.interval_after))
    # 추천 질문 칩(U-42)은 CPU·메모리·디스크 자원 알람에서만 만들어진다 — 카탈로그에 없어 1건 더한다
    cpu = make_payload(
        db_id=DB_ID,
        server_name="svr-web-03",
        severity=2,
        alarm_name="CPU 사용률",
        alarm_id=f"MANUAL-{run_id}-CPU",
        ip_address="10.0.1.3",
        resource_type="server.Cpus",
        resource_name="CPU",
        conditions="> 90%, 3회 연속",
        condition_log="[CPU 사용률 95.3% (> 90%, 3회 연속)]",
    )
    r.xadd(STREAM, {"data": json.dumps(cpu, ensure_ascii=False)})
    print("보냄 cpu-threshold: svr-web-03 CPU 사용률", flush=True)
    print(f"워커 처리 대기 최대 {wait_s}s …", flush=True)
    deadline = time.time() + wait_s
    last = -1
    while time.time() < deadline:
        n = len(got[CH_SSE])
        if n == last and n > 0 and time.time() > deadline - wait_s + 120:
            break
        last = n
        time.sleep(15)
    stop.set()
    th.join(timeout=5)
    (OUT / "sse.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in got[CH_SSE]), "utf-8"
    )
    (OUT / "incident.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in got[CH_INC]), "utf-8"
    )
    src = APP_DIRS["alarm"] / "logs" / "alarm_decisions.jsonl"
    if src.exists():
        shutil.copy2(src, OUT / "decisions.jsonl")
    print(
        f"SSE {len(got[CH_SSE])}건 · 사건 {len(got[CH_INC])}건 · 결정 기록 {'있음' if src.exists() else '없음'}"
    )


def replay(delay: float = 0.4) -> None:
    r = redis.Redis.from_url(REDIS_URL)
    for name, ch in (("incident.jsonl", CH_INC), ("sse.jsonl", CH_SSE)):
        path = OUT / name
        if not path.exists():
            continue
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                r.publish(ch, line)
                time.sleep(delay)


def seed_decisions(app_dir: Path) -> int:
    """결정 기록을 캡처 서버 저장소로 옮긴다(시각만 현재로 이동)."""
    src = OUT / "decisions.jsonl"
    if not src.exists():
        return 0
    recs = [json.loads(ln) for ln in src.read_text("utf-8").splitlines() if ln.strip()]
    stamps = [dt.datetime.fromisoformat(r["ts"]) for r in recs if r.get("ts")]
    if not stamps:
        return 0
    now = dt.datetime.now(stamps[-1].tzinfo)
    shift = (now - dt.timedelta(minutes=5)) - max(stamps)
    for r in recs:
        if r.get("ts"):
            r["ts"] = (dt.datetime.fromisoformat(r["ts"]) + shift).isoformat()
    dst = app_dir / "logs" / "alarm_decisions.jsonl"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), "utf-8")
    return len(recs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true")
    g.add_argument("--replay", action="store_true")
    ap.add_argument("--wait", type=int, default=900, help="녹화 시 워커 처리 대기 상한(초)")
    a = ap.parse_args()
    if a.record:
        record(a.wait)
    else:
        replay()


if __name__ == "__main__":
    sys.exit(main())
