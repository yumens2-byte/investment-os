#!/usr/bin/env bash
# Hero Shorts: YouTube via Zernio (2026-10-18); TikTok remains suspended.
set -euo pipefail
PLATFORM="${HERO_ZERNIO_PLATFORM:-youtube}"
PLATFORM="${PLATFORM,,}"
COMBINED="${HERO_COMBINED:-0}"
if [ "$PLATFORM" = "tiktok" ]; then
  echo "TIKTOK_PUBLISH_SUSPENDED: 마스터 지시로 TikTok 발행 중단"
  exit 0
fi
if [ "$COMBINED" = "1" ]; then
  # usage: HERO_COMBINED=1 $0 <ep> <final_video> <title> <media_url>
  if [ "$PLATFORM" != "youtube" ]; then
    echo "합본 발행은 YouTube 경로를 사용하세요" >&2
    exit 64
  fi
  if [ "$#" -lt 4 ]; then
    echo "usage(combined): $0 <ep> <final_video> <title> <media_url>" >&2
    exit 64
  fi
  EP="${1#ep}"; FINAL_VIDEO="$2"; TITLE="$3"; MEDIA_URL="$4"
  PLAN_JSON="gen/data/out/ep${EP}_plan.json"
  CAPTION_TXT="gen/data/out/ep${EP}_caption.txt"
  # Existing plan/QC/audio/approval gates are enforced by pipeline.
  CMD=(python3 -m gen.hero_shorts.pipeline zernio_publish --ep "$EP"
       --plan "$PLAN_JSON" --final-video "$FINAL_VIDEO"
       --caption-file "$CAPTION_TXT" --media-url "$MEDIA_URL" --platform youtube
       --require-audible-audio)
else
  if [ "$#" -lt 4 ]; then
    echo "usage: $0 <ep> <plan_json> <caption_txt> <media_url> [schedule_at]" >&2
    exit 64
  fi
  EP="${1#ep}"; PLAN_JSON="$2"; CAPTION_TXT="$3"; MEDIA_URL="$4"
  CMD=(python3 -m gen.hero_shorts.pipeline zernio_publish --ep "$EP"
       --plan "$PLAN_JSON" --caption-file "$CAPTION_TXT" --media-url "$MEDIA_URL"
       --platform "$PLATFORM" --require-audible-audio)
  if [ -n "${5:-}" ]; then CMD+=(--schedule-at "$5"); fi
fi
if [ "$PLATFORM" = "youtube" ]; then
  # 2026-10-18: 소스 기본값 제거 — 계정 ID 는 시크릿/환경변수로만 주입한다.
  if [ -z "${HERO_YOUTUBE_ACCOUNT_ID:-}" ]; then
    echo "HERO_YOUTUBE_ACCOUNT_ID 미설정 — 소스 기본값은 2026-10-18 제거되었습니다. GitHub Secrets/환경변수로 주입하세요" >&2
    exit 78
  fi
  CMD+=(--account-id "$HERO_YOUTUBE_ACCOUNT_ID")
fi
CMD+=(--max-wait-sec "${HERO_ZERNIO_MAX_WAIT_SEC:-900}"
      --poll-interval-sec "${HERO_ZERNIO_POLL_INTERVAL_SEC:-30}")
"${CMD[@]}"
