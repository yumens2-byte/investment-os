"""EDT 개발 표준 정적 검사기 (std_check).

Notion 「EDT 개발 표준 정본」의 STD 규약 중 yml·코드 텍스트만으로 판정되는 항목을 검사한다.
기존 위반은 baseline 파일에 고정하고, baseline 에 없는 '오류'만 실패로 판정한다(STD-CI-04).
검사기는 보고만 하고 코드를 고치지 않는다.
검사 대상은 git 추적 파일이다(git ls-files). 새 파일은 git add 이후에 검사된다.

사용
  python tools/std_check.py                       # baseline 비교, 신규 오류 있으면 exit 1
  python tools/std_check.py --verbose             # 경고 상세까지 출력
  python tools/std_check.py --write-baseline      # 현재 오류 전체를 baseline 으로 기록(마스터 승인 후에만)

종료코드
  0 = 신규 오류 없음 · 1 = 신규 오류 있음 · 2 = 실행 설정 오류(baseline 파일 없음·손상)

변경 이력
  1.0.1 (2026-10-21, DN D-A6) baseline 파일 없음 → 종료코드 2(STD-CHECK-C002). 등록 목록 없음 → C-ENV-03 경고 1건.
        C-MODE-02 규약 표기 STD-MODE-03 → STD-MODE-06(오류 문구는 baseline 키 보존을 위해 그대로).
  1.0.0 (2026-10-20) 최초 — 검사 14종.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

VERSION = "1.0.1"

import yaml  # noqa: E402  (VERSION 상수 뒤 third-party, STD-CI-05)

ERROR = "오류"
WARN = "경고"

DEFAULT_BASELINE = ".std/baseline.json"
DEFAULT_REGISTERED = ".std/registered_names.txt"
BUILTIN_SECRETS = {"GITHUB_TOKEN"}

# 실행 결과 키에 줄 번호를 넣지 않는다 — 줄이 밀려도 baseline 이 유지되도록.
_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_FALSE_CHAIN = re.compile(r"&&\s*'false'\s*\|\|")
_INPUT_FALLBACK = re.compile(r"\binputs\.[A-Za-z_][\w-]*.*?\|\|\s*\(?\s*(?:vars|secrets)\.")
_SCHEDULE_CMP = re.compile(r"github\.event\.schedule\s*==\s*'([^']+)'")
_SECRET_REF = re.compile(r"\b(secrets|vars)\.([A-Za-z_][A-Za-z0-9_]*)")
_DRY_NEG = re.compile(r"""(!=\s*["']false["']|not\s+in\s*\(\s*["']false["'])""")
_PY_ENTRY = re.compile(r"\bpython3?\s+(?:-m\s+([A-Za-z_][\w.]*)|([\w./-]+\.py))")
_FONT_PKG = re.compile(r"\bfonts-[a-z0-9-]+")
_NETWORK_LOGGERS = ("hpack", "httpx", "httpcore", "urllib3")
_SKIP_MODULES = {"pip", "pytest", "playwright", "venv", "unittest", "compileall", "json.tool"}
_SECRET_PATTERNS = (
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Anthropic 키", re.compile(r"sk-ant-[A-Za-z0-9_-]{30,}")),
    ("OpenAI 키", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("Google API 키", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("GitHub 토큰", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,})")),
    ("Meta 토큰", re.compile(r"\bEAA[A-Za-z0-9]{60,}")),
    ("Telegram 봇 토큰", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
)
_TEXT_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".sh",
                  ".sql", ".js", ".mjs", ".ts", ".html", ".env", ""}


@dataclass(frozen=True)
class Finding:
    check: str
    std: str
    severity: str
    path: str
    line: int
    detail: str

    @property
    def key(self) -> str:
        return f"{self.check}|{self.path}|{self.detail}"


# ── 파일 수집 ──────────────────────────────────────────────

def tracked_files(root: pathlib.Path) -> list[pathlib.Path]:
    """git 추적 파일. git 이 없으면 디렉터리 순회(.git·캐시 제외)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                             check=True, timeout=30).stdout.decode("utf-8", "replace")
        return [root / p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv"}
        return [p for p in root.rglob("*") if p.is_file() and not (skip & set(p.relative_to(root).parts))]


def _rel(root: pathlib.Path, path: pathlib.Path) -> str:
    return path.relative_to(root).as_posix()


def _read(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


# ── 워크플로 ───────────────────────────────────────────────

def load_workflows(root: pathlib.Path) -> tuple[list[tuple[str, str, dict]], list[Finding]]:
    """(경로, 원문, 파싱결과) 목록과 파싱 실패 Finding."""
    loaded, findings = [], []
    wf_dir = root / ".github" / "workflows"
    for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]):
        rel = _rel(root, path)
        text = _read(path) or ""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            findings.append(Finding("C-YAML-01", "STD-CI-01", ERROR, rel, 1, f"YAML 파싱 실패: {type(exc).__name__}"))
            continue
        if not isinstance(data, dict) or "jobs" not in data:
            findings.append(Finding("C-YAML-01", "STD-CI-01", ERROR, rel, 1, "jobs 섹션 없음"))
            continue
        loaded.append((rel, text, data))
    return loaded, findings


def _on_block(data: dict) -> dict:
    on = data.get("on", data.get(True))  # PyYAML 은 키 on 을 True 로 읽는다
    return on if isinstance(on, dict) else {}


def _schedule_crons(data: dict) -> list[str]:
    sched = _on_block(data).get("schedule") or []
    return [str(item.get("cron", "")).strip() for item in sched if isinstance(item, dict)]


def check_mode_expressions(workflows) -> list[Finding]:
    """C-MODE-01 — `&& 'false' ||` 와 이벤트 가드 없는 inputs 폴백 (STD-MODE-02)."""
    out = []
    for rel, text, _ in workflows:
        for m in _EXPR.finditer(text):
            expr = " ".join(m.group(1).split())
            line = _line_of(text, m.start())
            if _FALSE_CHAIN.search(expr):
                out.append(Finding("C-MODE-01", "STD-MODE-02", ERROR, rel, line,
                                   f"`&& 'false' ||` 형태: {expr}"))
            elif _INPUT_FALLBACK.search(expr) and "github.event_name" not in expr:
                out.append(Finding("C-MODE-01", "STD-MODE-02", ERROR, rel, line,
                                   f"이벤트 가드 없는 inputs 폴백: {expr}"))
    return out


def check_cron_if(workflows) -> list[Finding]:
    """C-CRON-01 — job if 의 schedule 비교 문자열이 on.schedule 에 없음 (STD-SCHED-01)."""
    out = []
    for rel, text, data in workflows:
        crons = set(_schedule_crons(data))
        for m in _SCHEDULE_CMP.finditer(text):
            if m.group(1).strip() not in crons:
                out.append(Finding("C-CRON-01", "STD-SCHED-01", ERROR, rel, _line_of(text, m.start()),
                                   f"schedule 비교 '{m.group(1)}' 가 on.schedule 에 없음 (해당 분기 무발화)"))
    return out


def check_cron_minute(workflows) -> list[Finding]:
    """C-CRON-02 — cron 분 필드가 정각·반각 (STD-SCHED-02)."""
    out = []
    for rel, _, data in workflows:
        for cron in _schedule_crons(data):
            minute = cron.split()[0] if cron.split() else ""
            if minute in {"0", "00", "30"}:
                out.append(Finding("C-CRON-02", "STD-SCHED-02", WARN, rel, 0, f"정각·반각 cron '{cron}'"))
    return out


def check_registered_names(workflows, registered: set[str] | None,
                           registered_rel: str = DEFAULT_REGISTERED) -> list[Finding]:
    """C-ENV-03 — yml 의 secrets/vars 이름이 등록 목록에 없음 (STD-CFG-02)."""
    if registered is None:
        # 목록 파일이 없으면 검사할 수 없다 — 조용히 넘기지 않고 경고로 남긴다(v1.0.1).
        return [Finding("C-ENV-03", "STD-CFG-02", WARN, registered_rel, 0,
                        "등록 목록 파일 없음 — 미등록 이름 검사 생략")]
    out = []
    for rel, text, _ in workflows:
        seen = set()
        for m in _SECRET_REF.finditer(text):
            kind, name = m.group(1), m.group(2)
            token = f"{kind}.{name}"
            if token in seen or (kind == "secrets" and name in BUILTIN_SECRETS):
                continue
            seen.add(token)
            if token not in registered:
                out.append(Finding("C-ENV-03", "STD-CFG-02", ERROR, rel, _line_of(text, m.start()),
                                   f"등록 목록에 없는 이름 {token}"))
    return out


def _iter_steps(data: dict):
    for job_id, job in (data.get("jobs") or {}).items():
        if isinstance(job, dict):
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    yield job_id, step


def check_cache_keys(workflows) -> list[Finding]:
    """C-CACHE-01 — actions/cache(full) 를 run_id 없는 고정 키로 사용 (STD-SCHED-06)."""
    out = []
    for rel, _, data in workflows:
        for job_id, step in _iter_steps(data):
            uses = str(step.get("uses", ""))
            if not uses.startswith("actions/cache@"):
                continue
            with_ = step.get("with") or {}
            key = str(with_.get("key", ""))
            path = str(with_.get("path", ""))
            immutable = any(w in f"{key} {path}".lower() for w in ("apt", "font", "pip", "deps"))
            if "run_id" not in key and not immutable:
                out.append(Finding("C-CACHE-01", "STD-SCHED-06", WARN, rel, 0,
                                   f"job {job_id}: full cache 고정 키 '{key}' — 상태 파일이면 "
                                   "restore@v4 + run_id 키로"))
    return out


def check_font_packages(workflows) -> list[Finding]:
    """C-PKG-01 — 같은 워크플로 안 job 간 폰트 패키지 목록 불일치 (STD-SCHED-07)."""
    out = []
    for rel, _, data in workflows:
        per_job: dict[str, set[str]] = {}
        for job_id, step in _iter_steps(data):
            pkgs = set(_FONT_PKG.findall(str(step.get("run", ""))))
            if pkgs:
                per_job.setdefault(job_id, set()).update(pkgs)
        if len({frozenset(v) for v in per_job.values()}) > 1:
            detail = " / ".join(f"{j}:{','.join(sorted(v))}" for j, v in sorted(per_job.items()))
            out.append(Finding("C-PKG-01", "STD-SCHED-07", WARN, rel, 0, f"job 간 폰트 설치 불일치 — {detail}"))
    return out


def workflow_entrypoints(root: pathlib.Path, workflows) -> set[str]:
    """run 스텝의 python -m X / python X.py 가 가리키는 레포 내 .py 경로."""
    entries = set()
    for _, _, data in workflows:
        for _, step in _iter_steps(data):
            for mod, script in _PY_ENTRY.findall(str(step.get("run", ""))):
                if mod:
                    if mod.split(".")[0] in _SKIP_MODULES:
                        continue
                    base = root / mod.replace(".", "/")
                    for cand in (base.with_suffix(".py"), base / "__main__.py"):
                        if cand.is_file():
                            entries.add(_rel(root, cand))
                            break
                elif (root / script).is_file():
                    entries.add(pathlib.PurePosixPath(script).as_posix())
    return entries


# ── 파이썬 ─────────────────────────────────────────────────

def _is_env_read(node: ast.AST) -> bool:
    """os.environ.get(...) / os.getenv(...) / os.environ[...] 인가."""
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os":
            return True
        if (isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(f.value, ast.Attribute)
                and f.value.attr == "environ"):
            return True
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ")


def check_python(root: pathlib.Path, py_files: list[pathlib.Path], entrypoints: set[str]) -> list[Finding]:
    """C-ENV-02 직접 숫자 파싱 · C-MODE-02 DRY_RUN 부정 판정 · C-VER-01 진입점 VERSION · C-SEC-02 로거."""
    out = []
    for path in py_files:
        rel = _rel(root, path)
        text = _read(path)
        if text is None:
            continue
        runtime = not (rel.startswith("tests/") or "/tests/" in rel)  # 테스트 픽스처는 런타임 규약 대상 아님
        for lineno, line in enumerate(text.splitlines(), 1):
            if runtime and "DRY_RUN" in line and _DRY_NEG.search(line):
                out.append(Finding("C-MODE-02", "STD-MODE-06", ERROR, rel, lineno,
                                   f"DRY_RUN 부정 판정 — 빈 값을 dry_run 으로 보는 이 모듈과 "
                                   f"'== \"true\"' 발행 계층이 엇갈림(08-11 고아 발행): {line.strip()}"))
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            out.append(Finding("C-PY-01", "STD-CI-01", ERROR, rel, 0, "파이썬 문법 오류(파싱 불가)"))
            continue
        for node in ast.walk(tree):
            if (runtime and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"int", "float"}
                    and node.args and _is_env_read(node.args[0])):
                out.append(Finding("C-ENV-02", "STD-CFG-01", ERROR, rel, node.lineno,
                                   f"{node.func.id}(환경변수) 직접 파싱: {ast.unparse(node)[:120]}"))
        if rel in entrypoints and not rel.startswith("tests/"):
            has_version = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION"
                                                                for t in n.targets) for n in tree.body)
            if not has_version:
                out.append(Finding("C-VER-01", "STD-DEP-05", WARN, rel, 0, "워크플로 진입 모듈에 VERSION 상수 없음"))
        if "basicConfig(" in text and not any(f'"{n}"' in text or f"'{n}'" in text for n in _NETWORK_LOGGERS):
            out.append(Finding("C-SEC-02", "STD-SEC-01", WARN, rel, 0, "로깅 설정에 네트워크 로거 하향 없음"))
    return out


# ── 레포 전반 ──────────────────────────────────────────────

def check_secrets(root: pathlib.Path, files: list[pathlib.Path]) -> list[Finding]:
    """C-SEC-01 — 추적 파일 안 자격증명 형태 문자열 (STD-GOV-07, STD-SEC-03). 값은 출력하지 않는다."""
    out = []
    for path in files:
        if path.suffix.lower() not in _TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        text = _read(path)
        if not text:
            continue
        for label, pattern in _SECRET_PATTERNS:
            for m in pattern.finditer(text):
                out.append(Finding("C-SEC-01", "STD-SEC-03", ERROR, _rel(root, path), _line_of(text, m.start()),
                                   f"{label} 형태 문자열({m.group(0)[:6]}…)"))
    return out


def check_nested_dirs(root: pathlib.Path, files: list[pathlib.Path]) -> list[Finding]:
    """C-NEST-01 — 같은 이름 폴더 중첩(부분 업로드 사고, threads-promo verify_repo 검사 12 이식, 깊이 무관)."""
    nested = set()
    for path in files:
        parts = path.relative_to(root).parts[:-1]
        for i in range(1, len(parts)):
            if parts[i] == parts[i - 1]:
                nested.add("/".join(parts[: i + 1]))
                break
    return [Finding("C-NEST-01", "STD-DEP-02", ERROR, d, 0, "같은 이름 폴더 중첩 — 실행 경로 밖 복제본일 수 있음")
            for d in sorted(nested)]


# ── baseline · 실행 ───────────────────────────────────────

def load_registered(root: pathlib.Path, rel: str) -> set[str] | None:
    path = root / rel
    if not path.is_file():
        return None
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def run_checks(root: pathlib.Path, registered_rel: str = DEFAULT_REGISTERED) -> list[Finding]:
    files = tracked_files(root)
    workflows, findings = load_workflows(root)
    entrypoints = workflow_entrypoints(root, workflows)
    py_files = [p for p in files if p.suffix == ".py" and p.is_file()]
    findings += check_mode_expressions(workflows)
    findings += check_cron_if(workflows)
    findings += check_cron_minute(workflows)
    findings += check_registered_names(workflows, load_registered(root, registered_rel), registered_rel)
    findings += check_cache_keys(workflows)
    findings += check_font_packages(workflows)
    findings += check_python(root, py_files, entrypoints)
    findings += check_secrets(root, [p for p in files if p.is_file()])
    findings += check_nested_dirs(root, files)
    return sorted(findings, key=lambda f: (f.severity != ERROR, f.check, f.path, f.line))


def load_baseline(path: pathlib.Path) -> set[str]:
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("errors", []))


def write_baseline(path: pathlib.Path, findings: list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({f.key for f in findings if f.severity == ERROR})
    body = {"version": VERSION, "note": "기존 위반 고정 목록 — 해당 파일 수정 시 함께 해소하고 이 목록에서 뺀다",
            "errors": keys}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDT 개발 표준 정적 검사기")
    parser.add_argument("--root", default=".", help="레포 루트")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--registered", default=DEFAULT_REGISTERED)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    print(f"[std_check] v{VERSION} 시작 | root={root}")
    findings = run_checks(root, args.registered)
    baseline_path = root / args.baseline

    if args.write_baseline:
        write_baseline(baseline_path, findings)
        print(f"[std_check] baseline 기록: {args.baseline} (오류 {sum(f.severity == ERROR for f in findings)}건)")
        return 0

    if not baseline_path.is_file():
        # 10-20 사고: .std/ 누락 시 기존 오류가 전부 '신규'로 보였다 — 설정 오류로 분리한다.
        print(f"::error title=STD-CHECK-C002::baseline 파일 없음: {args.baseline} "
              f"(최초 도입이면 마스터 승인 후 --write-baseline 으로 생성)")
        return 2
    try:
        baseline = load_baseline(baseline_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"::error title=STD-CHECK-C001::baseline 파일을 읽을 수 없음: {exc}")
        return 2

    errors = [f for f in findings if f.severity == ERROR]
    new_errors = [f for f in errors if f.key not in baseline]
    known = [f for f in errors if f.key in baseline]
    warns = [f for f in findings if f.severity == WARN]
    resolved = sorted(baseline - {f.key for f in errors})

    for f in new_errors:
        loc = f"{f.path}:{f.line}" if f.line else f.path
        print(f"::error file={f.path},line={max(f.line, 1)},title={f.check} {f.std}::{f.detail}")
        print(f"  [신규 오류] {f.check} {f.std} {loc} — {f.detail}")
    if args.verbose:
        for f in known:
            print(f"  [기존 오류] {f.check} {f.path} — {f.detail}")
        for f in warns:
            print(f"  [경고] {f.check} {f.std} {f.path} — {f.detail}")
    for key in resolved:
        print(f"  [해소됨] baseline 에서 빼도 됨: {key}")

    by_check: dict[str, int] = {}
    for f in findings:
        by_check[f"{f.check}({f.severity})"] = by_check.get(f"{f.check}({f.severity})", 0) + 1
    print("[std_check] 집계 " + ", ".join(f"{k}={v}" for k, v in sorted(by_check.items())))
    print(f"[std_check] 신규 오류 {len(new_errors)} · 기존 오류 {len(known)} · "
          f"경고 {len(warns)} · 해소 {len(resolved)}")
    return 1 if new_errors else 0


if __name__ == "__main__":
    sys.exit(main())
