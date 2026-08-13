import hmac
import os
import secrets

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.deps import issue_session
from app.api.v1.router import api_v1_router
from app.config import get_settings

# Optional password gate for a public deployment. Unset means the demo
# is open, which is the right default for a portfolio piece but is a
# choice worth making deliberately.
_DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "").strip()


def _ensure_signing_key() -> None:
    """Never sign sessions with the committed placeholder.

    `api_key` doubles as the cookie-signing secret. Left at its default,
    every deployed instance would sign with a string published in this
    repository, so anyone could mint a valid session. That costs nothing
    while the demo is open to everyone — there is nothing behind the gate
    — but it stops being harmless the moment DEMO_PASSWORD is set and the
    gate is assumed to hold. A hole that only opens later, when someone
    adds the very feature meant to close it, is the kind worth removing
    now.

    A random per-process key means sessions do not survive a restart.
    That is the correct trade for a demo: the cost is re-issuing a cookie
    on the next page load, which the app shell does automatically.
    """
    settings = get_settings()
    if settings.api_key_is_placeholder:
        settings.api_key = secrets.token_urlsafe(32)


def _cookies_secure(request: Request) -> bool:
    """Secure cookies over HTTPS, plain over local http.

    Hardcoding secure=True would silently break local development, where
    the browser refuses a Secure cookie on http://localhost — and the
    failure looks like "the app is randomly logged out" rather than
    anything to do with cookies.
    """
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded == "https"

app = FastAPI(
    title="DataFactZ Breach Analytics API",
    version="0.1.0",
    description="Ingest a heterogeneous breach-dump corpus, extract PII, resolve entities, and serve the exposure table.",
)

# In development the frontend runs on its own Vite server and needs CORS.
# In a single-container deployment the API serves the built frontend from
# the same origin, so no cross-origin request happens at all — which is
# also why the deployed build needs no CORS relaxation.
_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Every error is a real non-200 status with a structured body — never a
    # 200 with an error message baked into it (handbook section 6.2).
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_v1_router)


@app.on_event("startup")
def _startup() -> None:
    _ensure_signing_key()


# --- optional: serve the built frontend from the same process ---------
#
# Only mounted when FRONTEND_DIST points at a real build, so local
# development is unaffected — there the frontend is served by Vite with
# hot reload. One container serving both means no CORS, no second
# service to keep alive, and one URL to share, which is what makes a
# free single-instance host workable.
_FRONTEND_DIST = os.getenv("FRONTEND_DIST", "").strip()
if _FRONTEND_DIST and os.path.isdir(_FRONTEND_DIST):
    _assets = os.path.join(_FRONTEND_DIST, "assets")
    if os.path.isdir(_assets):
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str, request: Request):
        """Serves the SPA shell for any non-API path.

        Declared AFTER the API router so real endpoints win the match;
        this only catches what the router did not claim. Without it a
        deep link like /persons/74 returns 404 on refresh, because the
        route exists only in the browser's router.

        Serving the shell is also where the browser gets its session
        cookie, which is what lets the frontend ship with no API key in
        it at all.
        """
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)

        response = FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))
        if not _DEMO_PASSWORD:
            # Open demo: anyone who can load the page can use the API it
            # is built on. Be precise about what this is — it stops the
            # KEY leaking, it is not access control. Set DEMO_PASSWORD to
            # gate it.
            issue_session(response, get_settings(), secure=_cookies_secure(request))
        return response


@app.post("/api/v1/session", include_in_schema=False)
def open_session(request: Request, response: Response, body: dict | None = None):
    """Exchanges the demo password for a session cookie.

    Only meaningful when DEMO_PASSWORD is set; without it the shell hands
    out sessions freely and there is nothing to exchange.
    """
    if not _DEMO_PASSWORD:
        raise HTTPException(status_code=404, detail="password login is not enabled")

    supplied = (body or {}).get("password", "")
    if not hmac.compare_digest(str(supplied), _DEMO_PASSWORD):
        raise HTTPException(status_code=401, detail="incorrect password")

    issue_session(response, get_settings(), secure=_cookies_secure(request))
    return {"status": "ok"}
