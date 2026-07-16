"""FastAPI application for phi.

Serves:
- API endpoints under /api/* and /health (consumed by both the SvelteKit
  frontend and external automations)
- The SvelteKit static build mounted at / as a SPA (with fallback to
  index.html so client-side routes work). The frontend lives in
  bot/web/, builds to bot/web/build/, and is copied into the docker
  image at /app/web/.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import logfire
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from bot.config import settings
from bot.core.atlas import get_atlas
from bot.core.atproto_client import bot_client
from bot.core.discovery_pool import get_filtered_pool
from bot.core.docket import get_docket
from bot.core.profile_manager import ProfileManager
from bot.logging_config import _clear_uvicorn_handlers
from bot.memory import NamespaceMemory
from bot.services.notification_poller import NotificationPoller
from bot.status import bot_status
from bot.ui import activity_router

logger = logging.getLogger("bot.main")

logfire.configure(
    send_to_logfire=settings.logfire.send_to_logfire,
    environment=settings.logfire.environment,
    token=settings.logfire.write_token,
    console=logfire.ConsoleOptions(
        min_log_level="debug" if settings.debug else "info",
    ),
)

# instrument the interesting stuff — skip httpx (poll noise) since
# anthropic/openai integrations already trace their own HTTP calls.
# each call is wrapped individually so a missing dep degrades to a no-op.
for _instrument in (
    logfire.instrument_pydantic_ai,
    logfire.instrument_anthropic,
    logfire.instrument_openai,
):
    try:
        _instrument()
    except Exception as _e:
        logger.warning(f"logfire instrumentation failed ({_instrument.__name__}): {_e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    _clear_uvicorn_handlers()  # uvicorn re-installs handlers on startup
    logger.info(f"starting phi as @{settings.bluesky_handle}")

    await bot_client.authenticate()

    profile_manager = ProfileManager(bot_client.client)
    await profile_manager.initialize()
    app.state.profile_manager = profile_manager

    # Start notification polling — needed before the bio rewrite because the
    # poller owns the PhiAgent instance we call process_bio on.
    poller = NotificationPoller(bot_client)
    app.state.poller = poller
    await poller.start()

    # Phi rewrites her own bio at every startup. Best-effort — if the bio
    # call fails (rate limit, model error, etc), fall back to the existing
    # online-suffix flow rather than blocking startup on it.
    try:
        await poller.handler.agent.process_bio()
    except Exception as e:
        logger.warning(f"bio rewrite at startup failed: {e}; falling back to suffix")
        await profile_manager.set_online_status(True)

    logger.info("phi is online, listening for mentions")

    yield

    logger.info("shutting down phi")
    await poller.stop()

    # Set offline status
    await profile_manager.set_online_status(False)

    logger.info("phi shutdown complete")


limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title=settings.bot_name,
    description="phi: a bluesky bot with episodic memory",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda request, exc: JSONResponse(
        status_code=429,
        content={"error": "rate limit exceeded", "detail": str(exc)},
    ),
)

try:
    logfire.instrument_fastapi(app, excluded_urls="/health")
except Exception as _e:
    logger.warning(f"logfire fastapi instrumentation failed: {_e}")

app.include_router(activity_router)


@app.get("/health")
async def health():
    """Health check endpoint — also consumed by the frontend's status pill."""
    return {
        "status": "healthy",
        "polling_active": bot_status.polling_active,
        "paused": bot_status.paused,
    }


def _check_control_token(request: Request):
    """Validate bearer token for control endpoints."""
    if not settings.control_token:
        return JSONResponse({"error": "control token not configured"}, status_code=503)
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {settings.control_token}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


@app.post("/api/control/pause")
async def pause(request: Request):
    """Pause notification processing. Unread notifications accumulate until resumed."""
    if err := _check_control_token(request):
        return err
    bot_status.record_paused()
    logger.info("paused via API")
    if pm := getattr(app.state, "profile_manager", None):
        await pm.set_online_status(False)
    return {"paused": True}


@app.post("/api/control/resume")
async def resume(request: Request):
    """Resume notification processing. Queued notifications will be processed on next poll."""
    if err := _check_control_token(request):
        return err
    bot_status.record_resumed()
    logger.info("resumed via API")
    if pm := getattr(app.state, "profile_manager", None):
        await pm.set_online_status(True)
    return {"paused": False}


# Named scheduled passes an external scheduler (prefect) can kick off. The
# bot exposes WHAT can run; prefect owns WHEN — a new scheduled behavior
# should be a prefect deployment hitting this endpoint, not a poller slot.
_TRIGGER_SLOTS = {
    "cycle": lambda handler: handler.cycle,
    "reflection": lambda handler: handler.daily_reflection,
    "chicken-precheck": lambda handler: handler.chicken_precheck,
    "chicken-scout": lambda handler: handler.chicken_scout,
    "curation": lambda handler: handler.curation,
    "editorial": lambda handler: handler.editorial,
    "character-retro": lambda handler: handler.character_retro,
}


@app.post("/api/control/trigger/{slot}")
async def trigger_slot(slot: str, request: Request, background_tasks: BackgroundTasks):
    """Run a named scheduled pass in the background (bearer control token)."""
    if err := _check_control_token(request):
        return err
    slot_fn = _TRIGGER_SLOTS.get(slot)
    if slot_fn is None:
        return JSONResponse(
            {"error": f"unknown slot {slot!r}", "slots": sorted(_TRIGGER_SLOTS)},
            status_code=404,
        )
    poller: NotificationPoller | None = getattr(app.state, "poller", None)
    if not poller:
        return JSONResponse({"error": "poller not available"}, status_code=503)
    background_tasks.add_task(slot_fn(poller.handler))
    logger.info(f"{slot} triggered via API")
    return {"triggered": slot}


@app.post("/api/control/post")
async def trigger_post(request: Request, background_tasks: BackgroundTasks):
    """Trigger one cognitive cycle immediately (legacy alias for trigger/cycle)."""
    return await trigger_slot("cycle", request, background_tasks)


_abilities_cache: list | None = None


@app.get("/api/abilities")
async def abilities():
    """Phi's currently-registered function-tools — name, docstring, and
    whether owner-gated. Pulled live from `PhiAgent.get_capabilities()`,
    which introspects `agent._function_toolset.tools`.

    Cached for the process lifetime: tools are registered at startup and
    don't change without a restart, so re-introspecting per request is
    pointless work.
    """
    global _abilities_cache
    if _abilities_cache is not None:
        return JSONResponse(_abilities_cache)
    poller = getattr(app.state, "poller", None)
    if poller is None:
        return JSONResponse({"error": "agent not ready"}, status_code=503)
    try:
        _abilities_cache = poller.handler.agent.get_capabilities()
        return JSONResponse(_abilities_cache)
    except Exception as e:
        logger.warning(f"abilities introspection failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


_skills_cache: list | None = None


@app.get("/api/skills")
async def skills():
    """Phi's installed skill packages — load-on-demand domain knowledge.

    Walks `settings.skills_dir`, parses each `SKILL.md`'s frontmatter for
    name + description, lists sibling `.md` files as resources. Cached for
    process lifetime; skills register at startup like tools.
    """
    global _skills_cache
    if _skills_cache is not None:
        return JSONResponse(_skills_cache)
    import re

    base = Path(settings.skills_dir)
    if not base.exists():
        _skills_cache = []
        return JSONResponse(_skills_cache)

    out: list[dict] = []
    front_re = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
    name_re = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
    desc_re = re.compile(
        r"^description:\s*(.+?)(?=\n\w+:|\Z)", re.MULTILINE | re.DOTALL
    )

    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text()
        except Exception:
            continue
        m = front_re.match(content)
        if not m:
            continue
        front = m.group(1)
        name_m = name_re.search(front)
        desc_m = desc_re.search(front)
        name = name_m.group(1).strip() if name_m else entry.name
        description = " ".join(desc_m.group(1).split()).strip() if desc_m else ""
        resources = sorted(p.name for p in entry.iterdir() if p.suffix == ".md")
        out.append(
            {
                "name": name,
                "description": description,
                "resources": resources,
            }
        )
    _skills_cache = out
    return JSONResponse(out)


_user_view_cache: dict[str, tuple[float, dict]] = {}
_USER_VIEW_TTL = 60  # seconds


@app.get("/api/users/{handle}")
async def user_view(handle: str):
    """What phi currently carries about a person — pure read of state.

    Joins per-kind counts, the synthesized relationship summary (written by
    the compact flow in my-prefect-server), and the most recent atomic
    observations. No embedding, no LLM, no fabrication — every field is a
    direct read of rows in the user's tpuf namespace.
    """
    now = time.monotonic()
    cached = _user_view_cache.get(handle)
    if cached and now < cached[0]:
        return JSONResponse(cached[1])

    poller: NotificationPoller | None = getattr(app.state, "poller", None)
    if poller is None:
        return JSONResponse({"error": "agent not ready"}, status_code=503)
    memory = poller.handler.agent.memory
    if memory is None:
        return JSONResponse({"error": "memory not configured"}, status_code=503)

    # Resolve DID — best effort; not all handles resolve (deleted accounts,
    # bridged-from-mastodon, etc). Endpoint still returns useful state without it.
    did: str | None = None
    try:
        await bot_client.authenticate()
        profile = bot_client.client.app.bsky.actor.get_profile(params={"actor": handle})
        did = profile.did
    except Exception:
        pass

    user_ns = memory.get_user_namespace(handle)

    # Per-kind counts. top_k=200 is enough margin for the UI signal —
    # most users have far fewer of each kind, and >200 of any one kind
    # renders the same way visually anyway.
    counts: dict[str, int] = {"observation": 0, "interaction": 0, "summary": 0}
    for kind in counts:
        active_filter = (
            ["And", [["kind", "Eq", kind], ["status", "NotEq", "superseded"]]]
            if kind == "observation"
            else {"kind": ["Eq", kind]}
        )
        try:
            resp = user_ns.query(
                rank_by=("created_at", "desc"),
                top_k=200,
                filters=active_filter,
                include_attributes=["kind"],
            )
            counts[kind] = len(resp.rows or [])
        except Exception:
            pass  # namespace may not exist yet; counts stay 0

    # first_seen / last_seen across all kinds.
    first_seen: str | None = None
    last_seen: str | None = None
    try:
        latest = user_ns.query(
            rank_by=("created_at", "desc"),
            top_k=1,
            include_attributes=["created_at"],
        )
        if latest.rows:
            last_seen = getattr(latest.rows[0], "created_at", None)
        earliest = user_ns.query(
            rank_by=("created_at", "asc"),
            top_k=1,
            include_attributes=["created_at"],
        )
        if earliest.rows:
            first_seen = getattr(earliest.rows[0], "created_at", None)
    except Exception:
        pass

    # Summary — text + its created_at (one query, since we want both).
    summary_obj: dict | None = None
    try:
        summary_resp = user_ns.query(
            rank_by=("created_at", "desc"),
            top_k=1,
            filters={"kind": ["Eq", "summary"]},
            include_attributes=["content", "created_at"],
        )
        if summary_resp.rows:
            row = summary_resp.rows[0]
            summary_obj = {
                "content": getattr(row, "content", ""),
                "created_at": getattr(row, "created_at", None),
            }
    except Exception:
        pass

    # Recent observations — content, tags, created_at, source_uris.
    recent_observations: list[dict] = []
    try:
        obs_resp = user_ns.query(
            rank_by=("created_at", "desc"),
            top_k=5,
            filters=[
                "And",
                [["kind", "Eq", "observation"], ["status", "NotEq", "superseded"]],
            ],
            include_attributes=["content", "tags", "created_at", "source_uris"],
        )
        for row in obs_resp.rows or []:
            recent_observations.append(
                {
                    "content": getattr(row, "content", ""),
                    "tags": getattr(row, "tags", []) or [],
                    "created_at": getattr(row, "created_at", None),
                    "source_uris": getattr(row, "source_uris", []) or [],
                }
            )
    except Exception:
        pass

    is_stranger = await memory.is_stranger(handle)

    payload = {
        "handle": handle,
        "did": did,
        "is_stranger": is_stranger,
        "counts": counts,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "summary": summary_obj,
        "recent_observations": recent_observations,
    }
    _user_view_cache[handle] = (now + _USER_VIEW_TTL, payload)
    return JSONResponse(payload)


_discovery_cache_data: list | None = None
_discovery_cache_expires: float = 0.0
_DISCOVERY_CACHE_TTL = 60  # seconds


@app.get("/api/discovery")
async def discovery():
    """Discovery pool — filtered to what phi actually sees in her prompt.

    Joins the upstream operator-likes pool (hub) with phi's per-author
    interaction state. The frontend reads this so the public page stays
    aligned with phi's view; previously it called hub directly and showed
    a different (raw) list than the one phi was reasoning over.
    """
    global _discovery_cache_data, _discovery_cache_expires
    now = time.monotonic()
    if _discovery_cache_data is not None and now < _discovery_cache_expires:
        return JSONResponse(_discovery_cache_data)

    memory: NamespaceMemory | None = None
    if settings.turbopuffer_api_key and settings.openai_api_key:
        try:
            memory = NamespaceMemory(api_key=settings.turbopuffer_api_key)
        except Exception as e:
            logger.debug(f"discovery: memory client init failed: {e}")

    try:
        entries = await get_filtered_pool(memory)
    except Exception as e:
        logger.warning(f"discovery: get_filtered_pool failed: {e}")
        return JSONResponse([], status_code=200)

    _discovery_cache_data = entries
    _discovery_cache_expires = now + _DISCOVERY_CACHE_TTL
    return JSONResponse(entries)


_chicken_cache: dict[str, tuple[float, dict | list]] = {}
_CHICKEN_CACHE_TTL = 60  # seconds
_CHICKEN_API = "https://topchicken.cee.wtf/api"
_CHICKEN_PATHS = {
    "trader": lambda: f"trader/{bot_client.client.me.did}"
    if bot_client.client.me
    else None,
    "market": lambda: "market",
    "results": lambda: "results",
}


@app.get("/api/chicken/{name}")
async def chicken(name: str):
    """Read-only proxy for phi's top chicken market surface.

    topchicken.cee.wtf serves no CORS headers, so the /market page can't
    fetch it from the browser — these three whitelisted reads pass through
    here instead (60s cache). trader is pinned to phi's own DID.
    """
    make_path = _CHICKEN_PATHS.get(name)
    if make_path is None:
        return JSONResponse({"error": "unknown resource"}, status_code=404)
    path = make_path()
    if path is None:
        return JSONResponse({"error": "not ready"}, status_code=503)

    now = time.monotonic()
    cached = _chicken_cache.get(name)
    if cached and now < cached[0]:
        return JSONResponse(cached[1])

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(f"{_CHICKEN_API}/{path}")
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": "upstream"}, status_code=e.response.status_code)
    except Exception as e:
        logger.warning(f"chicken proxy {name} failed: {e}")
        return JSONResponse({"error": "unreachable"}, status_code=502)

    _chicken_cache[name] = (now + _CHICKEN_CACHE_TTL, data)
    return JSONResponse(data)


@app.get("/api/atlas")
async def atlas():
    """phi's atlas — daily 2D map of every PDS record + TurboPuffer row,
    written by the phi-atlas Prefect flow. The fetch is cached in-process
    by the PDS record CID, so a hot endpoint with a stale atlas reuses the
    parsed JSON; a new atlas write invalidates automatically.
    """
    try:
        data = await get_atlas()
    except Exception as e:
        logger.warning(f"atlas fetch failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)
    if data is None:
        return JSONResponse({"error": "no atlas record on PDS yet"}, status_code=404)
    return JSONResponse(data)


@app.get("/api/docket")
async def docket():
    """phi's daily promotion docket — 5-15 work-item candidates emitted by
    the `docket` Prefect flow after each atlas regeneration. Each candidate
    cites private evidence + nearby public anchors + a suggested action.
    Same record-CID-keyed cache pattern as /api/atlas.
    """
    try:
        data = await get_docket()
    except Exception as e:
        logger.warning(f"docket fetch failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)
    if data is None:
        return JSONResponse({"error": "no docket record on PDS yet"}, status_code=404)
    return JSONResponse(data)


_graph_cache_data: dict | None = None
_graph_cache_expires: float = 0.0
_GRAPH_CACHE_TTL = 60  # seconds


@app.get("/api/memory/graph")
@limiter.limit("10/minute")
async def memory_graph_data(request: Request):
    """Return graph nodes and edges as JSON."""
    global _graph_cache_data, _graph_cache_expires
    now = time.monotonic()
    if _graph_cache_data is not None and now < _graph_cache_expires:
        return JSONResponse(_graph_cache_data)

    try:
        memory = NamespaceMemory(api_key=settings.turbopuffer_api_key)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, memory.get_graph_data)
        _graph_cache_data = data
        _graph_cache_expires = now + _GRAPH_CACHE_TTL
        return JSONResponse(data)
    except Exception as e:
        logger.warning(f"memory graph failed: {e}")
        return JSONResponse(
            {"nodes": [], "edges": [], "error": str(e)}, status_code=500
        )


# --- frontend mount ---
#
# bot/web/ is a sveltekit project built with adapter-static. the build
# directory is copied into /app/web/ in the docker runtime stage. in dev,
# this directory may not exist (just run `bun run dev` separately and let
# vite proxy /api/* to the python server) — we mount conditionally so dev
# of the python side doesn't fail.
#
# routing layering:
#   1. all explicit @app.get/@app.post handlers above (api, control, health)
#   2. StaticFiles mount at "/" — serves index.html for "/" and any real
#      file under /app/web/* (assets, favicon)
#   3. 404 handler — for client-side routes (/feed, /mind, etc) that have
#      no corresponding file, falls back to index.html so the svelte
#      router takes over.
#
# the previous version registered an @app.get("/{full_path:path}") catch-all
# BEFORE the mount, which intercepted every request including JS assets and
# returned index.html with text/html content-type — browsers refuse to load
# js modules served as text/html, so the SPA never booted.

WEB_DIR = Path(settings.web_build_dir)
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    logger.info(f"frontend mounted from {WEB_DIR}")

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc):  # noqa: ARG001
        # Only fall back for browser navigation requests; api/health 404s
        # should still return JSON.
        path = request.url.path
        if path.startswith("/api/") or path == "/health":
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(WEB_DIR / "index.html")
else:
    logger.warning(
        f"frontend build not found at {WEB_DIR} — only API routes will be served"
    )
