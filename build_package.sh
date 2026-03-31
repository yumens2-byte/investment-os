#!/bin/bash
# 패키지 생성 스크립트 (참고용)
# GTT팀 납품 시 실행

cd /home/claude
zip -r investment_comic_v2.0_$(date +%Y%m%d).zip comic_v2/ \
  --exclude "*.pyc" \
  --exclude "__pycache__/*" \
  --exclude ".DS_Store"

echo "패키지 생성 완료"
