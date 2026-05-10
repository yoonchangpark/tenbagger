-- init.sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 기업 기본 정보
CREATE TABLE IF NOT EXISTS companies (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(10) UNIQUE NOT NULL,
    name            VARCHAR(100),
    market          VARCHAR(20),
    dart_code       VARCHAR(20),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 연간 재무 데이터
CREATE TABLE IF NOT EXISTS financials_annual (
    id                  SERIAL PRIMARY KEY,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    year                INTEGER NOT NULL,
    revenue             BIGINT,
    operating_profit    BIGINT,
    net_income          BIGINT,
    total_assets        BIGINT,
    total_equity        BIGINT,
    total_debt          BIGINT,
    cash                BIGINT,
    current_assets      BIGINT,
    current_liab        BIGINT,
    cfo                 BIGINT,
    capex               BIGINT,
    fcf                 BIGINT,
    eps                 BIGINT,
    dps                 BIGINT,
    payout_ratio        FLOAT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (company_id, year)
);

-- 스코어링 결과 캐시
CREATE TABLE IF NOT EXISTS scores (
    id                  SERIAL PRIMARY KEY,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    ticker              VARCHAR(10) NOT NULL,
    name                VARCHAR(100),
    market              VARCHAR(20),
    total_score         FLOAT,
    grade               VARCHAR(20),
    growth_score        FLOAT,
    stability_score     FLOAT,
    cashflow_score      FLOAT,
    dividend_score      FLOAT,
    consistency_score   FLOAT,
    revenue_cagr_5y     FLOAT,
    eps_cagr_5y         FLOAT,
    avg_roe_5y          FLOAT,
    avg_operating_margin FLOAT,
    avg_fcf_margin      FLOAT,
    debt_ratio          FLOAT,
    current_ratio       FLOAT,
    avg_payout_ratio    FLOAT,
    dividend_yield      FLOAT,
    per                 FLOAT,
    pbr                 FLOAT,
    close               BIGINT,
    market_cap          BIGINT,
    analyzed_at         TIMESTAMPTZ DEFAULT NOW()
);

-- 스코어 조회 인덱스
CREATE INDEX IF NOT EXISTS idx_scores_ticker    ON scores(ticker);
CREATE INDEX IF NOT EXISTS idx_scores_grade     ON scores(grade);
CREATE INDEX IF NOT EXISTS idx_scores_total     ON scores(total_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_analyzed  ON scores(analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker);

-- =============================================
-- Phase 1: 인증 & 구독 스키마 (v2.0 추가)
-- =============================================

-- 사용자 테이블
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(100),
    password_hash   VARCHAR(255),       -- 이메일 로그인용 (카카오 로그인은 NULL 가능)
    kakao_id        VARCHAR(100) UNIQUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP
);

-- 구독 테이블
CREATE TABLE IF NOT EXISTS subscriptions (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    tier        VARCHAR(20) NOT NULL DEFAULT 'free',    -- free, pro, premium
    status      VARCHAR(20) NOT NULL DEFAULT 'active',  -- active, expired, cancelled
    started_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP,
    payment_key VARCHAR(255)
);

-- 관심종목 테이블 (구독자용)
CREATE TABLE IF NOT EXISTS watchlist (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    ticker          VARCHAR(10) NOT NULL,
    name            VARCHAR(100),
    added_at        TIMESTAMP DEFAULT NOW(),
    alert_enabled   BOOLEAN DEFAULT TRUE,
    UNIQUE(user_id, ticker)
);

-- 결제 내역 테이블
CREATE TABLE IF NOT EXISTS payments (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    order_id    VARCHAR(100) UNIQUE NOT NULL,
    payment_key VARCHAR(255),
    amount      INTEGER NOT NULL,
    tier        VARCHAR(20) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'done',
    paid_at     TIMESTAMP DEFAULT NOW()
);

-- 카카오 챗봇 채널 구독자 (Push 알림 대상)
CREATE TABLE IF NOT EXISTS kakao_bot_subscribers (
    id              SERIAL PRIMARY KEY,
    bot_user_key    VARCHAR(255) UNIQUE NOT NULL,  -- userRequest.user.id
    first_seen      TIMESTAMP DEFAULT NOW(),
    last_seen       TIMESTAMP DEFAULT NOW(),
    push_enabled    BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_kakao_sub_key ON kakao_bot_subscribers(bot_user_key);

-- 인증 관련 인덱스
CREATE INDEX IF NOT EXISTS idx_users_email          ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_kakao_id       ON users(kakao_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user   ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_user       ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker     ON watchlist(ticker);
CREATE INDEX IF NOT EXISTS idx_payments_user        ON payments(user_id);

-- =============================================
-- Phase 2: 구독 가치 강화 스키마 (v2.1 추가)
-- =============================================

-- ETL 실행 상태 (재배포 후 이어받기용)
CREATE TABLE IF NOT EXISTS etl_run_state (
    id          SERIAL PRIMARY KEY,
    run_date    DATE UNIQUE NOT NULL,
    status      VARCHAR(20) DEFAULT 'running',  -- running | done | failed
    last_ticker VARCHAR(10),
    processed   INTEGER DEFAULT 0,
    total       INTEGER DEFAULT 0,
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- 등급 이력 스냅샷 (가상 포트폴리오 시뮬레이터 기반 데이터)
-- ETL 실행마다 당일 1건씩 누적 → 날짜별 TENBAGGER 등급 이력 추적
CREATE TABLE IF NOT EXISTS score_history (
    id            SERIAL PRIMARY KEY,
    ticker        VARCHAR(10) NOT NULL,
    name          VARCHAR(100),
    market        VARCHAR(20),
    grade         VARCHAR(20) NOT NULL,
    total_score   FLOAT,
    growth_score  FLOAT,
    close_price   BIGINT,         -- ETL 실행 당일 종가 (매수 기준가)
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_score_history_grade  ON score_history(grade, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_score_history_ticker ON score_history(ticker, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_score_history_date   ON score_history(snapshot_date DESC);

-- 주가 일간 캐시 (pykrx 반복 호출 방지 — 차트·스파크라인·포트폴리오 공유)
CREATE TABLE IF NOT EXISTS price_daily_cache (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(10) NOT NULL,
    trade_date  DATE NOT NULL,
    open_price  INTEGER,
    close_price INTEGER,
    high_price  INTEGER,
    low_price   INTEGER,
    volume      BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_price_cache ON price_daily_cache(ticker, trade_date DESC);

-- 가상 포트폴리오 설정 (사용자별 저장)
CREATE TABLE IF NOT EXISTS virtual_portfolios (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name           VARCHAR(100) DEFAULT '내 포트폴리오',
    invest_amount  BIGINT DEFAULT 100000000,     -- 투자 원금 (원)
    grade_filter   TEXT DEFAULT 'TENBAGGER',     -- 쉼표 구분 복수 가능
    weight_method  VARCHAR(20) DEFAULT 'equal',  -- equal | score_weighted
    start_date     DATE NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_virtual_portfolio_user ON virtual_portfolios(user_id);

-- =============================================
-- Phase 5: 뉴스 감성 분석 스키마 (v2.2 추가)
-- =============================================

-- 수집된 뉴스 기사 (article_hash로 중복 방지 → Delta 업데이트)
CREATE TABLE IF NOT EXISTS news_articles (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(10),          -- NULL이면 업종 뉴스
    sector          VARCHAR(50),          -- 업종 뉴스일 때 사용
    article_hash    VARCHAR(64) UNIQUE NOT NULL,  -- SHA256(url or title)
    title           TEXT NOT NULL,
    url             TEXT,
    published_at    TIMESTAMPTZ,
    source          VARCHAR(100),
    sentiment_score FLOAT,               -- -1.0 ~ +1.0
    sentiment_label VARCHAR(20),         -- positive | neutral | negative
    key_topics      TEXT[],              -- GPT 추출 키워드 (배열)
    analyzed_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_news_ticker   ON news_articles(ticker, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_sector   ON news_articles(sector, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_hash     ON news_articles(article_hash);
CREATE INDEX IF NOT EXISTS idx_news_analyzed ON news_articles(analyzed_at DESC);

-- 종목별 일간 뉴스 감성 집계 (API 응답용)
CREATE TABLE IF NOT EXISTS news_sentiment (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(10) NOT NULL,
    name            VARCHAR(100),
    sentiment_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    avg_score       FLOAT,               -- 평균 감성 점수 (-1~+1)
    signal          VARCHAR(20),         -- BUY_SIGNAL | SELL_SIGNAL | CAUTION | NEUTRAL
    article_count   INTEGER DEFAULT 0,
    positive_count  INTEGER DEFAULT 0,
    negative_count  INTEGER DEFAULT 0,
    neutral_count   INTEGER DEFAULT 0,
    key_topics      TEXT[],              -- 주요 키워드 Top5
    summary         TEXT,               -- GPT 요약 (1~2문장)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, sentiment_date)
);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker ON news_sentiment(ticker, sentiment_date DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_signal ON news_sentiment(signal, sentiment_date DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_date   ON news_sentiment(sentiment_date DESC);

-- 업종별 일간 뉴스 감성 집계
CREATE TABLE IF NOT EXISTS sector_sentiment (
    id              SERIAL PRIMARY KEY,
    sector          VARCHAR(50) NOT NULL,
    sentiment_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    avg_score       FLOAT,
    signal          VARCHAR(20),
    article_count   INTEGER DEFAULT 0,
    positive_count  INTEGER DEFAULT 0,
    negative_count  INTEGER DEFAULT 0,
    key_topics      TEXT[],
    summary         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(sector, sentiment_date)
);
CREATE INDEX IF NOT EXISTS idx_sector_sentiment      ON sector_sentiment(sector, sentiment_date DESC);
CREATE INDEX IF NOT EXISTS idx_sector_sentiment_date ON sector_sentiment(sentiment_date DESC);

-- ─────────────────────────────────────────────────────────────────────
-- Phase 6: AI 투자위원회 캐시 (한 종목당 24시간 동일 결과 재사용)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS committee_cache (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(10) NOT NULL,
    name            VARCHAR(100),
    decision        VARCHAR(20),
    confidence      FLOAT,
    consensus_score FLOAT,
    result_json     JSONB NOT NULL,
    analyzed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(ticker, analyzed_at)
);
CREATE INDEX IF NOT EXISTS idx_committee_ticker ON committee_cache(ticker, analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_committee_decision ON committee_cache(decision, analyzed_at DESC);
