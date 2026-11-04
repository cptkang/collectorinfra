"""plans/211 W6 독립 검증(verifier) — 관리자 「다시 찾기」 플래그 off 대조 · 응답 보안.

plans/211 §9.3 「플래그」: A·B·C·E는 `NOISE_APM_HOST_MAP_AUTO_ENABLED` 안의 동작 교정이고 두 플래그
off = 현행 비트 동일. 앱·저장소·게이트웨이 대역은 W6 admin 테스트의 것을 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

from tests.test_api.test_plan210_w6_api import ADMIN_BG, BASE, _app
from tests.test_api.test_plan211_w6_admin import (
    SMS_ROWS,
    _Cache,
    _Gateway,
    _pods_gateway,
    _Repo,
    live,  # noqa: F401 — 픽스처
)


def _client(repo: _Repo, *, auto: bool) -> tuple[Any, Any, _Cache]:
    client, app = _app(repo=repo, auto=auto)
    cache = _Cache()
    app.state.apm_host_map_cache = cache
    return client, app, cache


def test_auto_off_domain_preview_makes_no_gateway_or_live_calls(live: dict[str, Any]) -> None:  # noqa: F811
    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", [])}  # 색인 miss → 직접 조회 경로까지 간다
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _pods_gateway()
    client, _app_obj, _cache = _client(repo, auto=False)
    res = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    assert live["gw"].calls == [] and live["node"] == [] and live["vm"] == []


def test_auto_off_apply_is_refused_and_writes_nothing(live: dict[str, Any]) -> None:  # noqa: F811
    """적용은 off에서 거부되고 쓰기·표지·사본 0(미리보기 계산이 쓰기를 남기지 않는다)."""
    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", SMS_ROWS)}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _pods_gateway()
    client, app, cache = _client(repo, auto=False)
    res = client.post(f"{BASE}/{domain['id']}/resolve", headers=ADMIN_BG,
                      json={"apply": True, "version": domain["version"]})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "apm_host_map_auto_disabled"
    assert repo.many == [] and repo.superseded == [] and cache.puts == []


def test_tried_carries_no_exception_text_for_unexpected_gateway_error(
    live: dict[str, Any],  # noqa: F811
) -> None:
    """게이트웨이 호출기가 예상 밖 예외(비 GatewayReadError)를 내도 `tried`·응답에 원문 없음."""
    secret = "token=s3cr3t-at-host-10.0.0.9"

    class _Boom(_Gateway):
        async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((tool, dict(args)))
            raise ValueError(secret)

    repo = _Repo()
    repo.snapshots = {"sms_inventory": ("run-0", SMS_ROWS)}
    domain = repo.seed(scope="domain", match_key="", basis="itam", state="failed")
    live["gw"] = _Boom({})
    client, _app_obj, _cache = _client(repo, auto=True)
    res = client.post(f"{BASE}/{domain['id']}/preview", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    assert secret not in res.text and "s3cr3t" not in res.text
    assert res.json()["tried"]["instances"].startswith("snapshot:")
