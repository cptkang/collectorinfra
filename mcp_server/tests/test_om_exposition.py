"""om_exposition.py 테스트 — 벤더 중립 노출 기계 (plans/92 §4.5 [v3] · plans/87 J7 공유).

직렬화 골든(OpenMetrics 1.0 / text 0.0.4) · Accept 협상 · 명시 타임스탬프 금지 · 전역 REGISTRY
비접촉 · 캐시 TTL(주입 시계)·single-flight·실패 전파 · Starlette 핸들러 · 종료 정리 배선을
DB·네트워크 없이 확인한다.
"""

from __future__ import annotations

import asyncio

import pytest

try:
    from prometheus_client import REGISTRY, generate_latest
    from prometheus_client.core import GaugeMetricFamily
    from prometheus_client.openmetrics.parser import text_string_to_metric_families
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mcp_server import om_exposition as om
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="prometheus-client/starlette 미설치")

#: Prometheus 2.x 스크레이프 기본 Accept(OpenMetrics 우선).
PROM_ACCEPT = (
    "application/openmetrics-text;version=1.0.0,"
    "application/openmetrics-text;version=0.0.1;q=0.75,"
    "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
)
OM_CONTENT_TYPE = "application/openmetrics-text; version=1.0.0; charset=utf-8; escaping=underscores"
TEXT_004_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _demo_families() -> list:
    return [
        om.gauge_family(
            "demo_temperature_celsius", "demo help", ["node", "zone"],
            [(["a", "z1"], 21.5), (["b", "z1"], 3)],
            unit="celsius",
        ),
        om.info_family("demo_build", "build info", ["version"], [["1.2.3"]]),
        om.flag_gauge(
            "demo_bridge_truncated", "truncated flag", "source", {"s1": True, "s2": False}
        ),
    ]


GOLDEN_OM = """\
# HELP demo_temperature_celsius demo help
# TYPE demo_temperature_celsius gauge
# UNIT demo_temperature_celsius celsius
demo_temperature_celsius{node="a",zone="z1"} 21.5
demo_temperature_celsius{node="b",zone="z1"} 3.0
# HELP demo_build build info
# TYPE demo_build info
demo_build_info{version="1.2.3"} 1.0
# HELP demo_bridge_truncated truncated flag
# TYPE demo_bridge_truncated gauge
demo_bridge_truncated{source="s1"} 1.0
demo_bridge_truncated{source="s2"} 0.0
# EOF
"""

GOLDEN_004 = """\
# HELP demo_temperature_celsius demo help
# TYPE demo_temperature_celsius gauge
demo_temperature_celsius{node="a",zone="z1"} 21.5
demo_temperature_celsius{node="b",zone="z1"} 3.0
# HELP demo_build_info build info
# TYPE demo_build_info gauge
demo_build_info{version="1.2.3"} 1.0
# HELP demo_bridge_truncated truncated flag
# TYPE demo_bridge_truncated gauge
demo_bridge_truncated{source="s1"} 1.0
demo_bridge_truncated{source="s2"} 0.0
"""


# =====================================================================
# 직렬화 · 협상
# =====================================================================


class TestRenderExposition:
    """render_exposition 골든·협상·불변식."""

    def test_openmetrics_golden(self):
        """Prometheus Accept → OpenMetrics 1.0 전문(UNIT·info 타입·# EOF)."""
        body, content_type = om.render_exposition(_demo_families(), PROM_ACCEPT)
        assert content_type == OM_CONTENT_TYPE
        assert body.decode("utf-8") == GOLDEN_OM

    def test_text_004_golden(self):
        """*/* → text 0.0.4 전문(info는 _info gauge · UNIT·# EOF 없음)."""
        body, content_type = om.render_exposition(_demo_families(), "*/*")
        assert content_type == TEXT_004_CONTENT_TYPE
        assert body.decode("utf-8") == GOLDEN_004

    @pytest.mark.parametrize("accept", [None, "", "text/plain", "application/json"])
    def test_default_is_text_004(self, accept):
        """Accept가 없거나 OpenMetrics가 아니면 text 0.0.4로 답한다."""
        _, content_type = om.render_exposition(_demo_families(), accept)
        assert content_type == TEXT_004_CONTENT_TYPE

    def test_openmetrics_output_parses_strict_without_timestamps(self):
        """1.0 출력은 strict 파서를 통과하고, 어느 샘플에도 타임스탬프가 없다."""
        body, _ = om.render_exposition(_demo_families(), PROM_ACCEPT)
        families = list(text_string_to_metric_families(body.decode("utf-8")))
        assert [f.name for f in families] == [
            "demo_temperature_celsius", "demo_build", "demo_bridge_truncated",
        ]
        samples = [s for f in families for s in f.samples]
        assert samples and all(s.timestamp is None for s in samples)

    def test_sample_lines_have_no_timestamp_token(self):
        """두 형식 모두 샘플 줄은 `이름{라벨} 값` 2토큰뿐이다(명시 타임스탬프 없음)."""
        for accept in (PROM_ACCEPT, "*/*"):
            body, _ = om.render_exposition(_demo_families(), accept)
            for line in body.decode("utf-8").splitlines():
                if line.startswith("#") or not line:
                    continue
                assert len(line.rsplit("} ", 1)[1].split()) == 1, line

    def test_explicit_timestamp_rejected(self):
        """샘플에 명시 타임스탬프가 있으면 ValueError — 브리지 불변식."""
        fam = GaugeMetricFamily("demo_ts", "ts", labels=["a"])
        fam.add_metric(["x"], 1.0, timestamp=1_700_000_000)
        with pytest.raises(ValueError, match="명시 타임스탬프 금지"):
            om.render_exposition([fam], PROM_ACCEPT)

    def test_global_registry_untouched(self):
        """요청 스코프 레지스트리만 쓴다 — 프로세스 전역 REGISTRY에 흔적이 없다."""
        om.render_exposition(_demo_families(), PROM_ACCEPT)
        om.render_exposition(_demo_families(), PROM_ACCEPT)  # 두 번 렌더해도 중복 등록 오류 없음
        assert "demo_temperature_celsius" not in generate_latest(REGISTRY).decode("utf-8")

    def test_empty_families(self):
        """패밀리가 없어도 1.0은 # EOF 한 줄로 유효하다."""
        body, _ = om.render_exposition([], PROM_ACCEPT)
        assert body == b"# EOF\n"


class TestFamilyHelpers:
    """패밀리 헬퍼 계약."""

    def test_flag_gauge_values(self):
        """참=1 · 거짓=0 · 입력 순서 유지."""
        fam = om.flag_gauge("x_up", "up", "src", {"b": True, "a": False})
        assert [(s.labels["src"], s.value) for s in fam.samples] == [("b", 1.0), ("a", 0.0)]

    def test_label_values_stringified(self):
        """라벨 값은 문자열로, 값은 float로 정규화한다."""
        fam = om.gauge_family("x_n", "n", ["sev"], [([3], 2)])
        assert fam.samples[0].labels == {"sev": "3"}
        assert fam.samples[0].value == 2.0

    def test_helpers_emit_no_timestamp(self):
        """헬퍼가 만든 샘플은 타임스탬프가 없다."""
        for fam in _demo_families():
            assert all(s.timestamp is None for s in fam.samples)


# =====================================================================
# 캐시 — TTL · single-flight · 실패
# =====================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestExpositionCache:
    """ExpositionCache TTL·single-flight·실패 전파."""

    async def test_ttl_reuses_until_expiry(self):
        """TTL 안에서는 재수집하지 않고, 지나면 1회 재수집한다(주입 시계)."""
        calls = 0

        async def collect():
            nonlocal calls
            calls += 1
            return [om.flag_gauge("x_up", "up", "s", {"a": True})]

        clock = _Clock()
        cache = om.ExpositionCache(collect, ttl_seconds=300, clock=clock)
        first = await cache.get()
        clock.now += 299
        assert await cache.get() is first
        assert calls == 1
        clock.now += 2
        await cache.get()
        assert calls == 2

    async def test_zero_ttl_collects_every_time(self):
        """TTL 0이면 매 요청 수집한다."""
        calls = 0

        async def collect():
            nonlocal calls
            calls += 1
            return []

        cache = om.ExpositionCache(collect, ttl_seconds=0, clock=_Clock())
        await cache.get()
        await cache.get()
        assert calls == 2

    async def test_single_flight(self):
        """동시 요청 10건이 와도 수집 함수는 1회만 불린다."""
        calls = 0
        release = asyncio.Event()

        async def collect():
            nonlocal calls
            calls += 1
            await release.wait()
            return [om.flag_gauge("x_up", "up", "s", {"a": True})]

        cache = om.ExpositionCache(collect, ttl_seconds=60, clock=_Clock())
        tasks = [asyncio.create_task(cache.get()) for _ in range(10)]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks)
        assert calls == 1
        assert all(r is results[0] for r in results)

    async def test_failure_propagates_and_is_not_cached(self):
        """수집 예외는 삼키지 않고 전파하며, 실패는 캐시하지 않는다(다음 요청이 재수집)."""
        calls = 0

        async def collect():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("원천 불가")
            return []

        cache = om.ExpositionCache(collect, ttl_seconds=300, clock=_Clock())
        with pytest.raises(RuntimeError):
            await cache.get()
        assert await cache.get() == []
        assert calls == 2


# =====================================================================
# Starlette 핸들러
# =====================================================================


def _app_with(collect) -> Starlette:
    cache = om.ExpositionCache(collect, ttl_seconds=60)
    return Starlette(routes=[Route("/m", om.make_exposition_endpoint(cache), methods=["GET"])])


class TestEndpoint:
    """make_exposition_endpoint 협상·실패 응답."""

    def test_negotiates_two_formats(self):
        """OpenMetrics Accept → 1.0(# EOF), */* → 0.0.4. 본문은 렌더 결과 그대로."""

        async def collect():
            return _demo_families()

        with TestClient(_app_with(collect)) as client:
            r_om = client.get("/m", headers={"accept": PROM_ACCEPT})
            r_txt = client.get("/m", headers={"accept": "*/*"})
        assert r_om.status_code == 200
        assert r_om.headers["content-type"] == OM_CONTENT_TYPE
        assert r_om.text == GOLDEN_OM
        assert r_txt.status_code == 200
        assert r_txt.headers["content-type"] == TEXT_004_CONTENT_TYPE
        assert r_txt.text == GOLDEN_004

    def test_collect_failure_is_503(self):
        """수집 불가는 503 + 사유(예외 타입) — Prometheus에서 up 0으로 드러난다."""

        async def collect():
            raise RuntimeError("boom")

        with TestClient(_app_with(collect)) as client:
            r = client.get("/m", headers={"accept": PROM_ACCEPT})
        assert r.status_code == 503
        assert "RuntimeError" in r.text


# =====================================================================
# 종료 정리 배선
# =====================================================================


class _Owner:
    """약한 참조 키로 쓸 소유자(해시 가능)."""


class TestShutdownHooks:
    """register_shutdown · install_shutdown_hooks."""

    def test_no_hooks_leaves_app_untouched(self):
        """등록된 정리 함수가 없으면 lifespan을 감싸지 않는다(비트 동일)."""
        app = Starlette()
        before = app.router.lifespan_context
        om.install_shutdown_hooks(app, _Owner())
        assert app.router.lifespan_context is before

    def test_hooks_run_on_shutdown_in_order_despite_failure(self):
        """앱 종료 시 등록 순서대로 부르고, 앞 함수의 실패가 뒤 정리를 막지 않는다."""
        owner = _Owner()
        calls: list[str] = []

        async def bad():
            calls.append("bad")
            raise RuntimeError("close 실패")

        async def good():
            calls.append("good")

        om.register_shutdown(owner, bad)
        om.register_shutdown(owner, good)
        app = Starlette()
        om.install_shutdown_hooks(app, owner)
        with TestClient(app):
            assert calls == []
        assert calls == ["bad", "good"]
