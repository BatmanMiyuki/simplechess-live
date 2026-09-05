"""
SimpleChess Leaderboard API — API non officielle

Expose le classement en direct de SimpleChess (Europe Echecs) pour les modes
bullet, blitz, rapid (standard), chess960 et puzzle battle, en consommant
l'API publique de la zone de jeu https://api.echecs.com (endpoint
public/liveplay/top, sans authentification requise).

⚠️ Projet non officiel, à but informatif. Ne pas abuser de l'API amont
(voir README.md). Les données amont ne changent qu'environ toutes les
60 minutes ("refreshDelayMin"), un cache local + rafraîchissement manuel
sont donc prévus.

Lancer :  uvicorn main:app --host 0.0.0.0 --port 8000
Docs   :  http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UPSTREAM_BASE = "https://api.echecs.com"

# Headers requis par l'API amont (identité "app web" de la zone de jeu).
UPSTREAM_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "App-Lang": "EN",
    "App-Site": "SIM",
    "App-Platform": "website",
    "App-Name": "simplechessWeb",
    "App-Version": "5.5.41",
    "App-Impl": "1",
}

# Modes supportés (param `type` de l'API amont) -> libellés publics.
MODES: dict[str, dict[str, str]] = {
    "bullet":     {"id": "bullet",     "label": "Bullet",     "icon": "🚀"},
    "blitz":      {"id": "blitz",      "label": "Blitz",      "icon": "⚡"},
    "standard":   {"id": "standard",   "label": "Rapid",      "icon": "🕒"},
    "chess960":   {"id": "chess960",   "label": "Chess960",   "icon": "🎲"},
    "puzzlesbatl": {"id": "puzzlesbatl", "label": "Puzzle Battle", "icon": "🧩"},
}

# Alias acceptés dans les URLs (rapid -> standard, 960 -> chess960, ...).
MODE_ALIASES = {
    "rapid": "standard", "r": "standard", "s": "standard", "std": "standard",
    "960": "chess960", "f": "chess960", "fischerrandom": "chess960",
    "lb": "bullet", "l": "bullet", "b": "blitz", "puzzle": "puzzlesbatl",
    "p": "puzzlesbatl", "puzzlesbatle": "puzzlesbatl",
}

# Type tel que l'API amont le nomme pour chaque mode (param `type` de /top).
UPSTREAM_TYPE = {
    "bullet": "bullet",
    "blitz": "blitz",
    "standard": "standard",
    "chess960": "960",       # "chess960" renvoie une liste vide côté amont !
    "puzzlesbatl": "puzzlesbatl",
}


def resolve_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode in MODES:
        return mode
    if mode in MODE_ALIASES:
        return MODE_ALIASES[mode]
    raise HTTPException(
        status_code=404,
        detail=f"Mode inconnu '{mode}'. Modes disponibles : {', '.join(MODES)}",
    )


# ---------------------------------------------------------------------------
# Client amont + cache (in-memory, single-flight)
# ---------------------------------------------------------------------------

class UpstreamClient:
    """Client HTTP vers api.echecs.com avec cache TTL et anti-stampede."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=UPSTREAM_BASE,
            headers=UPSTREAM_HEADERS,
            timeout=httpx.Timeout(15.0),
        )
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # anti-abus : fréquence minimale entre deux fetch forcés d'une même clé
        self._last_forced: dict[str, float] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def post(
        self,
        path: str,
        body: dict,
        *,
        ttl: float = 60.0,
        force: bool = False,
        key: Optional[str] = None,
    ) -> dict:
        """POST JSON vers l'amont, avec cache TTL (secondes) et single-flight."""
        key = key or f"{path}:{sorted(body.items())}"

        if not force:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]

        # single-flight : une seule requête amont à la fois par clé
        async with self._lock_for(key):
            hit = self._cache.get(key)
            if not force and hit and time.time() - hit[0] < ttl:
                return hit[1]

            if force:
                last = self._last_forced.get(key, 0.0)
                if time.time() - last < 10.0:
                    raise HTTPException(
                        status_code=429,
                        detail="Rafraîchissement forcé trop fréquent "
                               "(min. 10 s entre deux forcages).",
                    )
                self._last_forced[key] = time.time()

            try:
                resp = await self._client.post(path, json=body)
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"API amont injoignable : {exc.__class__.__name__}",
                ) from exc

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"API amont a répondu HTTP {resp.status_code}",
                )

            data = resp.json()
            if not data.get("success"):
                err = data.get("error") or {}
                err_text = str(err.get("text", "")) if isinstance(err, dict) else str(err)
                # L'amont signale notamment 'player_not_found' -> 404 propre
                if "not_found" in err_text.lower():
                    raise HTTPException(status_code=404, detail=err_text)
                raise HTTPException(
                    status_code=502,
                    detail=f"Erreur API amont : {err_text or data}",
                )

            self._cache[key] = (time.time(), data)
            return data

    async def close(self) -> None:
        await self._client.aclose()


upstream = UpstreamClient()

app = FastAPI(
    title="SimpleChess Leaderboard API",
    description=(
        "API non officielle du classement en direct SimpleChess (Europe Echecs), "
        "par mode de jeu : bullet, blitz, rapid, chess960, puzzle battle."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def _shutdown() -> None:  # pragma: no cover
    await upstream.close()


# ---------------------------------------------------------------------------
# Helpers de transformation
# ---------------------------------------------------------------------------

LEADERBOARD_FIELDS = [
    "username", "title", "country", "rank",
    "elo", "eloBest", "wins", "losses", "draws", "total",
]


def parse_flat_list(flat: dict) -> list[dict]:
    """Transforme la flatList amont {fields, values} en objets JSON."""
    fields = flat.get("fields", [])
    out = []
    for row in flat.get("values", []):
        out.append(dict(zip(fields, row)))
    return out


def to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def to_str(value) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["Système"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "simplechess-leaderboard-api",
        "time": int(time.time()),
        "modes": list(MODES),
    }


@app.get("/api/modes", tags=["Système"])
async def modes() -> dict:
    return {"modes": list(MODES.values()), "count": len(MODES)}


@app.get("/api/leaderboard/{mode}", tags=["Classement"])
async def leaderboard(
    mode: str,
    limit: int = Query(100, ge=1, le=100, description="Nombre de joueurs à renvoyer"),
    country: Optional[str] = Query(None, description="Filtrer par code pays (ex. FRA)"),
    search: Optional[str] = Query(None, description="Filtrer par pseudo (insensible à la casse)"),
    refresh: bool = Query(False, description="Forcer le rafraîchissement de l'API amont"),
) -> JSONResponse:
    mode_id = resolve_mode(mode)

    raw = await upstream.post(
        "/public/liveplay/top",
        {"type": UPSTREAM_TYPE[mode_id]},
        ttl=60.0,
        force=refresh,
        key=f"top:{mode_id}",
    )

    data = raw.get("data", {})
    flat = data.get("flatList", {})
    players = parse_flat_list(flat)

    refresh_delay_min = None
    if data.get("0") == "refreshDelayMin":
        refresh_delay_min = to_int(data.get("1"))

    # filtres
    if country:
        players = [p for p in players if p.get("country", "").upper() == country.upper()]
    if search:
        needle = search.strip().lower()
        players = [p for p in players if needle in to_str(p.get("username")).lower()]

    limit = min(limit, len(players))
    top = players[:limit]

    return JSONResponse({
        "mode": mode_id,
        "mode_label": MODES[mode_id]["label"],
        "generated_at": int(time.time()),
        "upstream_refresh_delay_min": refresh_delay_min,
        "count": len(top),
        "source": UPSTREAM_BASE,
        "unofficial": True,
        "top": [
            {
                "rank": to_int(p.get("rank")),
                "username": to_str(p.get("username")),
                "title": to_str(p.get("title")),
                "country": to_str(p.get("country")),
                "elo": to_int(p.get("elo")),
                "elo_best": to_int(p.get("eloBest")),
                "wins": to_int(p.get("wins")),
                "losses": to_int(p.get("losses")),
                "draws": to_int(p.get("draws")),
                "games": to_int(p.get("total")),
            }
            for p in top
        ],
    })


@app.get("/api/player/{username}", tags=["Joueur"])
async def player(
    username: str,
    refresh: bool = Query(False, description="Forcer le rafraîchissement"),
) -> JSONResponse:
    uname = username.strip()
    if not uname:
        raise HTTPException(status_code=400, detail="Pseudo vide.")

    profile_raw = await upstream.post(
        "/public/player/profile/get",
        {"username": uname},
        ttl=300.0,
        force=refresh,
        key=f"profile:{uname.lower()}",
    )
    profile = profile_raw.get("data", {})

    # Si le joueur n'existe pas, l'amont renvoie un objet quasi vide.
    if not profile.get("username"):
        raise HTTPException(status_code=404, detail=f"Joueur '{uname}' introuvable.")

    ratings_raw = await upstream.post(
        "/public/player/profile/stats/ratings",
        {"username": uname},
        ttl=300.0,
        force=refresh,
        key=f"ratings:{uname.lower()}",
    )
    flat = ratings_raw.get("data", {}).get("flatList", {})
    ratings_rows = parse_flat_list(flat)

    ratings = []
    for r in ratings_rows:
        ratings.append({
            "type": to_str(r.get("type")),
            "elo": to_int(r.get("elo")),
            "elo_best": to_int(r.get("eloBest")),
            "rank": to_int(r.get("rank")),
            "rd": to_float(r.get("rd")),
            "games": to_int(r.get("games")),
            "wins": to_int(r.get("wins")),
            "losses": to_int(r.get("losses")),
            "draws": to_int(r.get("draws")),
        })

    return JSONResponse({
        "username": to_str(profile.get("username")),
        "country": to_str(profile.get("country")),
        "seniority": to_int(profile.get("seniority")),
        "xp_level": to_int(profile.get("xpLevel")),
        "introduction": to_str(profile.get("introduction")),
        "registration_date": to_str(profile.get("registrationDate")),
        "last_connect_date": to_str(profile.get("lastConnectDate")),
        "avatar_url": f"{UPSTREAM_BASE}/public/player/avatar/{uname}",
        "ratings": ratings,
    })


@app.get("/api/player/{username}/rating/{mode}", tags=["Joueur"])
async def player_rating(username: str, mode: str) -> JSONResponse:
    data = await player(username)
    payload = json.loads(data.body)
    mode_id = resolve_mode(mode)
    # Type tel que l'API amont le nomme dans les ratings du joueur
    upstream_type = UPSTREAM_TYPE[mode_id]
    for r in payload["ratings"]:
        if r["type"] == upstream_type:
            return JSONResponse({"username": payload["username"], "mode": mode_id, **r})
    raise HTTPException(
        status_code=404,
        detail=f"Pas de classement {mode_id} pour {payload['username']}.",
    )


# ---------------------------------------------------------------------------
# Page d'accueil + fichiers PWA (installable : manifest, service worker, icônes)
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse("index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse("manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse("sw.js", media_type="application/javascript")


@app.get("/config.js", include_in_schema=False)
async def config() -> FileResponse:
    return FileResponse("config.js", media_type="application/javascript")


from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount("/static", StaticFiles(directory="static"), name="static")
