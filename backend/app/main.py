import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.company import router as company_router
from app.api.search import router as search_router
from app.api.screener import router as screener_router
from app.api.backtest import router as backtest_router
from app.api.qualitative import router as qualitative_router
from app.api.kakao import router as kakao_router
from app.api.v2_dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.core.database import check_db

app = FastAPI(
    title="tenbagger API",
    description="Korean Stock Long-term Investment Analysis System",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(company_router)
app.include_router(search_router)
app.include_router(screener_router)
app.include_router(backtest_router)
app.include_router(qualitative_router)
app.include_router(kakao_router, prefix="/api/kakao", tags=["kakao"])
app.include_router(dashboard_router)
app.include_router(auth_router)


@app.get("/api/health")
def health():
    db_ok = check_db()
    from app.core.config import settings
    openai_preview = (settings.openai_api_key[:10] + "...") if settings.openai_api_key else "EMPTY"
    env_openai = os.environ.get("OPENAI_API_KEY", "")
    env_preview = (env_openai[:10] + "...") if env_openai else "NOT_IN_ENV"
    return {
        "status": "ok",
        "db": "connected" if db_ok else "disconnected",
        "version": "2.0.0",
        "openai_key_set": bool(settings.openai_api_key),
        "openai_key_preview": openai_preview,
        "env_openai": env_preview,
    }


_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
