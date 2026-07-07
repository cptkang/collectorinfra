"""Plan 52 노이즈 게이트 시나리오 E2E 실행기 (docs/16_plan52_noise_gate_test_guide.md §6).

임의 알람 이벤트를 발생시켜 (a) 게이트가 적절히 노이즈 캔슬링하는지, (b) LLM 분석과
연동되는지 확인한다. 로그인(토큰 발급)·이벤트 주입·결정 감사 확인을 자동화한다.

주입 경로 2가지(가이드 §6.2):
    - api   : POST /api/v1/alarm/analyze-test/raw — LLM 연동·심각도3 단락·보수적 PAGE 확인.
              ⚠ API 경로는 enricher를 거치지 않아 noise_context 미수집 → 게이트는 수집실패
              보수화로 severity>=1이면 항상 PAGE. 중요도/유지보수 시나리오는 redis 경로로만.
    - redis : XADD alarm:raw — 운영 경로 100%(워커 dedup·자가복구·매트릭스 신호 포함).
              사전조건: ALARM_ENABLED=true(+게이트 on, ALARM_MIN_SEVERITY=1 권장),
              폴스타 도커 픽스처(noise-test-* 서버, testdata/pg).

판정: 서버가 기록하는 결정 감사 JSONL(logs/alarm_decisions.jsonl)에서 주입한 alarm_id의
tier를 읽어 기대 티어와 비교한다(서버와 같은 호스트/리포지토리 루트에서 실행 전제).

사용 예:
    python scripts/noise_gate_scenario_test.py --list
    python scripts/noise_gate_scenario_test.py --mode api --user tester --password 'pass123!' --register
    python scripts/noise_gate_scenario_test.py --mode all
    python scripts/noise_gate_scenario_test.py --only api-sev3-page,worker-maint-suppress
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_DECISION_LOG = "logs/alarm_decisions.jsonl"
DEFAULT_DB_ID = "polestar_pg"          # 도커 폴스타 픽스처 프로필 (testdata/pg)
STREAM_KEY = "alarm:raw"

# 도커 폴스타 픽스처 서버 (testdata/pg/init/06_plan52_noise_fixtures.sql)
SRV_HIGH = "noise-test-high"    # importance_id=3(높음)
SRV_MED = "noise-test-med"      # importance_id=2(보통)
SRV_LOW = "noise-test-low"      # importance_id=1(낮음)
SRV_MAINT = "noise-test-maint"  # is_maintenance=1

OOM_LOG = "kernel: Out of memory: Killed process 12345 (java) score 900"

RUN_ID = uuid.uuid4().hex[:8]


# ─── 이벤트 생성/주입 ─────────────────────────────────────────────────────────

def make_payload(
    server: str,
    severity: int,
    alarm_name: str,
    *,
    db_id: str,
    resource: str = "CPU",
    condition_log: str = "cpu=95 (scenario test)",
    alarm_id: str | None = None,
) -> dict:
    """폴스타 단일행 JSON 템플릿과 동일한 키의 알람 페이로드를 생성한다."""
    return {
        "dbId": db_id,
        "serverName": server,
        "hostname": server,
        "ipAddress": "10.0.0.1",
        "resourceAncestry": "",
        "alarmId": alarm_id or f"SIM-{RUN_ID}-{uuid.uuid4().hex[:8]}",
        "severity": severity,
        "alarmStatus": "NOT_ACK",
        "resourceType": "server.Server",
        "resourceName": resource,
        "alarmName": alarm_name,
        "alarmTime": datetime.now().strftime("%Y%m%d%H%M%S"),
        "conditions": "cpu>90",
        "conditionLog": condition_log,
    }


def inject_api(ctx: dict, payload: dict, *, dry_run: bool = False) -> dict:
    """analyze-test/raw로 주입한다. dry_run=False면 게이트·notifier까지 실행된다.

    channels=[]로 외부 채널 실발송은 차단한다(게이트 판단·감사 기록은 그대로 수행).
    """
    resp = ctx["http"].post(
        f"{ctx['base_url']}/api/v1/alarm/analyze-test/raw",
        headers={"Authorization": f"Bearer {ctx['token']}"},
        json={
            "message": json.dumps(payload, ensure_ascii=False),
            "dry_run": dry_run,
            "send_notification": not dry_run,
            "channels": [],
            "query_history": False,
        },
        timeout=ctx["timeout"],
    )
    resp.raise_for_status()
    return resp.json()


def inject_redis(ctx: dict, payload: dict) -> None:
    """운영 경로 그대로 Redis Stream에 XADD한다(워커가 소비)."""
    import redis  # 프로젝트 의존성 (redis[hiredis])

    r = redis.Redis.from_url(ctx["redis_url"])
    r.xadd(STREAM_KEY, {"data": json.dumps(payload, ensure_ascii=False)})
    r.close()


# ─── 결정 감사 확인 ───────────────────────────────────────────────────────────

def log_offset(ctx: dict) -> int:
    path = Path(ctx["decision_log"])
    return path.stat().st_size if path.exists() else 0


def wait_decision(ctx: dict, alarm_id: str, offset: int, timeout: float) -> dict | None:
    """offset 이후 append된 JSONL에서 alarm_id의 결정 레코드를 기다린다."""
    path = Path(ctx["decision_log"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                fh.seek(offset)
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("alarm_id") == alarm_id:
                        return rec
        time.sleep(1.0)
    return None


# ─── 시나리오 ─────────────────────────────────────────────────────────────────
# 반환: (ok: bool, detail: str)

def scenario_api_sev3_page(ctx: dict) -> tuple[bool, str]:
    """심각도 3은 어떤 경우에도 PAGE (§6 step 0)."""
    payload = make_payload(SRV_MAINT, 3, f"NG-{RUN_ID}-a1", db_id=ctx["db_id"])
    off = log_offset(ctx)
    inject_api(ctx, payload)
    rec = wait_decision(ctx, payload["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "결정 감사 레코드 없음 (NOISE_ENABLE_NOISE_GATE=true 확인)"
    return rec["tier"] == "page", f"tier={rec['tier']} reason={rec['reason']}"


def scenario_api_conservative_page(ctx: dict) -> tuple[bool, str]:
    """API 경로는 noise_context 미수집 → 수집실패 보수화로 PAGE (재현율 우선 §6.3)."""
    payload = make_payload(SRV_LOW, 2, f"NG-{RUN_ID}-a2", db_id=ctx["db_id"])
    off = log_offset(ctx)
    inject_api(ctx, payload)
    rec = wait_decision(ctx, payload["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "결정 감사 레코드 없음"
    return rec["tier"] == "page", f"tier={rec['tier']} reason={rec['reason']}"


def scenario_api_llm_analysis(ctx: dict) -> tuple[bool, str]:
    """LLM 분석 생성 확인(L1) — summary/probable_cause가 condition_log 근거로 생성."""
    payload = make_payload(SRV_MED, 2, f"NG-{RUN_ID}-a3", db_id=ctx["db_id"])
    body = inject_api(ctx, payload, dry_run=True)
    if body.get("error"):
        return False, f"분석 오류: {body['error']}"
    analysis = body.get("analysis") or {}
    ok = bool(analysis.get("summary")) and bool(analysis.get("probable_cause"))
    return ok, f"summary={str(analysis.get('summary'))[:60]!r}"


def scenario_api_ai_boost(ctx: dict) -> tuple[bool, str]:
    """AI 심각도 상향(L3) — OOM 시그니처 sev1 → ai_severity=3 → PAGE 승격.

    서버에 NOISE_ENABLE_AI_SEVERITY_BOOST=true 필요(기본 제외 시나리오).
    """
    payload = make_payload(
        SRV_MED, 1, f"NG-{RUN_ID}-a4", db_id=ctx["db_id"], condition_log=OOM_LOG
    )
    off = log_offset(ctx)
    inject_api(ctx, payload)
    rec = wait_decision(ctx, payload["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "결정 감사 레코드 없음 (ALARM_MIN_SEVERITY=1 / boost 플래그 확인)"
    ai = (rec.get("signals") or {}).get("ai_severity")
    ok = rec["tier"] == "page" and ai == 3
    return ok, f"tier={rec['tier']} ai_severity={ai} reason={rec['reason']}"


def _worker_matrix(ctx: dict, server: str, severity: int, tag: str, expect: str) -> tuple[bool, str]:
    payload = make_payload(server, severity, f"NG-{RUN_ID}-{tag}", db_id=ctx["db_id"])
    off = log_offset(ctx)
    inject_redis(ctx, payload)
    rec = wait_decision(ctx, payload["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "결정 감사 레코드 없음 (ALARM_ENABLED/게이트/min_severity/워커 로그 확인)"
    imp = (rec.get("signals") or {}).get("importance")
    return rec["tier"] == expect, f"tier={rec['tier']} importance={imp} reason={rec['reason']}"


def scenario_worker_high_sev2(ctx: dict) -> tuple[bool, str]:
    """매트릭스: sev2 × 높음(noise-test-high) → PAGE (§3.2)."""
    return _worker_matrix(ctx, SRV_HIGH, 2, "r1", "page")


def scenario_worker_low_sev1(ctx: dict) -> tuple[bool, str]:
    """매트릭스: sev1 × 낮음(noise-test-low) → DASHBOARD (E3: SUPPRESS→DASHBOARD 셀).

    ALARM_MIN_SEVERITY=1이어야 severity 1이 게이트에 도달한다(§4.8).
    """
    return _worker_matrix(ctx, SRV_LOW, 1, "r2", "dashboard")


def scenario_worker_maint_suppress(ctx: dict) -> tuple[bool, str]:
    """유지보수(IS_MAINTENANCE=1, noise-test-maint) sev2 → SUPPRESS(감사 기록) (§3.5)."""
    return _worker_matrix(ctx, SRV_MAINT, 2, "r3", "suppress")


def scenario_worker_unknown_server(ctx: dict) -> tuple[bool, str]:
    """미등록 서버 → 미식별 중요도 = 보통 취급(R-4) → sev2×보통 = TICKET."""
    return _worker_matrix(ctx, f"ng-unknown-{RUN_ID}", 2, "r4", "ticket")


def scenario_worker_dedup(ctx: dict) -> tuple[bool, str]:
    """핑거프린트 dedup(§6.1): 동일 서버·알람명·resource 재발생은 결정 없이 억제."""
    name = f"NG-{RUN_ID}-r5"
    first = make_payload(SRV_HIGH, 2, name, db_id=ctx["db_id"])
    off = log_offset(ctx)
    inject_redis(ctx, first)
    rec = wait_decision(ctx, first["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "1건째 결정 레코드 없음"
    second = make_payload(SRV_HIGH, 2, name, db_id=ctx["db_id"])  # alarmId만 상이
    off2 = log_offset(ctx)
    inject_redis(ctx, second)
    dup = wait_decision(ctx, second["alarmId"], off2, ctx["absence_window"])
    if dup is not None:
        return False, f"2건째가 dedup되지 않고 결정됨: tier={dup['tier']}"
    return True, f"1건째 tier={rec['tier']}, 2건째 {ctx['absence_window']}s 내 미기록(재통보 억제)"


def scenario_worker_self_heal(ctx: dict) -> tuple[bool, str]:
    """자가복구 상관(§3.7): 발생 후 창 내 해소(sev0) → 해소는 SUPPRESS + 발생 매칭 종료."""
    name = f"NG-{RUN_ID}-r6"
    firing = make_payload(SRV_MED, 2, name, db_id=ctx["db_id"])
    off = log_offset(ctx)
    inject_redis(ctx, firing)
    rec = wait_decision(ctx, firing["alarmId"], off, ctx["timeout"])
    if rec is None:
        return False, "발생 알람 결정 레코드 없음"
    clear = make_payload(SRV_MED, 0, name, db_id=ctx["db_id"])
    off2 = log_offset(ctx)
    inject_redis(ctx, clear)
    crec = wait_decision(ctx, clear["alarmId"], off2, ctx["timeout"])
    if crec is None:
        return False, "해소(sev0) 결정 레코드 없음 (severity 0 워커 전달 §4.8 확인)"
    return crec["tier"] == "suppress", (
        f"발생 tier={rec['tier']} → 해소 tier={crec['tier']} reason={crec['reason']}"
    )


SCENARIOS: list[tuple[str, str, object]] = [
    # (id, mode, fn) — mode: api | redis
    ("api-sev3-page", "api", scenario_api_sev3_page),
    ("api-conservative-page", "api", scenario_api_conservative_page),
    ("api-llm-analysis", "api", scenario_api_llm_analysis),
    ("api-ai-boost", "opt", scenario_api_ai_boost),  # opt: --only로만 실행
    ("worker-high-sev2-page", "redis", scenario_worker_high_sev2),
    ("worker-low-sev1-dashboard", "redis", scenario_worker_low_sev1),
    ("worker-maint-suppress", "redis", scenario_worker_maint_suppress),
    ("worker-unknown-server-ticket", "redis", scenario_worker_unknown_server),
    ("worker-dedup", "redis", scenario_worker_dedup),
    ("worker-self-heal", "redis", scenario_worker_self_heal),
]


# ─── 인증 ─────────────────────────────────────────────────────────────────────

def get_token(ctx: dict, user: str, password: str, register: bool) -> str:
    """로그인해서 토큰을 받는다. --register면 계정이 없을 때 즉시 가입 후 재시도."""
    login = {"user_id": user, "password": password}
    resp = ctx["http"].post(f"{ctx['base_url']}/api/v1/auth/login", json=login, timeout=15)
    if resp.status_code == 401 and register:
        reg = ctx["http"].post(
            f"{ctx['base_url']}/api/v1/auth/register",
            json={"user_id": user, "username": user, "password": password},
            timeout=15,
        )
        if reg.status_code not in (200, 201, 409):
            raise SystemExit(f"계정 가입 실패({reg.status_code}): {reg.text}")
        resp = ctx["http"].post(f"{ctx['base_url']}/api/v1/auth/login", json=login, timeout=15)
    if resp.status_code != 200:
        raise SystemExit(f"로그인 실패({resp.status_code}): {resp.text} — --register 옵션 참고")
    return resp.json()["access_token"]


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=os.getenv("NG_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--user", default=os.getenv("NG_TEST_USER", "ng_tester"))
    parser.add_argument("--password", default=os.getenv("NG_TEST_PASSWORD", "ng_tester_pw1!"))
    parser.add_argument("--register", action="store_true", help="계정 없으면 자동 가입")
    parser.add_argument("--redis-url", default=os.getenv("NG_REDIS_URL", DEFAULT_REDIS_URL))
    parser.add_argument("--db-id", default=DEFAULT_DB_ID, help="이벤트 dbId (기본: 도커 폴스타)")
    parser.add_argument("--decision-log", default=DEFAULT_DECISION_LOG)
    parser.add_argument("--mode", choices=["api", "redis", "all"], default="api",
                        help="api=서버만 필요(기본) / redis=워커 운영경로 / all=둘 다")
    parser.add_argument("--only", default="", help="쉼표 구분 시나리오 id (mode 무시)")
    parser.add_argument("--timeout", type=float, default=90.0, help="결정 대기(초, LLM 지연 포함)")
    parser.add_argument("--absence-window", type=float, default=15.0,
                        help="dedup '미기록' 판정 대기(초)")
    parser.add_argument("--list", action="store_true", help="시나리오 목록만 출력")
    args = parser.parse_args()

    if args.list:
        for sid, mode, fn in SCENARIOS:
            print(f"{sid:32s} [{mode:5s}] {fn.__doc__.splitlines()[0]}")
        return 0

    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        selected = [s for s in SCENARIOS if s[0] in wanted]
        unknown = wanted - {s[0] for s in selected}
        if unknown:
            raise SystemExit(f"알 수 없는 시나리오: {', '.join(sorted(unknown))} (--list 참고)")
    else:
        modes = {"api": {"api"}, "redis": {"redis"}, "all": {"api", "redis"}}[args.mode]
        selected = [s for s in SCENARIOS if s[1] in modes]

    ctx = {
        "base_url": args.base_url.rstrip("/"),
        "redis_url": args.redis_url,
        "db_id": args.db_id,
        "decision_log": args.decision_log,
        "timeout": args.timeout,
        "absence_window": args.absence_window,
        "http": httpx.Client(),
    }
    ctx["token"] = get_token(ctx, args.user, args.password, args.register)
    print(f"로그인 OK — run_id={RUN_ID}, 시나리오 {len(selected)}개 실행\n")

    failures = 0
    for sid, _mode, fn in selected:
        try:
            ok, detail = fn(ctx)
        except Exception as exc:  # noqa: BLE001 — 시나리오별 격리, 다음 시나리오 계속
            ok, detail = False, f"예외: {exc}"
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{status}] {sid:32s} {detail}")

    print(f"\n결과: {len(selected) - failures}/{len(selected)} PASS")
    if failures:
        print("힌트: 게이트/워커 플래그(NOISE_ENABLE_NOISE_GATE, ALARM_ENABLED, "
              "ALARM_MIN_SEVERITY=1)와 폴스타 픽스처(testdata/pg) 상태를 확인하세요.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
