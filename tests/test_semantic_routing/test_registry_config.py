"""DB 레지스트리 정본(`config/db_registry.yaml`) 검증 — Plan 67 R2.

- 파생 동등성: 레지스트리에서 만든 DB_DOMAINS·존 매핑·위치 어휘가 기존 하드코딩과 동일
- 신규 DB 편입 리허설: 가짜 db_id 1개 추가 시 수정 파일 ≤2(레지스트리 + .env)
- 위치 어휘 정의처 1곳: src/ 어디에도 위치 표면어 튜플 사본이 없다(AST 실측)

※ 같은 디렉터리의 `test_db_registry.py`는 **연결 관리 레지스트리**(`DBRegistry`) 테스트로
   별개다. 이 파일은 선언 정본(YAML) 로더를 다룬다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from src.routing.domain_config import DB_DOMAINS, build_domains
from src.routing.registry import get_registry, load_registry
from src.routing.zones import all_zones, db_id_to_zone, zone_to_db_ids

REPO = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO / "config" / "db_registry.yaml"

# 레지스트리 도입 전(2026-07-29) 코드에 하드코딩돼 있던 값 — 동작 불변 기준선.
_LEGACY_LOCATION_TERMS = ("공동존", "김포", "여의도", "은행", "레거시", "은행존")
_LEGACY_PRODUCT_TOKENS = ("폴스타", "polestar", "포탈", "portal")
_LEGACY_LOCATION_DB_HINTS = {
    "polestar_cm_gp": ("김포",),
    "polestar_cm_yd": ("여의도",),
    "polestar_b0": ("은행", "레거시", "은행존"),
}
_LEGACY_EXCLUDING_REGIONS = {
    "polestar": ("여의도", "김포", "은행", "레거시"),
    "polestar_cm_gp": ("여의도", "은행", "레거시"),
    "polestar_cm_yd": ("김포", "은행", "레거시"),
    "polestar_b0": ("여의도", "김포"),
}
_LEGACY_CONTEXT_KEYWORDS = ("김포", "여의도", "은행", "공동존", "운영", "개발", "스테이징")
_LEGACY_NEW_DB_SIGNALS = (
    "김포", "여의도", "은행", "공동존", "운영", "개발", "스테이징",
    "polestar", "폴스타", "cloud_portal", "클라우드", "itsm", "itam",
)


class TestDerivationParity:
    """레지스트리 파생값이 기존 하드코딩과 동작상 동일한지."""

    def test_location_terms_identical(self):
        assert get_registry().location_terms() == _LEGACY_LOCATION_TERMS

    def test_product_terms_identical(self):
        assert get_registry().product_terms() == _LEGACY_PRODUCT_TOKENS

    def test_location_db_hints_identical(self):
        assert get_registry().location_db_hints() == _LEGACY_LOCATION_DB_HINTS

    def test_excluding_regions_equivalent(self):
        """경쟁 지역 배제표는 기존 집합의 상위집합이며 추가분은 판정에 영향이 없다.

        추가되는 "은행존"은 기존 항목 "은행"의 확장 문자열이라 부분문자열 판정
        (`region in hint`)에서 이미 동일하게 걸린다 — 배제 결과 불변.
        """
        derived = get_registry().excluding_region_terms()
        for db_id, legacy in _LEGACY_EXCLUDING_REGIONS.items():
            assert set(legacy) <= set(derived[db_id]), db_id
            extra = set(derived[db_id]) - set(legacy)
            assert all(
                any(base in term for base in legacy) for term in extra
            ), f"{db_id}: 판정에 영향을 주는 신규 배제 토큰 {extra}"

    def test_non_family_dbs_have_no_region_exclusion(self):
        """제품군 경쟁 상대가 없는 DB는 지역 배제를 적용하지 않는다(기존 동작)."""
        derived = get_registry().excluding_region_terms()
        assert derived.get("cloud_portal", ()) == ()
        assert "itsm" not in derived
        assert "itam" not in derived

    def test_context_and_signal_terms_are_supersets(self):
        """맥락 승계·새 DB 신호 어휘는 기존 목록을 모두 포함한다(누락 0)."""
        reg = get_registry()
        assert set(_LEGACY_CONTEXT_KEYWORDS) <= set(reg.location_signal_terms())
        assert set(_LEGACY_NEW_DB_SIGNALS) <= set(reg.new_db_signal_terms())

    def test_domains_and_zones(self):
        assert [d.db_id for d in DB_DOMAINS] == list(get_registry().db_ids())
        assert all_zones() == ["gongjon", "bankjon"]
        assert zone_to_db_ids("gongjon") == ["polestar_cm_gp", "polestar_cm_yd"]
        assert zone_to_db_ids("bankjon") == ["polestar_b0"]
        assert db_id_to_zone("polestar_b0") == "bankjon"
        # 존 미지정 DB(로컬 샌드박스)는 존 없음
        assert db_id_to_zone("polestar") is None

    def test_engine_and_schema_from_registry(self):
        """엔진·스키마 한정(D-057)도 레지스트리 단일 출처."""
        by_id = {d.db_id: d for d in DB_DOMAINS}
        assert by_id["polestar_b0"].db_engine == "db2"
        assert by_id["polestar_b0"].db_schema == "POLESTAR"
        assert by_id["polestar_cm_gp"].db_schema == "polestar"
        assert by_id["itsm"].db_schema == ""

    def test_disabled_entry_is_dropped(self, tmp_path):
        """enabled: false 항목은 등록에서 제외된다."""
        data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
        data["databases"][0]["enabled"] = False
        path = tmp_path / "db_registry.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        reg = load_registry(path)
        assert "polestar_b0" not in reg.db_ids()


def _registry_with_new_db(tmp_path: Path) -> Path:
    """정본을 복사해 가짜 DB 1개를 추가한 임시 레지스트리를 만든다."""
    data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    data["zones"].append({"code": "daejeonjon", "label": "대전존"})
    data["families"].append(
        {"name": "acme_mon", "product_terms": ["에크미"], "signal_terms": ["acme_mon", "에크미"]}
    )
    data["locations"].append({"term": "대전", "db_ids": ["acme_dc1"]})
    data["databases"].append({
        "db_id": "acme_dc1",
        "enabled": True,
        "display_name": "대전 IDC Acme 모니터링",
        "description": "대전 IDC 서버 사양·성능 데이터",
        "aliases": ["acme_dc1", "대전 에크미", "대전"],
        "env_connection_key": "ACME_DC1_CONNECTION",
        "env_type_key": "ACME_DC1_TYPE",
        "engine": "mysql",
        "db_schema": "acme",
        "family": "acme_mon",
        "zone": "daejeonjon",
    })
    path = tmp_path / "db_registry.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class TestNewDBOnboardingRehearsal:
    """신규 DB 편입 리허설 — 레지스트리 1곳 수정으로 전 소비처가 갱신되는가.

    성공 기준(Plan 67 §6.1): 신규 DB 1개 편입 시 수정 파일 ≤2
    (`config/db_registry.yaml` + `.env` 연결 문자열). 아래는 **코드 수정 0**을
    파생 결과로 실증한다.
    """

    @pytest.fixture(scope="class")
    def new_reg(self, tmp_path_factory):
        return load_registry(_registry_with_new_db(tmp_path_factory.mktemp("reg")))

    def test_domain_registered(self, new_reg):
        domains = build_domains(new_reg)
        entry = next(d for d in domains if d.db_id == "acme_dc1")
        assert entry.db_engine == "mysql"
        assert entry.db_schema == "acme"
        assert "대전 에크미" in entry.aliases

    def test_zone_mapping(self, new_reg):
        assert new_reg.zone_to_db_ids()["daejeonjon"] == ("acme_dc1",)
        assert new_reg.get("acme_dc1").zone == "daejeonjon"

    def test_location_vocabulary_updated(self, new_reg):
        assert "대전" in new_reg.location_terms()
        assert new_reg.location_db_hints()["acme_dc1"] == ("대전",)
        assert "대전" in new_reg.location_signal_terms()
        assert "에크미" in new_reg.new_db_signal_terms()
        assert "에크미" in new_reg.product_terms()

    def test_existing_dbs_unaffected(self, new_reg):
        """다른 제품군이므로 기존 폴스타 DB의 경쟁 지역 배제표는 그대로다."""
        excluding = new_reg.excluding_region_terms()
        assert "대전" not in excluding["polestar_cm_gp"]
        assert excluding["acme_dc1"] == ()
        assert new_reg.location_db_hints()["polestar_cm_gp"] == ("김포",)

    def test_prompt_render_picks_up_new_db(self, new_reg, monkeypatch):
        """라우팅 프롬프트의 DB 나열·위치 어휘도 레지스트리 파생이라 자동 반영된다."""
        import importlib

        # 패키지 __init__이 동명 함수를 재노출하므로 모듈 객체를 직접 가져온다.
        router = importlib.import_module("src.routing.semantic_router")
        monkeypatch.setattr(router, "get_registry", lambda: new_reg)
        prompt = router._build_router_prompt(build_domains(new_reg))
        assert "대전 IDC Acme 모니터링" in prompt      # {db_list}
        assert "대전" in prompt                        # {location_vocab}
        assert '"대전 알람" → acme_dc1' in prompt       # {location_db_examples}

    def test_onboarding_requires_registry_and_env_only(self, new_reg):
        """편입에 필요한 수정 파일은 레지스트리 + .env 2개뿐이다.

        코드 수정이 필요 없음을 ①모든 파생 산출물이 레지스트리 인자만으로 신규 DB를
        반영하고(아래) ②위치 어휘 정의가 src/에 남아 있지 않음(정의처 1곳 테스트)으로
        나눠 확인한다. `.env`는 연결 문자열/ACTIVE_DB_IDS(런타임 활성 판정, D-006) 몫.
        """
        derived = {
            "domains": [d.db_id for d in build_domains(new_reg)],
            "zones": list(new_reg.zone_codes()),
            "locations": list(new_reg.location_terms()),
            "hints": new_reg.location_db_hints(),
            "signals": list(new_reg.new_db_signal_terms()),
        }
        assert "acme_dc1" in derived["domains"]
        assert "daejeonjon" in derived["zones"]
        assert "대전" in derived["locations"]
        assert "acme_dc1" in derived["hints"]
        assert "대전" in derived["signals"]
        assert new_reg.get("acme_dc1").env_connection_key == "ACME_DC1_CONNECTION"


def _container_strings(node: ast.AST) -> list[str]:
    """할당 값이 컨테이너 리터럴일 때 그 안의 문자열 상수를 모은다."""
    out: list[str] = []
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        return out
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child.value)
    return out


class TestSingleDefinitionSite:
    """위치 표면어 튜플 사본이 src/에 남아 있지 않은지(정의처 1곳 단언)."""

    # 문서화된 예외(ux_improvement 병합 승계) — 값 사본이되 동기·파생이 보장된 지점.
    # ① utils.query_gen_common: 존 역질문 후단 게이트가 routing(infrastructure)에서
    #    소비해 registry(infrastructure) 임포트가 계층 규칙상 불가 → 값 사본 + 동기 가드
    #    (tests/test_routing/test_location_terms_sync.py)로 강제. 파생물(ZONE_SKIP/
    #    ZONE_CLARIFY/_ZONE_GROUP_TERMS)도 이 파일 안에서만 파생한다.
    # ② nodes.field_mapper._EXCLUSIVE_REGION_GROUPS: 상호 배타 지역 그룹 —
    #    registry locations와 입도가 달라(김포/여의도 분리, 공동존 제외) 파생 불가.
    #    locations 항목 추가 시 함께 갱신할 것.
    _ALLOWED_VALUE_COPY_FILES = {
        "src/utils/query_gen_common.py",
        "src/nodes/field_mapper.py",
    }

    def test_no_location_term_tuple_in_src(self):
        terms = set(get_registry().location_terms())
        offenders: list[str] = []
        for py in sorted((REPO / "src").rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - 방어
                continue
            for node in ast.walk(tree):
                value = getattr(node, "value", None) if isinstance(
                    node, (ast.Assign, ast.AnnAssign)
                ) else None
                if value is None:
                    continue
                if any(s in terms for s in _container_strings(value)):
                    offenders.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
        offenders = [
            o for o in offenders
            if o.rsplit(":", 1)[0] not in self._ALLOWED_VALUE_COPY_FILES
        ]
        assert offenders == [], (
            "위치 표면어 튜플 사본이 남아 있습니다 — 정의는 config/db_registry.yaml "
            "한 곳이어야 합니다(Plan 67 R2, 허용 예외는 _ALLOWED_VALUE_COPY_FILES 참조):\n  "
            + "\n  ".join(offenders)
        )

    def test_registry_file_is_the_definition(self):
        raw = REGISTRY_FILE.read_text(encoding="utf-8")
        for term in get_registry().location_terms():
            assert f"term: {term}" in raw
