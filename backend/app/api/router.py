from fastapi import APIRouter

from app.api import uploads, analysis, improvements, versions, mt5, settings_api, projects, tradingview

api_router = APIRouter()

api_router.include_router(projects.router, prefix="/projects", tags=["Projects"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["Uploads"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["Analysis"])
api_router.include_router(improvements.router, prefix="/improvements", tags=["Improvements"])
api_router.include_router(versions.router, prefix="/versions", tags=["Versions"])
api_router.include_router(mt5.router, prefix="/mt5", tags=["MT5"])
api_router.include_router(tradingview.router, prefix="/tradingview", tags=["TradingView"])
api_router.include_router(settings_api.router, prefix="/settings", tags=["Settings"])
