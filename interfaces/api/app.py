# ============================================================
#  interfaces/api/app.py
#  FastAPI Application Entry Point & RFC 7807 Error Handlers
# ============================================================

from fastapi import FastAPI, Request, status, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from interfaces.api.routes import (
    auth as auth_routes,
    users as users_routes,
    jobs as jobs_routes,
    quick_convert as qc_routes,
    prompts as prompts_routes,
    apis as apis_routes,
    admin as admin_routes,
)

from core.exceptions.domain_exceptions import (
    DomainError,
    EntityNotFoundError,
    QuotaExceededError,
    AuthenticationError,
    ArtifactNotFoundError,
)
from core.ai.exceptions import AIError, AIChainExhaustedError

app = FastAPI(
    title="PolpoT Application API",
    description="REST API for PolpoT Document Conversion Backend serving Qt Desktop and external clients.",
    version="1.0.0",
)

# CORS middleware for local Qt / Web development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _problem_response(status_code: int, type_slug: str, title: str, detail: str, path: str) -> JSONResponse:
    """تولید ساختار خطای استاندارد RFC 7807 (application/problem+json)."""
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://polpot.app/errors/{type_slug}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": path,
        },
    )


# ─── ثبت Exception Handlerهای لایه دامنه و HTTP منطبق بر RFC 7807 ───

@app.exception_handler(StarletteHTTPException)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    type_slug = "http-error"
    if exc.status_code == 401:
        type_slug = "unauthorized"
    elif exc.status_code == 404:
        type_slug = "not-found"
    elif exc.status_code == 400:
        type_slug = "bad-request"
    elif exc.status_code == 403:
        type_slug = "forbidden"

    return _problem_response(
        status_code=exc.status_code,
        type_slug=type_slug,
        title="HTTP Error",
        detail=str(exc.detail),
        path=request.url.path,
    )


@app.exception_handler(EntityNotFoundError)
@app.exception_handler(ArtifactNotFoundError)
async def entity_not_found_handler(request: Request, exc: Exception):
    return _problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        type_slug="not-found",
        title="Resource Not Found",
        detail=str(exc),
        path=request.url.path,
    )


@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request: Request, exc: QuotaExceededError):
    return _problem_response(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        type_slug="quota-exceeded",
        title="Daily Quota Exceeded",
        detail=str(exc),
        path=request.url.path,
    )


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    return _problem_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        type_slug="unauthorized",
        title="Authentication Failed",
        detail=str(exc),
        path=request.url.path,
    )


@app.exception_handler(AIChainExhaustedError)
async def chain_exhausted_handler(request: Request, exc: AIChainExhaustedError):
    return _problem_response(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        type_slug="ai-chain-exhausted",
        title="AI Providers Unavailable",
        detail=str(exc),
        path=request.url.path,
    )


@app.exception_handler(AIError)
async def ai_error_handler(request: Request, exc: AIError):
    return _problem_response(
        status_code=status.HTTP_502_BAD_GATEWAY,
        type_slug="ai-provider-error",
        title="AI Provider Invocation Failed",
        detail=str(exc),
        path=request.url.path,
    )


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return _problem_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        type_slug="domain-error",
        title="Domain Validation Error",
        detail=str(exc),
        path=request.url.path,
    )


# ─── ثبت مسیرهای API v1 ─────────────────────────────────────────

app.include_router(auth_routes.router, prefix="/api/v1")
app.include_router(users_routes.router, prefix="/api/v1")
app.include_router(jobs_routes.router, prefix="/api/v1")
app.include_router(qc_routes.router, prefix="/api/v1")
app.include_router(prompts_routes.router, prefix="/api/v1")
app.include_router(apis_routes.router, prefix="/api/v1")
app.include_router(admin_routes.router, prefix="/api/v1")


@app.get("/health", tags=["Health"])

async def health_check():
    return {"status": "ok", "app": "PolpoT REST API", "version": "1.0.0"}
