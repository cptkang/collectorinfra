"""크로스-호스트 이벤트 상관 (Plan 60 E2 · D-078 — 결정적 온라인 그리디 군집 API).

서버 경계를 넘어 필드 유사도(Jaccard/Tanimoto)·시간 근접으로 사건을 군집한다. 예: 한
스위치 장애로 여러 서버가 동시에 CPU/네트워크 알람 → 대표 1건만 통보, 나머지는 억제.

온라인(스트리밍) 그리디 정의: 워커가 이벤트를 한 건씩 순차 처리하며 앞선 판정을 소급
변경할 수 없다. 첫 도착 이벤트가 클러스터 **대표**(통보)이고, 이후 유사 이벤트가
`correlation_min_cluster_size`번째 멤버부터 억제된다(소급 선출 없음).

이 모듈은 domain 계층에 위치하므로 **표준 라이브러리만** 의존한다(infra·config import 금지).
`server_name`은 토큰에서 제외한다 — 호스트 경계를 넘는 상관이 목적이므로 호스트 식별자가
유사도를 깎으면 안 된다. 시그니처 라벨(`scan_signature_severity` 결과)은 워커가 사전
계산해 인자로 넘긴다(정책 순수성 — domain은 값만 소비).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 구분자(공백·비단어·언더스코어)로 토큰 분리한다. \w는 유니코드(한글 포함) 문자·숫자,
# [\W_]는 그 외 + 언더스코어이므로 "server.Cpus"·"OOM 강제종료" 모두 자연 분해된다.
_TOKEN_SPLIT = re.compile(r"[\W_]+", re.UNICODE)

# (Plan 60 E7-c/d §17.5·17.6) 네트워크 장비 포맷의 사이트명 추출:
# "<장비ID>.<도메인>||(장애) <사이트명>" 에서 '(장애)' 이후 사이트 토큰을 뽑는다.
# 예: "S8530JUM-4331-1.sotori.com||(장애) 세종대" → "세종대".
_SITE_MARKER = re.compile(r"\|\|\s*\([^)]*\)\s*(?P<site>[^|]+?)\s*$")


def extract_site_token(server_name: str = "", resource_name: str = "") -> str:
    """네트워크 장비 포맷에서 사이트/위치 토큰을 추출한다 (Plan 60 E7-c/d · §17.5·17.6).

    server_name/resource_name의 "<장비ID>.<도메인>||(장애) <사이트명>" 마커에서 '(...)' 이후
    사이트 토큰을 뽑는다(S5 실측 포맷). 마커가 없으면 빈 문자열을 반환한다(하위호환·무영향 —
    일반 서버 알람은 사이트 토큰이 없어 E2 상관에 무영향). 결정적(정규식·stdlib-only)·무부작용.
    워커가 이 값을 signature_tokens의 extra로 주입해 E2 상관에 사이트 차원을 더한다
    (correlation_site_dimension_enabled 시에만).

    Args:
        server_name: 서버/장비 식별 문자열(네트워크 장비 포맷 가능).
        resource_name: 자원 이름(네트워크 장비 포맷 가능).

    Returns:
        추출된 사이트 토큰(공백 제거). 마커 미식별 시 빈 문자열.
    """
    for source in (server_name, resource_name):
        m = _SITE_MARKER.search(str(source or ""))
        if m:
            site = m.group("site").strip()
            if site:
                return site
    return ""


def signature_tokens(
    alarm_name: str,
    resource_type: str,
    signature_label: str,
    extra: str = "",
) -> frozenset[str]:
    """알람 필드를 정규화 토큰 집합으로 변환한다(소문자·구분자 분리).

    `server_name`은 의도적으로 파라미터에서 제외한다 — 호스트 경계를 넘는 상관이 목적이라
    호스트 식별자가 유사도를 깎으면 안 된다(§4.2). 각 필드를 소문자화한 뒤 비단어·언더스코어
    경계로 분해하여 빈 토큰을 버린 frozenset을 반환한다(결정적·순서 무관).

    Args:
        alarm_name: 알람 이름(예: "CPU Utilization").
        resource_type: 자원 타입(예: "server.Cpus").
        signature_label: 워커가 사전 산출한 시그니처 라벨(scan_signature_severity 결과, 없으면 "").
        extra: 추가 토큰 소스(예약 — 기본 빈 문자열).

    Returns:
        정규화된 토큰 frozenset. 전 필드가 비어 있으면 빈 frozenset.
    """
    tokens: set[str] = set()
    for field_value in (alarm_name, resource_type, signature_label, extra):
        text = str(field_value or "").lower()
        for token in _TOKEN_SPLIT.split(text):
            if token:
                tokens.add(token)
    return frozenset(tokens)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """두 토큰 집합의 Jaccard(=Tanimoto) 유사도 |a∩b| / |a∪b| 를 산출한다(결정적).

    빈 합집합(둘 다 비어 있음)은 정의되지 않으므로 0.0을 반환한다(과억제 방지).

    Args:
        a: 토큰 집합.
        b: 토큰 집합.

    Returns:
        0.0~1.0 유사도. 합집합이 비면 0.0.
    """
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


@dataclass
class ClusterState:
    """사건창 내 활성 클러스터의 온라인 상태(워커가 db_id 스코프별로 보관).

    representative_fp는 **첫 도착 이벤트**의 fingerprint(대표 식별자)로, 소급 변경하지 않는다.
    """

    representative_fp: str      # 대표 fingerprint = 첫 도착 이벤트(compute_fingerprint)
    tokens: frozenset[str]      # 대표 이벤트의 정규화 토큰(유사도 비교 기준)
    first_ts: float             # 클러스터 최초 생성 시각(대표 도착) — 동점 tie-break 기준
    last_ts: float              # 마지막 매칭 멤버 시각 — 만료 sweep 기준
    member_count: int = 1       # 대표 포함 누적 멤버 수
    # (Plan 60 E2 위상 가중) 대표의 resource_id — 워커가 클러스터 생성 시
    # graph.id_of(event.server_name)로 해소해 저장한다(None 가능 — 엣지 미보유 root 등).
    # 위상 인접성(is_related) 계산의 비교 대상. domain은 topology를 import하지 않고 값만 보관.
    representative_node: str | None = None


def match_cluster(
    clusters: list[ClusterState],
    tokens: frozenset[str],
    *,
    sim_threshold: float,
    adjacent: list[bool] | None = None,
    topo_weight: float = 0.0,
) -> tuple[int | None, float]:
    """대표 토큰과 최고점 클러스터의 (인덱스, 최종 유사도)를 반환한다(임계 미만이면 (None, 0.0)).

    각 클러스터 i의 점수 = `jaccard(cluster.tokens, tokens)`이며, `adjacent`가 주어지고
    `adjacent[i]`가 True면 **위상 인접 보너스** `topo_weight`를 더한다(E4 토폴로지 인접성 —
    존 내 매칭 정밀화). 임계 비교·동점 규칙은 **보너스 반영 후 점수**로 적용하므로, 인접
    토폴로지 노드면 필드 Jaccard가 임계에 약간 못 미쳐도 군집될 수 있다(위상 가중의 목적).

    결정성: 최고 점수를 택하되, **동점 시 first_ts 오름차순**(더 이른 대표)을 우선하고,
    그마저 동일하면 리스트 인덱스 오름차순(먼저 발견된 것)을 유지한다. 동일 입력 → 동일 출력.

    이 모듈은 topology를 import하지 않는다 — `adjacent`(bool 리스트)를 워커가 주입한다
    (정책 순수성·§10 워커-주입 패턴). `adjacent=None`·`topo_weight=0.0`(기본)이면 현행 필드
    Jaccard 판정과 **비트동일**(회귀 0).

    Args:
        clusters: 현재 활성 클러스터 리스트.
        tokens: 신규 이벤트의 정규화 토큰.
        sim_threshold: 이 값 이상이어야 동일 클러스터로 인정(미만이면 후보 제외).
        adjacent: clusters와 병렬인 위상 인접 여부 bool 리스트(None이면 보너스 없음).
        topo_weight: adjacent[i]가 True인 클러스터에 더할 유사도 보너스(기본 0.0).

    Returns:
        (매칭된 클러스터 인덱스, 최종 유사도) 튜플. 임계 이상 후보가 없으면 (None, 0.0).
        반환 유사도는 보너스 반영 후 값이다(감사 메타 노출용).
    """
    best_key: tuple[float, float] | None = None
    best_idx: int | None = None
    best_score: float = 0.0
    for i, cluster in enumerate(clusters):
        score = jaccard(cluster.tokens, tokens)
        if adjacent is not None and i < len(adjacent) and adjacent[i]:
            score += topo_weight  # 인접 토폴로지 노드 가중(E4 인접성 반영)
        if score < sim_threshold:
            continue
        # (점수 내림차순, first_ts 오름차순) — first_ts는 부호 반전해 최대화 비교로 통일.
        key = (score, -cluster.first_ts)
        if best_key is None or key > best_key:
            best_key = key
            best_idx = i
            best_score = score
    return best_idx, best_score
