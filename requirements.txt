# Investment OS — Python Dependencies (v1.8.0)
# v1.8.0 변경: playwright 추가 (HTML 대시보드 렌더링)

# 데이터 수집
yfinance==0.2.51
fredapi==0.5.2
feedparser==6.0.11

# 데이터 처리
pandas==2.2.3
numpy==1.26.4

# X (Twitter) 발행
tweepy==4.14.0

# 환경 설정
python-dotenv==1.0.1

# 스케줄러
schedule==1.2.2

# HTTP
requests==2.32.3

# 유틸리티
pytz==2024.2

# 제거됨 (v1.5.0):
# praw — Reddit API 유료화로 제거
matplotlib>=3.8.0
Pillow>=10.0.0
# v1.8.0 신규: HTML 대시보드 렌더링
playwright>=1.40.0
