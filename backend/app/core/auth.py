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


# ── 구독 등급 조회 ───────────────────────────────────────────────
TIER_RANK = {"free": 0, "basic": 1, "pro": 2}

# 판매 중단된 등급명 — DB에 남아 있는 값을 현재 최상위 등급으로 읽는다
LEGACY_TIERS = {"premium": "pro", "platinum": "pro"}

# 기능당 AI 체험 횟수 — 무료는 계정당 평생, 기본 플랜은 매주 월요일(KST)에 초기화된다
FREE_TRIAL_LIMIT = 3
BASIC_TRIAL_LIMIT = 3

# 주간 초기화 기준 시각. 주간 리포트(월요일 08:00 KST)와 같은 요일에 맞춘다.
KST_OFFSET = timedelta(hours=9)


def current_week_start_utc() -> datetime:
    """이번 주 월요일 00:00(KST)을 UTC로 돌려준다."""
    now_kst = datetime.utcnow() + KST_OFFSET
    monday_kst = (now_kst - timedelta(days=now_kst.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday_kst - KST_OFFSET


def resolve_tier(user: Optional[dict], db: Session) -> str:
    """
    사용자의 현재 구독 등급을 반환한다.
    비로그인 / 구독 없음 / 만료는 모두 "free", 오너 이메일은 항상 최상위 등급.
    """
    from sqlalchemy import text

    if not user:
        return "free"

    if settings.admin_email and user.get("email") == settings.admin_email:
        return "pro"

    row = db.execute(
        text(
            """
            SELECT tier, expires_at FROM subscriptions
            WHERE user_id = :uid AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """
        ),
        {"uid": user["id"]},
    ).fetchone()

    if not row:
        return "free"
    if row.expires_at and row.expires_at < datetime.utcnow():
        return "free"
    tier = row.tier or "free"
    return LEGACY_TIERS.get(tier, tier)


def get_current_tier(
    user: Optional[dict] = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> str:
    """
    비로그인 접근을 허용하되 등급별로 결과를 제한하는 엔드포인트용 Dependency.
    (예: 스크리너 — 무료는 상위 N개만)
    """
    return resolve_tier(user, db)


def require_subscription(tier: str = "pro"):
    """
    특정 구독 등급 이상인 사용자만 허용하는 Dependency 팩토리.
    사용 예) Depends(require_subscription("pro"))
    비로그인은 401, 등급 미달은 403을 반환한다.
    """

    def _check(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        user_tier = resolve_tier(current_user, db)

        if TIER_RANK.get(user_tier, 0) < TIER_RANK.get(tier, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"이 기능은 {tier} 이상 구독이 필요합니다. 현재: {user_tier}",
            )

        return {**current_user, "subscription_tier": user_tier}

    return _check


def require_subscription_or_trial(feature: str, tier: str = "pro"):
    """
    tier 이상이면 통과. 그 아래 등급은 feature당 정해진 횟수만큼 체험할 수 있다.
      · 무료  — 계정당 FREE_TRIAL_LIMIT회 (초기화 없음)
      · 기본  — 매주 월요일 00:00 KST에 BASIC_TRIAL_LIMIT회로 초기화
    체험 기록은 요청이 정상 처리된 뒤에 남기므로, 분석이 실패하면 횟수가 깎이지 않는다.
    비로그인은 401 — 체험 횟수는 계정 단위로만 셀 수 있다.
    """

    def _check(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        from sqlalchemy import text

        user_tier = resolve_tier(current_user, db)
        ctx = {**current_user, "subscription_tier": user_tier}

        if TIER_RANK.get(user_tier, 0) >= TIER_RANK.get(tier, 0):
            yield {**ctx, "trial": False}
            return

        weekly = user_tier == "basic"
        limit = BASIC_TRIAL_LIMIT if weekly else FREE_TRIAL_LIMIT
        # 무료는 전체 기록을, 기본 플랜은 이번 주 기록만 센다
        since = current_week_start_utc() if weekly else datetime.min
        params = {"uid": current_user["id"], "feature": feature, "since": since}

        used = db.execute(
            text(
                """
                SELECT COUNT(*) FROM ai_trial_usage
                WHERE user_id = :uid AND feature = :feature AND used_at >= :since
                """
            ),
            params,
        ).scalar() or 0

        if used >= limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"이번 주 체험 {limit}회를 모두 사용하셨습니다. 다음 주 월요일에 다시 {limit}회가 주어집니다. "
                    f"지금 바로 이용하시려면 {tier} 구독이 필요합니다."
                    if weekly else
                    f"무료 체험 {limit}회를 모두 사용하셨습니다. 계속 이용하시려면 {tier} 구독이 필요합니다."
                ),
            )

        # 엔드포인트는 이 dict를 그대로 받는다. 캐시된 결과를 돌려주는 등
        # AI 호출이 실제로 일어나지 않은 경우 consume_trial을 False로 바꾸면 차감하지 않는다.
        trial_ctx = {
            **ctx,
            "trial": True,
            "trial_remaining": limit - used - 1,
            "trial_weekly": weekly,
            "consume_trial": True,
        }
        yield trial_ctx

        if not trial_ctx.get("consume_trial", True):
            return

        # 엔드포인트가 예외 없이 끝났을 때만 체험 1회를 소진시킨다
        db.execute(
            text(
                """
                INSERT INTO ai_trial_usage (user_id, feature)
                SELECT :uid, :feature
                WHERE (
                    SELECT COUNT(*) FROM ai_trial_usage
                    WHERE user_id = :uid AND feature = :feature AND used_at >= :since
                ) < :limit
                """
            ),
            {**params, "limit": limit},
        )
        db.commit()

    return _check
