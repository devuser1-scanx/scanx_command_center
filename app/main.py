import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.api.v1.routes.ws_dashboard import poll_dashboard_timeline_loop
from app.core.config import settings
from app.integrations.gcs_reports import list_report_groups

# Paths that serve Swagger/ReDoc's own HTML + CDN-hosted assets in debug
# mode - a strict CSP there would break those pages, and they're already
# gated off entirely (docs_url/redoc_url are None) when settings.debug is
# False.
_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


async def add_security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

    if request.url.path not in _DOCS_PATHS:
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

    return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    poll_task = asyncio.create_task(poll_dashboard_timeline_loop())

    # Pre-warms the report groups cache so the first Reports tab load
    # doesn't pay the full bucket-listing cost (~15-20s across ~2k blobs).
    asyncio.create_task(asyncio.to_thread(list_report_groups))

    try:
        yield
    finally:
        poll_task.cancel()

        with suppress(asyncio.CancelledError):
            await poll_task


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.middleware("http")(add_security_headers)

    app.include_router(api_router)
    return app


app = create_app()
