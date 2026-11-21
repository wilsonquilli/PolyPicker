"""PolyPicker public API -- anonymous, read-only (FR-11)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from polypicker.api.routers import games, meta, players, predictions, teams
from polypicker.config import get_settings

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """Reject every write verb before it reaches a route (FR-11.2, FR-11.3)."""

    async def dispatch(self, request: Request, call_next):
        if request.method not in READ_METHODS:
            return JSONResponse(status_code=405, headers={"Allow": "GET, HEAD, OPTIONS"}, content={
                "error": {"code": "read_only", "message": "The PolyPicker API is read-only."}})
        response = await call_next(request)
        if request.method == "GET" and response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=60, stale-while-revalidate=300")
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="PolyPicker API",
        version="1.0.0",
        description="Read-only NBA schedules, statistics and model forecasts. Forecasts are statistical "
                    "estimates, not guarantees. No wagering functionality exists.",
        openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs", redoc_url=None,
    )
    app.add_middleware(ReadOnlyMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
                       allow_methods=["GET"], allow_headers=["*"])

    @app.exception_handler(HTTPException)
    async def http_error(_, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": _code(exc.status_code), "message": exc.detail}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_request", "message": "Invalid query parameters",
            "details": [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]}})

    for r in (meta.router, games.router, teams.router, players.router, predictions.router):
        app.include_router(r, prefix="/api/v1")
    return app


def _code(status: int) -> str:
    return {404: "not_found", 405: "read_only", 400: "bad_request"}.get(status, "error")


app = create_app()
