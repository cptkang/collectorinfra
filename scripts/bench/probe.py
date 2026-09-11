"""자식 프로세스 격리 실행 — 설정이 **실제로 어떻게 읽혔는지** 되받아온다.

왜 자식 프로세스인가. `src/config.py:1188-1191`이 못박은 실측이 있다:

    nested config를 인스턴스 기본값으로 두면 **모듈 임포트 시점의 env로 고정**되어,
    이후 `os.environ` 변경 + `load_config.cache_clear()` 재로드가 반영되지 않는다
    (2026-07-15 E1 하네스 A/B 무효화 실측).

즉 같은 프로세스에서 env를 갈아끼우며 335필드를 도는 방식은 **전 항목이 거짓 통과**한다.
그래서 필드마다 파이썬을 새로 띄워 `load_config()`를 한 번 하고 결과만 받아온다(경량 모드).

에코를 비교하면 세 가지가 한꺼번에 드러난다.
  * 주입이 먹었는가            → L3 주입 실효성
  * 그 값으로 기동이 되는가     → L2 기동 안전성
  * 값을 바꾸면 무언가 달라지는가 → L4 소비 실증
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: 자식이 실행할 코드. 설정 전체를 직렬화해 stdout 마지막 줄로 낸다.
#: `--server`를 띄우지 않으므로 그래프 빌드·DB 접속이 없다(1~2초).
_ECHO_SNIPPET = r"""
import json, sys
sys.path.insert(0, %(root)r)
try:
    from src.config import load_config
    cfg = load_config()
    flat = {}
    def walk(obj, prefix=""):
        data = obj.model_dump() if hasattr(obj, "model_dump") else {}
        for name, value in data.items():
            key = f"{prefix}{name}"
            if isinstance(value, dict):
                walk_dict(value, key + ".")
            else:
                flat[key] = _safe(value, key)
    def walk_dict(d, prefix):
        for name, value in d.items():
            key = f"{prefix}{name}"
            if isinstance(value, dict):
                walk_dict(value, key + ".")
            else:
                flat[key] = _safe(value, key)
    import hashlib, os as _os
    # 부모가 카탈로그(D-129)에서 계산해 넘긴 민감 경로. 이름 추측보다 정확하다.
    _SENSITIVE_PATHS = set(json.loads(_os.environ.get("__BENCH_SENSITIVE_PATHS__", "[]")))
    # 카탈로그가 놓친 것을 위한 보조 규칙 — **부분 문자열이 아니라 꼬리 일치**다.
    # (`token`을 부분 일치로 보면 `prompt_token_budget` 같은 숫자를 시크릿으로 오판한다 — 2026-09-11 실측)
    _SENSITIVE_TAILS = ("_secret", "_password", "_api_key", "_token", "_connection_string")
    def _safe(v, key=""):
        try:
            json.dumps(v)
        except Exception:
            v = str(v)
        low = key.lower()
        sensitive = low in _SENSITIVE_PATHS or any(low.endswith(t) for t in _SENSITIVE_TAILS)
        if sensitive and v not in (None, "", 0, False):
            # 값은 남기지 않고 **변화 여부만** 보존한다(L4는 값이 아니라 변화를 본다).
            return "sha256:" + hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:16]
        return v
    walk(cfg)
    print("__BENCH_ECHO__" + json.dumps({"ok": True, "config": flat}, ensure_ascii=False))
except Exception as exc:
    print("__BENCH_ECHO__" + json.dumps(
        {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:4000]},
        ensure_ascii=False))
"""

_MARKER = "__BENCH_ECHO__"


@dataclass(frozen=True)
class EchoResult:
    """자식 프로세스 1회 실행 결과.

    `ok=False`는 **예외가 아니라 데이터**다 — 기동 실패 자체가 L2의 측정 대상이므로
    호출부가 try/except로 감싸지 않아도 되게 한다(침묵 폴백 금지의 반대편: 실패를 실어 나른다).
    """

    ok: bool
    config: Mapping[str, object] = field(default_factory=dict)
    error_type: Optional[str] = None
    error: Optional[str] = None
    stderr_tail: Optional[str] = None

    def value_of(self, dotted: str) -> object:
        return self.config.get(dotted)


#: 자식 실행기 시그니처. 테스트는 여기에 가짜를 주입해 프로세스를 띄우지 않는다.
Runner = Callable[[Mapping[str, str], float], "subprocess.CompletedProcess[str]"]


def _default_runner(env: Mapping[str, str], timeout: float) -> "subprocess.CompletedProcess[str]":
    code = _ECHO_SNIPPET % {"root": str(_PROJECT_ROOT)}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_PROJECT_ROOT),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def echo_config(
    overrides: Optional[Mapping[str, str]] = None,
    *,
    base_env: Optional[Mapping[str, str]] = None,
    timeout: float = 60.0,
    runner: Optional[Runner] = None,
) -> EchoResult:
    """env를 주입해 자식에서 설정을 읽고, **자식이 실제로 읽은 값**을 돌려준다.

    Args:
        overrides: 주입할 env. `None` 값은 그 키를 **삭제**한다(기본값 경로 확인용).
        base_env: 기본은 현재 `os.environ` 사본.
        timeout: 자식 상한(초).
        runner: 테스트 주입용.

    Returns:
        `EchoResult`. 실패해도 예외를 던지지 않는다.
    """
    env = dict(base_env if base_env is not None else os.environ)
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    # 자식이 부모의 사이트 패키지 대신 프로젝트를 보게 한다(비-editable 사본 오독 방지 — D-162 실측).
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("__BENCH_SENSITIVE_PATHS__", json.dumps(sorted(sensitive_config_paths())))

    run = runner or _default_runner
    try:
        proc = run(env, timeout)
    except subprocess.TimeoutExpired:
        return EchoResult(ok=False, error_type="TimeoutExpired",
                          error=f"자식 프로세스가 {timeout}초 안에 끝나지 않았다")
    except Exception as exc:  # 실행 자체가 불가한 경우도 데이터로 만든다
        return EchoResult(ok=False, error_type=type(exc).__name__, error=str(exc)[:2000])

    payload = _extract(proc.stdout or "")
    stderr_tail = (proc.stderr or "").strip()[-2000:] or None
    if payload is None:
        return EchoResult(
            ok=False, error_type="NoEcho",
            error="자식이 에코를 내지 않았다(임포트 단계에서 죽었을 수 있다)",
            stderr_tail=stderr_tail,
        )
    if not payload.get("ok"):
        return EchoResult(
            ok=False,
            error_type=str(payload.get("error_type") or "Unknown"),
            error=str(payload.get("error") or ""),
            stderr_tail=stderr_tail,
        )
    return EchoResult(ok=True, config=dict(payload.get("config") or {}), stderr_tail=stderr_tail)


def sensitive_config_paths() -> frozenset[str]:
    """카탈로그가 민감으로 판정한 필드의 **에코 경로** 집합.

    이름 휴리스틱보다 정확하다 — 카탈로그는 `SecretStr` 타입과 명시 목록을 둘 다 안다.
    임포트 실패(카탈로그를 못 읽는 환경)에도 프로브는 돌아야 하므로 빈 집합으로 강등한다.
    """
    try:
        from src.api import settings_catalog as sc
    except Exception:
        return frozenset()
    paths = set()
    for spec in sc.field_index().values():
        if spec.is_secret or spec.is_sensitive:
            prefix = "" if spec.group_key == "general" else f"{spec.group_key}."
            paths.add(f"{prefix}{spec.field_name}".lower())
    return frozenset(paths)


def _extract(stdout: str) -> Optional[dict]:
    """에코 마커가 붙은 줄만 골라 파싱한다.

    설정 로딩 중 경고·로그가 stdout에 섞일 수 있어 **마지막 줄 가정은 쓰지 않는다**
    (`eval_text2sql`가 감사 로그 때문에 겪은 것과 같은 유형의 오염).
    """
    for line in reversed(stdout.splitlines()):
        idx = line.find(_MARKER)
        if idx >= 0:
            try:
                return json.loads(line[idx + len(_MARKER):])
            except json.JSONDecodeError:
                continue
    return None


def detect_nondeterministic_keys(
    *,
    runs: int = 2,
    base_env: Optional[Mapping[str, str]] = None,
    runner: Optional[Runner] = None,
) -> frozenset[str]:
    """같은 조건으로 여러 번 읽어 **매번 달라지는 필드**를 찾는다.

    실측(2026-09-11): `auth.jwt_secret`이 미설정 시 기동마다 새로 생성된다. 이 한 필드가
    설정 지문을 매번 바꿔 L4(소비 실증)를 전부 "변함"으로 만든다.

    하드코딩 제외 목록을 두지 않는 이유는 같은 성질의 필드가 나중에 또 생기기 때문이다 —
    **목록을 손으로 관리하면 낡는다**(`UNCONSUMED_KEYS`가 겪은 것과 같은 문제).
    """
    seen: list[Mapping[str, object]] = []
    for _ in range(max(2, runs)):
        res = echo_config(base_env=base_env, runner=runner)
        if not res.ok:
            return frozenset()  # 기준을 못 읽으면 판정 보류(빈 집합)
        seen.append(res.config)
    keys = set().union(*(set(c) for c in seen))
    return frozenset(k for k in keys if len({str(c.get(k)) for c in seen}) > 1)


def config_fingerprint(
    result: EchoResult,
    *,
    exclude_prefixes: tuple[str, ...] = (),
    exclude_keys: frozenset[str] = frozenset(),
) -> str:
    """실효 설정 전체의 안정 해시 — L4 소비 실증의 비교 단위."""
    import hashlib

    items = sorted(
        (k, v) for k, v in result.config.items()
        if k not in exclude_keys and not any(k.startswith(p) for p in exclude_prefixes)
    )
    blob = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
