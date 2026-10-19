"""
gen/nuna_weekly/backends/fal.py — fal queue 백엔드 (실 / dummy)
================================================================
gen/hero_shorts/generator.py(_fal_call)의 queue 프로토콜을 이식했다(import 하지 않음).

    1) POST {config.FAL_QUEUE_BASE}/{endpoint}   (Authorization: Key <FAL_AI_KEY>)
    2) 응답 request_id / status_url / response_url → pending JSON 을 폴링 전에 기록
    3) status_url 폴링 — "COMPLETED" 면 종료, "FAILED"/"CANCELED" 면 실패 (hero 와 동일 문자열)
    4) response_url 조회 → images[0].url 또는 video.url → out_path 로 다운로드
    5) pending 삭제

같은 endpoint + signature(payload 정렬 JSON 의 sha256) pending 이 있으면 재제출 없이 재개한다
(재실행 시 이중 과금 방지). 로그에는 request_id 만 남긴다(URL·키 금지).
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import config, secret_env
from . import FalResult

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.backends.fal")

STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = ("FAILED", "CANCELED")


class FalError(RuntimeError):
    """fal 호출 실패 (메시지는 마스킹됨)."""


def request_signature(payload: dict[str, Any]) -> str:
    """payload 정렬 JSON 의 sha256 — pending 재개 판정용."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_video_endpoint(endpoint: str, payload: dict[str, Any]) -> bool:
    return endpoint == config.FAL_CLIP_ENDPOINT or "video" in endpoint or "duration" in payload


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_pending(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("pending 파일 해독 실패 → 무시: %s (%s)", path.name, type(e).__name__)
        return None


def _save_pending(path: Path, job: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


class FalQueueBackend:
    name = "fal"

    def __init__(self, session: Any = None, sleep: Callable[[float], None] = time.sleep,
                 poll_interval: float = 3.0, max_polls: int = 100, timeout: int = 60) -> None:
        log.info("[NunaWeekly:backends.fal] v%s 시작", VERSION)
        if poll_interval <= 0:
            raise ValueError(f"poll_interval 은 0보다 커야 함: {poll_interval}")
        if max_polls <= 0:
            raise ValueError(f"max_polls 는 1 이상: {max_polls}")
        self._key = secret_env.require("FAL_AI_KEY")
        if session is None:
            import requests

            session = requests.Session()
        self.s = session
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.timeout = timeout

    # ── HTTP 공용 ──
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Key {self._key}"}

    def _json(self, method: str, url: str, phase: str, body: Any = None) -> dict[str, Any]:
        headers = self._auth()
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            r = self.s.request(method, url, headers=headers, timeout=self.timeout,
                               data=None if body is None else json.dumps(body, ensure_ascii=False))
        except Exception as e:
            raise FalError(secret_env.redact(f"fal {phase} 호출 실패: {type(e).__name__}: {e}", 300)) from None
        if r.status_code >= 400:
            hint = {401: "FAL_AI_KEY 무효", 402: "fal 크레딧 부족",
                    403: "접근 거부 — 모델 접근권·빌링 확인"}.get(r.status_code, "")
            raise FalError(f"fal {phase} 실패 HTTP {r.status_code} {hint} — "
                           f"{secret_env.redact(r.text, 400)}")
        try:
            return r.json()
        except Exception:
            raise FalError(f"fal {phase} 응답 JSON 아님: {secret_env.redact(r.text, 200)}") from None

    def _download(self, url: str, out_path: Path) -> None:
        try:
            r = self.s.get(url, timeout=max(self.timeout, 120))
        except Exception as e:
            raise FalError(secret_env.redact(f"fal 결과 다운로드 실패: {type(e).__name__}: {e}", 300)) from None
        if r.status_code >= 400:
            raise FalError(f"fal 결과 다운로드 실패 HTTP {r.status_code}")
        content = r.content or b""
        if not content:
            raise FalError("fal 결과 다운로드 0바이트")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.name + ".part")
        tmp.write_bytes(content)
        tmp.replace(out_path)

    # ── 본 호출 ──
    def run(self, endpoint: str, payload: dict[str, Any], out_path: Path,
            pending_path: Path) -> FalResult:
        out_path = Path(out_path)
        pending_path = Path(pending_path)
        sig = request_signature(payload)
        pending = _load_pending(pending_path)
        resumable = bool(pending and pending.get("endpoint") == endpoint
                         and pending.get("signature") == sig
                         and pending.get("status_url") and pending.get("response_url"))
        if resumable:
            job = pending  # type: ignore[assignment]
            log.warning("기존 fal 작업 재개(재제출 없음): request_id=%s", job.get("request_id"))
        else:
            sub = self._json("POST", f"{config.FAL_QUEUE_BASE}/{endpoint}", "제출", body=payload)
            job = {"endpoint": endpoint, "signature": sig, "request_id": sub.get("request_id"),
                   "status_url": sub.get("status_url"), "response_url": sub.get("response_url"),
                   "created_at": _now_utc()}
            if not job["request_id"] or not job["status_url"] or not job["response_url"]:
                raise FalError(f"fal 제출 응답 불완전 — request_id={job.get('request_id')}")
            _save_pending(pending_path, job)   # 폴링 전에 기록 — 중단 후 재개 근거
            log.info("fal 작업 제출: request_id=%s", job["request_id"])

        req_id = str(job["request_id"])
        last = "SUBMITTED"
        for i in range(self.max_polls):
            self.sleep(self.poll_interval)
            st = self._json("GET", job["status_url"], "폴링")
            last = str(st.get("status", "UNKNOWN"))
            log.debug("fal 폴링 %d회: %s request_id=%s", i + 1, last, req_id)
            if last == STATUS_COMPLETED:
                break
            if last in STATUS_FAILED:
                raise FalError(f"fal 작업 실패 status={last} request_id={req_id}")
        else:
            raise FalError(f"fal 작업 타임아웃({self.poll_interval * self.max_polls:.0f}s) "
                           f"request_id={req_id} last_status={last} — pending 유지(재실행 시 재개)")

        data = self._json("GET", job["response_url"], "결과조회")
        try:
            if is_video_endpoint(endpoint, payload):
                url = data["video"]["url"]
            else:
                url = data["images"][0]["url"]
        except (KeyError, IndexError, TypeError):
            raise FalError(f"fal 결과 형식 불일치 request_id={req_id} keys={sorted(data)[:10]}") from None
        self._download(url, out_path)
        pending_path.unlink(missing_ok=True)
        log.info("fal 다운로드 완료: %s request_id=%s", out_path.name, req_id)
        return FalResult(path=out_path, remote_url=url, request_id=req_id,
                         meta={"endpoint": endpoint, "status": last})


# --------------------------------------------------------------------------- #
# dummy — 0원·무네트워크
# --------------------------------------------------------------------------- #
class DummyFal:
    name = "dummy"

    def __init__(self) -> None:
        log.info("[NunaWeekly:backends.fal] v%s 시작 (dummy)", VERSION)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def _image(out_path: Path) -> None:
        from PIL import Image, ImageDraw

        w, h = config.VIDEO_W, config.VIDEO_H
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        for y in range(h):  # 네이비 → 실버 세로 그라데이션
            t = y / (h - 1)
            draw.line([(0, y), (w, y)], fill=(int(20 + 170 * t), int(30 + 165 * t), int(70 + 130 * t)))
        draw.text((w // 2 - 60, h // 2), "IRON NUNA (dummy)", fill=(0, 255, 255))
        img.save(out_path, format="PNG")

    @staticmethod
    def _video(out_path: Path, duration: int) -> None:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"testsrc2=size={config.VIDEO_W}x{config.VIDEO_H}:rate={config.VIDEO_FPS}:duration={duration}",
             "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path)],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise FalError(f"더미 영상 생성 실패: {r.stderr[:300]}")

    def run(self, endpoint: str, payload: dict[str, Any], out_path: Path,
            pending_path: Path) -> FalResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.calls.append((endpoint, dict(payload)))
        if is_video_endpoint(endpoint, payload):
            self._video(out_path, int(payload["duration"]))
        else:
            self._image(out_path)
        rid = "dummy-" + request_signature(payload)[:8]
        return FalResult(path=out_path, remote_url=f"https://dummy.invalid/{out_path.name}",
                         request_id=rid, meta={"endpoint": endpoint, "status": STATUS_COMPLETED})
