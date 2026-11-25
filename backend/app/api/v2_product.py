"""
제품 흐름 v2 — 테마(① 찾는다)와 검증 일지 API

GET /api/v2/themes               승인된 테마 목록 (후보 수 포함)
GET /api/v2/themes/{id}          내러티브 + 4필터 + 후보(5고리 단계순) + 제외 종목
GET /api/v2/snapshots            검증 일지 (최신순)
GET /api/v2/snapshots/{id}       스냅샷 한 건 (승인 당시 내용 그대로 + 분기 점검)
GET /api/v2/candidates/{ticker}  종목 화면: 5고리 근거 + 재무 3년 + 시장 반영 1년

원본은 content/product/*.json, 기동 시 domain/product_seed.py가 DB로 옮긴다.
초안(draft) 테마는 공개하지 않는다.
"""
import time

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.domain.candidate_view import build_candidate

from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2", tags=["product"])

_THEME_COLS = "id, name, line, competence, money, invert, safety, sources, approved_by, approved_at"


def _theme_row(r) -> dict:
    d = dict(r._mapping)
    d["approved_at"] = d["approved_at"].isoformat() if d.get("approved_at") else None
    return d


@router.get("/themes")
def list_themes():
    db = SessionLocal()
    try:
        rows = db.execute(text(f"""
            SELECT {_THEME_COLS},
                   (SELECT COUNT(*) FROM product_theme_candidates c
                     WHERE c.theme_id = t.id AND c.excluded_reason IS NULL) AS candidate_count
              FROM product_themes t
             WHERE status = 'approved'
             ORDER BY approved_at DESC NULLS LAST, id
        """)).fetchall()
        return {"themes": [_theme_row(r) for r in rows]}
    finally:
        db.close()


@router.get("/themes/{theme_id}")
def get_theme(theme_id: str):
    db = SessionLocal()
    try:
        row = db.execute(text(f"SELECT {_THEME_COLS} FROM product_themes WHERE id = :id AND status = 'approved'"),
                         {"id": theme_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="승인된 테마가 없습니다")
        cands = db.execute(text("""
            SELECT ticker, name, stage, survival_pass, excluded_reason
              FROM product_theme_candidates WHERE theme_id = :id
             ORDER BY stage DESC NULLS LAST, name
        """), {"id": theme_id}).fetchall()
        theme = _theme_row(row)
        theme["candidates"] = [dict(c._mapping) for c in cands if not c.excluded_reason]
        theme["excluded"] = [dict(c._mapping) for c in cands if c.excluded_reason]
        return theme
    finally:
        db.close()


@router.get("/snapshots")
def list_snapshots(limit: int = 50):
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, ticker, name, theme_id, version, approved_by, approved_at, price_at,
                   notion_url, payload->>'stage' AS stage
              FROM product_snapshots
             ORDER BY approved_at DESC, id DESC
             LIMIT :limit
        """), {"limit": max(1, min(limit, 200))}).fetchall()
        out = []
        for r in rows:
            d = dict(r._mapping)
            d["approved_at"] = d["approved_at"].isoformat()
            out.append(d)
        return {"snapshots": out}
    finally:
        db.close()


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM product_snapshots WHERE id = :id"), {"id": snapshot_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="스냅샷이 없습니다")
        snap = dict(row._mapping)
        snap["approved_at"] = snap["approved_at"].isoformat()
        snap["created_at"] = snap["created_at"].isoformat() if snap.get("created_at") else None
        checks = db.execute(text("""
            SELECT quarter, link, stage_before, stage_after, verdict, evidence_url, note, checked_at
              FROM product_quarterly_checks WHERE snapshot_id = :id ORDER BY quarter, link
        """), {"id": snapshot_id}).fetchall()
        snap["quarterly_checks"] = [
            {**dict(c._mapping), "checked_at": c.checked_at.isoformat() if c.checked_at else None}
            for c in checks
        ]
        return snap
    finally:
        db.close()


# DART·주가·수급 호출이 묶여 있으므로 6시간 캐시. 근거(DB)는 테마 동기화 때만 바뀐다.
_CAND_CACHE: dict[str, tuple[float, dict]] = {}
_CAND_TTL = 6 * 3600


@router.get("/candidates/{ticker}")
async def get_candidate(ticker: str):
    hit = _CAND_CACHE.get(ticker)
    if hit and time.time() - hit[0] < _CAND_TTL:
        return hit[1]
    data = await build_candidate(ticker)
    if not data.get("found"):
        raise HTTPException(status_code=404, detail="DART에서 종목을 찾을 수 없습니다")
    if data.get("financials"):
        _CAND_CACHE[ticker] = (time.time(), data)
    return data
