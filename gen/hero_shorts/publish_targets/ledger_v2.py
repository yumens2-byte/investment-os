"""
gen/hero_shorts/publish_targets/ledger_v2.py — 원장 스키마 v2 (가산 전용)
=========================================================================
설계서 §3. 기존 회차 레코드에 `targets{youtube,threads}` 를 **가산**한다.
틱톡 블록(`tiktok{}`)과 기존 키는 **절대 수정하지 않는다** — 해시로 보증한다.

안전 원칙:
    - 마이그레이션은 멱등(idempotent) — 두 번 돌려도 changed=False.
    - 틱톡 블록 해시가 전후 다르면 즉시 중단(exit 6).
    - 미러 실패는 exit 5(backfill 관례 준수).
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .base import (
    STATUS_BLOCKED,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    STATUS_SKIPPED,
)

log = logging.getLogger("hero_shorts.publish_targets.ledger_v2")

VERSION = "1.0.0"
SCHEMA_VERSION = 2
TARGET_PLATFORMS = ("youtube", "threads")

DEFAULT_LEDGER = Path("gen/data/publish_state.json")


# --------------------------------------------------------------------------- #
# 로드/저장
# --------------------------------------------------------------------------- #
def load_ledger(path=DEFAULT_LEDGER) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"원장 없음: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_ledger(doc: Dict[str, Any], path=DEFAULT_LEDGER) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)


def backup_ledger(path=DEFAULT_LEDGER) -> Optional[str]:
    """`.bak` 백업(backfill 관례와 동일 경로)."""
    p = Path(path)
    if not p.exists():
        return None
    b = Path(str(p) + ".bak")
    b.write_bytes(p.read_bytes())
    log.info("원장 백업: %s", b)
    return str(b)


def episode_keys(doc: Dict[str, Any]) -> List[str]:
    return sorted([k for k in doc if str(k).startswith("ep") and str(k)[2:].isdigit()],
                  key=lambda k: int(k[2:]))


# --------------------------------------------------------------------------- #
# 틱톡 블록 불변 보증
# --------------------------------------------------------------------------- #
def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def tiktok_hashes(doc: Dict[str, Any]) -> Dict[str, str]:
    """회차별 `tiktok{}` 블록 해시. 블록이 없으면 해시 생략."""
    out = {}
    for ep in episode_keys(doc):
        rec = doc[ep]
        if isinstance(rec, dict) and isinstance(rec.get("tiktok"), dict):
            out[ep] = hashlib.sha256(_canonical(rec["tiktok"])).hexdigest()
    return out


def assert_tiktok_unchanged(before: Dict[str, str], after: Dict[str, str]) -> None:
    """틱톡 블록이 한 바이트라도 바뀌면 중단."""
    if before == after:
        return
    changed = sorted(set(before) | set(after))
    diff = [ep for ep in changed if before.get(ep) != after.get(ep)]
    raise RuntimeError(f"틱톡 블록 변경 감지 — 중단(무손실 보존 위반): {diff}")


# --------------------------------------------------------------------------- #
# 마이그레이션 (가산 전용 · 멱등)
# --------------------------------------------------------------------------- #
def ensure_schema(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """스키마 v2 가산 적용. 원본을 제자리 수정하고 리포트를 반환한다.

    변경 항목만 손댄다: `_schema`(부재 시), 회차별 `targets`(부재 시).
    """
    report = {
        "schema_set": False,
        "targets_added": [],
        "already_v2": False,
        "episodes": len(episode_keys(doc)),
    }
    if doc.get("_schema") != SCHEMA_VERSION:
        doc["_schema"] = SCHEMA_VERSION
        report["schema_set"] = True
    else:
        report["already_v2"] = True

    for ep in episode_keys(doc):
        rec = doc[ep]
        if not isinstance(rec, dict):
            continue
        if "targets" not in rec:
            rec["targets"] = {}
            report["targets_added"].append(ep)
        for plat in TARGET_PLATFORMS:
            if plat not in rec["targets"]:
                rec["targets"][plat] = {"status": STATUS_PENDING, "attempts": []}

    report["changed"] = report["schema_set"] or bool(report["targets_added"])
    return doc, report


def migrate(path=DEFAULT_LEDGER, *, apply=False, mirror=False) -> Dict[str, Any]:
    """원장 v2 마이그레이션 실행.

    Returns: {"changed","report","backup","tiktok_ok","written","mirrored"}
    Raises: RuntimeError(틱톡 블록 변경) / FileNotFoundError
    """
    doc = load_ledger(path)
    before = tiktok_hashes(doc)
    backup = backup_ledger(path) if apply else None
    doc, report = ensure_schema(doc)
    after = tiktok_hashes(doc)
    assert_tiktok_unchanged(before, after)

    written = None
    if apply and report["changed"]:
        written = save_ledger(doc, path)

    mirrored = []
    if apply and mirror and written:
        mirrored = _mirror(path)

    return {
        "changed": report["changed"],
        "report": report,
        "backup": backup,
        "tiktok_ok": True,
        "written": written,
        "mirrored": mirrored,
    }


def _mirror(path) -> List[str]:
    """AI Drive 미러 — 미러 실패는 exit 5 로 알린다(backfill 관례)."""
    import subprocess

    p = Path(path)
    drive = "/hero-shorts"
    try:
        cp = subprocess.run(
            ["gsk", "aidrive", "upload", "--local_file", str(p),
             "--upload_path", f"{drive}/{p.name}", "--on-conflict", "overwrite"],
            capture_output=True, text=True, timeout=300,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip()[:200])
        log.info("원장 미러링 완료: %s → aidrive:%s", p.name, drive)
        return [str(p)]
    except Exception as exc:
        raise RuntimeError(f"미러 실패(exit 5): {exc}") from exc


# --------------------------------------------------------------------------- #
# targets 읽기/쓰기
# --------------------------------------------------------------------------- #
def target_status(record: Dict[str, Any], platform: str) -> str:
    """미기록이면 pending 으로 간주(설계서 §3.1)."""
    t = (record.get("targets") or {}).get(platform) or {}
    return str(t.get("status") or STATUS_PENDING)


def read_target(record: Dict[str, Any], platform: str) -> Dict[str, Any]:
    return dict((record.get("targets") or {}).get(platform) or {})


def set_target(record: Dict[str, Any], platform: str, result) -> Dict[str, Any]:
    """발행 결과를 원장 블록에 기록. 시도 이력은 누적한다.

    `attempts` 는 기존 값 뒤에 덧붙인다 — 상한 판정이 이력 길이를 본다.
    """
    targets = record.setdefault("targets", {})
    prev = targets.get(platform) or {}
    prev_attempts = list(prev.get("attempts") or [])
    block = result.to_ledger() if hasattr(result, "to_ledger") else dict(result)
    block["attempts"] = prev_attempts + list(block.get("attempts") or [])
    targets[platform] = block
    return block


def attempts_used(record: Dict[str, Any], platform: str) -> int:
    return len(((record.get("targets") or {}).get(platform) or {}).get("attempts") or [])


def plan_pending(records: Dict[str, Dict[str, Any]], platforms=TARGET_PLATFORMS,
                 max_attempts: int = 3) -> List[Dict[str, Any]]:
    """발행 계획 — 미발행 타깃만 산출(멱등성 핵심).

    제외: published(재발행 금지) · blocked/skipped(사람 개입 필요) · 상한 초과
    포함: pending · failed(상한 내)
    """
    out: List[Dict[str, Any]] = []
    for ep in episode_keys(records):
        rec = records[ep]
        if not isinstance(rec, dict):
            continue
        for plat in platforms:
            st = target_status(rec, plat)
            if st in (STATUS_PUBLISHED, STATUS_BLOCKED, STATUS_SKIPPED):
                continue
            used = attempts_used(rec, plat)
            if used >= max_attempts:
                log.warning("재시도 상한 도달 — 제외: %s:%s (%d회)", ep, plat, used)
                continue
            out.append({
                "ep": ep,
                "platform": plat,
                "status": st,
                "attempts_used": used,
                "idempotency_key": f"{ep}:{plat}",
            })
    return out


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
