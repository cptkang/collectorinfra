"""domain_config 모듈 테스트 (v2 - keywords 제거, aliases 추가)."""

import pytest

from src.routing.domain_config import (
    DB_DOMAINS,
    DBDomainConfig,
    get_all_db_ids,
    get_domain_by_id,
)


class TestDBDomains:
    """DB_DOMAINS 정의 테스트."""

    def test_four_domains_defined(self):
        """4개의 DB 도메인이 정의되어 있다."""
        assert len(DB_DOMAINS) == 4

    def test_all_domain_ids(self):
        """모든 도메인 식별자가 올바르다."""
        ids = {d.db_id for d in DB_DOMAINS}
        assert ids == {"polestar", "cloud_portal", "itsm", "itam"}

    def test_no_keywords_field(self):
        """각 도메인에 keywords 필드가 없다 (v2에서 제거됨)."""
        for domain in DB_DOMAINS:
            assert not hasattr(domain, "keywords"), (
                f"{domain.db_id}에 keywords 필드가 여전히 존재합니다."
            )

    def test_each_domain_has_aliases(self):
        """각 도메인에 aliases가 정의되어 있다."""
        for domain in DB_DOMAINS:
            assert len(domain.aliases) > 0, f"{domain.db_id}에 aliases가 없습니다."

    def test_each_domain_has_env_keys(self):
        """각 도메인에 환경변수 키가 정의되어 있다."""
        for domain in DB_DOMAINS:
            assert domain.env_connection_key, f"{domain.db_id}에 env_connection_key가 없습니다."
            assert domain.env_type_key, f"{domain.db_id}에 env_type_key가 없습니다."

    def test_polestar_aliases(self):
        """Polestar DB의 별칭이 정의되어 있다."""
        polestar = get_domain_by_id("polestar")
        assert polestar is not None
        aliases_lower = [a.lower() for a in polestar.aliases]
        assert "polestar" in aliases_lower

    def test_cloud_portal_aliases(self):
        """Cloud Portal DB의 별칭이 정의되어 있다."""
        cp = get_domain_by_id("cloud_portal")
        assert cp is not None
        # 한국어 별칭이 포함되어야 한다
        assert any("클라우드" in a for a in cp.aliases)

    def test_itsm_aliases(self):
        """ITSM DB의 별칭이 정의되어 있다."""
        itsm = get_domain_by_id("itsm")
        assert itsm is not None
        aliases_lower = [a.lower() for a in itsm.aliases]
        assert "itsm" in aliases_lower

    def test_itam_aliases(self):
        """ITAM DB의 별칭이 정의되어 있다."""
        itam = get_domain_by_id("itam")
        assert itam is not None
        aliases_lower = [a.lower() for a in itam.aliases]
        assert "itam" in aliases_lower

    def test_each_domain_has_description(self):
        """각 도메인에 설명이 있다."""
        for domain in DB_DOMAINS:
            assert len(domain.description) > 0, f"{domain.db_id}에 description이 없습니다."


class TestGetDomainById:
    """get_domain_by_id 함수 테스트."""

    def test_valid_id(self):
        """유효한 식별자로 조회한다."""
        result = get_domain_by_id("polestar")
        assert result is not None
        assert result.db_id == "polestar"

    def test_invalid_id(self):
        """유효하지 않은 식별자로 조회하면 None을 반환한다."""
        result = get_domain_by_id("nonexistent")
        assert result is None


class TestGetAllDbIds:
    """get_all_db_ids 함수 테스트."""

    def test_returns_all_ids(self):
        """모든 DB 식별자를 반환한다."""
        ids = get_all_db_ids()
        assert len(ids) == 4
        assert "polestar" in ids
        assert "cloud_portal" in ids
        assert "itsm" in ids
        assert "itam" in ids
