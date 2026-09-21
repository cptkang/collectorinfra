"""holmes bash 검증 경계 — **파일을 만드는 형태를 거부**한다 (D-235).

**왜 필요한가** (2026-09-17·09-21 실측):
holmes 0.36.0의 bash 허용목록은 **prefix 매칭**이고, 검증은 `bashlex`를 세그먼트 분해에만 쓴다.
리다이렉트는 세그먼트 문자열 안에 남아 명령 이름으로 매칭되므로, `uptime`이 허용이면
`uptime > /tmp/x`도 **허용**된다. 설정으로 막을 방법이 없다 — `BashExecutorConfig`에는
`allow`·`deny`·`builtin_allowlist`뿐이고(실측), `deny`도 prefix라 리다이렉트를 표현할 수 없다.

원격 프로파일은 bash를 꺼서 닫았지만(D-233), **로컬 배치의 bash는 그 배치의 유일한 L3 증거
수집 수단**이라(`plans/sre-agent/02` §8) 끌 수 없다. 그래서 허용목록은 그대로 두고, 검증 경계에서
**쓰기 형태만** 거부한다 — 읽기 전용 조사(`VM_DIAG_ALLOW`·부하 가드 형태)는 종전과 같다.

**무엇을 거부하는가**: 출력 리다이렉트(`>`·`>>`·`>|`·`&>`·`>&` — `/dev/null`과 fd 복제 `2>&1`은 제외) ·
`sort -o|--output` · `uniq <입력> <출력>` · `tee` · `dd of=`. 입력 리다이렉트(`<`)와 heredoc은 읽기라 통과한다.

**어디에 거는가**: `RunBashCommand._validate_command` — `requires_approval()`과 `_invoke()`가 **둘 다**
지나는 단일 관문이다. 그래서 승인으로도 뚫리지 않고(거부는 승인 대상이 아니다), bash를 켜는 프로파일이
늘어도 가드는 한 곳이다. 거부는 holmes의 `DenyReason.DENY_LIST` 경로를 타므로 **사유가 도구 결과
`error`로 LLM에 전달된다**(조용히 막으면 모델이 같은 시도를 반복한다 — 9B 퇴행 실측).

**안전성**: 쓰기 형태가 아니면 **원래 검증 결과 객체를 그대로 반환**한다(no-op). 파싱 불가 명령은
판정하지 않고 holmes에 맡긴다(holmes가 이미 승인 필요로 돌린다). 설치 실패는 예외를 올리지 않고
경고만 남긴다 — 가드 설치 실패가 조사를 막아서는 안 된다(`llm_message_guard` 전례).

계층: infrastructure(외부 패키지 어댑터). 상위(application)는 `install_bash_write_guard()`만 부른다.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 몽키패치 멱등성 표식 — 재설치·중복 호출을 무해하게 만든다.
_GUARD_FLAG = "_sre_bash_write_guarded"

# 출력 리다이렉트 연산자(입력 `<`·heredoc `<<`는 읽기라 대상이 아니다).
_WRITE_REDIRECTS = (">", ">>", ">|", "&>", ">&")


def _redirect_target(command: str, output: Any) -> str | None:
    """리다이렉트 대상이 **파일**이면 그 문자열을, 아니면 None(무시 대상)을 돌려준다."""
    if output is None:
        return None
    if hasattr(output, "pos"):                      # word 노드 — 파일 경로
        target = command[output.pos[0] : output.pos[1]]
    else:                                           # fd 복제(2>&1 등)는 정수로 온다
        target = str(output)
    target = target.strip()
    if not target or target.isdigit():              # `2>&1` — 파일이 아니다
        return None
    if target in ("/dev/null", "/dev/stderr", "/dev/stdout"):
        return None                                 # 버리기·표준 스트림은 파일을 만들지 않는다
    return target


def write_form_reason(command: str) -> str | None:
    """명령이 **파일을 만드는 형태**면 사유를, 아니면 None을 돌려준다(순수 함수 · 실행하지 않는다).

    Args:
        command: 검증 대상 bash 명령 문자열.

    Returns:
        거부 사유 문자열 또는 None. 파싱 불가는 None(holmes 판정에 맡긴다).
    """
    try:
        import bashlex
    except ImportError:  # pragma: no cover — holmes 의존이라 설치돼 있다
        return None

    try:
        parts = bashlex.parse(command)
    except Exception:  # noqa: BLE001 — ParsingError·NotImplementedError 등: holmes가 승인 필요로 돌린다
        return None

    found: list[str] = []

    class _Visitor(bashlex.ast.nodevisitor):
        def visitredirect(self, node, input, type, output, heredoc):  # noqa: A002 — bashlex 시그니처
            if type in _WRITE_REDIRECTS:
                target = _redirect_target(command, output)
                if target:
                    found.append(f"리다이렉트 '{type}' → {target}")

        def visitcommand(self, node, parts):
            words = [command[p.pos[0] : p.pos[1]] for p in parts if getattr(p, "kind", "") == "word"]
            if not words:
                return
            name, args = words[0], words[1:]
            positional = [a for a in args if not a.startswith("-")]
            if name == "sort" and any(a == "-o" or a.startswith("-o") or a.startswith("--output") for a in args):
                found.append("sort 출력 파일(-o/--output)")
            elif name == "uniq" and len(positional) >= 2:
                found.append(f"uniq 출력 파일({positional[1]})")
            elif name == "tee":
                found.append("tee 파일 쓰기")
            elif name == "dd" and any(a.startswith("of=") for a in args):
                found.append("dd 출력 파일(of=)")

    for part in parts:
        _Visitor().visit(part)

    if not found:
        return None
    return " · ".join(dict.fromkeys(found))


def _deny_result(reason: str, command: str):
    """holmes 거부 결과를 만든다 — 사유가 도구 결과 `error`로 LLM에 전달된다."""
    from holmes.plugins.toolsets.bash.validation import DenyReason, ValidationResult, ValidationStatus

    return ValidationResult(
        status=ValidationStatus.DENIED,
        deny_reason=DenyReason.DENY_LIST,
        message=(
            f"쓰기 형태라 거부됨({reason}). 이 조사 프로파일은 읽기 전용이다 — "
            f"파일을 만들거나 덮어쓰지 말고 출력을 그대로 받아 읽어라. 명령: {command}"
        ),
    )


def install_bash_write_guard() -> bool:
    """holmes bash 검증 경계(`RunBashCommand._validate_command`)에 쓰기 형태 거부를 설치한다.

    Returns:
        True면 이번 호출에서 설치했고, False면 설치하지 않았다(이미 설치됨 · holmes 부재).
        **어느 경우에도 예외를 올리지 않는다** — 가드 설치 실패가 조사를 막아서는 안 된다.
    """
    try:
        from holmes.plugins.toolsets.bash.bash_toolset import RunBashCommand
    except Exception as exc:  # noqa: BLE001 — holmes 구조 변경·부재
        logger.warning("bash 쓰기 가드 미설치(holmes bash toolset 로드 실패): %s: %s", type(exc).__name__, exc)
        return False

    original = getattr(RunBashCommand, "_validate_command", None)
    if original is None:
        logger.warning("bash 쓰기 가드 미설치: RunBashCommand._validate_command 없음(holmes 구조 변경)")
        return False
    if getattr(original, _GUARD_FLAG, False):
        return False

    import functools

    @functools.wraps(original)
    def guarded_validate(self, command_str, suggested_prefixes, context):
        reason = write_form_reason(str(command_str or ""))
        if reason:
            logger.info("bash 쓰기 형태 거부(%s): %s", reason, command_str)
            return _deny_result(reason, str(command_str))
        return original(self, command_str, suggested_prefixes, context)   # ★ 그 외는 원본 판정 그대로

    setattr(guarded_validate, _GUARD_FLAG, True)
    RunBashCommand._validate_command = guarded_validate  # type: ignore[method-assign]
    logger.debug("bash 쓰기 가드 설치 완료(RunBashCommand._validate_command)")
    return True
