"""앵커 없는 동의어 집합 선파서 테스트 (D-142).

`"vcore, cpu, core은 동의어이다. 캐시에 등록하라."` 같은 **대칭 집합**을 LLM 없이
확정한다. 앵커가 있는 기존 표현(`"hostname에 '서버명' 추가"`)은 매칭되지 않아야
기존 `add-synonym` 경로가 보존된다.
"""

from __future__ import annotations

import pytest

from src.utils.synonym_set_parser import parse_synonym_set


class TestPositiveCases:
    @pytest.mark.parametrize("text,expected", [
        # 요건 원문
        ("vcore, cpu, core은 동의어이다. 캐시에 등록하라.", ["vcore", "cpu", "core"]),
        # 조사 변형
        ("vcore, cpu, core는 동의어입니다. 등록해줘", ["vcore", "cpu", "core"]),
        ("메모리, memory, mem이 동의어야. 캐시에 추가해줘", ["메모리", "memory", "mem"]),
        # 표현 변형
        ("disk, 디스크는 같은 말이야. 등록해줘", ["disk", "디스크"]),
        ("cpu, 프로세서는 서로 유사어다. 저장해줘", ["cpu", "프로세서"]),
        ("net, network, 네트워크는 동일한 의미입니다. 캐시에 등록", ["net", "network", "네트워크"]),
        # 공백 변형
        ("vcore,cpu,core은 동의어이다. 등록하라", ["vcore", "cpu", "core"]),
        ("vcore ,  cpu ,core 은 동의어이다. 등록하라", ["vcore", "cpu", "core"]),
        # 어순 변형 (등록 동사가 앞)
        ("캐시에 등록해줘. vcore, cpu, core은 동의어야", ["vcore", "cpu", "core"]),
        # 허용 문자
        ("usage_pct, usage-rate, 사용률은 동의어다. 등록해줘", ["usage_pct", "usage-rate", "사용률"]),
    ])
    def test_parses_symmetric_sets(self, text, expected):
        assert parse_synonym_set(text) == expected

    def test_duplicates_removed_preserving_order(self):
        assert parse_synonym_set("cpu, vcore, cpu, core은 동의어다. 등록해줘") == [
            "cpu", "vcore", "core"
        ]

    def test_max_set_size_allowed(self):
        words = [f"w{i}" for i in range(20)]
        text = f"{', '.join(words)}은 동의어다. 등록해줘"
        assert parse_synonym_set(text) == words


class TestNegativeCases:
    @pytest.mark.parametrize("text", [
        # 앵커가 있는 기존 add-synonym 표현 — 이 경로를 뺏으면 안 된다
        "hostname에 '서버호스트' 유사 단어를 추가해줘",
        "usage_pct의 유사 단어를 '사용률, 사용비율'로 변경해줘",
        "hostname에서 '호스트네임' 유사 단어를 삭제해줘",
        "hostname의 유사 단어를 생성해줘",
        "hostname의 유사 단어를 보여줘",
        # 동의어 선언이지만 등록 의사가 없음 — 단순 질문·서술
        "vcore, cpu, core은 동의어인가요?",
        "vcore와 cpu는 동의어입니다",
        # 등록 의사는 있지만 집합 선언이 없음
        "캐시에 등록해줘",
        "유사어를 등록하라",
        # 무관한 질의
        "CPU 사용률이 가장 높은 서버 알려줘",
        "polestar DB의 스키마 캐시를 갱신해줘",
        "",
    ])
    def test_rejects_non_symmetric_set(self, text):
        assert parse_synonym_set(text) is None


class TestValidation:
    def test_single_word_rejected(self):
        """집합은 최소 2개다 — 혼자서는 동의어일 수 없다."""
        assert parse_synonym_set("cpu는 동의어다. 등록해줘") is None

    def test_over_max_size_rejected(self):
        words = [f"w{i}" for i in range(21)]
        assert parse_synonym_set(f"{', '.join(words)}은 동의어다. 등록해줘") is None

    def test_too_long_word_rejected(self):
        long_word = "x" * 65
        assert parse_synonym_set(f"cpu, {long_word}은 동의어다. 등록해줘") is None

    def test_max_length_word_allowed(self):
        word = "x" * 64
        assert parse_synonym_set(f"cpu, {word}은 동의어다. 등록해줘") == ["cpu", word]

    @pytest.mark.parametrize("bad", ["cpu*", "cpu;drop", "cpu'", 'cpu"', "cpu(1)", "cpu/core"])
    def test_disallowed_characters_rejected(self, bad):
        """허용 문자 밖이면 집합 전체를 거부한다 (부분 등록 금지)."""
        assert parse_synonym_set(f"{bad}, memory는 동의어다. 등록해줘") is None

    def test_deduplicate_to_single_rejected(self):
        """중복 제거 후 1개만 남으면 거부한다."""
        assert parse_synonym_set("cpu, cpu, cpu는 동의어다. 등록해줘") is None


class TestNoLLMDependency:
    def test_parser_has_no_external_dependencies(self):
        """선파서는 LLM·Redis·설정을 import하지 않는다 (결정적 경로 보장)."""
        import ast
        import inspect

        from src.utils import synonym_set_parser

        tree = ast.parse(inspect.getsource(synonym_set_parser))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")

        for mod in imported:
            assert not any(
                bad in mod.lower()
                for bad in ("llm", "redis", "config", "langchain", "schema_cache")
            ), f"선파서가 {mod}에 의존하면 결정적 경로가 아니게 된다"

    def test_parser_is_synchronous(self):
        """비동기가 아니다 — I/O를 하지 않는다는 뜻이다."""
        import inspect

        from src.utils.synonym_set_parser import parse_synonym_set

        assert not inspect.iscoroutinefunction(parse_synonym_set)

    def test_repeated_calls_are_identical(self):
        """같은 입력은 항상 같은 결과다."""
        text = "vcore, cpu, core은 동의어이다. 캐시에 등록하라."
        results = {tuple(parse_synonym_set(text) or ()) for _ in range(20)}

        assert len(results) == 1
