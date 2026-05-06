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

-- 인증 관련 인덱스
CREATE INDEX IF NOT EXISTS idx_users_email          ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_kakao_id       ON users(kakao_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user   ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_user       ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker     ON watchlist(ticker);
CREATE INDEX IF NOT EXISTS idx_payments_user        ON payments(user_id);
