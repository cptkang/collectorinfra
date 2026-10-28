"""kind별 기대 소스 YAML 로더 (plans/198 I-10 — 미조회 소스 표기).

`config/expected_sources.yaml`(패키지 루트 기준 · 플레이북 로더와 같은 parents[2])을 읽어
`domain.failure_guards.ExpectedSources`로 만든다(`allowed`는 선택 — plans/198 I-9 툴셋 좁히기).
형식이 틀리면 `ValueError`를 낸다 — 호출자
(dispatcher)가 경고 로그를 남기고 표기를 건너뛴다. **읽기 전용**이다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sre_agent.domain.failure_guards import ExpectedSources

#: 기본 기대 소스 파일.
DEFAULT_EXPECTED_SOURCES_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "expected_sources.yaml"
)


def _str_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{where}: 문자열 목록이 아니다")
    return tuple(value)


def load_expected_sources(path: Path | str = DEFAULT_EXPECTED_SOURCES_PATH) -> ExpectedSources:
    """YAML을 읽어 검증한다(파일 없음·형식 오류는 예외)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("최상위가 매핑이 아니다")
    sources, kinds = data.get("sources"), data.get("kinds")
    if not isinstance(sources, dict) or not isinstance(kinds, dict):
        raise ValueError("sources·kinds 매핑이 필요하다")
    parsed_sources = {str(k): _str_list(v, f"sources.{k}") for k, v in sources.items()}
    parsed_kinds = {str(k): _str_list(v, f"kinds.{k}") for k, v in kinds.items()}
    allowed = data.get("allowed") or {}
    if not isinstance(allowed, dict):
        raise ValueError("allowed는 매핑이어야 한다")
    parsed_allowed = {str(k): _str_list(v, f"allowed.{k}") for k, v in allowed.items()}
    for key, table in (("kinds", parsed_kinds), ("allowed", parsed_allowed)):
        for kind, names in table.items():
            unknown = [n for n in names if n not in parsed_sources]
            if unknown:
                raise ValueError(f"{key}.{kind}: 정의되지 않은 소스 {unknown}")
    return ExpectedSources(sources=parsed_sources, kinds=parsed_kinds, allowed=parsed_allowed)


__all__ = ["DEFAULT_EXPECTED_SOURCES_PATH", "load_expected_sources"]
