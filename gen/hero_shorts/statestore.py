"""
gen/hero_shorts/statestore.py — 발행 원장 내구화(지속 동기화)
==============================================================
샌드박스/CI는 실행마다 파일시스템이 초기화될 수 있다.
publish_state.json / costs.json 을 AI Drive(/hero-shorts/)에 미러링해
중복발행 차단 원장이 재시작에도 유지되게 한다.
모든 동작은 best-effort(실패 시 경고 로그 후 계속). 비활성화: HERO_STATE_SYNC=0
"""
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("hero_shorts.statestore")

DRIVE_DIR = "/hero-shorts"


def _enabled():
    return os.getenv("HERO_STATE_SYNC", "1") != "0"


def _gsk(args):
    cp = subprocess.run(["gsk", *args], capture_output=True, text=True, timeout=300)
    if cp.returncode != 0:
        raise RuntimeError(
            f"gsk {' '.join(args[:2])} 실패 exit={cp.returncode} — {cp.stderr.strip()[:200]}"
        )
    return cp.stdout


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
                  "--upload_path", f"{DRIVE_DIR}/{p.name}", "--overwrite", "true"])
            out.append(str(p))
            log.info("원장 미러링 완료: %s → aidrive:%s", p.name, DRIVE_DIR)
        except Exception as exc:
            try:                                   # 드라이브 하위 디렉토리 부재 대비 — mkdir 1회 후 재시도
                _gsk(["aidrive", "mkdir", DRIVE_DIR])
                _gsk(["aidrive", "upload", "--local_file", str(p),
                      "--upload_path", f"{DRIVE_DIR}/{p.name}", "--overwrite", "true"])
                out.append(str(p))
                log.info("원장 미러링 완료(mkdir 후 재시도 성공): %s", p.name)
            except Exception as exc2:
                log.warning("원장 미러링 실패(무시하고 계속): %s — %s / 재시도: %s", p.name, exc, exc2)
    return out


def restore_if_missing(paths):
    """로컬 원장이 없을 때만 AI Drive에서 복원. 복원한 경로 리스트 반환."""
    if not _enabled():
        return []
    out = []
    for p in map(Path, paths):
        if p.exists():
            continue
        try:
            _gsk(["aidrive", "download", f"{DRIVE_DIR}/{p.name}", str(p)])
            out.append(str(p))
            log.info("원장 복원: aidrive:%s/%s → %s", DRIVE_DIR, p.name, p)
        except Exception as exc:
            log.warning("원장 복원 실패(신규 실행으로 간주): %s — %s", p.name, exc)
    return out
