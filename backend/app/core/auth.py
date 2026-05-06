"""
backend/app/core/auth.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JWT 생성/검증, 비밀번호 해시, FastAPI Dependency 제공
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal

# ── 비밀번호 해시 컨텍스트 (bcrypt) ─────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── OAuth2 Bearer 토큰 스킴 ─────────────────────────────────────
# tokenUrl은 실제 로그인 엔드포인트 경로 (Swagger UI 연동용)
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v2/auth/login",
    auto_error=False,   # 토큰 없어도 None 반환 (선택적 인증용)
)


# ── DB 세션 Dependency ───────────────────────────────────────────
def get_db():
    """요청마다 DB 세션을 생성하고 종료 시 닫는 Dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 비밀번호 유틸 ────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    """평문 비밀번호를 bcrypt 해시로 변환"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """평문 비밀번호와 해시 비교"""
    return pwd_context.verify(plain, hashed)


# ── JWT 토큰 생성 ────────────────────────────────────────────────
def create_access_token(user_id: int, email: str) -> str:
    """액세스 토큰 생성 (유효기간: 1시간)"""
    expire = datetime.utcnow() + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "email": email,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    """리프레시 토큰 생성 (유효기간: 7일)"""
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """JWT 토큰 검증 및 페이로드 반환. 실패 시 예외 발생"""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"토큰이 유효하지 않습니다: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI Dependency: 현재 사용자 ──────────────────────────────
def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Authorization 헤더의 Bearer 토큰으로 현재 사용자 조회.
    토큰 없거나 유효하지 않으면 401 반환.
    """
    from sqlalchemy import text

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="액세스 토큰이 아닙니다.",
        )

    user_id = int(payload["sub"])
    row = db.execute(
        text("SELECT id, email, name, created_at, last_login FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
        )

    return {"id": row.id, "email": row.email, "name": row.name}


def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    토큰이 있으면 사용자 반환, 없으면 None 반환 (비로그인 접근 허용 엔드포인트용).
    """
    if not token:
        return None
    try:
        return get_current_user(token, db)
    except HTTPException:
        return None


def require_subscription(tier: str = "pro"):
    """
    특정 구독 등급 이상인 사용자만 허용하는 Dependency 팩토리.
    사용 예) Depends(require_subscription("pro"))
    """
    tier_rank = {"free": 0, "pro": 1, "premium": 2}

    def _check(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        from sqlalchemy import text

        row = db.execute(
            text(
                """
                SELECT tier, status, expires_at FROM subscriptions
                WHERE user_id = :uid AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"uid": current_user["id"]},
        ).fetchone()

        user_tier = row.tier if row else "free"

        # 만료 체크
        if row and row.expires_at and row.expires_at < datetime.utcnow():
            user_tier = "free"

        if tier_rank.get(user_tier, 0) < tier_rank.get(tier, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"이 기능은 {tier} 이상 구독이 필요합니다. 현재: {user_tier}",
            )

        return {**current_user, "subscription_tier": user_tier}

    return _check
