"""plans/151 W3 — 반출 묶음 가림·잔존 검사(§6.5 · D-324 ⑥).

픽스처 run(`tests/fixtures/investigation_bench/export/run/`)에 내부 값을 심어 두고 실행 환경
(OS 계정 · 호스트명 · 홈)은 monkeypatch로 같은 값을 심는다. 실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from investigation_bench import export
from investigation_bench.export import ExportLeakError, export_bundle

_FIX_RUN = Path(__file__).resolve().parent / "fixtures" / "investigation_bench" / "export" / "run"
_RUN_ID = "20261013-120000-closed"

_PLANTED = (
    "gpu-vllm.corp.internal",          # api_base 호스트
    "benchsvc",                        # 실행 OS 계정
    "10.20.30.40",                     # 사설 IP
    "172.16.5.9",
    "192.168.0.7",
    "/home/benchsvc",                  # 홈 경로
    "/Users/someone",
    "gpu-node7.corp.internal",         # GPU 서버 호스트명(manifest · socket)
    "gpu-node7",                       # 짧은 호스트명
    "vllmop",                          # nvidia-smi 프로세스 사용자
    "/opt/vllm/venv/bin/python3",      # nvidia-smi 프로세스 경로
)


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(export.getpass, "getuser", lambda: "benchsvc")
    monkeypatch.setattr(export.socket, "gethostname", lambda: "gpu-node7.corp.internal")
    monkeypatch.setenv("HOME", "/home/benchsvc")
    d = tmp_path / _RUN_ID
    shutil.copytree(_FIX_RUN, d)
    lines = [f"line {i:03d}" for i in range(300)]
    lines[-5] = ("connect http://gpu-vllm.corp.internal:8000/v1 from gpu-node7 (10.20.30.40)"
                 " as benchsvc")
    lines[-4] = "cwd /home/benchsvc/collectorinfra/sre_agent · peer 172.16.5.9 · 192.168.0.7"
    lines[-3] = "worker /opt/vllm/venv/bin/python3 owned by vllmop · copy from /Users/someone/x"
    lines[-2] = f"run dir {d}"
    lines[-1] = "loopback http://127.0.0.1:19185/v1 localhost ok"
    (d / "arm-control" / "stderr.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _all_text(bundle: Path) -> str:
    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_planted_values_are_all_masked(run_dir: Path) -> None:
    bundle = export_bundle(run_dir)
    assert bundle == run_dir.with_name(_RUN_ID + ".bundle")
    text = _all_text(bundle)
    for value in _PLANTED:
        assert value not in text, value
    assert str(run_dir) not in text
    assert "<redacted:api-host>" in text and "<private-ip>" in text and "<home>" in text
    assert "<redacted:host>" in text and "<redacted:user>" in text and "<redacted:path>" in text
    # 루프백은 가리지 않는다(외부망 MLX 기록 유지)
    assert "http://127.0.0.1:19185/v1" in text and "localhost" in text
    # JSON 파일은 가린 뒤에도 그대로 읽힌다
    json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for line in (bundle / "raw.jsonl").read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_bundle_contents_and_stderr_tail(run_dir: Path) -> None:
    bundle = export_bundle(run_dir)
    files = sorted(str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file())
    assert files == [
        "arm-control/settings_echo.json", "arm-control/stderr.log", "arm-exp_a/settings_echo.json",
        "gpu_samples.jsonl", "manifest.json", "raw.jsonl", "report.md", "scores.jsonl",
        "world_calls.jsonl",
    ]
    # 감사 파일·pids·세계 YAML은 싣지 않는다 — 세계는 커밋 해시로만 가리킨다
    assert not list(bundle.rglob("*.yaml")) and not list(bundle.rglob("audit.jsonl"))
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == "f4a95f52971e0227cfb761a258e1b9d010f97950"
    tail = (bundle / "arm-control" / "stderr.log").read_text(encoding="utf-8").splitlines()
    assert len(tail) == export.STDERR_TAIL_LINES
    assert tail[0] == "line 100"


def test_briefing_kept_verbatim(run_dir: Path) -> None:
    def briefings(path: Path) -> list[object]:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r["briefing"] for r in rows if not r["briefing"].get("stub")]

    bundle = export_bundle(run_dir)
    assert briefings(bundle / "raw.jsonl") == briefings(run_dir / "raw.jsonl")
    text = (bundle / "raw.jsonl").read_text(encoding="utf-8")
    assert "bw-e01-app01 /var/log 파일시스템 98.4% — logrotate 실패 3회 이후 증가" in text


def test_extra_secrets_masked(run_dir: Path) -> None:
    (run_dir / "report.md").write_text("내부 티켓 OPS-SECRET-77 참조\n", encoding="utf-8")
    bundle = export_bundle(run_dir, extra_secrets=["OPS-SECRET-77"])
    assert "OPS-SECRET-77" not in _all_text(bundle)


def _no_bundle(run_dir: Path) -> None:
    assert not run_dir.with_name(_RUN_ID + ".bundle").exists()
    leftovers = [p.name for p in run_dir.parent.iterdir() if p.name != _RUN_ID]
    assert leftovers == []  # 임시 디렉터리도 지웠다


def test_residual_encoded_secret_blocks_bundle(run_dir: Path) -> None:
    # 가림은 원문만 바꾼다 — 퍼센트 인코딩으로 남은 원값은 잔존 검사가 잡고 묶음을 만들지 않는다
    log = run_dir / "arm-control" / "stderr.log"
    text = log.read_text(encoding="utf-8") + "GET /x?k=vault-key%2Falpha\n"
    log.write_text(text, encoding="utf-8")
    with pytest.raises(ExportLeakError) as exc:
        export_bundle(run_dir, extra_secrets=["vault-key/alpha"])
    assert "arm-control/stderr.log" in str(exc.value)
    assert "vault-key" not in str(exc.value)  # 오류 문구에 원값을 싣지 않는다
    _no_bundle(run_dir)


def test_any_residual_blocks_bundle(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export.Masker, "mask", lambda self, text: text)
    with pytest.raises(ExportLeakError):
        export_bundle(run_dir)
    _no_bundle(run_dir)


def test_failed_export_keeps_previous_bundle(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = export_bundle(run_dir)
    before = _all_text(bundle)
    monkeypatch.setattr(export.Masker, "mask", lambda self, text: text)
    with pytest.raises(ExportLeakError):
        export_bundle(run_dir)
    assert _all_text(bundle) == before


def test_root_user_not_masked(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export.getpass, "getuser", lambda: "root")
    body = "root cause: /var/log 디스크 포화\n"
    (run_dir / "report.md").write_text(body, encoding="utf-8")
    bundle = export_bundle(run_dir)
    assert (bundle / "report.md").read_text(encoding="utf-8") == body


def _manifest(run_dir: Path) -> dict[str, object]:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def _set_manifest(run_dir: Path, doc: dict[str, object]) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_secret_key_values_masked_placeholders_kept(run_dir: Path) -> None:
    doc = _manifest(run_dir)
    doc["spec"] = {"llm": {"api_key": "sk-bench-9f8e7d", "auth_token": "dummy"},
                   "instrumentation_keys": ["incomplete", "elapsed_s"]}
    doc["spec_text"] = "llm:\n  api_key: sk-bench-9f8e7d\n  auth_token: dummy\n"
    _set_manifest(run_dir, doc)
    text = _all_text(export_bundle(run_dir))
    assert "sk-bench-9f8e7d" not in text and "<redacted:secret>" in text
    assert "auth_token: dummy" in text                      # 자리표시는 남긴다
    assert '"incomplete"' in text                           # `*_keys` 목록은 비밀 칸이 아니다


def test_internal_paths_outside_home_repo_masked(run_dir: Path) -> None:
    doc = _manifest(run_dir)
    doc["spec_path"] = "/srv/kbsre/work/spec.yaml"
    doc["versions"] = {"venv": "/opt/kbsre/bench/.venv", "log_dir": "/var/log"}
    _set_manifest(run_dir, doc)
    log = run_dir / "arm-control" / "stderr.log"
    log.write_text("load /srv/kbsre/work/spec.yaml\nexec /opt/kbsre/bench/.venv/bin/python\n",
                   encoding="utf-8")
    bundle = export_bundle(run_dir)
    text = _all_text(bundle)
    assert "/srv/kbsre" not in text and "/opt/kbsre" not in text
    # 표준 시스템 디렉터리 값은 가리지 않는다 — 브리핑의 /var/log 구성요소가 남는다
    assert "/var/log 파일시스템 98.4%" in (bundle / "raw.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("addr", [
    "fd12:3456:789a::15", "fe80::1", "FC00:ab::9", "100.64.0.1", "100.127.255.9",
])
def test_private_ipv6_and_cgnat_masked(run_dir: Path, addr: str) -> None:
    (run_dir / "arm-control" / "stderr.log").write_text(f"peer {addr} refused\n", encoding="utf-8")
    text = (export_bundle(run_dir) / "arm-control" / "stderr.log").read_text(encoding="utf-8")
    assert text == "peer <private-ip> refused\n"


@pytest.mark.parametrize("addr", ["100.128.0.1", "8.8.8.8", "2001:db8::1"])
def test_public_addresses_not_masked(run_dir: Path, addr: str) -> None:
    (run_dir / "arm-control" / "stderr.log").write_text(f"peer {addr}\n", encoding="utf-8")
    text = (export_bundle(run_dir) / "arm-control" / "stderr.log").read_text(encoding="utf-8")
    assert addr in text


def test_short_identifiers_not_masked_and_counted(run_dir: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export.getpass, "getuser", lambda: "ops")
    monkeypatch.setattr(export.socket, "gethostname", lambda: "db")
    body = "ops 팀 확인 · db 세션 락 · ops db\n"
    (run_dir / "report.md").write_text(body, encoding="utf-8")
    bundle = export_bundle(run_dir)
    assert (bundle / "report.md").read_text(encoding="utf-8") == body
    info = _manifest(bundle)["export"]
    assert isinstance(info, dict)
    assert info["skipped_short_identifiers"] == 2 and info["min_ident_len"] == export.MIN_IDENT_LEN


def test_redaction_counts_recorded_in_bundle_manifest(run_dir: Path) -> None:
    bundle = export_bundle(run_dir)
    info = _manifest(bundle)["export"]
    assert isinstance(info, dict)
    counts = info["redactions"]
    assert counts["<redacted:api-host>"] >= 3 and counts["<private-ip>"] >= 3
    assert counts["<redacted:user>"] >= 1 and counts["<home>"] >= 1
    assert info["skipped_short_identifiers"] == 0
    # 원본 run의 manifest는 그대로다
    assert "export" not in _manifest(run_dir)
