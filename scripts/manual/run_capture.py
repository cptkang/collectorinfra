"""캡처 전 과정 1명령 (plans/116 §4.5 · V-8).

    python -m scripts.manual.run_capture [--only ID ...] [--keep]

1. 캡처 DB 재생성 + 스냅샷(``capture`` 프로필 — 재생 그래프 · LLM 호출 0)
2. 녹화한 결정 기록을 캡처 서버 저장소로 옮김(시각만 현재로)
3. 캡처 서버 기동(18981) — **자기가 띄운 PID 만** 종료한다
4. 시드 — 사례 계정·사용자 몇 명·침묵 규칙 1건(API 로만 넣는다 — DB 를 직접 쓰지 않는다)
5. ``capture.py`` 실행(uv 로 playwright 를 붙여 별도 프로세스) → ``build.py``

전제: Redis(6380) · 샌드박스 PG 컨테이너(polestar_pg) 가동. 브라우저는 playwright 캐시.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

import httpx

from scripts.manual import alarms, snapshot
from scripts.manual.snapshot import (
    ADMIN_PASSWORD,
    ADMIN_USER,
    DEMO_PASSWORD,
    DEMO_USER,
    FIXTURES,
    PORT,
    REPO,
)

BASE = f"http://127.0.0.1:{PORT}"
PY = sys.executable

# 사용자 관리 화면에 보일 가상의 동료 — 실존 인물이 아니다
_PEOPLE = [
    ("kim_infra", "김인프라", "인프라운영팀"),
    ("lee_db", "이디비", "DB운영팀"),
    ("park_noc", "박관제", "관제센터"),
]


def _wait_health(timeout: float = 60) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"{BASE}/api/v1/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("캡처 서버가 뜨지 않았다 — build/manual_capture/serve-capture.log 확인")


def _seed() -> None:
    c = httpx.Client(base_url=BASE, timeout=30)
    for uid, name, dept, pw in [(DEMO_USER, "홍길동", "인프라운영팀", DEMO_PASSWORD)] + [
        (u, n, d, "Manual-Peer-2026") for u, n, d in _PEOPLE
    ]:
        c.post(
            "/api/v1/auth/register",
            json={"user_id": uid, "username": name, "password": pw, "department": dept},
        )
    tok = c.post(
        "/api/v1/auth/login", json={"user_id": ADMIN_USER, "password": ADMIN_PASSWORD}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    # 사례 계정은 샌드박스 DB 조회 권한, 관제 담당은 알림그룹(존)을 준다 — 사용자 관리 화면의 예시값
    c.put(
        f"/api/v1/admin/users/{DEMO_USER}/permissions",
        headers=h,
        json={"allowed_db_ids": ["polestar"]},
    )
    # ↑ 사례 계정은 샌드박스만 — 존 DB 권한을 주면 「전체 서버…」 같은 대량 조회에 존 역질문이 먼저 발동해
    #   녹화 재생 화면이 가려진다. 존 역질문·조회 대상 장면은 전 존 권한인 관리자로 찍는다(captures.yaml)
    c.put("/api/v1/admin/users/park_noc", headers=h, json={"alarm_zones": ["gongjon"]})
    c.post(
        "/api/v1/admin/noise/silences",
        headers=h,
        json={
            "server_name": "svr-bkp-*",
            "max_severity": 2,
            "duration_seconds": 86400,
            "reason": "백업 서버 교체 작업(예시)",
        },
    )
    # 로그인 실패 1건 — 감사 로그의 이벤트 종류가 보이도록
    c.post("/api/v1/auth/login", json={"user_id": "lee_db", "password": "wrong-password"})


def _serve_and_capture(profile: str, only: list[str] | None, keep: bool) -> int:
    """프로필 스냅샷으로 캡처 서버를 띄우고 그 서버 소속 장면을 찍는다. 자기가 띄운 PID 만 종료한다."""
    app = snapshot.build(profile)
    if profile == "capture":
        n = alarms.seed_decisions(app)
        print(f"스냅샷 {app} · 결정 기록 {n}건")
    env = {
        **os.environ,
        "MANUAL_MODE": "replay",
        "MANUAL_PORT": str(PORT),
        "MANUAL_FIXTURES_DIR": str(FIXTURES / "queries"),
    }
    log = open(snapshot.BUILD / f"serve-{profile}.log", "w")
    proc = subprocess.Popen(
        [PY, "-m", "scripts.manual._serve"], cwd=app, env=env, stdout=log, stderr=log
    )
    try:
        _wait_health()
        _seed()
        cmd = [
            "uv",
            "run",
            "-q",
            "--no-project",
            "--with",
            "playwright==1.63.0",
            "--with",
            "pyyaml",
            "--with",
            "redis",
            "python",
            str(REPO / "scripts/manual/capture.py"),
            "--base",
            BASE,
            "--server",
            profile,
        ]
        if only:
            cmd += ["--only", *only]
        return subprocess.run(cmd, cwd=REPO).returncode
    finally:
        if not keep:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--keep", action="store_true", help="끝나도 마지막 캡처 서버를 내리지 않는다")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()

    import socket

    with socket.socket() as sk:
        if sk.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(
                f"포트 {PORT} 이 이미 쓰이고 있다 — 이전 캡처 서버(--keep)나 녹화 서버를 먼저 내린다"
            )
    snapshot.reset_capture_db()
    profiles = ("capture", "capture-zones")
    rc = 0
    for i, profile in enumerate(profiles):
        rc |= _serve_and_capture(profile, a.only, keep=a.keep and i == len(profiles) - 1)
    if not a.no_build:
        subprocess.run([PY, "-m", "scripts.manual.build"], cwd=REPO, check=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
