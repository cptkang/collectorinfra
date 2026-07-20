"""DB 어댑터 레지스트리·배선 검증 (Plan 63 P2, D-089).

- 부트스트랩이 폴스타 어댑터를 등록하는지(죽은 레지스트리 방지, D-086 계열)
- get_adapter가 db_id/polestar_db_ids로 담당 어댑터를 조회하는지
- system_template/validator_checks 훅이 이동-불변으로 동작하는지
- query_generator·query_validator가 어댑터 디스패치로 배선됐는지(정의만 있고 소비처 없는 것 방지)
"""

import inspect
import sys

from src.db_adapters import get_adapter, registered_adapters
from src.db_adapters.polestar.prompts import (
    POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
)


class TestRegistryBootstrap:
    def test_polestar_registered_on_import(self):
        """`from src.db_adapters import ...`만으로 폴스타 어댑터가 등록된다."""
        assert "polestar" in {a.name for a in registered_adapters()}

    def test_get_adapter_owns_polestar(self):
        adapter = get_adapter("polestar_cm_gp", {"polestar_cm_gp", "polestar_cm_yd"})
        assert adapter is not None
        assert adapter.name == "polestar"

    def test_get_adapter_none_for_non_polestar(self):
        assert get_adapter("generic_mon", {"polestar_cm_gp"}) is None
        assert get_adapter("polestar_cm_gp", None) is None
        assert get_adapter(None, {"polestar_cm_gp"}) is None


class TestAdapterHooks:
    def test_system_template_by_intent(self):
        adapter = get_adapter("polestar", {"polestar"})
        assert adapter.system_template("alarm_query") is POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template("data_query") is POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
        assert adapter.system_template(None) is POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def test_validator_checks_detect_routing_misuse(self):
        adapter = get_adapter("polestar", {"polestar"})
        checks = adapter.validator_checks()
        assert len(checks) >= 1
        bad_sql = "SELECT name FROM t WHERE GROUP_PATH LIKE '%x%' LIMIT 10"
        assert any(check(bad_sql) for check in checks)
        good_sql = "SELECT name FROM t WHERE avail_status = 0 LIMIT 10"
        assert not any(check(good_sql) for check in checks)


class TestConsumerWiring:
    """공용 코어가 어댑터 디스패치를 실제로 소비하는지(죽은 배선 방지)."""

    def test_query_generator_uses_get_adapter(self):
        # src.nodes.__init__이 동명 함수로 모듈 속성을 가리므로 sys.modules에서 모듈을 가져온다.
        import src.nodes.query_generator  # noqa: F401
        qg = sys.modules["src.nodes.query_generator"]
        assert "get_adapter" in inspect.getsource(qg._build_system_prompt)

    def test_query_validator_uses_get_adapter(self):
        import src.nodes.query_validator  # noqa: F401
        qv = sys.modules["src.nodes.query_validator"]
        src = inspect.getsource(qv)
        assert "get_adapter" in src
        assert "validator_checks" in src
