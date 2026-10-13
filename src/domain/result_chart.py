"""조회 결과 그래프 판정(plans/160 A-1 · D-327) — 행 목록만 보고 그릴 그래프 종류와 축을 정한다.

**무엇인가.** 화면 표로 보인 결과 행(마스킹 후)을 받아 「선 그래프(시간 축)」·「막대 그래프(범주
축)」·「그릴 수 없음」을 결정적으로 판정하고, 판정 결과로 시리즈 데이터를 만든다. LLM은 쓰지
않는다 — 같은 행이면 언제나 같은 그래프다.

**칼럼 유형은 값 모양으로만 정한다.** 칼럼 이름은 보지 않는다(이름 리터럴은 DB·도구마다 달라
과적합된다 — D-179). None·빈 문자열은 판정 분모에서 뺀다.

- 시간(`time`): datetime/date 객체 또는 ISO 날짜시각 문자열의 파싱 성공률 ≥ 90%. 또는 epoch —
  전부 정수(정수형 float 포함)이고 전부 초 범위 [1e9, 4e9] 또는 전부 ms 범위 [1e12, 4e12]이며
  인접 쌍의 단조 비감소 비율 ≥ 90%(전체 순서 또는 고유값 ≤ 10인 범주 칼럼별 그룹 — 어느 쪽이든).
  범위 안의 큰 정수 ID라도 순서가 무작위면 시간이 아니다.
- 수치(`numeric`): int/float/Decimal 또는 수치 문자열(앞뒤 공백 · 천 단위 쉼표 · `%` 접미 허용)의
  파싱 성공률 ≥ 90%. bool은 수치가 아니다.
- 범주(`category`): 그 밖. 값이 전부 비면 `empty`.

**종류.** `line` = 시간 칼럼 ≥ 1(첫 번째가 x) + 수치 ≥ 1 — 고유값 ≥ 2인 첫 범주 칼럼으로 시리즈를
나눈다. `bar` = 시간 없음 + 고유값 ≥ 2인 범주(첫 번째가 x) + 수치 ≥ 1 + 행 ≤ 50. 그 밖과 행 < 2는
None. 식별자성 수치는 y에서 뺀다 — 측정값(상위 N 건수 등)이 우연히 전부 고유할 수 있으므로 선·막대
모두 「정수 · 전부 고유 · 정렬값이 등차열(행 ≥ 3)」일 때만. **한 시리즈 안의 x는 고유하다** —
(시리즈, x) 쌍이 겹치면 다른 범주 칼럼 하나로 나눠 고유해지는 것을 쓰고(막대는 고유값 2~10), 없으면
None이다(겹친 점은 화면이 덮어써 표와 다른 그래프가 된다). **텍스트 위주 목록은 그리지 않는다** —
변하는 범주 칼럼(빈 값 제외 고유값 ≥ 2)이 2개를 넘으면 None이다(호스트·IP·OS 목록, 알람 이력 등).
상수 칼럼(고유값 1)은 세지 않는다.

**시간 x의 직렬화.** epoch ms(int). datetime은 시간대와 무관하게 **그 값의 벽시계**를
`calendar.timegm(dt.timetuple())`로 옮긴다(naive를 로컬·UTC로 재해석하지 않는다 — 화면은 UTC로
그리면 원 값 그대로 보인다). epoch 칼럼 값은 그대로(초면 ×1000) 쓴다.

**중첩 시계열 평탄화(`chart_rows`).** 부모 행 하나에 「지표 키 → 점(dict) 목록」을
담은 칼럼이 있으면(예: 인스턴스당 1행 + 지표별 시계열) 판정 전에 시간 점마다 1행으로
편다. 판정은 값 모양만 본다 — non-null 값이 전부 dict이고, 그 값이 전부 dict의 list이며,
안쪽 dict에서 epoch 정수 필드 1개와 수치 필드 1개가 식별되면 중첩 칼럼이다. 안쪽 키
이름은 데이터에서 읽는다. 판정·데이터·값 위치 계산의 행 번호는 모두 `chart_rows` 결과
기준이다.

**표 분할(`split_tables`).** 복합 턴의 이어 붙인 행은 표 경계(`[{label, rows}, …]`)가 유효할
때만 표마다 나눈다 — 판정은 표마다 따로 한다(plans/160 W2).

계층: domain — 순수 · 표준 라이브러리만 · I/O·LLM·설정 0.
"""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

ColumnKind = Literal["time", "numeric", "category", "empty"]

#: 유형 판정 성공률 하한 — 시간·수치 공통.
_PARSE_RATIO = 0.9
#: epoch 단조 비감소 비율 하한(인접 쌍 기준).
_MONOTONIC_RATIO = 0.9
#: epoch 그룹 판정에 쓰는 범주 칼럼의 고유값 상한.
_GROUP_MAX_UNIQUE = 10
#: 막대 그래프 행 상한(상위 N 질의).
_BAR_MAX_ROWS = 50
#: 「변하는 범주 칼럼」(고유값 ≥ 2인 범주) 수 상한 — 넘으면 텍스트 위주 목록으로 보고 그리지 않는다.
_MAX_VARYING_CATEGORIES = 2
_EPOCH_S = (1e9, 4e9)
_EPOCH_MS = (1e12, 4e12)

_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_NUM_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_INT_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)$")


@dataclass(frozen=True)
class ChartSpec:
    """그래프 판정 결과 — 축과 종류만 담는다(데이터 없음)."""

    kind: Literal["line", "bar"]
    x: str
    y: tuple[str, ...]
    series_by: str | None
    x_type: Literal["time", "category"]
    #: 시리즈 분할 시 범주 전체 고유값 수(분할하지 않으면 0).
    series_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict."""
        return {
            "kind": self.kind,
            "x": self.x,
            "y": list(self.y),
            "series_by": self.series_by,
            "x_type": self.x_type,
            "series_total": self.series_total,
        }


# ── 값 파싱 ──────────────────────────────────────────────────────────


def is_blank(value: Any) -> bool:
    """None·빈(공백뿐인) 문자열이면 True — 판정 분모에서 뺀다."""
    return value is None or (isinstance(value, str) and not value.strip())


def parse_number(value: Any) -> float | None:
    """수치 모양이면 float로, 아니면 None. bool·NaN·무한대는 수치가 아니다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            num = float(value)
        except (OverflowError, ValueError):
            return None
        return num if math.isfinite(num) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("%"):
        text = text[:-1].rstrip()
    if not text or not any(ch.isdigit() for ch in text) or not _NUM_RE.match(text):
        return None
    try:
        num = float(text.replace(",", ""))
    except ValueError:
        return None
    return num if math.isfinite(num) else None


def parse_datetime(value: Any) -> datetime | None:
    """datetime/date 객체 또는 ISO 날짜시각 문자열이면 datetime으로, 아니면 None(epoch는 제외)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _epoch_int(value: Any) -> int | None:
    """정수(정수형 float·Decimal 포함)면 int, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        num = parse_number(value)
        if num is not None and num.is_integer():
            return int(num)
    return None


def _in_range(num: int, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= num <= bounds[1]


def datetime_to_ms(dt: datetime) -> int:
    """datetime의 벽시계를 epoch ms로 — 시간대 재해석 없이 일관되게."""
    return calendar.timegm(dt.timetuple()) * 1000 + dt.microsecond // 1000


def time_value_to_ms(value: Any) -> int | None:
    """시간 칼럼 값 하나를 epoch ms로. 해석할 수 없으면 None."""
    if is_blank(value):
        return None
    dt = parse_datetime(value)
    if dt is not None:
        return datetime_to_ms(dt)
    num = _epoch_int(value)
    if num is None:
        return None
    if _in_range(num, _EPOCH_S):
        return num * 1000
    if _in_range(num, _EPOCH_MS):
        return num
    return None


def category_key(value: Any) -> str:
    """범주 값의 비교·표시용 문자열."""
    return value.strip() if isinstance(value, str) else str(value)


# ── 칼럼 유형 판정 ─────────────────────────────────────────────────────


def _column_order(rows: list[dict[str, Any]]) -> list[str]:
    order: dict[str, None] = {}
    for row in rows:
        for key in row:
            order.setdefault(key, None)
    return list(order)


def _non_blank(rows: list[dict[str, Any]], col: str) -> list[Any]:
    return [row.get(col) for row in rows if not is_blank(row.get(col))]


def _monotonic_ratio(seq: list[int]) -> tuple[int, int]:
    """(비감소 인접 쌍 수, 전체 인접 쌍 수)."""
    pairs = len(seq) - 1
    if pairs <= 0:
        return 0, 0
    ok = sum(1 for a, b in zip(seq, seq[1:], strict=False) if b >= a)
    return ok, pairs


def _is_epoch_column(rows: list[dict[str, Any]], col: str, group_cols: list[str]) -> bool:
    """정수 · 같은 epoch 범위 · 단조 비감소(전체 또는 그룹별)면 True."""
    indexed: list[tuple[int, int]] = []
    for idx, row in enumerate(rows):
        value = row.get(col)
        if is_blank(value):
            continue
        num = _epoch_int(value)
        if num is None:
            return False
        indexed.append((idx, num))
    if len(indexed) < 2:
        return False
    nums = [n for _, n in indexed]
    if not (
        all(_in_range(n, _EPOCH_S) for n in nums) or all(_in_range(n, _EPOCH_MS) for n in nums)
    ):
        return False
    ok, pairs = _monotonic_ratio(nums)
    if pairs and ok / pairs >= _MONOTONIC_RATIO:
        return True
    for group_col in group_cols:
        groups: dict[str, list[int]] = {}
        for idx, num in indexed:
            groups.setdefault(category_key(rows[idx].get(group_col)), []).append(num)
        ok_sum = pairs_sum = 0
        for seq in groups.values():
            ok, pairs = _monotonic_ratio(seq)
            ok_sum += ok
            pairs_sum += pairs
        if pairs_sum and ok_sum / pairs_sum >= _MONOTONIC_RATIO:
            return True
    return False


def _ratio(values: list[Any], pred: Any) -> float:
    return sum(1 for v in values if pred(v)) / len(values)


def classify_columns(rows: list[dict[str, Any]]) -> dict[str, ColumnKind]:
    """칼럼마다 유형을 값 모양으로만 판정한다(순서 = 첫 행 키 순서 + 뒤 행의 새 키)."""
    columns = _column_order(rows)
    values = {col: _non_blank(rows, col) for col in columns}
    kinds: dict[str, ColumnKind] = {}
    pending_epoch: list[str] = []
    for col in columns:
        vals = values[col]
        if not vals:
            kinds[col] = "empty"
        elif _ratio(vals, lambda v: parse_datetime(v) is not None) >= _PARSE_RATIO:
            kinds[col] = "time"
        elif _ratio(vals, lambda v: parse_number(v) is not None) >= _PARSE_RATIO:
            kinds[col] = "numeric"
            pending_epoch.append(col)
        else:
            kinds[col] = "category"
    # epoch 그룹 판정용 범주 칼럼 — 고유값 2~10.
    group_cols = [
        col
        for col in columns
        if kinds[col] == "category"
        and 2 <= len({category_key(v) for v in values[col]}) <= _GROUP_MAX_UNIQUE
    ]
    for col in pending_epoch:
        if _is_epoch_column(rows, col, group_cols):
            kinds[col] = "time"
    return kinds


# ── 중첩 시계열 평탄화 ─────────────────────────────────────────────────


def _epoch_key(points: list[dict[str, Any]], key: str) -> bool:
    """안쪽 필드가 전부(빈 값 제외) 같은 epoch 범위 정수인가."""
    nums: list[int] = []
    for point in points:
        value = point.get(key)
        if is_blank(value):
            continue
        num = _epoch_int(value)
        if num is None:
            return False
        nums.append(num)
    if not nums:
        return False
    return all(_in_range(n, _EPOCH_S) for n in nums) or all(_in_range(n, _EPOCH_MS) for n in nums)


def _numeric_key(points: list[dict[str, Any]], key: str) -> bool:
    """안쪽 필드가 전부(빈 값 제외) 수치이고 하나 이상 값이 있는가."""
    values = [point.get(key) for point in points if not is_blank(point.get(key))]
    return bool(values) and all(parse_number(v) is not None for v in values)


def _nested_series(rows: list[dict[str, Any]], col: str) -> tuple[str, str] | None:
    """중첩 시계열 칼럼이면 (안쪽 시간 키, 안쪽 값 키), 아니면 None."""
    values = [row.get(col) for row in rows if row.get(col) is not None]
    mappings = [v for v in values if isinstance(v, dict)]
    if not mappings or len(mappings) != len(values):
        return None
    points: list[dict[str, Any]] = []
    for mapping in mappings:
        for series in mapping.values():
            if not isinstance(series, list) or not all(isinstance(p, dict) for p in series):
                return None
            points.extend(series)
    if not points:
        return None
    keys: dict[str, None] = {}
    for point in points:
        for key in point:
            keys.setdefault(key, None)
    time_keys = [k for k in keys if _epoch_key(points, k)]
    if len(time_keys) != 1:
        return None
    value_keys = [k for k in keys if k != time_keys[0] and _numeric_key(points, k)]
    if len(value_keys) != 1:
        return None
    return time_keys[0], value_keys[0]


def chart_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """중첩 시계열 칼럼이 있으면 시간 점마다 1행으로 편 행을, 없으면 rows를 그대로 돌려준다.

    평탄 행 = 부모의 범주형 스칼라 칼럼(문자열 값만) + 안쪽 시간 키 + 지표 키별 값(그 시각에 점이
    없으면 None). 부모의 수치 스칼라(현재값·정수 ID)는 싣지 않는다 — 지표 키와 이름이 겹치고 ID가
    y로 오인된다. 중첩 칼럼이 둘 이상이면 첫 번째만 편다. 평탄 행에는 중첩 칼럼이 없으므로 다시
    적용해도 결과가 같다(멱등).
    """
    columns = _column_order(rows)
    nested: tuple[str, str, str] | None = None
    for col in columns:
        found = _nested_series(rows, col)
        if found is not None:
            nested = (col, *found)
            break
    if nested is None:
        return rows
    col, time_key, value_key = nested
    kinds = classify_columns(rows)
    scalar_cats = [
        c
        for c in columns
        if c != col
        and kinds[c] == "category"
        and all(isinstance(v, str) for v in _non_blank(rows, c))
    ]
    flat: list[dict[str, Any]] = []
    for row in rows:
        mapping = row.get(col)
        if not isinstance(mapping, dict):
            continue
        by_time: dict[int, dict[str, float | None]] = {}
        for metric, series in mapping.items():
            for point in series:
                t = _epoch_int(point.get(time_key))
                if t is None:
                    continue
                by_time.setdefault(t, {})[metric] = parse_number(point.get(value_key))
        base = {c: row.get(c) for c in scalar_cats}
        for t in sorted(by_time):
            flat_row: dict[str, Any] = dict(base)
            flat_row[time_key] = t
            for metric in mapping:
                flat_row[metric] = by_time[t].get(metric)
            flat.append(flat_row)
    return flat


# ── 그래프 종류 판정 ───────────────────────────────────────────────────


def _unique_count(rows: list[dict[str, Any]], col: str) -> int:
    return len({category_key(v) for v in _non_blank(rows, col)})


def _integer_values(rows: list[dict[str, Any]], col: str) -> list[float] | None:
    """칼럼 값이 전부(빈 값 제외) 정수 표기면 그 목록, 아니면 None.

    정수 표기 = int · 소수부 없는 Decimal · 소수점 없는 정수 문자열. float는 측정값으로 보고
    정수로 치지 않는다(`2.0`처럼 우연히 정수값인 측정값을 식별자로 오인하지 않게).
    """
    nums: list[float] = []
    for value in _non_blank(rows, col):
        if isinstance(value, bool) or isinstance(value, float):
            return None
        if isinstance(value, Decimal):
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or exponent < 0:
                return None
        elif isinstance(value, str) and not _INT_RE.match(value.strip()):
            return None
        num = parse_number(value)
        if num is None or not num.is_integer():
            return None
        nums.append(num)
    return nums


def _is_identifier(rows: list[dict[str, Any]], col: str) -> bool:
    """정수 · 전부 고유 · 정렬값이 등차열(행 ≥ 3)인 수치 칼럼 — 연번·ID로 보고 y에서 뺀다."""
    nums = _integer_values(rows, col)
    if nums is None or len(rows) < 3 or len(set(nums)) != len(rows):
        return False
    ordered = sorted(nums)
    step = ordered[1] - ordered[0]
    return all(b - a == step for a, b in zip(ordered, ordered[1:], strict=False))


def _x_key(value: Any, x_type: Literal["time", "category"]) -> int | str | None:
    """x 값의 비교 키 — `build_chart_data`가 점의 x로 쓰는 값과 같다(빈 값·해석 불가는 None)."""
    if x_type == "time":
        return time_value_to_ms(value)
    return None if is_blank(value) else category_key(value)


def _pairs_unique(
    rows: list[dict[str, Any]], x: str, x_type: Literal["time", "category"], series_by: str | None
) -> bool:
    """(시리즈, x) 쌍이 행마다 고유한가 — 한 시리즈에 같은 x 점이 둘이면 그래프가 섞인다."""
    seen: set[tuple[str | None, int | str]] = set()
    for row in rows:
        xk = _x_key(row.get(x), x_type)
        if xk is None:
            continue
        if series_by is None:
            sk: str | None = None
        else:
            raw = row.get(series_by)
            sk = "(빈 값)" if is_blank(raw) else category_key(raw)
        if (sk, xk) in seen:
            return False
        seen.add((sk, xk))
    return True


def _pick_series(
    rows: list[dict[str, Any]],
    x: str,
    x_type: Literal["time", "category"],
    candidates: list[tuple[str | None, int]],
) -> tuple[str | None, int] | None:
    """후보 순서대로 (시리즈, x) 쌍이 고유해지는 첫 분할 — 없으면 None(섞인 시리즈 금지)."""
    for series_by, total in candidates:
        if _pairs_unique(rows, x, x_type, series_by):
            return series_by, total
    return None


def detect_chart(rows: list[dict[str, Any]]) -> ChartSpec | None:
    """행 목록으로 그래프 종류·축을 판정한다. 그릴 수 없으면 None.

    한 시리즈 안에서 x는 고유해야 한다 — 선은 첫 범주로 나누고(범주가 없으면 나누지 않고)
    (시리즈, x) 쌍이 겹치면 다른 범주 칼럼 하나로 나눠 고유해지는 것을 쓴다. 막대는 x가 행마다
    고유하면 나누지 않고, 겹치면 다른 범주 칼럼(고유값 2~10)으로 나눠 고유해지는 것을 쓴다. 어느
    쪽도 안 되면 None이다(예: 시각·서버·지표·값의 긴 표 — 한 칼럼으로는 시리즈가 갈리지 않는다).
    """
    if len(rows) < 2:
        return None
    kinds = classify_columns(rows)
    times = [c for c, k in kinds.items() if k == "time"]
    nums = [c for c, k in kinds.items() if k == "numeric"]
    cats = [c for c, k in kinds.items() if k == "category"]
    split_cats = [(c, n) for c in cats if (n := _unique_count(rows, c)) >= 2]
    if len(split_cats) > _MAX_VARYING_CATEGORIES:
        return None
    if times and nums:
        y = tuple(c for c in nums if not _is_identifier(rows, c))
        if not y:
            return None
        line_candidates: list[tuple[str | None, int]] = (
            [(c, n) for c, n in split_cats] if split_cats else [(None, 0)]
        )
        picked = _pick_series(rows, times[0], "time", line_candidates)
        if picked is None:
            return None
        series_by, total = picked
        return ChartSpec(
            kind="line", x=times[0], y=y, series_by=series_by, x_type="time", series_total=total
        )
    if not times and split_cats and nums and len(rows) <= _BAR_MAX_ROWS:
        y = tuple(c for c in nums if not _is_identifier(rows, c))
        if not y:
            return None
        x = split_cats[0][0]
        bar_candidates: list[tuple[str | None, int]] = [(None, 0)] + [
            (c, n) for c, n in split_cats[1:] if n <= _GROUP_MAX_UNIQUE
        ]
        picked = _pick_series(rows, x, "category", bar_candidates)
        if picked is None:
            return None
        series_by, total = picked
        return ChartSpec(
            kind="bar", x=x, y=y, series_by=series_by, x_type="category", series_total=total
        )
    return None


# ── 시리즈 데이터 ─────────────────────────────────────────────────────


def _downsample(points: list[list[Any]], max_points: int) -> list[list[Any]]:
    """균등 간격으로 max_points개만 남긴다(첫·끝 점 유지)."""
    n = len(points)
    if n <= max_points:
        return points
    keep = max(max_points, 2)
    indices = sorted({round(i * (n - 1) / (keep - 1)) for i in range(keep)})
    return [points[i] for i in indices]


def build_chart_data(
    rows: list[dict[str, Any]], spec: ChartSpec, *, max_points: int = 2000, max_series: int = 10
) -> dict[str, Any]:
    """판정 결과로 시리즈 데이터를 만든다(JSON 안전).

    시간 x는 epoch ms(int)이고 점은 x 오름차순이다. 막대 x는 범주 문자열이고 점은 **행 순서**
    그대로다(상위 N 질의의 정렬을 보존한다). y 파싱 실패·빈 값인 점은 생략한다. 범주 고유값이
    max_series를 넘으면 점 수 합(동률이면 y 합) 상위 max_series개만 남기고 숨긴 수를
    `hidden_series`에 싣는다. 시리즈별 점이 max_points를 넘으면 균등 축소하고 `downsampled`.
    """
    groups: dict[str | None, dict[str, list[list[Any]]]] = {}
    for row in rows:
        raw_x = row.get(spec.x)
        x: Any
        if spec.x_type == "time":
            x = time_value_to_ms(raw_x)
        else:
            x = None if is_blank(raw_x) else category_key(raw_x)
        if x is None:
            continue
        if spec.series_by is None:
            group: str | None = None
        else:
            raw_group = row.get(spec.series_by)
            group = "(빈 값)" if is_blank(raw_group) else category_key(raw_group)
        bucket = groups.setdefault(group, {col: [] for col in spec.y})
        for col in spec.y:
            y = parse_number(row.get(col))
            if y is not None:
                bucket[col].append([x, y])

    hidden = 0
    if spec.series_by is not None and len(groups) > max_series:

        def weight(item: tuple[str | None, dict[str, list[list[Any]]]]) -> tuple[int, float]:
            pts = [p for col_pts in item[1].values() for p in col_pts]
            return len(pts), sum(p[1] for p in pts)

        ranked = sorted(groups.items(), key=weight, reverse=True)
        kept = {name for name, _ in ranked[:max_series]}
        hidden = len(groups) - len(kept)
        groups = {name: data for name, data in groups.items() if name in kept}

    series: list[dict[str, Any]] = []
    downsampled = False
    for group, by_col in groups.items():
        for col, points in by_col.items():
            if spec.x_type == "time":
                points = sorted(points, key=lambda p: p[0])
            if len(points) > max_points:
                points = _downsample(points, max_points)
                downsampled = True
            if group is None:
                name = col
            elif len(spec.y) > 1:
                name = f"{group} · {col}"
            else:
                name = group
            series.append({"name": name, "column": col, "points": points})

    return {
        "kind": spec.kind,
        "x": spec.x,
        "y": list(spec.y),
        "series_by": spec.series_by,
        "x_type": spec.x_type,
        "series": series,
        "downsampled": downsampled,
        "hidden_series": hidden,
    }


# ── 표 분할 ───────────────────────────────────────────────────────────


def split_tables(rows: list[dict[str, Any]], tables: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    """복합 턴의 이어 붙인 행을 표 경계(`[{label, rows}, …]`)대로 나눈다 — (라벨, 행) 목록.

    경계가 유효할 때만 나눈다 — 비지 않은 리스트이고, 항목마다 `rows`가 0 이상 정수(bool 제외)이며,
    그 합이 `len(rows)`와 같다. 그 밖(경계 없음 · 어긋남)은 한 표 `[("", rows)]`다. 순서는 경계
    순서이고, 결과의 위치가 표 번호다(0부터).
    """
    sizes: list[int] = []
    if isinstance(tables, list) and tables:
        for table in tables:
            size = table.get("rows") if isinstance(table, dict) else None
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                sizes = []
                break
            sizes.append(size)
    if not sizes or sum(sizes) != len(rows):
        return [("", rows)]
    out: list[tuple[str, list[dict[str, Any]]]] = []
    start = 0
    for table, size in zip(tables, sizes, strict=True):
        out.append((str(table.get("label") or ""), rows[start:start + size]))
        start += size
    return out
