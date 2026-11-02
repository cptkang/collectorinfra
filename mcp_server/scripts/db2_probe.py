"""DB2 드라이버(ibm_db) 실측 프로브 (plans/206 W0 ④ · W1 착수 전 실측 ①~③ · 폐쇄망용).

로컬 venv에는 ``ibm_db``가 없어 W1(DB2 연결 풀) 설계 전제를 내부망 설치본에서 확인한다.

1. 스레드 간 순차 사용 — 연결 1개를 만들고 매번 다른 스레드에서 차례로(동시 아님) N회 실행
   (+ 그 연결의 autocommit 현재값)
2. 시그니처 — ``ibm_db.active``·``free_stmt``·``pconnect``·``connect``·``autocommit``
   (``inspect.signature``, 실패하면 ``__doc__`` 첫 줄)와 ``SQL_ATTR_QUERY_TIMEOUT``·
   ``SQL_AUTOCOMMIT_ON`` 상수 존재 여부
3. ``pconnect`` 동작 — 두 스레드가 각각 부른 반환 핸들이 같은 객체·같은 연결인지
4. 비용 — ``ibm_db.connect`` 단독 N회 vs 같은 연결에서 SQL 실행+fetch N회 · ``active`` N회 ·
   비율과 판정. 풀 경로는 꺼낼 때마다 ``active``를 부르므로 풀이 아끼는 몫은
   ``connect − active``이고, 판정은 이 값을 실행 p50과 비교한다

읽기 전용이다(D-003) — 실행 SQL은 기본 ``SELECT 1 FROM SYSIBM.SYSDUMMY1``이고, ``--sql``로 바꾼
SQL도 ``validate_readonly``를 통과해야 실행한다. 연결 문자열·비밀번호는 출력하지 않는다 —
드라이버 예외 메시지도 연결 문자열을 지우고 비밀값 꼴을 가린 뒤 싣는다.

연결 문자열은 ``--source``(``config.toml``의 ``[[sources]]`` + ``.env``의
``{SOURCE}_CONNECTION`` — 서버와 같은 로더)나 ``--conn-env``(환경변수 이름)로 받는다.

사용:
    cd mcp_server && python scripts/db2_probe.py --source my_db2
    python scripts/db2_probe.py --conn-env DB2_PROBE_CONNECTION --only 3,4 --repeat 20

종료 코드: 0 = 선택 항목 전부 정상 · 1 = 한 항목이라도 예외 · 2 = 인자·설정 오류(``ibm_db`` 미설치 ·
소스 없음 · 읽기 전용 위반 SQL).
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# `python scripts/db2_probe.py`로 실행해도 패키지(`mcp_server`)가 잡히게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.config import load_config  # noqa: E402
from mcp_server.log_masking import mask_secrets  # noqa: E402
from mcp_server.security import ReadOnlyViolationError, validate_readonly  # noqa: E402

DEFAULT_SQL = "SELECT 1 FROM SYSIBM.SYSDUMMY1"
#: ③에서 두 핸들이 같은 DB 연결인지 가리는 SQL — 연결마다 다른 애플리케이션 ID를 돌려준다.
APP_ID_SQL = "SELECT APPLICATION_ID() AS app_id FROM SYSIBM.SYSDUMMY1"
#: W0 판정 기준(plans/206 §3 W0) — 접속 비용이 실행 p50 대비 이 비율 이상이면 유의.
SIGNIFICANT_RATIO = 0.30
_SIGNATURE_NAMES = ("active", "free_stmt", "pconnect", "connect", "autocommit")
_CONSTANT_NAMES = ("SQL_ATTR_QUERY_TIMEOUT", "SQL_AUTOCOMMIT_ON")
_ALL_ITEMS = (1, 2, 3, 4)


class ProbeArgError(Exception):
    """인자·설정 오류(종료 코드 2)."""


def _safe_error(exc: BaseException, conn_str: str) -> str:
    """예외를 출력 가능한 한 줄로 만든다 — 연결 문자열을 지우고 비밀값 꼴을 가린다."""
    text = f"{type(exc).__name__}: {exc}"
    if conn_str:
        text = text.replace(conn_str, "<연결 문자열>")
    return mask_secrets(text)


def resolve_connection(args: argparse.Namespace) -> str:
    """``--source`` 또는 ``--conn-env``에서 연결 문자열을 얻는다. 값 자체는 출력하지 않는다."""
    if args.conn_env:
        conn_str = os.environ.get(args.conn_env, "")
        if not conn_str:
            raise ProbeArgError(f"환경변수 {args.conn_env}가 비어 있다")
        return conn_str
    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:  # 설정 오류는 traceback 대신 인자 오류(종료 코드 2)로
        raise ProbeArgError(mask_secrets(f"설정 로드 실패 — {type(exc).__name__}: {exc}")) from exc
    for src in config.sources:
        if src.name == args.source:
            if src.type != "db2":
                raise ProbeArgError(f"소스 {args.source}는 DB2가 아니다(type={src.type})")
            return src.connection
    names = ", ".join(s.name for s in config.sources) or "(없음)"
    raise ProbeArgError(f"활성 소스에 {args.source}가 없다 — 활성 소스: {names}")


def _run_sql(ibm_db: Any, conn: Any, sql: str) -> int:
    """SQL을 실행하고 전 행을 fetch한 뒤 문장을 해제한다. 행 수를 돌려준다."""
    stmt = ibm_db.exec_immediate(conn, sql)
    count = 0
    row = ibm_db.fetch_assoc(stmt)
    while row:
        count += 1
        row = ibm_db.fetch_assoc(stmt)
    if hasattr(ibm_db, "free_stmt"):
        ibm_db.free_stmt(stmt)
    return count


def _fetch_first(ibm_db: Any, conn: Any, sql: str) -> Any:
    """SQL 첫 행의 첫 값을 돌려준다(③ 연결 동일성 판정용)."""
    stmt = ibm_db.exec_immediate(conn, sql)
    row = ibm_db.fetch_tuple(stmt)
    if hasattr(ibm_db, "free_stmt"):
        ibm_db.free_stmt(stmt)
    return row[0] if row else None


def probe_thread_sequential(ibm_db: Any, conn_str: str, sql: str, n: int) -> None:
    """① 연결 1개를 매번 새 스레드에서 차례로 N회 쓴다 — 실행마다 성공/예외와 스레드 이름."""
    print(f"[1] 스레드 간 순차 사용 — 연결 1개 · {n}회 · 매회 새 스레드")
    conn = ibm_db.connect(conn_str, "", "")
    try:
        print(f"  autocommit 현재값: {ibm_db.autocommit(conn)!r}")
    except Exception as exc:  # 값 확인 실패는 순차 사용 판정과 무관 — 사유만 싣는다
        print(f"  autocommit 현재값: 확인 불가 · {_safe_error(exc, conn_str)}")
    ok = 0
    try:
        for i in range(n):
            # 실행기를 매번 새로 만들어 직전과 다른 스레드에서 돌게 한다(유휴 스레드 재사용 방지).
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"probe-{i}") as pool:
                future = pool.submit(
                    lambda: (threading.current_thread().name, _run_sql(ibm_db, conn, sql))
                )
                try:
                    thread_name, rows = future.result()
                    ok += 1
                    print(f"  #{i + 1} 성공 · 스레드={thread_name} · 행={rows}")
                except Exception as exc:  # 회차별 결과를 모두 보이려고 개별로 잡는다
                    print(f"  #{i + 1} 예외 · {_safe_error(exc, conn_str)}")
    finally:
        ibm_db.close(conn)
    verdict = "가능" if ok == n else "불가 또는 불안정"
    print(f"  → 성공 {ok}/{n} — 연결 핸들 스레드 간 순차 사용 {verdict}")


def _describe(obj: Any) -> str:
    """호출 가능 객체의 시그니처 — 실패하면 ``__doc__`` 첫 줄."""
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        doc = (getattr(obj, "__doc__", None) or "").strip()
        return f"(signature 불가) doc: {doc.splitlines()[0] if doc else '(없음)'}"


def probe_signatures(ibm_db: Any) -> None:
    """② 드라이버 함수 시그니처와 상수(쿼리 타임아웃·autocommit) 존재 여부."""
    print("[2] 시그니처")
    for name in _SIGNATURE_NAMES:
        obj = getattr(ibm_db, name, None)
        print(f"  ibm_db.{name}: {'없음' if obj is None else _describe(obj)}")
    for name in _CONSTANT_NAMES:
        attr = getattr(ibm_db, name, None)
        print(f"  ibm_db.{name}: {'없음' if attr is None else f'있음 (= {attr!r})'}")


def probe_pconnect(ibm_db: Any, conn_str: str) -> None:
    """③ 같은 연결 문자열의 ``pconnect``를 두 스레드에서 불러 핸들·연결 동일성을 본다."""
    print("[3] pconnect 동작 — 두 스레드에서 각각 호출")
    handles = []
    for i in range(2):
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"pconn-{i}") as pool:
            handles.append(pool.submit(ibm_db.pconnect, conn_str, "", "").result())
    first, second = handles
    print(f"  같은 객체(is): {first is second}")
    try:
        app_ids = [_fetch_first(ibm_db, h, APP_ID_SQL) for h in handles]
        same_conn = app_ids[0] is not None and app_ids[0] == app_ids[1]
        print(f"  같은 DB 연결(APPLICATION_ID 일치): {same_conn}")
    except Exception as exc:  # 조회 실패는 ③ 실패가 아니다 — is 비교로 판단한다
        same_conn = False
        print(f"  APPLICATION_ID 미지원 — is 비교만 유효 · {_safe_error(exc, conn_str)}")
    if first is second or same_conn:
        print("  → 스레드 간 같은 연결을 돌려준다 — 동시 실행을 나누려면 직접 풀이 필요하다")
    else:
        print("  → 스레드마다 다른 연결이다 — 드라이버 영속 연결로 풀을 대신할 여지가 있다")
    print("  주의: pconnect 연결은 ibm_db.close로 실제로 닫히지 않을 수 있다(영속 연결 — "
          "해제는 프로세스 종료 시). 풀에 쓰려면 닫는 방법을 드라이버 문서로 따로 확인한다")


def _stats(values: list[float]) -> str:
    """평균·p50·최소·최대(ms) 한 줄."""
    return (f"평균 {statistics.fmean(values):.1f} · p50 {statistics.median(values):.1f} · "
            f"최소 {min(values):.1f} · 최대 {max(values):.1f} ms")


def probe_cost(ibm_db: Any, conn_str: str, sql: str, n: int) -> None:
    """④ ``connect`` 단독 N회 vs 같은 연결의 실행+fetch N회·``active`` N회 — 비율과 W0 판정."""
    print(f"[4] 비용 — connect 단독 {n}회 vs 같은 연결 실행+fetch {n}회 · active {n}회")
    connect_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        conn = ibm_db.connect(conn_str, "", "")
        connect_ms.append((time.perf_counter() - start) * 1000)
        ibm_db.close(conn)  # 닫기는 측정에서 뺀다
    exec_ms: list[float] = []
    conn = ibm_db.connect(conn_str, "", "")
    try:
        for _ in range(n):
            start = time.perf_counter()
            _run_sql(ibm_db, conn, sql)
            exec_ms.append((time.perf_counter() - start) * 1000)
        active_ms: list[float] = []
        for _ in range(n):
            start = time.perf_counter()
            ibm_db.active(conn)
            active_ms.append((time.perf_counter() - start) * 1000)
    finally:
        ibm_db.close(conn)
    print(f"  connect: {_stats(connect_ms)}")
    print(f"  실행+fetch: {_stats(exec_ms)}")
    print(f"  active: {_stats(active_ms)}")
    exec_p50 = statistics.median(exec_ms)
    if exec_p50 <= 0:
        print("  비율: 산출 불가(실행 p50 = 0)")
        return
    connect_mean = statistics.fmean(connect_ms)
    saved = connect_mean - statistics.fmean(active_ms)
    print(f"  비율(connect 평균 / 실행 p50): {connect_mean / exec_p50 * 100:.0f}%")
    # 풀 경로 = active + 실행 — 풀이 아끼는 몫은 connect − active다(판정 기준).
    ratio = saved / exec_p50
    print(f"  비율((connect 평균 − active 평균) / 실행 p50 · 판정 기준): {ratio * 100:.0f}%")
    if ratio >= SIGNIFICANT_RATIO:
        print("  → DB2 접속 비용 유의 — 풀 효과 기대")
    else:
        print(f"  → 풀이 아끼는 몫이 실행 p50의 {SIGNIFICANT_RATIO * 100:.0f}% 미만 — 풀 효과 작음")


def _parse_only(raw: str) -> list[int]:
    """``--only 1,3`` → [1, 3]. 범위 밖 값은 인자 오류."""
    items = sorted({int(x) for x in raw.split(",") if x.strip()})
    if not items or any(i not in _ALL_ITEMS for i in items):
        raise argparse.ArgumentTypeError("--only는 1~4를 쉼표로 적는다(예: 1,3)")
    return items


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 돌려준다(모듈 독스트링 참조)."""
    parser = argparse.ArgumentParser(description="DB2 드라이버 실측 프로브 (plans/206 W0·W1)")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--source", help="config.toml [[sources]]의 DB2 소스 이름")
    target.add_argument("--conn-env", help="연결 문자열을 담은 환경변수 이름")
    parser.add_argument("--config", default=None, help="config.toml 경로(기본: 패키지 루트)")
    parser.add_argument("--sql", default=DEFAULT_SQL, help=f"실행 SQL(기본: {DEFAULT_SQL})")
    parser.add_argument("--only", type=_parse_only, default=list(_ALL_ITEMS),
                        help="실행 항목(쉼표 구분 · 기본 1,2,3,4)")
    parser.add_argument("--repeat", type=int, default=0,
                        help="반복 횟수(기본: ① 5회 · ④ 10회)")
    args = parser.parse_args(argv)
    if args.repeat < 0:
        parser.error("--repeat는 0 이상이어야 한다")

    try:
        ibm_db = importlib.import_module("ibm_db")
    except ImportError:
        print("ibm_db 미설치 — 내부망 venv(mcp_server 의존성 설치본)에서 실행한다", file=sys.stderr)
        return 2
    try:
        validate_readonly(args.sql)
    except ReadOnlyViolationError as exc:
        print(f"읽기 전용 위반 SQL — 실행하지 않는다: {exc}", file=sys.stderr)
        return 2
    try:
        conn_str = resolve_connection(args)
    except ProbeArgError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    runners = {
        1: lambda: probe_thread_sequential(ibm_db, conn_str, args.sql, args.repeat or 5),
        2: lambda: probe_signatures(ibm_db),
        3: lambda: probe_pconnect(ibm_db, conn_str),
        4: lambda: probe_cost(ibm_db, conn_str, args.sql, args.repeat or 10),
    }
    failed = 0
    for item in args.only:
        try:
            runners[item]()
        except Exception as exc:  # 항목별 독립 — 하나가 실패해도 나머지를 진행한다
            failed += 1
            print(f"  [{item}] 실패 · {_safe_error(exc, conn_str)}")
        print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
