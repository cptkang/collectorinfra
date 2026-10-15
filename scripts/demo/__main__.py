"""시연 세션 실행기 CLI.

    python -m scripts.demo --list                        # 세션 목록·채워진 질의 미리보기 (서버 호출 없음)
    python -m scripts.demo --check --env-file <파일>      # 자리표·업로드·소스 점검만 (서버 호출 없음)
    python -m scripts.demo --core --user U --password P  # ★ 핵심 세트를 떠 있는 서버에 질의
    python -m scripts.demo --only G3-1,G4-2 --user U --password P

세션 하나가 웹 대화 1건(thread_id 1개)이 된다. 상세: docs/35_demo_script_guide.md

콘솔 출력은 ASCII 구두점만 쓴다 - cp949 콘솔에서 em-dash 가 UnicodeEncodeError 를 낸다.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import REPO_ROOT
from .catalog import (
    DEFAULT_ENV_PATH,
    SESSIONS_PATH,
    CatalogError,
    Environment,
    Session,
    Turn,
    load_catalog,
    load_environment,
    render_session,
    select_sessions,
)
from .client import DemoClient, TurnResult, auto_answer, pending_question

OUTPUT_ROOT = REPO_ROOT / "logs" / "demo"
#: 한 턴에서 연속으로 자동 응답할 수 있는 역질문 수(존 -> 소스 -> 승인이 이어져도 3회).
MAX_AUTO_ANSWERS = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.demo",
        description="시연 세션을 떠 있는 웹 서버에 질의해 웹 대화 이력으로 남긴다 (판정 없음).",
    )
    mode = p.add_argument_group("동작")
    mode.add_argument("--list", action="store_true", help="세션 목록과 채워진 질의 미리보기 (서버 호출 없음)")
    mode.add_argument("--check", action="store_true", help="자리표·업로드·소스 점검만 (서버 호출 없음)")

    sel = p.add_argument_group("선택")
    sel.add_argument("--core", action="store_true", help="핵심 세트(core: true)만")
    sel.add_argument("--group", action="append", default=[], help="군 id (반복 가능 · 예: A)")
    sel.add_argument("--only", default=[], type=lambda v: v.split(","), help="세션 id 목록 (쉼표 구분)")
    sel.add_argument("--sessions", type=Path, default=SESSIONS_PATH, help="세션 카탈로그 YAML")
    sel.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH,
                     help="환경 값 파일 (연동 소스·자리표 값). 폐쇄망 값 파일은 저장소 밖에 둔다")

    srv = p.add_argument_group("서버")
    srv.add_argument("--base-url", default="http://127.0.0.1:8000", help="웹 서버 주소")
    srv.add_argument("--user", help="이력을 남길 사용자 ID (인증이 켜진 서버)")
    srv.add_argument("--password", help="사용자 비밀번호")
    srv.add_argument("--client-id",
                     help="인증이 꺼진 서버에서 이력을 붙일 브라우저 식별자 "
                          "(브라우저 localStorage 'query_client_id' 값)")
    srv.add_argument("--admin-user", help="운영자 ID - 서버의 실효 LLM 프로바이더 확인용(선택)")
    srv.add_argument("--admin-password", help="운영자 비밀번호")
    srv.add_argument("--clarify", choices=["auto", "stop"], default="auto",
                     help="예상하지 않은 역질문: auto = 웹 칩과 같은 구조화 답으로 자동 응답, "
                          "stop = 그 세션을 멈추고 웹에서 발표자가 답한다")
    srv.add_argument("--timeout", type=float, default=600.0, help="SSE 무응답 상한(초)")
    srv.add_argument("--yes", action="store_true", help="과금 프로바이더 확인 프롬프트 생략")
    return p


# --- 준비 -----------------------------------------------------------------


def _load(args: argparse.Namespace) -> tuple[list[Session], list[tuple[Session, str]], Environment, list[str]]:
    catalog = load_catalog(args.sessions)
    env = load_environment(args.env_file)
    chosen, skipped = select_sessions(catalog, env, only=args.only, groups=args.group, core_only=args.core)
    problems = list(catalog.problems)
    rendered: list[Session] = []
    for session in chosen:
        filled, issues = render_session(session, env)
        problems.extend(issues)
        rendered.append(filled)
    return rendered, skipped, env, problems


def _print_plan(sessions: list[Session], skipped: list[tuple[Session, str]], env: Environment,
                *, verbose: bool, catalog_path: Path) -> None:
    print(f"카탈로그: {catalog_path}")
    print(f"환경: {env.name} ({env.path})")
    print(f"연동 소스: {', '.join(env.sources) or '(없음)'}")
    current = None
    for s in sessions:
        if s.group_id != current:
            current = s.group_id
            print(f"\n[{s.group_id}] {s.group_name}")
        star = "*" if s.core else " "
        print(f" {star} {s.id}  {s.title}  ({len(s.turns)}턴)")
        if verbose:
            if s.shows:
                print(f"      보여주는 것: {s.shows}")
            for i, t in enumerate(s.turns, start=1):
                print(f"      {i}. {_turn_label(t)}")
    if skipped:
        print("\n건너뜀:")
        for s, reason in skipped:
            print(f"   {s.id}  {s.title} - {reason}")
    turns = sum(len(s.turns) for s in sessions)
    print(f"\n실행 대상: {len(sessions)}세션 {turns}턴 · 건너뜀 {len(skipped)}세션 (* = 핵심 세트)")


def _turn_label(t: Turn) -> str:
    parts = [t.query or "(직전 역질문의 원 질의)"]
    if t.upload:
        parts.append(f"[업로드 {t.upload}]")
    if t.selected_db_ids:
        parts.append(f"[존 선택 {','.join(t.selected_db_ids)}]")
    if t.selected_sources:
        parts.append(f"[소스 선택 {','.join(t.selected_sources)}]")
    if t.selected_apm_source_ids:
        parts.append(f"[제니퍼 선택 {','.join(t.selected_apm_source_ids)}]")
    if t.form_fill_answers:
        parts.append(f"[양식 답 {', '.join(t.form_fill_answers)}]")
    if t.expect_clarify:
        parts.append("(역질문 기대)")
    return " ".join(parts)


def _providers(client: DemoClient, args: argparse.Namespace) -> tuple[tuple[str, str], str]:
    if args.admin_user:
        found, origin = client.admin_providers(args.admin_user, args.admin_password or "")
        if found:
            return found, origin
        print(f"  ! {origin} - 로컬 설정으로 대신 판정합니다")
    try:
        from src.config import load_config

        cfg = load_config()
        return (str(cfg.llm.provider or "unknown").lower(),
                str(cfg.orchestrator.provider or "unknown").lower()), "로컬 .env"
    except Exception as exc:  # 못 읽으면 외부로 본다 - 과금 게이트를 열지 않는다
        unknown = f"unknown({type(exc).__name__})"
        return (unknown, unknown), "판독 실패"


def _billing_gate(client: DemoClient, args: argparse.Namespace) -> bool:
    """과금 프로바이더면 사람의 확인을 받는다(CLAUDE.md 과금 게이트 · D-222 판정 규칙)."""
    from scripts.scenario.preflight import external_planes

    (worker, orch), origin = _providers(client, args)
    external = external_planes(worker, orch)
    print(f"LLM 프로바이더 ({origin}): 워커={worker} · 오케스트레이터={orch}")
    if not external:
        return True
    print(f"  ! 비과금 집합 밖 평면: {', '.join(external)} - 질의마다 외부 API 비용이 발생할 수 있습니다")
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("  중단 - 대화형 확인을 받을 수 없습니다. 진행하려면 --yes 를 붙이세요")
        return False
    return input("  계속하려면 yes 를 입력하세요: ").strip().lower() == "yes"


def _check_health(client: DemoClient, env: Environment) -> bool:
    health, err = client.health()
    if err:
        print(f"중단 - {err}")
        return False
    status_map: dict[str, bool] = (health or {}).get("db_status_map") or {}
    print(f"서버 상태: {health.get('status')} · DB {', '.join(f'{k}={v}' for k, v in status_map.items()) or '-'}")
    connected = {k for k, v in status_map.items() if v}
    # SQL 소스만 헬스로 대조할 수 있다. 비SQL 소스(prometheus·apm·docs)는 환경 값 파일 선언을 믿는다.
    if "polestar" in env.sources and not any(k.startswith("polestar") for k in connected):
        print("  ! 환경 파일은 polestar 를 선언했지만 연결된 폴스타 DB 가 없습니다")
    if "itam" in env.sources and "itam" not in connected:
        print("  ! 환경 파일은 itam 을 선언했지만 itam 이 연결돼 있지 않습니다")
    return True


# --- 실행 -----------------------------------------------------------------


def _turn_body(turn: Turn, thread_id: str, last: Optional[TurnResult]) -> dict[str, Any]:
    body: dict[str, Any] = {"thread_id": thread_id}
    query = turn.query
    if not query:
        # 역질문 답 턴 - 웹 UI 처럼 원 질의를 다시 보내고 라우팅은 구조화 필드가 정한다.
        if turn.form_fill_answers:
            query = "[양식 미해결 항목 답변]"
        else:
            clar = (last.done.get("clarification") or last.done.get("apm_source_clarification")) if last else None
            query = str((clar or {}).get("original_query") or (last.sent.get("query") if last else "") or "")
    body["query"] = query
    if turn.selected_db_ids:
        body["selected_db_ids"] = list(turn.selected_db_ids)
    if turn.selected_sources:
        body["selected_sources"] = list(turn.selected_sources)
    if turn.selected_apm_source_ids:
        body["selected_apm_source_ids"] = list(turn.selected_apm_source_ids)
    if turn.form_fill_answers:
        body["form_fill_answers"] = turn.form_fill_answers
        body["form_fill_remember"] = turn.form_fill_remember
    return body


def _summary(text: str, width: int = 90) -> str:
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 3] + "..."


def run_session(
    client: DemoClient, session: Session, env: Environment, args: argparse.Namespace, out_dir: Path,
) -> list[TurnResult]:
    thread_id = str(uuid.uuid4())
    results: list[TurnResult] = []
    last: Optional[TurnResult] = None
    print(f"\n=== {session.id} {session.title}  (thread {thread_id[:8]})")
    for i, turn in enumerate(session.turns, start=1):
        body = _turn_body(turn, thread_id, last)
        # 폼필 답변 턴은 JSON 경로다 - 파일을 다시 올리지 않는다(웹 UI 와 같다).
        upload = turn.upload_path if turn.upload and not turn.form_fill_answers else None
        print(f"  {i}. {_turn_label(turn)}")
        result = client.send(body, upload)
        _report(result, client, out_dir, session.id, i)
        results.append(result)
        last = result

        answered = 0
        while result.ok and not turn.expect_clarify and answered < MAX_AUTO_ANSWERS:
            question = pending_question(result.done)
            if question is None:
                break
            if args.clarify == "stop" or question.kind == "text":
                print(f"     > 역질문({question.kind}) - 웹에서 이어서 답하세요. 이 세션은 여기서 멈춥니다")
                return results
            reply = auto_answer(question, last_query=str(body.get("query") or ""),
                                zone_preference=env.zone_preference,
                                apm_preference=env.apm_source_preference)
            if reply is None:
                print(f"     > 역질문({question.kind})에 자동으로 답할 선택지가 없습니다 - 세션 중단")
                return results
            reply["thread_id"] = thread_id
            # 파일 경로의 존 역질문은 웹 UI 처럼 같은 파일을 다시 올린다.
            again = upload if question.kind == "db_select" and question.payload.get("has_file") else None
            print(f"     > 자동 응답({question.kind}): {_answer_label(reply)}")
            result = client.send(reply, again)
            result.auto_answer = True
            _report(result, client, out_dir, session.id, i)
            results.append(result)
            last = result
            answered += 1
        if result.error:
            print("     > 오류로 이 세션의 남은 턴을 건너뜁니다")
            break
    return results


def _answer_label(reply: dict[str, Any]) -> str:
    for key in ("selected_db_ids", "selected_sources", "selected_apm_source_ids"):
        if reply.get(key):
            return f"{key}={','.join(reply[key])}"
    if reply.get("form_fill_answers"):
        return "양식 미해결 열 비움: " + ", ".join(reply["form_fill_answers"])
    return str(reply.get("query") or "")


def _report(result: TurnResult, client: DemoClient, out_dir: Path, sid: str, turn_no: int) -> None:
    if result.error:
        print(f"     X {result.error}  ({result.elapsed_s}s)")
        return
    done = result.done
    rows = done.get("row_count")
    extra = f" · {rows}행" if rows else ""
    print(f"     - {result.status}{extra} · {result.elapsed_s}s · {_summary(result.response)}")
    if done.get("has_file") and done.get("query_id"):
        name = str(done.get("file_name") or f"{sid}-{turn_no}.xlsx")
        saved = client.download(str(done["query_id"]), out_dir / "files" / f"{sid}-{turn_no}-{name}")
        result.downloaded = str(saved) if saved else None
        print(f"     - 산출 파일: {saved or '내려받기 실패(웹 이력에서 받으세요)'}")


def _write_outputs(out_dir: Path, records: list[tuple[Session, list[TurnResult]]], env: Environment) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as fh:
        for session, results in records:
            for r in results:
                fh.write(json.dumps({
                    "session": session.id, "query": r.sent.get("query"), "thread_id": r.sent.get("thread_id"),
                    "auto_answer": r.auto_answer, "status": r.status, "elapsed_s": r.elapsed_s,
                    "row_count": r.done.get("row_count"), "error": r.error, "response": r.response,
                    "file": r.downloaded,
                }, ensure_ascii=False) + "\n")
    md = [f"# 시연 대화록 - {env.name}", "",
          f"- 생성: {datetime.now():%Y-%m-%d %H:%M}",
          f"- 연동 소스: {', '.join(env.sources)}", ""]
    current = None
    for session, results in records:
        if session.group_id != current:
            current = session.group_id
            md += [f"## {session.group_id} · {session.group_name}", ""]
        md += [f"### {session.id} {session.title}", ""]
        if session.shows:
            md += [f"> 보여주는 것: {session.shows}", ""]
        for r in results:
            who = "자동 응답" if r.auto_answer else "질의"
            md += [f"**{who}**: {r.sent.get('query')}  ",
                   f"*{r.status} · {r.elapsed_s}s*", ""]
            md += [r.error or r.response or "(빈 응답)", ""]
    path = out_dir / "transcript.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # 응답 본문·카탈로그 문구에는 cp949 밖 문자가 섞인다 - 출력 때문에 시연 런이 죽지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        sessions, skipped, env, problems = _load(args)
    except CatalogError as exc:
        print(f"중단 - {exc}")
        return 2

    if args.list or args.check:
        _print_plan(sessions, skipped, env, verbose=args.list, catalog_path=args.sessions)
    if problems:
        print("\n문제:")
        for p in problems:
            print(f"  - {p}")
        print("자리표 문제면 환경 값 파일의 values 를 채우세요 (docs/35_demo_script_guide.md 「실제 데이터 입력」)")
        return 1
    if not sessions:
        print("\n실행할 세션이 없습니다 - --group·--only·--core 선택과 카탈로그 파일"
              f"({args.sessions})을 확인하세요")
        return 1
    if args.list or args.check:
        print("\n점검 통과" if args.check else "")
        return 0
    if not args.user and not args.client_id:
        print("! --user/--password 도 --client-id 도 없습니다 - 인증이 켜진 서버면 401, "
              "꺼진 서버면 웹 대화 목록에 나타나지 않습니다(소유 키 없음)")

    with DemoClient(args.base_url, user=args.user, password=args.password,
                    client_id=args.client_id, timeout_s=args.timeout) as client:
        if not _check_health(client, env):
            return 1
        err = client.login()
        if err:
            print(f"중단 - {err}")
            return 1
        if not _billing_gate(client, args):
            print("중단 - 과금 확인을 받지 못했습니다")
            return 1
        print(f"실행: {len(sessions)}세션 · 건너뜀 {len(skipped)}세션")
        for s, reason in skipped:
            print(f"  - 건너뜀 {s.id}: {reason}")

        out_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
        records: list[tuple[Session, list[TurnResult]]] = []
        try:
            for session in sessions:
                records.append((session, run_session(client, session, env, args, out_dir)))
        except KeyboardInterrupt:
            print("\n중단됨 - 지금까지의 결과를 기록합니다")
        transcript = _write_outputs(out_dir, records, env)

    failed = sum(1 for _, rs in records for r in rs if r.error)
    print(f"\n완료: {len(records)}세션 · 오류 턴 {failed}건")
    print(f"대화록: {transcript}")
    print("웹 화면에서 같은 계정(또는 같은 브라우저)으로 대화 목록을 여세요.")
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
