#!/usr/bin/env bash
# v2.8.1 — 플랫폼 분기(instagram|tiktok) + 합본 발행 경로.
# 합본: HERO_COMBINED=1 이면 3편 분할 대신 전편 1편으로 발행한다
#       (2026-10-13 마스터 지시. 60초 초과 무관 — 플랫폼 한도 3초~10분).
# tiktok 시 HERO_TIKTOK_ACCOUNT_ID 필수 — Zernio 대시보드에서 틱톡 연결 후 _id 주입.
set -euo pipefail

PLATFORM="${HERO_ZERNIO_PLATFORM:-instagram}"
COMBINED="${HERO_COMBINED:-0}"

# The combined branch always invokes the TikTok publisher.
if [ "${PLATFORM,,}" = "tiktok" ] || [ "$COMBINED" = "1" ]; then
  echo "TIKTOK_PUBLISH_SUSPENDED: 마스터 지시로 TikTok 발행 중단"
  exit 0
fi

if [ "$COMBINED" = "1" ]; then
  # usage: HERO_COMBINED=1 HERO_ZERNIO_PLATFORM=tiktok $0 <ep> <final_video> <title> <media_url>
  if [ "$#" -lt 4 ]; then
    echo "usage(combined): HERO_COMBINED=1 $0 <ep> <final_video> <title> <media_url>" >&2
    exit 64
  fi
  EP="$1"; FINAL_VIDEO="$2"; TITLE="$3"; MEDIA_URL="$4"
  OUT="${HERO_COMBINED_MANIFEST:-gen/data/out/tiktok_combined.json}"

  if [ "$PLATFORM" = "tiktok" ] && [ -z "${HERO_TIKTOK_ACCOUNT_ID:-}" ]; then
    echo "HERO_TIKTOK_ACCOUNT_ID 미설정 — Zernio 대시보드에서 틱톡 연결 후 _id를 주입하세요" >&2
    exit 65
  fi

  python3 -m gen.hero_shorts.tiktok.runner combined \
    --video "$FINAL_VIDEO" --title "$TITLE" --out "$OUT"

  PUB=(python3 -m gen.hero_shorts.tiktok.runner publish
       --manifest "$OUT" --segment 1 --media-url "$MEDIA_URL")
  if [ "$PLATFORM" = "tiktok" ]; then
    PUB+=(--account-id "$HERO_TIKTOK_ACCOUNT_ID")
  fi
  if [ -n "${HERO_TIKTOK_PRIVACY_LEVEL:-}" ]; then
    PUB+=(--privacy-level "$HERO_TIKTOK_PRIVACY_LEVEL")
  fi
  echo "▶ 합본 발행: ep=$EP platform=$PLATFORM"
  "${PUB[@]}"
  exit $?
fi

# ── 기존 3편 분할 경로 (인자 1~5 동일, 하위 호환) ──
if [ "$#" -lt 4 ]; then
  echo "usage: $0 <ep> <plan_json> <caption_txt> <media_url> [schedule_at]" >&2
  exit 64
fi
EP="$1"
PLAN_JSON="$2"
CAPTION_TXT="$3"
MEDIA_URL="$4"
SCHEDULE_AT="${5:-}"

CMD=(python3 -m gen.hero_shorts.pipeline zernio_publish --ep "$EP" --plan "$PLAN_JSON" --caption-file "$CAPTION_TXT" --media-url "$MEDIA_URL" --platform "$PLATFORM" --max-wait-sec "${HERO_ZERNIO_MAX_WAIT_SEC:-900}" --poll-interval-sec "${HERO_ZERNIO_POLL_INTERVAL_SEC:-30}" --require-audible-audio)

if [ "$PLATFORM" = "tiktok" ]; then
  if [ -z "${HERO_TIKTOK_ACCOUNT_ID:-}" ]; then
    echo "HERO_TIKTOK_ACCOUNT_ID 미설정 — Zernio 대시보드에서 틱톡 연결 후 _id를 주입하세요" >&2
    exit 65
  fi
  CMD+=(--account-id "$HERO_TIKTOK_ACCOUNT_ID")
fi

if [ -n "$SCHEDULE_AT" ]; then
  CMD+=(--schedule-at "$SCHEDULE_AT")
fi
"${CMD[@]}"
