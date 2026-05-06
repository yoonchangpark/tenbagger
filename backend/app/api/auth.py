"""
backend/app/api/auth.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/api/v2/auth/* 인증 라우터
  POST /api/v2/auth/register         이메일 회원가입
  POST /api/v2/auth/login            이메일 로그인 → JWT 반환
  GET  /api/v2/auth/me               현재 사용자 정보 + 구독 상태
  POST /api/v2/auth/logout           클라이언트 토큰 삭제 안내
  GET  /api/v2/auth/kakao            카카오 OAuth 시작 URL 반환
  GET  /api/v2/auth/kakao/callback   카카오 OAuth 콜백 처리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_db,
    hash_password,
    verify_password,
)
from app.core.config import settings

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])


# ── Pydantic 스키마 ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("비밀번호는 6자 이상이어야 합니다.")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


# ── 회원가입 ─────────────────────────────────────────────────────

@router.post("/register", summary="이메일 회원가입", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """
    이메일/비밀번호로 신규 계정을 생성합니다.
    가입 즉시 free 구독이 자동 생성됩니다.
    """
    # 중복 이메일 체크
    existing = db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": body.email},
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 이메일입니다.",
        )

    # 사용자 생성
    row = db.execute(
        text(
            """
            INSERT INTO users (email, name, password_hash, created_at)
            VALUES (:email, :name, :password_hash, NOW())
            RETURNING id, email, name, created_at
            """
        ),
        {
            "email": body.email,
            "name": body.name or body.email.split("@")[0],
            "password_hash": hash_password(body.password),
        },
    ).fetchone()
    db.commit()

    user_id = row.id

    # free 구독 자동 생성
    db.execute(
        text(
            """
            INSERT INTO subscriptions (user_id, tier, status, started_at)
            VALUES (:uid, 'free', 'active', NOW())
            """
        ),
        {"uid": user_id},
    )
    db.commit()

    access_token = create_access_token(user_id, row.email)
    refresh_token = create_refresh_token(user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={"id": row.id, "email": row.email, "name": row.name},
    )


# ── 로그인 ───────────────────────────────────────────────────────

@router.post("/login", summary="이메일 로그인")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    이메일/비밀번호로 로그인합니다.
    성공 시 access_token(1시간)과 refresh_token(7일)을 반환합니다.
    """
    row = db.execute(
        text("SELECT id, email, name, password_hash FROM users WHERE email = :email"),
        {"email": body.email},
    ).fetchone()

    if not row or not row.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    if not verify_password(body.password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    # last_login 갱신
    db.execute(
        text("UPDATE users SET last_login = NOW() WHERE id = :uid"),
        {"uid": row.id},
    )
    db.commit()

    access_token = create_access_token(row.id, row.email)
    refresh_token = create_refresh_token(row.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={"id": row.id, "email": row.email, "name": row.name},
    )


# ── 내 정보 ──────────────────────────────────────────────────────

@router.get("/me", summary="현재 사용자 정보 + 구독 상태")
def me(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    현재 로그인 사용자의 정보와 구독 상태를 반환합니다.
    """
    # 구독 조회
    sub = db.execute(
        text(
            """
            SELECT tier, status, started_at, expires_at
            FROM subscriptions
            WHERE user_id = :uid AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """
        ),
        {"uid": current_user["id"]},
    ).fetchone()

    # 관심종목 수
    wl_count = db.execute(
        text("SELECT COUNT(*) FROM watchlist WHERE user_id = :uid"),
        {"uid": current_user["id"]},
    ).scalar()

    subscription = {
        "tier": sub.tier if sub else "free",
        "status": sub.status if sub else "none",
        "expires_at": sub.expires_at.isoformat() if sub and sub.expires_at else None,
    }

    return {
        "user": current_user,
        "subscription": subscription,
        "watchlist_count": wl_count or 0,
    }


# ── 로그아웃 ─────────────────────────────────────────────────────

@router.post("/logout", summary="로그아웃 (클라이언트 토큰 삭제)")
def logout():
    """
    서버 측 상태가 없는 JWT 방식이므로,
    클라이언트에서 localStorage의 토큰을 삭제하면 됩니다.
    """
    return {"message": "클라이언트의 access_token과 refresh_token을 삭제하세요."}


# ── 카카오 OAuth ─────────────────────────────────────────────────

@router.get("/kakao", summary="카카오 OAuth 로그인 URL 반환")
def kakao_login():
    """
    카카오 OAuth 인증 시작 URL을 반환합니다.
    KAKAO_CLIENT_ID 환경변수가 설정되어 있어야 합니다.
    """
    if not settings.kakao_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="KAKAO_CLIENT_ID가 설정되지 않았습니다. .env 파일에 추가해주세요.",
        )

    kakao_auth_url = (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={settings.kakao_client_id}"
        f"&redirect_uri={settings.kakao_redirect_uri}"
        "&response_type=code"
    )
    return {"url": kakao_auth_url}


@router.get("/kakao/callback", summary="카카오 OAuth 콜백 처리")
async def kakao_callback(
    code: str = Query(..., description="카카오 인가 코드"),
    db: Session = Depends(get_db),
):
    """
    카카오 OAuth 인가 코드를 받아 토큰 교환 후 사용자를 생성/로그인합니다.
    """
    if not settings.kakao_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="KAKAO_CLIENT_ID가 설정되지 않았습니다.",
        )

    # 1단계: 인가 코드 → 카카오 액세스 토큰 교환
    async with httpx.AsyncClient() as client:
        token_data = {
            "grant_type": "authorization_code",
            "client_id": settings.kakao_client_id,
            "redirect_uri": settings.kakao_redirect_uri,
            "code": code,
        }
        if settings.kakao_client_secret:
            token_data["client_secret"] = settings.kakao_client_secret

        token_resp = await client.post(
            "https://kauth.kakao.com/oauth/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"카카오 토큰 교환 실패: {token_resp.text}",
        )

    kakao_access_token = token_resp.json().get("access_token")

    # 2단계: 카카오 사용자 정보 조회
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {kakao_access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="카카오 사용자 정보 조회 실패",
        )

    kakao_data = user_resp.json()
    kakao_id = str(kakao_data["id"])
    kakao_account = kakao_data.get("kakao_account", {})
    email = kakao_account.get("email", f"kakao_{kakao_id}@kakao.com")
    nickname = kakao_data.get("properties", {}).get("nickname", "카카오 사용자")

    # 3단계: 기존 계정 조회 또는 신규 생성
    row = db.execute(
        text("SELECT id, email, name FROM users WHERE kakao_id = :kid OR email = :email"),
        {"kid": kakao_id, "email": email},
    ).fetchone()

    if row:
        # 기존 사용자: kakao_id 업데이트 (이메일로 가입된 경우)
        db.execute(
            text("UPDATE users SET kakao_id = :kid, last_login = NOW() WHERE id = :uid"),
            {"kid": kakao_id, "uid": row.id},
        )
        db.commit()
        user_id, user_name = row.id, row.name
    else:
        # 신규 사용자 생성
        new_row = db.execute(
            text(
                """
                INSERT INTO users (email, name, kakao_id, created_at)
                VALUES (:email, :name, :kid, NOW())
                RETURNING id, name
                """
            ),
            {"email": email, "name": nickname, "kid": kakao_id},
        ).fetchone()
        db.commit()

        user_id, user_name = new_row.id, new_row.name

        # free 구독 자동 생성
        db.execute(
            text(
                "INSERT INTO subscriptions (user_id, tier, status) VALUES (:uid, 'free', 'active')"
            ),
            {"uid": user_id},
        )
        db.commit()

    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id)

    # 프론트엔드로 토큰을 전달하는 리다이렉트 (login.html이 파싱)
    base = settings.kakao_redirect_uri.split("/api/")[0]  # http://localhost:8000
    redirect_url = (
        f"{base}/login.html"
        f"?access_token={access_token}"
        f"&refresh_token={refresh_token}"
        f"&name={user_name}"
    )

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=redirect_url)
