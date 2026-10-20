"""std_check 회귀 테스트 — 과거 사고를 양성 케이스로, 표준 패턴을 음성 케이스로 고정한다."""
from __future__ import annotations

import json
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import std_check as sc  # noqa: E402


def _repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _checks(root: pathlib.Path) -> list[str]:
    return [f.check for f in sc.run_checks(root)]


WF_HEAD = """\
name: t
"on":
  schedule:
    - cron: "37 21 * * 1-5"
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [dry_run, shadow, live]
        default: dry_run
"""


# ── C-MODE-01 ────────────────────────────────────────────

def test_mode_false_chain_threads_0926(tmp_path):
    # 09-26 threads-promo: mode=dry_run 수동 실행이 vars.DRY_RUN=false 로 실발행
    wf = WF_HEAD + """jobs:
  a:
    runs-on: ubuntu-latest
    env:
      DRY_RUN: >-
        ${{ github.event_name == 'workflow_dispatch' && inputs.mode == 'live'
        && 'false' || vars.DRY_RUN || 'true' }}
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/a.yml": wf})
    assert "C-MODE-01" in _checks(root)


def test_mode_unguarded_input_fallback_kr_0830(tmp_path):
    # 08-30 kr: 이벤트 가드 없는 inputs.mode || vars.REPLY_MODE
    wf = WF_HEAD + """jobs:
  a:
    runs-on: ubuntu-latest
    env:
      REPLY_MODE: ${{ inputs.mode || vars.REPLY_MODE || 'dry_run' }}
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/a.yml": wf})
    assert "C-MODE-01" in _checks(root)


def test_mode_standard_expression_passes(tmp_path):
    wf = WF_HEAD + """jobs:
  a:
    runs-on: ubuntu-latest
    env:
      RUN_MODE: ${{ github.event_name == 'workflow_dispatch' && inputs.mode || vars.PIPELINE_MODE || 'dry_run' }}
      SESSION: ${{ github.event.inputs.mode || 'tweet' }}
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/a.yml": wf})
    assert "C-MODE-01" not in _checks(root)


# ── C-MODE-02 ────────────────────────────────────────────

def test_python_dry_run_negative_0811(tmp_path):
    root = _repo(tmp_path, {
        "bad.py": 'import os\nDRY = os.environ.get("DRY_RUN", "true").lower() != "false"\n',
        "bad2.py": 'import os\nDRY = os.getenv("DRY_RUN", "").lower() not in ("false", "0")\n',
        "ok.py": 'import os\nDRY = os.environ.get("DRY_RUN", "true").lower() == "true"\n',
    })
    hits = {f.path for f in sc.run_checks(root) if f.check == "C-MODE-02"}
    assert hits == {"bad.py", "bad2.py"}


# ── C-CRON-01 / 02 ───────────────────────────────────────

def test_cron_if_mismatch_bug_v1_0810(tmp_path):
    # 08-10 BUG-V1: cron "0 23 * * 0-6" 인데 if 는 0-4 비교 → 전량 skip
    wf = """name: v
"on":
  schedule:
    - cron: "0 23 * * 0-6"
jobs:
  c20:
    if: github.event.schedule == '0 23 * * 0-4'
    runs-on: ubuntu-latest
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/v.yml": wf})
    found = sc.run_checks(root)
    assert any(f.check == "C-CRON-01" and "0 23 * * 0-4" in f.detail for f in found)
    assert any(f.check == "C-CRON-02" and f.severity == sc.WARN for f in found)


def test_cron_if_match_passes(tmp_path):
    wf = """name: v
"on":
  schedule:
    - cron: "37 21 * * 1-5"
jobs:
  m:
    if: github.event.schedule == '37 21 * * 1-5'
    runs-on: ubuntu-latest
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/v.yml": wf})
    assert not {"C-CRON-01", "C-CRON-02"} & set(_checks(root))


# ── C-ENV-02 / C-ENV-03 ──────────────────────────────────

def test_env_int_direct_parse_h1_0820(tmp_path):
    root = _repo(tmp_path, {
        "cfg.py": 'import os\nA = int(os.environ.get("MAX_ACTIONS", "5"))\nB = float(os.getenv("RATE", "1"))\n'
                  'C = int(os.environ["N"])\n',
        "ok.py": 'import os\ndef env_int(n, d):\n    return d\nA = env_int("MAX_ACTIONS", 5)\n',
    })
    hits = [f for f in sc.run_checks(root) if f.check == "C-ENV-02"]
    assert len(hits) == 3 and all(f.path == "cfg.py" for f in hits)


def test_unregistered_secret_name_i1_0823(tmp_path):
    # 08-23 I-1: 기억으로 쓴 GEMINI_API_KEY_MAIN 은 등록되지 않은 이름
    wf = WF_HEAD + """jobs:
  a:
    runs-on: ubuntu-latest
    env:
      K1: ${{ secrets.GEMINI_API_KEY_MAIN }}
      K2: ${{ secrets.GEMINI_API_KEY }}
      T: ${{ secrets.GITHUB_TOKEN }}
    steps: [{run: "echo"}]
"""
    root = _repo(tmp_path, {".github/workflows/a.yml": wf,
                            ".std/registered_names.txt": "# 이름만\nsecrets.GEMINI_API_KEY\n"})
    hits = [f.detail for f in sc.run_checks(root) if f.check == "C-ENV-03"]
    assert hits == ["등록 목록에 없는 이름 secrets.GEMINI_API_KEY_MAIN"]


def test_registered_check_skipped_without_file(tmp_path):
    wf = WF_HEAD + "jobs:\n  a:\n    runs-on: x\n    env: {K: '${{ secrets.ANY }}'}\n    steps: [{run: echo}]\n"
    root = _repo(tmp_path, {".github/workflows/a.yml": wf})
    assert "C-ENV-03" not in _checks(root)


# ── C-SEC-01 ─────────────────────────────────────────────

def test_secret_patterns_and_masking(tmp_path):
    jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20
    root = _repo(tmp_path, {"leak.md": f"key: {jwt}\n", "dummy.py": 'K = "sk-ant-test"\n'})
    hits = [f for f in sc.run_checks(root) if f.check == "C-SEC-01"]
    assert [f.path for f in hits] == ["leak.md"]
    assert jwt not in hits[0].detail and hits[0].detail.startswith("JWT")


# ── C-NEST-01 / C-YAML-01 ────────────────────────────────

def test_nested_dir_and_broken_yaml(tmp_path):
    root = _repo(tmp_path, {"gen/hero_shorts/hero_shorts/x.py": "X = 1\n",
                            ".github/workflows/bad.yml": "name: [unclosed\n"})
    checks = _checks(root)
    assert "C-NEST-01" in checks and "C-YAML-01" in checks


# ── C-VER-01 / C-CACHE-01 / C-PKG-01 ─────────────────────

def test_entrypoint_version_cache_fonts(tmp_path):
    wf = WF_HEAD + """jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/cache@v4
        with: {path: data/state.json, key: state-v1}
      - run: sudo apt-get install -y fonts-nanum
      - run: python -m pkg.run && python main.py
  b:
    runs-on: ubuntu-latest
    steps:
      - run: sudo apt-get install -y fonts-noto-cjk
"""
    root = _repo(tmp_path, {".github/workflows/a.yml": wf,
                            "pkg/__init__.py": "", "pkg/run.py": "VERSION = '1.0.0'\n",
                            "main.py": "print('x')\n"})
    found = sc.run_checks(root)
    ver = {f.path for f in found if f.check == "C-VER-01"}
    assert ver == {"main.py"}
    assert any(f.check == "C-CACHE-01" for f in found)
    assert any(f.check == "C-PKG-01" for f in found)


# ── baseline 동작 ────────────────────────────────────────

def test_baseline_known_new_and_resolved(tmp_path, capsys):
    root = _repo(tmp_path, {"a.py": 'import os\nA = int(os.getenv("A", "1"))\n'})
    assert sc.main(["--root", str(root), "--write-baseline"]) == 0
    assert sc.main(["--root", str(root)]) == 0  # 기존 오류만 → 통과

    (root / "b.py").write_text('import os\nB = int(os.getenv("B", "1"))\n', encoding="utf-8")
    assert sc.main(["--root", str(root)]) == 1  # 신규 오류 → 실패

    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "b.py").unlink()
    capsys.readouterr()
    assert sc.main(["--root", str(root)]) == 0
    assert "[해소됨]" in capsys.readouterr().out


def test_baseline_keys_ignore_line_numbers(tmp_path):
    root = _repo(tmp_path, {"a.py": 'import os\nA = int(os.getenv("A", "1"))\n'})
    sc.main(["--root", str(root), "--write-baseline"])
    (root / "a.py").write_text('import os\n\n\n# 줄 이동\nA = int(os.getenv("A", "1"))\n', encoding="utf-8")
    assert sc.main(["--root", str(root)]) == 0


def test_corrupt_baseline_is_config_error(tmp_path):
    root = _repo(tmp_path, {".std/baseline.json": "{not json"})
    assert sc.main(["--root", str(root)]) == 2


def test_baseline_file_shape(tmp_path):
    root = _repo(tmp_path, {"a.py": 'import os\nA = int(os.getenv("A", "1"))\n'})
    sc.main(["--root", str(root), "--write-baseline"])
    data = json.loads((root / ".std/baseline.json").read_text(encoding="utf-8"))
    assert data["version"] == sc.VERSION and len(data["errors"]) == 1


@pytest.mark.parametrize("minute,warn", [("0", True), ("30", True), ("37", False), ("*/10", False)])
def test_cron_minute_values(tmp_path, minute, warn):
    wf = (f'name: x\n"on":\n  schedule:\n    - cron: "{minute} 1 * * *"\n'
          'jobs:\n  a:\n    runs-on: x\n    steps: [{run: echo}]\n')
    root = _repo(tmp_path, {".github/workflows/x.yml": wf})
    assert ("C-CRON-02" in _checks(root)) is warn


def test_tests_dir_not_counted_as_entrypoint(tmp_path):
    wf = WF_HEAD + "jobs:\n  a:\n    runs-on: x\n    steps: [{run: python tests/test_x.py}]\n"
    root = _repo(tmp_path, {".github/workflows/a.yml": wf, "tests/test_x.py": "print(1)\n"})
    assert "C-VER-01" not in _checks(root)


def test_runtime_rules_skip_tests_dir(tmp_path):
    root = _repo(tmp_path, {"tests/test_fix.py": 'import os\nD = os.getenv("DRY_RUN", "") != "false"\n'
                                                 'N = int(os.getenv("N", "1"))\n'})
    assert not {"C-MODE-02", "C-ENV-02"} & set(_checks(root))
