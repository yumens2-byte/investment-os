"""
gen/hero_shorts/generator.py — 컷 생성 백엔드 (fal H3 / 더미)
===============================================================
두 백엔드를 동일 인터페이스로 제공한다:
    generate("fal",   request, out_path)   — fal H3 실생성 (현금, 승인 후만)
    generate("dummy", request, out_path)   — ffmpeg 더미 (전수테스트 전용, 0원)

비용 통제 (마스터 지시 "불가피한 비용만"):
    - 월 상한 BUDGET_CAP_MONTHLY_USD(기본 $30) — gen/data/costs.json 누적,
      도달 시 fal 생성 실행 전에 중단한다(호출 자체를 차단).
    - fal 실측 가격 768P $0.06/s — duration 초 단위 환산해 기록.

fal 호출 프로토콜 (queue API):
    1) POST https://queue.fal.run/{FAL_ENDPOINT}  (Authorization: Key <키>)
    2) 응답의 status_url 폴링 (1~3s 간격, 상한 300s)
    3) 완료 응답의 video.url 다운로드
    FAL_ENDPOINT 소스 기본값: minimax/h3/text-to-video (가격 실측 완료, 마스터 승인 v2.5.2). 환경변수로 override 가능.
"""
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("hero_shorts.generator")

BASE = Path(__file__).resolve().parent.parent          # gen/
COSTS_FILE = BASE / "data" / "costs.json"
FAL_PRICE_PER_SEC = 0.06                                # 768P 실측 $/초


# ── 비용 원장 ───────────────────────────────────────────────────────
def _load_costs():
    if COSTS_FILE.exists():
        return json.loads(COSTS_FILE.read_text(encoding="utf-8"))
    return {"month": "", "usd": 0.0}


def budget_check(duration_sec, cap=None):
    """월 예산 내 실행 가능 여부 확인 — 초과 시 RuntimeError.

    cap: None 이면 환경변수 BUDGET_CAP_MONTHLY_USD, 그마저 없으면 30.0.
    """
    cap = float(cap or os.getenv("BUDGET_CAP_MONTHLY_USD", "30"))
    import datetime
    cur = datetime.date.today().strftime("%Y-%m")
    costs = _load_costs()
    used = costs["usd"] if costs.get("month") == cur else 0.0
    need = duration_sec * FAL_PRICE_PER_SEC
    if used + need > cap:
        log.error("예산 캡 도달: 사용 $%.2f + 필요 $%.2f > 캡 $%.2f", used, need, cap)
        raise RuntimeError(f"월 예산 초과 — fal 생성 차단 (누적 ${used:.2f}/{cap})")
    log.info("예산 확인 OK: $%.2f + $%.2f ≤ $%.2f", used, need, cap)


def budget_record(duration_sec):
    """실행 완료분 비용 기록 — 월 단위 누적."""
    import datetime
    cur = datetime.date.today().strftime("%Y-%m")
    costs = _load_costs()
    if costs.get("month") != cur:
        costs = {"month": cur, "usd": 0.0}
    costs["usd"] += duration_sec * FAL_PRICE_PER_SEC
    COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COSTS_FILE.write_text(json.dumps(costs, indent=2), encoding="utf-8")
    log.info("비용 기록: +$%.2f → 월 누적 $%.2f", duration_sec * FAL_PRICE_PER_SEC, costs["usd"])


# ── fal H3 백엔드 ───────────────────────────────────────────────────
KNOWN_PRICES = {"minimax/h3/text-to-video": 0.06}   # 가격 실측 완료 엔드포인트 전용
DEFAULT_FAL_ENDPOINT = "minimax/h3/text-to-video"   # 소스 기본값(마스터 승인 v2.5.2) — env override 가능


def _request_signature(request):
    return json.dumps(request, ensure_ascii=False, sort_keys=True)


def _pending_file(out_path):
    out_path = Path(out_path)
    return out_path.with_name(out_path.name + ".fal_pending.json")


def _pending_now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_pending(out_path):
    path = _pending_file(out_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("대기 작업 파일 해독 실패: %s (%s)", path, e)
        return None


def _save_pending(out_path, payload):
    path = _pending_file(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _clear_pending(out_path):
    path = _pending_file(out_path)
    if path.exists():
        path.unlink()


def _validate_request(request):
    """사전 밸리데이션 (v2.4.4) — 실패 시 호출 자체가 없으므로 0원.

    ①요청 스키마(기설계 단위 준수) ②G1/G2 프롬프트 게이트(비용 전 검증).
    """
    from . import canon as C
    if not str(request.get("prompt", "")).strip():
        raise ValueError("프롬프트 공백 — 생성 거부")
    dur = request.get("duration")
    if not isinstance(dur, int) or not (1 <= dur <= 10):
        raise ValueError(f"duration={dur} 허용 밖(정수 1~10) — 기설계 단위 위반, 생성 거부")
    if request.get("resolution") not in ("480P", "768P"):
        raise ValueError("resolution 비정상 — 생성 거부")
    if request.get("aspect_ratio") != "9:16":
        raise ValueError("aspect_ratio 9:16 아님 — 생성 거부")
    prompt = request["prompt"]
    missing = [t for t in C.PROMPT_TOKENS if t not in prompt]
    if missing:
        raise ValueError(f"캐논 토큰 누락 {missing} — G2 사전위반, 생성 거부(비용 전 차단)")
    bad = [f for f in C.FORBIDDEN_GLOBAL if f in prompt]
    bad += [f for v in C.FORBIDDEN_PER_CHARACTER.values() for f in v if f in prompt]
    if bad:
        raise ValueError(f"금지 문구 {bad} — G1 사전위반, 생성 거부(비용 전 차단)")


def _validate_output(out_path, requested_duration):
    """출력 후 기술검증 (v2.4.4) — 과금 발생 후이므로 이상 시 CRITICAL 기록 + 예외."""
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_format", str(out_path)], capture_output=True, text=True)
    dur = float(json.loads(r.stdout)["format"]["duration"])
    size = Path(out_path).stat().st_size
    if abs(dur - requested_duration) > 2 or size < 10240:
        log.critical("출력 이상: %.1fs(요청 %ds) %dB — 과금분 수동 확인 필요, 재생성 금지",
                     dur, requested_duration, size)
        raise RuntimeError("출력 검증 실패 — 과금 발생, 마스터 확인 전 재생성 금지")
    log.info("출력 검증 OK: %.1fs %dB", dur, size)


def _fal_json(req, phase):
    """fal 호출 공용 — HTTP 오류 시 응답 본문(원인)을 반드시 노출(403 등 원인 규명용)."""
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = "(본문 해독 실패)"
        hint = {401: "FAL_AI_KEY 무효·포맷 확인(fal 대시보드 Keys)",
                402: "fal 크레딧 부족",
                403: "접근 거부 — 모델 접근권·계정 빌링·키 상태 확인"}.get(e.code, "")
        raise RuntimeError(f"fal {phase} 실패 HTTP {e.code} {hint} — fal 응답: {detail}") from e


def _fal_call(request, out_path):
    """fal queue API 실호출. 키·엔드포인트 미설정 시 즉시 실패(추측 방지)."""
    key = os.getenv("FAL_AI_KEY", "")
    endpoint = os.getenv("FAL_ENDPOINT", DEFAULT_FAL_ENDPOINT)
    missing = [n for n, v in (("FAL_AI_KEY", key), ("FAL_ENDPOINT", endpoint)) if not v]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} 미설정 — fal 실생성은 승인 후 주입")
    if endpoint not in KNOWN_PRICES and not os.getenv("FAL_PRICE_OVERRIDE"):
        raise RuntimeError(f"가격 미확인 엔드포인트({endpoint}) — 과금 실측 전 실행 금지(v2.4.4)")
    _validate_request(request)                  # 비용 전 밸리데이션 — ①스키마 ②G1/G2
    duration = int(request.get("duration", 10))
    poll_interval = float(os.getenv("FAL_POLL_INTERVAL_SEC", "3"))
    max_polls = int(os.getenv("FAL_MAX_POLLS", "100"))
    if poll_interval <= 0:
        raise ValueError(f"FAL_POLL_INTERVAL_SEC={poll_interval} 허용 밖 — 0보다 커야 함")
    if max_polls <= 0:
        raise ValueError(f"FAL_MAX_POLLS={max_polls} 허용 밖 — 1 이상이어야 함")

    pending = _load_pending(out_path)
    request_signature = _request_signature(request)
    pending_matches = (
        pending
        and pending.get("endpoint") == endpoint
        and pending.get("request_signature") == request_signature
        and pending.get("status_url")
        and pending.get("response_url")
    )

    if pending_matches:
        job = pending
        log.warning(
            "기존 fal 작업 재개: request_id=%s last_status=%s polls=%s",
            job.get("request_id"), job.get("last_status"), job.get("poll_count", 0),
        )
    else:
        budget_check(duration)                  # 신규 제출 전에만 비용 게이트
        url = f"https://queue.fal.run/{endpoint}"
        body = json.dumps(request).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Authorization": f"Key {key}",
                                              "Content-Type": "application/json"})
        submitted = _fal_json(req, "제출")
        job = {
            "request_id": submitted.get("request_id"),
            "status_url": submitted.get("status_url"),
            "response_url": submitted.get("response_url"),
            "endpoint": endpoint,
            "request_signature": request_signature,
            "submitted_at": _pending_now_utc(),
            "last_status": "SUBMITTED",
            "poll_count": 0,
        }
        if not job.get("status_url") or not job.get("response_url"):
            raise RuntimeError(f"fal 제출 응답 불완전 — request_id={job.get('request_id')}")
        _save_pending(out_path, job)
        log.info("fal 작업 제출: %s", job.get("request_id"))

    status_url = job["status_url"]
    resp_url = job["response_url"]
    last_status = job.get("last_status", "SUBMITTED")
    for i in range(max_polls):
        time.sleep(poll_interval)
        st = _fal_json(urllib.request.Request(
                status_url, headers={"Authorization": f"Key {key}"}), "폴링")
        last_status = st.get("status", "UNKNOWN")
        job["last_status"] = last_status
        job["poll_count"] = int(job.get("poll_count", 0)) + 1
        job["last_polled_at"] = _pending_now_utc()
        _save_pending(out_path, job)
        log.debug("fal 폴링 %d회: %s", i + 1, last_status)
        if last_status == "COMPLETED":
            break
        if last_status in ("FAILED", "CANCELED"):
            raise RuntimeError(
                f"fal 작업 실패 status={last_status} request_id={job.get('request_id')} — {json.dumps(st, ensure_ascii=False)}"
            )
    else:
        timeout_sec = poll_interval * max_polls
        raise TimeoutError(
            f"fal 작업 타임아웃({timeout_sec:.0f}s) request_id={job.get('request_id')} "
            f"last_status={last_status} pending={_pending_file(out_path)}"
        )

    result = _fal_json(urllib.request.Request(
            resp_url, headers={"Authorization": f"Key {key}"}), "결과조회")
    video_url = result["video"]["url"]
    urllib.request.urlretrieve(video_url, out_path)
    _validate_output(out_path, duration)        # 과금 후 기술검증 — 이상 시 재생성 금지
    budget_record(duration)
    _clear_pending(out_path)
    log.info("fal 다운로드 완료: %s (%.0fs분)", out_path, duration)
    return str(out_path)


# ── 더미 백엔드 (전수테스트 전용 — 0원) ─────────────────────────────
def _dummy_call(request, out_path):
    """ffmpeg 더미 컷 — 9:16·duration 초·사인톤 오디오. QC 파이프라인 검증용."""
    duration = int(request.get("duration", 10))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import subprocess
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           "-i", f"testsrc2=size=720x1280:rate=30:duration={duration}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-shortest", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("더미 생성 실패: %s", r.stderr[:200])
        raise RuntimeError("ffmpeg 더미 생성 실패")
    log.info("더미 컷 생성: %s (%ds, 0원)", out_path, duration)
    return str(out_path)


def generate(backend, request, out_path):
    """통합 진입점 — backend 는 'fal' 또는 'dummy'."""
    log.info("컷 생성 시작: backend=%s → %s", backend, out_path)
    if backend == "fal":
        return _fal_call(request, out_path)
    if backend == "dummy":
        return _dummy_call(request, out_path)
    raise ValueError(f"알 수 없는 백엔드: {backend}")
