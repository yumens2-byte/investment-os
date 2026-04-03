-- ============================================
-- Investment OS — 주간 데이터 적재 테이블 (v1.0)
-- Supabase SQL Editor에서 실행
-- ============================================

-- 1. daily_snapshots — 일별 시장 스냅샷
CREATE TABLE IF NOT EXISTS daily_snapshots (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  snapshot_date DATE NOT NULL UNIQUE,
  spy_change FLOAT,
  vix FLOAT,
  oil_wti FLOAT,
  us10y FLOAT,
  nasdaq_change FLOAT,
  dollar_index FLOAT,
  usdkrw FLOAT,
  fear_greed INT,
  fear_greed_label TEXT,
  btc_usd FLOAT,
  fed_funds_rate FLOAT,
  hy_spread FLOAT,
  yield_curve FLOAT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. daily_analysis — 일별 분석 결과
CREATE TABLE IF NOT EXISTS daily_analysis (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  analysis_date DATE NOT NULL UNIQUE,
  regime TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  trading_signal TEXT NOT NULL,
  regime_score INT,
  etf_rank JSONB,
  etf_allocation JSONB,
  market_score JSONB,
  buy_watch TEXT[],
  reduce_list TEXT[],
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. daily_news — 일별 뉴스 분석
CREATE TABLE IF NOT EXISTS daily_news (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  news_date DATE NOT NULL UNIQUE,
  rss_sentiment TEXT,
  rss_score FLOAT,
  rss_headline_count INT,
  gemini_sentiment TEXT,
  top_issues JSONB,
  key_risk TEXT,
  top_headlines JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 4. daily_alerts — Alert 발동 이력
CREATE TABLE IF NOT EXISTS daily_alerts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  alert_date DATE NOT NULL,
  alert_type TEXT NOT NULL,
  alert_level TEXT NOT NULL,
  trigger_value TEXT,
  tweet_id TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON daily_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_analysis_date ON daily_analysis(analysis_date);
CREATE INDEX IF NOT EXISTS idx_news_date ON daily_news(news_date);
CREATE INDEX IF NOT EXISTS idx_alerts_date ON daily_alerts(alert_date);
