from pydantic_settings import BaseSettings
from typing import Dict, Any
import secrets

class Settings(BaseSettings):
    dart_api_key: str = ""
    openai_api_key: str = ""
    database_url: str = "postgresql://tenbagger:tenbagger123@db:5432/tenbagger"
    redis_url: str = "redis://redis:6379/0"

    weight_growth: float = 0.30
    weight_stability: float = 0.20
    weight_cashflow: float = 0.20
    weight_dividend: float = 0.15
    weight_consistency: float = 0.15

    # JWT 인증 설정
    # JWT_SECRET 환경변수 미설정 시 실행마다 랜덤 생성 (재시작 시 토큰 무효화됨)
    jwt_secret: str = secrets.token_hex(32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8시간
    refresh_token_expire_days: int = 7

    # 카카오 OAuth 설정
    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    kakao_redirect_uri: str = "http://localhost:8000/api/v2/auth/kakao/callback"

    # 카카오 챗봇 Push 설정 (Open Builder)
    kakao_bot_id: str = ""   # Open Builder > 봇 설정 > 봇 ID

    # 토스페이먼츠 설정
    toss_client_key: str = ""   # 프론트엔드용 (공개)
    toss_secret_key: str = ""   # 백엔드용 (비공개)

    # 관리자 API 시크릿 키
    admin_secret: str = ""      # ADMIN_SECRET 환경변수

    # 오너/관리자 이메일 (항상 premium 티어로 처리)
    admin_email: str = "yoonchang.park@gmail.com"  # ADMIN_EMAIL 환경변수로 재정의 가능

    # 네이버 뉴스 검색 API (뉴스 감성 분석용)
    naver_client_id: str = ""      # NAVER_CLIENT_ID 환경변수
    naver_client_secret: str = ""  # NAVER_CLIENT_SECRET 환경변수

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

THRESHOLDS = {
    "revenue_cagr_excellent": 20.0,
    "eps_cagr_excellent": 20.0,
    "roe_excellent": 15.0,
    "debt_ratio_safe": 50.0,
    "debt_ratio_caution": 150.0,
    "fcf_margin_excellent": 15.0,
    "payout_ratio_ideal_min": 20.0,
    "payout_ratio_ideal_max": 50.0,
}

GRADE_THRESHOLDS = {
    "TENBAGGER": {"min_total": 7.5, "min_growth": 8.0},
    "COMPOUNDER": {"min_total": 6.5},
    "WATCHLIST": {"min_total": 5.0},
}
