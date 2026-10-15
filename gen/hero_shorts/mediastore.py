"""
gen/hero_shorts/mediastore.py — 완성 미디어 AI Drive 보관 (v2.9.0)
==================================================================
설계 정본: reports/runner3_tiktok_design_2026-10-15.md (C-2, 마스터 승인 2026-10-15)

배경(실측 2026-10-15):
    러너1 이 발행용 공개 URL 을 만들 때 쓰는 `gsk download` 토큰 URL 은 24시간만 유효하다.
    러너1(08:00 KST) → 러너3(10:30 KST) 은 2.5시간이라 정상 경로는 문제없지만,
    발행 실패·지연·재시도 시점에 URL 이 만료되면 발행할 원본이 사라진다(설계 F-3).

해결:
    완성 영상을 AI Drive `/hero-shorts/media/` 에 원본 보관하고 경로를
    원장 `ep<N>.combined.aidrive_path` 에 남긴다.
    URL 만료 시 `refresh_public_url()` 이 원본을 내려받아 새 공개 URL 을 만든다.

비용: 0크레딧. gsk CLI 환경(Genspark 세션/스케줄)에서만 동작한다.
비활성화: HERO_MEDIA_ARCHIVE=0
"""
import logging
import os
import subprocess
from pathlib import Path

from .mediashare import _first_url, upload_media

log = logging.getLogger("hero_shorts.mediastore")

DRIVE_DIR = "/hero-shorts"
MEDIA_DIR = f"{DRIVE_DIR}/media"


def _enabled():
    return os.getenv("HERO_MEDIA_ARCHIVE", "1") != "0"


def _gsk(args, timeout=300):
    cp = subprocess.run(["gsk", *args], check=False, capture_output=True, text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(
            f"gsk {' '.join(args[:2])} 실패 exit={cp.returncode} — {cp.stderr.strip()[:200]}"
        )
    return cp.stdout


def archive_name(ep, local_path):
    """AI Drive 보관 파일명 — 회차 접두어로 충돌을 막는다(중복 접두 금지)."""
    name = Path(local_path).name
    return name if name.startswith(f"ep{ep}") else f"ep{ep}_{name}"


def archive_media(local_path, ep):
    """완성 영상 → AI Drive `/hero-shorts/media/`.

    반환: {"aidrive_path", "link", "bytes", "skipped"}
    실패 시 예외를 올린다 — 호출부(러너1)가 경고 후 계속 진행할지 판단한다(발행 차단 아님).
    """
    p = Path(local_path)
    if not p.exists():
        raise RuntimeError(f"보관 대상 없음: {p}")
    if not _enabled():
        log.info("미디어 보관 비활성(HERO_MEDIA_ARCHIVE=0) — 건너뜀: %s", p.name)
        return {"aidrive_path": None, "link": None, "bytes": p.stat().st_size, "skipped": True}

    target = f"{MEDIA_DIR}/{archive_name(ep, p)}"
    args = ["aidrive", "upload", "--local_file", str(p),
            "--upload_path", target, "--overwrite", "true"]
    try:
        out = _gsk(args)
    except Exception as exc:                     # 드라이브 하위 디렉토리 부재 대비 — mkdir 1회 후 재시도
        log.warning("미디어 보관 1차 실패 — mkdir 후 재시도: %s", exc)
        _gsk(["aidrive", "mkdir", MEDIA_DIR])
        out = _gsk(args)
    log.info("미디어 보관 완료: %s → aidrive:%s (%dB)", p.name, target, p.stat().st_size)
    return {"aidrive_path": target, "link": _first_url(out),
            "bytes": p.stat().st_size, "skipped": False}


def restore_media(aidrive_path, dest):
    """AI Drive 보관본 → 로컬 경로. 성공 시 Path 반환."""
    d = Path(dest)
    d.parent.mkdir(parents=True, exist_ok=True)
    _gsk(["aidrive", "download", aidrive_path, str(d)])
    if not d.exists():
        raise RuntimeError(f"보관본 복원 실패 — 파일 미생성: {d}")
    log.info("미디어 복원: aidrive:%s → %s", aidrive_path, d)
    return d


def refresh_public_url(ep, aidrive_path=None, local_dir=None, name=None):
    """만료된 공개 URL 대체 — AI Drive 원본 복원 후 `gsk upload` 로 새 공개 URL 생성(0크레딧).

    설계 F-3: media_url 부재/만료 시 이 경로로 재업로드하고, 실패하면 발행을 중단한다.
    """
    tmp_dir = Path(local_dir or os.getenv("HERO_MEDIA_DIR") or "/tmp/hero_media")
    fname = name or f"ep{ep}_final_voiced.mp4"
    src = aidrive_path or f"{MEDIA_DIR}/{fname}"
    local = restore_media(src, tmp_dir / fname)
    media = upload_media(local)
    log.info("공개 URL 재발급 완료: %s → %s", src, media["public_url"])
    # D-6(2026-10-15 실측): Zernio 는 토큰 URL(?token=…)을 판별 실패("no file extension and no
    # stored content type")로 거부한다. 토큰 없는 래퍼 URL 은 발행에 성공했다.
    # → 발행용 media_url 은 래퍼 URL 을 정본으로 삼고, 토큰 URL 은 보조로 남긴다.
    return {"media_url": media["wrapper_url"],
            "wrapper_url": media["wrapper_url"], "public_url": media["public_url"],
            "local": str(local), "aidrive_path": src}


def resolve_publish_url(ep, aidrive_path=None, local_dir=None, name=None):
    """발행 직전 media_url 확정 — Zernio 가 실제로 인게스트할 수 있는 URL 을 돌려준다.

    1순위: AI Drive 원본 → `gsk upload` 래퍼 URL(토큰 없음, 실측 발행 성공)
    2순위: 로컬 http.server + `.mp4` 확장자 공개 URL(publish_runtime.resolve_public_media_url)
    둘 다 실패하면 RuntimeError — 호출측은 발행을 중단한다(F-3 fail-closed).
    """
    try:
        info = refresh_public_url(ep, aidrive_path=aidrive_path, local_dir=local_dir, name=name)
        return info["media_url"], info
    except Exception as exc:
        log.warning("래퍼 URL 재발급 실패(%s) — 확장자 URL 로 폴백", exc)
    from .publish_runtime import resolve_public_media_url
    local = str(Path(local_dir or "/tmp/hero_media") / (name or f"ep{ep}_final_voiced.mp4"))
    return resolve_public_media_url(local), {"media_url": None, "local": local}
