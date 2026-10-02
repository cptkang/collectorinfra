"""문서 -> YAML 초안 파서 (plans/94 §3.4).

이관은 **기계적인 절반만** 한다. 파서가 조용히 케이스를 빠뜨리면 커버리지가 부풀려지므로
건수와 멀티턴 묶음을 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.scenario.import_docs import (
    DOC29,
    DOC_SYNONYM,
    parse_doc29,
    parse_synonym,
    render_group,
)


def test_docs29_전군을_읽는다() -> None:
    parsed = parse_doc29()
    assert set(parsed) == set("ABCDEFGHIJK"), f"누락 군: {set('ABCDEFGHIJK') - set(parsed)}"


def test_docs29_군별_건수가_문서와_일치한다() -> None:
    """문서의 A 12 · B 12 · C 13 · D 6 · E 7 · F 8 · H 17 · I 8 · J 10 · K 10."""
    parsed = parse_doc29()
    expected = {"A": 12, "B": 12, "C": 13, "D": 6, "E": 7, "F": 8,
                "H": 17, "I": 8, "J": 10, "K": 10}
    actual = {group: len(items) for group, items in parsed.items() if group in expected}
    assert actual == expected


def test_G군은_멀티턴_한_시나리오로_묶인다() -> None:
    """G-01-1 ~ G-01-5 는 턴이지 시나리오가 아니다 - 스레드를 이어 보내야 한다."""
    parsed = parse_doc29()
    by_id = {item["id"]: item for item in parsed["G"]}
    assert set(by_id) == {"G-01", "G-02", "G-03", "G-04"}
    assert len(by_id["G-01"]["turns"]) == 5
    assert len(by_id["G-02"]["turns"]) == 3
    assert len(by_id["G-03"]["turns"]) == 1


def test_docs29_전체_턴_수가_113이다() -> None:
    """문서가 선언한 113건(턴 기준)을 한 건도 흘리지 않는다."""
    parsed = parse_doc29()
    total_turns = sum(len(item["turns"]) for items in parsed.values() for item in items)
    assert total_turns == 113


def test_유사어_문서_32건을_읽는다() -> None:
    items = parse_synonym()
    assert len(items) == 32


def test_후행_문자가_붙은_ID도_읽는다() -> None:
    """SYN-I-03b 처럼 변형 접미가 붙은 케이스가 조용히 빠지지 않는다."""
    ids = {item["id"] for item in parse_synonym()}
    assert "SYN-I-03b" in ids


def test_초안은_전부_manual_review_로_나온다(tmp_path: Path) -> None:
    """자동 변환만으로는 expect 가 산문이라 기계 단언이 되지 않는다."""
    text = render_group("A", parse_doc29()["A"], env="closed")
    data = yaml.safe_load(text)
    for scenario in data["scenarios"]:
        for turn in scenario["turns"]:
            assert set(turn["expect"]) == {"manual_review"}


def test_초안에_plans_필드가_비지_않는다(tmp_path: Path) -> None:
    """빈 plans 는 로더가 거부한다(V1) - 초안 단계부터 채워야 한다."""
    for group in ("A", "H", "L"):
        items = parse_synonym() if group == "L" else parse_doc29()[group]
        data = yaml.safe_load(render_group(group, items, env="closed"))
        for scenario in data["scenarios"]:
            assert scenario["plans"], f"{scenario['id']}: plans 가 비었다"


def test_폼필_군_초안은_업로드_경로를_갖는다() -> None:
    """endpoint=file_stream 인데 upload 가 없으면 로더가 거부한다."""
    data = yaml.safe_load(render_group("H", parse_doc29()["H"], env="closed"))
    for scenario in data["scenarios"]:
        assert scenario["endpoint"] == "file_stream"
        assert scenario["upload"].endswith(".xlsx")


def test_프롬프트에_콜론이_있어도_YAML_이_깨지지_않는다() -> None:
    """겹따옴표 스칼라로 감싼다 - 프롬프트에는 콜론/따옴표가 흔하다."""
    items = [{"id": "Z-01", "turns": [{"query": 'a: "b" \\ c', "notes": None}]}]
    data = yaml.safe_load(render_group("A", items, env="closed"))
    assert data["scenarios"][0]["turns"][0]["send"]["query"] == 'a: "b" \\ c'


def test_프롬프트_칼럼을_헤더로_찾는다() -> None:
    """군마다 프롬프트 칼럼 위치가 다르다. 고정 인덱스로 집으면 엉뚱한 셀을 담는다.

    실측 2026-09-11: H군 표는 `| ID | 양식 칼럼 구성 | 입력 질의 | ... |` 라
    2번째 칼럼을 집으면 **양식 파일 경로**가 프롬프트가 됐다(17건 전건 오염).
    """
    parsed = parse_doc29()
    h_prompts = {item["id"]: item["turns"][0]["query"] for item in parsed["H"]}
    assert h_prompts["H-01"] == "김포 서버로 채워줘"
    assert h_prompts["H-03"] == "여의도 리소스 현황 채워줘"
    assert not any("testdata/" in q for q in h_prompts.values())


def test_셀_안의_이스케이프_파이프가_칼럼을_밀지_않는다() -> None:
    r"""H군 셀의 `호스트명 \| IP주소 \| ...` 가 여섯 칼럼으로 쪼개지면 이후가 전부 밀린다."""
    parsed = parse_doc29()
    for item in parsed["H"]:
        assert "\\" not in item["turns"][0]["query"]
        assert not item["turns"][0]["query"].rstrip().endswith("\\")


def test_프롬프트가_아닌_군은_미작성으로_표시된다() -> None:
    """I군은 '시나리오'(산문), K군은 '방법'(다른 군 반복)이다.

    산문을 프롬프트로 담아 실행하면 무의미한 결과에 돈만 나간다.
    """
    parsed = parse_doc29()
    assert all(not item["authored"] for item in parsed["I"])
    assert all(not item["authored"] for item in parsed["K"])
    assert all(item["authored"] for item in parsed["A"])
    assert all(item["authored"] for item in parsed["H"])


def test_F군은_2턴이_구조화_필드라_사람이_작성한다() -> None:
    """`selected_db_ids=[polestar_cm_gp]` 를 query 로 넣으면 거짓 프롬프트가 된다."""
    by_id = {item["id"]: item for item in parse_doc29()["F"]}
    assert by_id["F-01"]["authored"] is False      # 2턴 있음
    assert by_id["F-05"]["authored"] is True       # 2턴 '—'
    assert len(by_id["F-01"]["turns"]) == 1        # 2턴을 자동 생성하지 않는다


def test_미작성_표시가_YAML_로_나간다() -> None:
    text = render_group("K", parse_doc29()["K"], env="closed")
    data = yaml.safe_load(text)
    assert all(s["prompt_authored"] is False for s in data["scenarios"])


def test_원문_문서가_여전히_존재한다() -> None:
    """이관 원천이 사라지면 재생성이 불가능하다."""
    assert DOC29.exists() and DOC_SYNONYM.exists()
