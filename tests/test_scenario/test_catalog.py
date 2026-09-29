"""카탈로그 로더 수용 기준 V1·V2·V16 (plans/94 §10)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.scenario import REPO_ROOT
from scripts.scenario.catalog import CatalogError, load_catalog
from tests.test_scenario.conftest import GOOD_GROUP, write


def _load(scenario_dir: Path, profiles_path: Path):
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


def test_정상_카탈로그는_로드된다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP)
    catalog = _load(scenario_dir, profiles_path)
    assert [s.id for s in catalog.scenarios] == ["T-01"]
    assert catalog.plans_index() == {94: ["T-01"]}


def test_V1_plans_빈_리스트를_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace("plans: [94]", "plans: []"))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("plans" in error for error in exc.value.errors)


def test_V1_plans_필드_자체가_없어도_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace("    plans: [94]\n", ""))
    with pytest.raises(CatalogError):
        _load(scenario_dir, profiles_path)


def test_V2_ID_중복을_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP)
    write(scenario_dir / "u.yaml", GOOD_GROUP.replace("id: T\n", "id: U\n"))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("중복" in error for error in exc.value.errors)


def test_V2_미정의_프로파일을_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        '    title: "정상"', '    title: "정상"\n    profile: 없는프로파일'))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("프로파일" in error for error in exc.value.errors)


def test_V2_군_목표_누락을_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace("  latency_target_ms: 10000\n", ""))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("latency_target_ms" in error for error in exc.value.errors)


def test_V16_착각_케이스는_대조군_없이_거부된다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        '    title: "정상"', '    title: "착각"\n    kind: misconception'))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("pair_with" in error for error in exc.value.errors)


def test_V16_대조군_쌍은_서로를_가리켜야_한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", """version: 1
group: {id: T, name: "t", latency_target_ms: 10000}
scenarios:
  - id: T-01
    plans: [94]
    title: "착각"
    kind: misconception
    pair_with: T-01C
    turns:
      - send: {query: "가동률"}
        expect: {status: completed}
  - id: T-01C
    plans: [94]
    title: "대조군"
    kind: control
    pair_with: T-99
    turns:
      - send: {query: "CPU 사용률"}
        expect: {status: completed}
""")
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("서로를 가리키지 않는다" in error for error in exc.value.errors)


def test_V16_대조군의_kind는_control_이어야_한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", """version: 1
group: {id: T, name: "t", latency_target_ms: 10000}
scenarios:
  - id: T-01
    plans: [94]
    title: "착각"
    kind: misconception
    pair_with: T-02
    turns:
      - send: {query: "가동률"}
        expect: {status: completed}
  - id: T-02
    plans: [94]
    title: "짝인데 정상군이 아님"
    kind: misuse
    pair_with: T-01
    turns:
      - send: {query: "삭제해줘"}
        expect: {status: completed}
""")
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("control 이 아니다" in error for error in exc.value.errors)


def test_파일_엔드포인트는_업로드_없이_거부된다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        '    title: "정상"', '    title: "폼필"\n    endpoint: file_stream'))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("upload" in error for error in exc.value.errors)


def test_정의_밖_대응등급을_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        '    title: "정상"', '    title: "정상"\n    response_modes: [answer, 없는등급]'))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("대응 등급" in error for error in exc.value.errors)


def test_깨진_정규식은_로드_시점에_거부된다(scenario_dir: Path, profiles_path: Path) -> None:
    """실행 도중 re.error 가 나면 그 시점까지의 측정이 통째로 날아간다.

    실측 2026-09-11: `(?i)` 를 표현식 중간에 둔 패턴이 Python 3.11+ 에서 거부되는데,
    그 사실이 모의 실행 중간에 드러나 런이 깨졌다. 1단에서 잡는 것이 가장 싸다.
    """
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}",
        '        expect: {sql_must_not_match: ["(?i)a.*(?i)b"]}'))
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path)
    assert any("정규식 컴파일 실패" in error for error in exc.value.errors)


def test_저장소_카탈로그의_정규식이_전건_컴파일된다() -> None:
    """정본 카탈로그에 깨진 패턴이 섞이면 실행이 중간에 죽는다."""
    import re

    catalog = load_catalog()
    for scenario in catalog.scenarios:
        for turn in scenario.turns:
            for key in ("sql_must_match", "sql_must_not_match"):
                for pattern in turn.expect.get(key) or []:
                    re.compile(str(pattern))  # 예외가 나면 테스트 실패


def test_정본_카탈로그의_프롬프트에_표_파싱_찌꺼기가_없다() -> None:
    """파서가 엉뚱한 칼럼을 집으면 양식 경로나 실행 방법이 프롬프트가 된다(실측 2026-09-11)."""
    catalog = load_catalog()
    for scenario in catalog.scenarios:
        if not scenario.prompt_authored:
            continue
        for turn in scenario.turns:
            query = turn.send.get("query")
            if not query:
                continue
            assert not query.rstrip().endswith("\\"), f"{scenario.id}: 표 이스케이프 잔여"
            assert "testdata/" not in query, f"{scenario.id}: 파일 경로가 프롬프트에 있다"
            assert not query.startswith("`"), f"{scenario.id}: 코드 표기가 벗겨지지 않았다"


#: 실행하지 않는(`prompt_authored: false`) 시나리오의 허용 목록 {id: 사유 주석에 있어야 할 표지}.
#: F·I 는 2026-09-14, K군(반복·동시성·쓰기 선행)과 SYN-F-05(시드 재적재 절차)는 2026-09-15 러너 동작
#: (replay·concurrent·setup·action)으로 전건 실행 가능해져 0건이었다(D-217). 새로 여는 건은
#: **구체 사유 주석**(`# 실행 불가: …` - 러너가 건너뛸 때 「사유는 시나리오 파일의 해당 항목
#: 주석에 있다」고 적는다)과 함께 여기에 더한다.
UNAUTHORED_ALLOWED: dict[str, str] = {
    # plans/122 K-3 - `sample/취합 예시1.xlsx`(D-145 부정 픽스처) 사용자 제공 대기.
    "H-18": "plans/122 K-3",
}


def _scenario_block(scenario_id: str, source_file: str) -> str:
    """시나리오 파일에서 `- id: <id>` 항목 한 건의 원문(다음 항목 앞까지 · 주석 포함)."""
    text = (REPO_ROOT / "testdata" / "scenarios" / source_file).read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^  - id: {re.escape(scenario_id)}\n.*?(?=^  - id: |\Z)", text)
    assert match, f"{source_file} 에서 {scenario_id} 항목을 찾지 못했다"
    return match.group(0)


def test_프롬프트_미작성_시나리오는_사유를_갖는다() -> None:
    """산문을 프롬프트로 실행하지 않는다 - 다만 조용히 사라지지도 않는다.

    미작성은 허용 목록에 있어야 하고, 시나리오 항목에 `# 실행 불가:` 사유 주석
    (허용 목록 표지 포함)이 있어야 한다 - 목록 밖 미작성이나 사유 없는 미작성이 생기면 실패한다.
    """
    catalog = load_catalog()
    unauthored = [s for s in catalog.scenarios if not s.prompt_authored]
    stray = sorted(s.id for s in unauthored if s.id not in UNAUTHORED_ALLOWED)
    assert not stray, f"허용 목록 밖 미작성 초안이 생겼다: {stray}"
    for scenario in unauthored:
        block = _scenario_block(scenario.id, Path(str(scenario.source_file)).name)
        reasons = [line.strip() for line in block.splitlines()
                   if line.strip().startswith("# 실행 불가:")]
        assert any(UNAUTHORED_ALLOWED[scenario.id] in reason for reason in reasons), (
            f"{scenario.id}: 구체 사유 주석"
            f"(`# 실행 불가: … {UNAUTHORED_ALLOWED[scenario.id]} …`)이 없다"
        )


def test_실제_저장소_카탈로그가_로드된다() -> None:
    """정본 카탈로그가 항상 로드 가능해야 한다 - 깨지면 무과금 1단에서 막힌다."""
    catalog = load_catalog()
    assert len(catalog.scenarios) > 100
    assert "baseline" in catalog.profiles
    # 모든 시나리오가 계획서 역추적을 갖는다(요건의 검증 지점).
    assert all(s.plans for s in catalog.scenarios)
