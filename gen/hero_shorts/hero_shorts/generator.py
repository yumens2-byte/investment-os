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
    FAL_ENDPOINT 는 .env 에서 주입 — 추측으로 기본값을 박지 않는다.
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


def _fal_call(request, out_path):
    """fal queue API 실호출. 키·엔드포인트 미설정 시 즉시 실패(추측 방지)."""
    key = os.getenv("FAL_AI_KEY", "")
    endpoint = os.getenv("FAL_ENDPOINT", "")
    if not key or not endpoint:
        raise RuntimeError("FAL_AI_KEY/FAL_ENDPOINT 미설정 — fal 실생성은 승인 후 주입")
    if endpoint not in KNOWN_PRICES and not os.getenv("FAL_PRICE_OVERRIDE"):
        raise RuntimeError(f"가격 미확인 엔드포인트({endpoint}) — 과금 실측 전 실행 금지(v2.4.4)")
    _validate_request(request)                  # 비용 전 밸리데이션 — ①스키마 ②G1/G2
    duration = int(request.get("duration", 10))
    budget_check(duration)                      # 호출 전 비용 게이트
    url = f"https://queue.fal.run/{endpoint}"
    body = json.dumps(request).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Key {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        job = json.load(r)
    log.info("fal 작업 제출: %s", job.get("request_id"))
    # 폴링 — status_url 응답이 COMPLETED 가 될 때까지 3s 간격 최대 100회
    status_url = job.get("status_url")
    resp_url = job.get("response_url")
    for i in range(100):
        time.sleep(3)
        with urllib.request.urlopen(urllib.request.Request(
                status_url, headers={"Authorization": f"Key {key}"}), timeout=30) as r:
            st = json.load(r)
        log.debug("fal 폴링 %d회: %s", i + 1, st.get("status"))
        if st.get("status") == "COMPLETED":
            break
    else:
        raise TimeoutError("fal 작업 타임아웃(300s)")
    with urllib.request.urlopen(urllib.request.Request(
            resp_url, headers={"Authorization": f"Key {key}"}), timeout=30) as r:
        result = json.load(r)
    video_url = result["video"]["url"]
    urllib.request.urlretrieve(video_url, out_path)
    _validate_output(out_path, duration)        # 과금 후 기술검증 — 이상 시 재생성 금지
    budget_record(duration)
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
