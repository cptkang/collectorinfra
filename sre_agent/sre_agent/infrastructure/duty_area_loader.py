"""조사 결과 영역별 판정 매핑 YAML 로더 (plans/201 W4 · D-354).

`config/duty_area_layers.yaml`(패키지 루트 기준 · 기대 소스 로더와 같은 parents[2])을 읽어
`domain.duty_view.DutyAreaMap`으로 만든다. 형식이 틀리면 `ValueError`를 낸다 — 호출자(dispatcher)가
경고 로그를 남기고 `duty_view`를 싣지 않는다. **읽기 전용**이다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from sre_agent.domain.duty_view import DutyAreaMap, parse_duty_area_map

#: 기본 매핑 파일.
DEFAULT_DUTY_AREA_LAYERS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "duty_area_layers.yaml"
)


def load_duty_area_map(path: Path | str = DEFAULT_DUTY_AREA_LAYERS_PATH) -> DutyAreaMap:
    """YAML을 읽어 검증한다(파일 없음·형식 오류는 예외)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("최상위가 매핑이 아니다")
    return parse_duty_area_map(data)


__all__ = ["DEFAULT_DUTY_AREA_LAYERS_PATH", "load_duty_area_map"]
