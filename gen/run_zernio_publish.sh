#!/usr/bin/env bash
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
# 발행 운영 식별값은 파이썬 소스 기본값(v2.5.2) 사용 — 필요시 환경변수 override
CMD=(python3 -m gen.hero_shorts.pipeline zernio_publish --ep "$EP" --plan "$PLAN_JSON" --caption-file "$CAPTION_TXT" --media-url "$MEDIA_URL" --platform instagram --max-wait-sec "${HERO_ZERNIO_MAX_WAIT_SEC:-900}" --poll-interval-sec "${HERO_ZERNIO_POLL_INTERVAL_SEC:-30}" --require-audible-audio)
if [ -n "$SCHEDULE_AT" ]; then
  CMD+=(--schedule-at "$SCHEDULE_AT")
fi
"${CMD[@]}"
