-- Phase 1 인증 스키마 적용 스크립트
-- 실행: docker exec tenbagger_db psql -U tenbagger -d tenbagger -f /docker-entrypoint-initdb.d/apply_auth_schema.sql
-- 또는: docker exec -i tenbagger_db psql -U tenbagger -d tenbagger < scripts/apply_auth_schema.sql

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(100),
    password_hash   VARCHAR(255),
    kakao_id        VARCHAR(100) UNIQUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    last_login      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    tier        VARCHAR(20) NOT NULL DEFAULT 'free',
    status      VARCHAR(20) NOT NULL DEFAULT 'active',
    started_at  TIMESTAMP DEFAULT NOW(),
    expires_at  TIMESTAMP,
    payment_key VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
    ticker          VARCHAR(10) NOT NULL,
    name            VARCHAR(100),
    added_at        TIMESTAMP DEFAULT NOW(),
    alert_enabled   BOOLEAN DEFAULT TRUE,
    UNIQUE(user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_users_email        ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_kakao_id     ON users(kakao_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_user     ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_ticker   ON watchlist(ticker);

SELECT 'Auth schema applied successfully' AS result;
