"""미들웨어 OS 근사 식별 (Plan 78 W7-1 · SPEC-middleware-os-identification).

실 호스트·실 명령 없이 검증한다 — `ps` 출력 텍스트를 픽스처로 넣고 판정 결과를 단언한다.
LLM 0건(D-127 무관) · `sre_agent` import 0건(D-118).

검증 대상(SPEC §Success Criteria):
    S1 종류·인스턴스 식별자·기동 인자 핵심값이 나온다
    S2 결정성 — 같은 입력에 항상 같은 출력 · LLM 호출 0
    S3 규칙이 **선언 파일에만** 있다 — 코드에 미들웨어명 하드코딩 0
    S4 미식별 시 **사유가 구조화되어 남는다**(빈 결과 금지)
    S5 **규칙 추가만으로** 신규 미들웨어가 식별된다(코드 변경 0)
    S8 `sre_agent`를 import하지 않는다

배경: 미들웨어 장애의 정본 소스는 APM(W7-2)이나 도입 시점·벤더가 미정이다(R-11).
OS 근사는 선행조건이 0이고, **APM 도입 후에도 존치**한다 — APM 미부착 미들웨어가 남고,
APM 자체가 죽었을 때 볼 수단이 필요하기 때문이다(§4.7.1).
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _module_ast() -> ast.Module:
    return ast.parse(inspect.getsource(mw))


def _imported_names() -> set[str]:
    """실제 import 대상만 뽑는다.

    ⚠ 원문 grep은 **docstring의 설명 문구까지** 잡는다 — 모듈이 "LLM을 쓰지 않는다"거나
    "`sre_agent` 소관이다"라고 *설명*하는 것은 의존이 아니다. 초안 테스트가 정확히 그 오탐을
    냈으므로(2026-08-27) 판정을 AST로 옮긴다.
    """
    names: set[str] = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _string_literals() -> list[str]:
    """코드에 박힌 문자열 리터럴(docstring 제외)."""
    tree = _module_ast()
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docstrings
    ]

from src.domain import middleware as mw

# ── 픽스처: 실제 `ps -eo pid,args` 출력에 가까운 형태 ────────────────────
TOMCAT = (
    "/usr/lib/jvm/java-17/bin/java -Djava.util.logging.config.file=/opt/tomcat/conf/logging.properties "
    "-Xms2048m -Xmx4096m -Dcatalina.base=/opt/tomcat/inst1 -Dcatalina.home=/opt/tomcat "
    "org.apache.catalina.startup.Bootstrap start"
)
NGINX = "nginx: master process /usr/sbin/nginx -c /etc/nginx/nginx.conf"
REDIS = "/usr/bin/redis-server 0.0.0.0:6379"
GREP_NOISE = "grep --color=auto tomcat"          # 오탐 유발 — 제외되어야 한다
PLAIN = "/usr/bin/python3 /opt/app/worker.py"    # 미들웨어 아님


def _procs(*items) -> list[dict]:
    return [{"pid": 1000 + i, "cmdline": c} for i, c in enumerate(items)]


class TestIdentification:
    """S1 — 무엇이 돌고 있는지 나와야 한다."""

    def test_identifies_middleware_kind(self):
        scan = mw.identify(_procs(TOMCAT, NGINX, REDIS))
        kinds = {m.kind for m in scan.matched}
        assert {"tomcat", "nginx", "redis"} <= kinds, (
            f"기본 규칙 세트가 식별하지 못했다: {kinds}"
        )

    def test_extracts_instance_and_launch_args(self):
        """인스턴스 구분과 기동 인자 핵심값(힙 설정 등)이 필요하다 — 한 호스트에 여럿일 수 있다."""
        scan = mw.identify(_procs(TOMCAT))
        m = next(x for x in scan.matched if x.kind == "tomcat")
        assert m.instance, "인스턴스 식별자가 비었다 — 같은 미들웨어 다중 인스턴스를 구분 못 한다"
        assert "inst1" in m.instance
        assert m.attributes.get("heap_max") == "4096m", (
            f"기동 인자 핵심값이 추출되지 않았다: {m.attributes}"
        )


class TestDeterminism:
    """S2 — 조사 결과를 신뢰하려면 같은 입력에 같은 출력이어야 한다(D-035)."""

    def test_same_input_same_output(self):
        procs = _procs(TOMCAT, NGINX, PLAIN)
        a = mw.identify(procs).model_dump()
        b = mw.identify(procs).model_dump()
        assert a == b

    def test_no_llm_dependency_in_module(self):
        """모듈이 LLM을 끌어오지 않는다 — 미식별은 '모델이 못 맞혔다'가 아니라 '규칙에 없다'여야 한다."""
        imported = " ".join(_imported_names()).lower()
        for token in ("llm", "openai", "instructor", "langchain", "anthropic"):
            assert token not in imported, f"LLM 의존 import: {token!r} (imports={_imported_names()})"


class TestRulesLiveInDeclarativeFile:
    """S3 — 정책은 코드가 아니라 선언적 파일에 있어야 한다(하네스 표 29 G)."""

    def test_no_middleware_names_hardcoded(self):
        """docstring이 아니라 **코드 문자열 리터럴**을 본다 — 설명은 하드코딩이 아니다."""
        literals = " ".join(_string_literals()).lower()
        for name in ("tomcat", "nginx", "redis", "jboss", "weblogic", "kafka"):
            assert name not in literals, (
                f"미들웨어명 {name!r}이 코드 리터럴에 박혔다 — 규칙은 선언 파일에만 있어야 한다."
            )

    def test_rules_load_from_config_file(self):
        rules = mw.load_signatures()
        assert rules, "선언 파일에서 규칙을 읽지 못했다"
        assert any(r.kind == "tomcat" for r in rules)


class TestUnmatchedIsExplained:
    """S4 — 빈 결과 금지. 왜 못 찾았는지가 남아야 규칙을 고칠 수 있다."""

    def test_unmatched_processes_are_reported(self):
        scan = mw.identify(_procs(PLAIN))
        assert scan.matched == []
        assert scan.unmatched, "미식별이 침묵했다 — 사유가 남지 않으면 규칙 개선이 불가능하다"
        u = scan.unmatched[0]
        assert u.pid and u.cmdline and u.reason

    def test_exclusion_prevents_false_positive(self):
        """`grep tomcat`이 tomcat으로 잡히면 조사 대상이 오염된다."""
        scan = mw.identify(_procs(GREP_NOISE))
        assert not any(m.kind == "tomcat" for m in scan.matched), (
            "제외 패턴이 동작하지 않아 오탐이 발생했다"
        )

    def test_empty_input_is_explicit(self):
        scan = mw.identify([])
        assert scan.matched == [] and scan.unmatched == []
        assert scan.note, "빈 입력에 대한 설명이 없다 — '미들웨어 없음'과 '조회 실패'가 구분되지 않는다"


class TestDeclarativeExtensibility:
    """S5 — 규칙 추가만으로 새 미들웨어가 잡혀야 한다(코드 변경 0)."""

    def test_new_rule_without_code_change(self):
        extra = mw.MiddlewareSignature(
            kind="custom-mq",
            match_any=["com.example.CustomMqBroker"],
            instance_from=r"-Dmq\.instance=(\S+)",
        )
        scan = mw.identify(
            _procs("/usr/bin/java -Dmq.instance=mq01 com.example.CustomMqBroker"),
            signatures=mw.load_signatures() + [extra],
        )
        assert any(m.kind == "custom-mq" and m.instance == "mq01" for m in scan.matched), (
            "선언 규칙을 추가했는데 식별되지 않았다 — 확장이 데이터로 끝나지 않는다"
        )


class TestBoundary:
    """S8 — D-118: 본체와 `sre_agent`는 양방향 import 0."""

    def test_does_not_import_sre_agent(self):
        """경계 위반은 **import**이지 언급이 아니다 — 모듈은 소관을 설명할 수 있어야 한다."""
        offenders = [n for n in _imported_names() if "sre_agent" in n]
        assert not offenders, f"D-118 경계 위반 — sre_agent import: {offenders}"
