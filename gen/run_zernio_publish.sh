#!/usr/bin/env bash
# v2.8.0 — 플랫폼 분기 추가(instagram|tiktok). 기존 호출 호환: 인자 1~5 동일.
# 플랫폼은 HERO_ZERNIO_PLATFORM 환경변수로 지정(기본 instagram).
# tiktok 시 HERO_TIKTOK_ACCOUNT_ID 필수 — Zernio 대시보드에서 틱톡 연결 후 _id 주입.
set -euo pipefail
if [ "$#" -lt 4 ]; then
  echo "usage: $0 <ep> <plan_json> <caption_txt> <media_url> [schedule_at]" >&2
  exit 64
fi
EP="$1"
PLAN_JSON="$2"
CAPTION_TXT="$3"
MEDIA_URL="$4"
SCHEDULE_AT="${5:-}"

PLATFORM="${HERO_ZERNIO_PLATFORM:-instagram}"

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
