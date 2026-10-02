"""시스템 소개 페이지 `/intro` 계약 (plans/124 · D-277).

서버 없이 확인되는 것만 고정한다. 장면 연출·3D 렌더는 브라우저 스크린샷으로 확인한다(plans/124 §8).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.config import AlarmConfig

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "src" / "static"
PAGE = STATIC / "intro.html"
OWN_ASSETS = [PAGE, *sorted((STATIC / "intro").glob("*.*"))]
VENDOR = STATIC / "vendor" / "three"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_route_serves_page_without_auth(client: TestClient) -> None:
    """① 무인증 정적 라우트(다른 HTML 화면과 같다)."""
    r = client.get("/intro")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<title>시스템 소개" in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/static/intro/scroll.js",
        "/static/intro/world.js",
        "/static/vendor/three/build/three.module.min.js",
    ],
)
def test_scripts_are_served_as_javascript(client: TestClient, path: str) -> None:
    """모듈 스크립트는 JavaScript MIME 이어야 브라우저가 실행한다(Windows 레지스트리 추정 방지)."""
    r = client.get(path)
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"], r.headers["content-type"]


def test_no_external_resources() -> None:
    """④ 폐쇄망 — 자체 자산에 외부 URL·프로토콜 상대 주소가 없다."""
    found = []
    for f in OWN_ASSETS:
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"""(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}[^\s"')]*""", text, re.I):
            found.append(f"{f.name}: {m.group(0)}")
    assert not found, found


def test_no_host_ip_or_account_literals() -> None:
    """① 페이지에 호스트·IP·계정을 싣지 않는다."""
    text = _html()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert "localhost" not in text
    assert not re.search(r"password|passwd|secret|token", text, re.I)


def test_importmap_and_addons_resolve_to_vendored_files() -> None:
    """④ importmap 대상과 3D 코드가 가져오는 addon 이 벤더 폴더에 실재한다."""
    raw = re.search(r'<script type="importmap">(.*?)</script>', _html(), re.S)
    assert raw
    imports = json.loads(raw.group(1))["imports"]
    core = STATIC / imports["three"].removeprefix("/static/")
    assert core.is_file(), core
    addons_root = STATIC / imports["three/addons/"].removeprefix("/static/")
    used = set()
    for f in (STATIC / "intro").glob("*.js"):
        used |= set(re.findall(r"""['"]three/addons/([^'"]+)['"]""", f.read_text(encoding="utf-8")))
    assert used, "3D 코드가 addon 을 하나도 가져오지 않는다 — 블룸 경로 누락"
    missing = [u for u in sorted(used) if not (addons_root / u).is_file()]
    assert not missing, missing


def test_vendor_files_match_recorded_sha256() -> None:
    """④ 벤더 사본은 README 에 기록한 원본 그대로다(수정·교체 감시)."""
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", readme, re.M)
    assert len(rows) >= 11
    recorded = {path for path, _ in rows}
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*.js")}
    assert on_disk == recorded, on_disk ^ recorded
    for path, digest in rows:
        assert hashlib.sha256((VENDOR / path).read_bytes()).hexdigest() == digest, path
    assert (VENDOR / "LICENSE").is_file()


def _facts() -> dict[str, int]:
    return {k: int(v) for k, v in re.findall(r'data-fact="([a-z_]+)"[^>]*>(\d+)<', _html())}


def test_facts_match_code() -> None:
    """⑥ 페이지 숫자는 코드·설정으로 센 값과 같다 — 코드가 바뀌면 페이지를 고친다."""
    facts = _facts()
    assert set(facts) == {"dedup_seconds", "tiers", "polestar_tools", "ops_dbs"}, facts

    assert facts["dedup_seconds"] == AlarmConfig.model_fields["dedup_ttl_seconds"].default

    policy = (REPO / "noise_gate" / "domain" / "notification_policy.py").read_text(encoding="utf-8")
    assert facts["tiers"] == len(re.findall(r"^TIER_[A-Z]+ = ", policy, re.M))

    tools = (REPO / "mcp_server" / "mcp_server" / "polestar_tools.py").read_text(encoding="utf-8")
    assert facts["polestar_tools"] == tools.count("@mcp.tool()")

    registry = yaml.safe_load((REPO / "config" / "db_registry.yaml").read_text(encoding="utf-8"))
    assert facts["ops_dbs"] == sum(1 for db in registry["databases"] if db.get("zone"))


def test_linked_only_from_main_page() -> None:
    """② 메인 화면에서만 새 탭으로 연다 — 머리글 제목 · 「매뉴얼 ▾」 메뉴.

    D-277 ② 부기(2026-09-29 사용자 지시).

    다른 화면에 링크를 더하면 이 테스트와 매뉴얼(D-255)을 같은 작업에서 갱신한다.
    """
    linked = sorted(
        p.relative_to(STATIC).as_posix()
        for p in STATIC.rglob("*.html")
        if p != PAGE and re.search(r"""href=["']/intro\b""", p.read_text(encoding="utf-8"))
    )
    assert linked == ["index.html"], linked
    main = (STATIC / "index.html").read_text(encoding="utf-8")

    def link_in(block_re: str, ident: str) -> str:
        block = re.search(block_re, main, re.S)
        assert block, block_re
        tag = re.search(rf'<a[^>]*id="{ident}"[^>]*>', block.group(1))
        assert tag, f"{ident} 가 제자리에 없다"
        return tag.group(0)

    menu_re = r'<details class="manual-menu" id="manualMenu">(.*?)</details>'
    menu = link_in(menu_re, "introLink")
    brand = link_in(r'<header>.*?<h1 class="has-mark">(.*?)</h1>', "brandIntroLink")
    for tag in (menu, brand):
        assert 'href="/intro"' in tag
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag
        assert "display: none" not in tag, "누구나 보여야 한다"
    assert len(re.findall(r"""href=["']/intro\b""", main)) == 2


def test_brand_returns_to_main() -> None:
    """소개 페이지 왼쪽 위 「KB AIOps 포탈」은 메인 화면으로 간다(같은 탭).

    2026-09-29 사용자 지시.
    """
    tag = re.search(r'<a[^>]*id="brandHome"[^>]*>', _html())
    assert tag
    assert 'href="/"' in tag.group(0)
    assert "target=" not in tag.group(0)


def _scroll_code() -> str:
    """주석을 뺀 scroll.js — 게이트 문장이 주석에만 남은 경우를 거른다."""
    text = (STATIC / "intro" / "scroll.js").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def test_vdi_behaviour_is_gated() -> None:
    """⑤ 개정(2026-09-30) — VDI 대응은 가상·소프트웨어 GPU 이거나 WebGL 이 없을 때만 한다.

    VDI 등: reduced-motion 이어도 3D를 켜고, 정지 등급이면 장면 영상을 보여 준다.
    그 밖의 PC: 기존 동작 그대로(reduced-motion 이면 정지 · 정지 배경은 그라데이션).
    조건별 동작은 브라우저로 실측했다(plans/124 §10) — 여기서는 게이트 문장만 고정한다.
    """
    code = _scroll_code()
    assert "state.vdi = renderer === null || VIRTUAL_GPU.test(renderer);" in code
    assert "if (reduce && !state.vdi) return 'static';" in code
    assert "state.tier === 'static' && state.vdi ? state.active : -1" in code


@pytest.mark.parametrize(
    ("renderer", "vdi"),
    [
        ("ANGLE (VMware, Inc., VMware SVGA 3D Direct3D11 vs_5_0 ps_5_0, D3D11)", True),
        ("ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)", True),
        ("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 10.0.0)))", True),
        ("ANGLE (NVIDIA, NVIDIA GRID T4-2Q Direct3D11 vs_5_0 ps_5_0, D3D11)", True),
        ("Citrix Indirect Display Adapter", True),
        ("Microsoft Remote Display Adapter", True),
        ("llvmpipe (LLVM 15.0.7, 256 bits)", True),
        ("ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Max, Unspecified Version)", False),
        ("ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)", False),
        ("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)", False),
        ("ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)", False),
    ],
)
def test_virtual_gpu_pattern(renderer: str, vdi: bool) -> None:
    """가상·소프트웨어 GPU 렌더러 이름만 VDI 로 본다 — 일반 GPU PC 는 기존 동작을 지킨다."""
    m = re.search(r"const VIRTUAL_GPU = /(.+)/i;", _scroll_code())
    assert m
    assert bool(re.search(m.group(1), renderer, re.I)) is vdi


def test_static_tier_has_a_video_per_scene() -> None:
    """⑤ 개정 — 정지 등급 배경은 장면마다 사전 렌더 반복 영상(scripts/intro_capture.py)이다."""
    html = _html()
    scenes = sorted(int(n) for n in re.findall(r'data-scene="(\d+)"', html))
    backdrop = re.search(r'<div class="backdrop"[^>]*>(.*?)</div>', html, re.S)
    assert backdrop
    clip = (
        r'<i><video src="(/static/intro/video/scene-(\d+)\.webm)"'
        r' muted loop playsinline preload="none">'
    )
    srcs = re.findall(clip, backdrop.group(1))
    assert [int(n) for _, n in srcs] == scenes
    for src, _ in srcs:
        f = STATIC / src.removeprefix("/static/")
        assert f.is_file(), f
        assert f.read_bytes()[:4] == b"\x1a\x45\xdf\xa3", f"{f.name} 가 webm(EBML)이 아니다"
        assert f.stat().st_size < 3 * 1024 * 1024, f"{f.name} 가 3MB 를 넘는다 — VDI 에서 늦게 뜬다"


def test_scene_video_is_served_as_webm(client: TestClient) -> None:
    """Windows 레지스트리에 .webm 이 없어도 video/webm 으로 나간다."""
    r = client.get("/static/intro/video/scene-0.webm")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/webm"


def test_page_assets_are_not_gitignored() -> None:
    """벤더 사본·영상이 `.gitignore` 에 걸려 폐쇄망 반입에서 빠지지 않는다.

    2026-09-30 실사례 — `build/` 규칙이 `vendor/three/build/` 를 삼켜 코어가 커밋되지 않았다.
    """
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("git 저장소가 아니다")
    files = [*VENDOR.rglob("*.js"), *(STATIC / "intro" / "video").glob("*.webm")]
    paths = [p.relative_to(REPO).as_posix() for p in files]
    r = subprocess.run(["git", "check-ignore", *paths], cwd=REPO, capture_output=True, text=True)
    assert r.stdout.strip() == "", r.stdout
