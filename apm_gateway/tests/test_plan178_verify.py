"""plans/178 검증(verifier) — `ApmTools` Facade·`ApmCore`·도구군 합성의 구조 계약.

동작 차분은 기준 사본 대비 하네스로 확인했고, 여기서는 회귀 시 깨질 구조 가정만 고정한다:
위임 서술자가 같은 코어를 공유하는 도구군 메서드를 돌려주는지, 옛 이름 별칭이 같은 객체인지,
새 모듈이 Facade(`tools`)를 끌어오지 않는지(순환 0).
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from apm_gateway.application import (
    apm_core,
    business_map,
    name_search,
    period_stats,
    tool_common,
    tools,
)
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.compare_tools import CompareTools
from apm_gateway.application.health_tools import HealthTools
from apm_gateway.application.instance_tools import InstanceTools
from apm_gateway.application.stats_tools import StatsTools
from apm_gateway.application.transaction_tools import TransactionTools
from conftest import make_tools, synthetic_handler

FORWARDED = {
    "apm_instance_map": ("_instance_tools", InstanceTools),
    "apm_runtime_health": ("_health_tools", HealthTools),
    "apm_resource_pool": ("_health_tools", HealthTools),
    "gateway_health": ("_health_tools", HealthTools),
    "apm_slow_transactions": ("_transaction_tools", TransactionTools),
    "apm_active_services": ("_transaction_tools", TransactionTools),
    "apm_events": ("_transaction_tools", TransactionTools),
    "apm_transaction_profile": ("_transaction_tools", TransactionTools),
    "apm_status_stats": ("_stats_tools", StatsTools),
    "apm_metrics": ("_stats_tools", StatsTools),
    "apm_source_changes": ("_compare_tools", CompareTools),
    "apm_transaction_trace": ("_compare_tools", CompareTools),
    "apm_change_impact": ("_compare_tools", CompareTools),
    "apm_period_compare": ("_compare_tools", CompareTools),
}

CORE_ALIASES = {
    "_at": "at",
    "_all_failed": "all_failed",
    "_reason_tail": "reason_tail",
    "_resolve": "resolve",
    "_inst_label": "inst_label",
    "_catalog": "catalog",
    "_metric_plan": "metric_plan",
    "_business_defs_of": "business_defs_of",
    "_event_options": "event_options",
    "_filter_events": "filter_events",
    "_event_row": "event_row",
}


@pytest.fixture
def facade(tmp_path):
    t, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
    )
    return t


def test_every_tool_coroutine_is_reachable_on_facade_and_groups_share_one_core(facade):
    for name, (attr, group_cls) in FORWARDED.items():
        bound = getattr(facade, name)
        group = getattr(facade, attr)
        assert isinstance(group, group_cls)
        assert group.core is facade, name
        assert bound.__self__ is group and bound.__func__ is getattr(group_cls, name), name
        assert inspect.iscoroutinefunction(bound), name
        # 클래스에서 읽으면 도구군 함수(서명은 self 뒤가 같다)
        assert getattr(tools.ApmTools, name) is getattr(group_cls, name)
        assert list(inspect.signature(bound).parameters) == list(
            inspect.signature(getattr(group_cls, name)).parameters
        )[1:]
    # Facade에 남은 도구
    assert inspect.iscoroutinefunction(facade.apm_app_health)
    assert facade.apm_app_health.__self__ is facade


def test_tool_group_instances_are_per_facade(tmp_path, facade):
    other, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool2")},
    )
    for attr, _ in FORWARDED.values():
        assert getattr(facade, attr) is not getattr(other, attr)
    assert facade._transaction_tools._profile_budget is not other._transaction_tools._profile_budget
    assert facade._health_tools._health_cache is None


def test_old_core_aliases_are_the_public_objects():
    for old, new in CORE_ALIASES.items():
        assert ApmCore.__dict__[old] is ApmCore.__dict__[new], old
        assert getattr(tools.ApmTools, old) is getattr(tools.ApmTools, new), old
    assert tools.ApmTools._period_stats is period_stats.period_stats
    assert tools.ApmTools._sum_stats is period_stats.sum_stats
    assert tools.ApmTools._stats_delta is period_stats.stats_delta
    assert tools.ApmTools._guid is CompareTools.guid


def test_old_module_names_on_tools_are_the_moved_objects():
    pairs = {
        "_Limits": tool_common.Limits,
        "_given": tool_common.is_given,
        "_check_n": tool_common.check_n,
        "_ABSOLUTE_MAX_MS": tool_common.ABSOLUTE_MAX_MS,
        "_hour_floor": tool_common.hour_floor,
        "_hour_ceil": tool_common.hour_ceil,
        "_positive_int": tool_common.positive_int,
        "_target_text": tool_common.target_text,
        "_nonneg_int": tool_common.nonneg_int,
        "_domain_order": name_search.domain_order,
        "_fresh": business_map.cache_fresh,
        "Window": tool_common.Window,
        "limit_notice": tool_common.limit_notice,
        "search_query": name_search.search_query,
        "QUERY_MAX": name_search.QUERY_MAX,
        "SOURCE_KIND": apm_core.SOURCE_KIND,
    }
    for name, obj in pairs.items():
        assert getattr(tools, name) is obj, name


@pytest.mark.parametrize(
    "module",
    [
        "tool_common",
        "name_search",
        "business_map",
        "apm_core",
        "instance_tools",
        "health_tools",
        "transaction_tools",
        "stats_tools",
        "compare_tools",
        "event_buffer",
    ],
)
def test_split_modules_import_alone_without_facade(module):
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        f"import apm_gateway.application.{module}\n"
        "print('apm_gateway.application.tools' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


def test_business_map_logger_name_kept():
    assert business_map.logger.name == "apm_gateway.application.tools"
