"""
gen/hero_shorts/statestore.py — 발행 원장 내구화(지속 동기화)
==============================================================
샌드박스/CI는 실행마다 파일시스템이 초기화될 수 있다.
publish_state.json / costs.json 을 AI Drive(/hero-shorts/)에 미러링해
중복발행 차단 원장이 재시작에도 유지되게 한다.
모든 동작은 best-effort(실패 시 경고 로그 후 계속). 비활성화: HERO_STATE_SYNC=0
"""
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger("hero_shorts.statestore")

DRIVE_DIR = "/hero-shorts"


def _enabled():
    return os.getenv("HERO_STATE_SYNC", "1") != "0"


_TRANSIENT = ("520", "502", "503", "504", "timed out", "timeout", "Connection reset")


def _gsk(args, retries=3):
    """gsk 호출 — 일시 장애(HTTP 520 등) 재시도 포함.

    D-9: 2026-10-15 실측 HTTP 520 1회 → 수동 재시도로 성공. 자동 재시도로 흡수한다.
    """
    last = None
    for attempt in range(1, retries + 1):
        cp = subprocess.run(["gsk", *args], capture_output=True, text=True, timeout=300)
        if cp.returncode == 0:
            return cp.stdout
        err = cp.stdout + chr(10) + cp.stderr
        last = RuntimeError(
            f"gsk {' '.join(args[:2])} 실패 exit={cp.returncode} — {cp.stderr.strip()[:200]}"
        )
        if attempt < retries and any(t in err for t in _TRANSIENT):
            log.warning("gsk 일시 장애 재시도 %d/%d: %s", attempt, retries, args[:2])
            time.sleep(2 * attempt)
            continue
        raise last
    raise last


def sync_states(paths):
    """로컬 원장 → AI Drive 미러링(기존 파일 덮어쓰기). 성공 경로 리스트 반환."""
    if not _enabled():
        return []
    out = []
    for p in map(Path, paths):
        if not p.exists():
            continue
        try:
            _gsk(["aidrive", "upload", "--local_file", str(p),
                  "--upload_path", f"{DRIVE_DIR}/{p.name}", "--on-conflict", "overwrite"])
            out.append(str(p))
            log.info("원장 미러링 완료: %s → aidrive:%s", p.name, DRIVE_DIR)
        except Exception as exc:
            try:                                   # 드라이브 하위 디렉토리 부재 대비 — mkdir 1회 후 재시도
                _gsk(["aidrive", "mkdir", DRIVE_DIR])
                _gsk(["aidrive", "upload", "--local_file", str(p),
                      "--upload_path", f"{DRIVE_DIR}/{p.name}", "--on-conflict", "overwrite"])
                out.append(str(p))
                log.info("원장 미러링 완료(mkdir 후 재시도 성공): %s", p.name)
            except Exception as exc2:
                log.warning("원장 미러링 실패(무시하고 계속): %s — %s / 재시도: %s", p.name, exc, exc2)
    return out


def _max_ep(path):
    """원장에서 최대 ep 번호 추출 — staleness 판정용. 실패 시 None."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    eps = [int(k[2:]) for k in d if str(k).startswith("ep") and str(k)[2:].isdigit()]
    return max(eps) if eps else None


def _remote_is_newer(path):
    """D-5: 정본(AI Drive)이 로컬보다 앞서면 True.

    파일 존재만으로 복원을 건너뛰면 stale 로컬이 정본을 덮어쓴다(2026-10-15 실측 1회).
    판정: 정본을 임시로 내려받아 최대 ep 를 비교 — 정본이 더 높으면 채택한다.
    """
    tmp = Path(tempfile.mkdtemp()) / Path(path).name
    try:
        _gsk(["aidrive", "download", f"{DRIVE_DIR}/{Path(path).name}", str(tmp)])
    except Exception:
        return False
    r, l = _max_ep(tmp), _max_ep(path)
    if r is not None and (l is None or r > l):
        log.warning("정본이 더 최신(원장 ep %s > 로컬 %s) — 정본 채택", r, l)
        return True
    return False


def restore_if_missing(paths):
    """로컬 원장이 없거나 정본이 더 최신일 때 AI Drive에서 복원. 복원한 경로 리스트 반환."""
    if not _enabled():
        return []
    out = []
    for p in map(Path, paths):
        if p.exists() and not _remote_is_newer(p):
            continue
        try:
            _gsk(["aidrive", "download", f"{DRIVE_DIR}/{p.name}", str(p)])
            out.append(str(p))
            log.info("원장 복원: aidrive:%s/%s → %s", DRIVE_DIR, p.name, p)
        except Exception as exc:
            log.warning("원장 복원 실패(신규 실행으로 간주): %s — %s", p.name, exc)
    return out
