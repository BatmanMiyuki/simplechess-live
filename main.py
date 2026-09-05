"""
ChessLive API — API non officielle des classements échecs en direct

Deux fournisseurs :
  1. SimpleChess  (Europe Echecs)   — REST  https://api.echecs.com
  2. SocialChess  (Woodchop S/W)    — WS    wss://api.socialchess.com?x=ws
     protocole : frame binaire "\x00" + JSON, commandes "usersByRank",
     "getUser" (identif. "anonymous":true, "build", "version", "wsId"...)

⚠️ Projet non officiel, à but informatif. Ne pas abuser des API amont.
Les données SimpleChess ne changent que ~toutes les 60 min → cache local.

La PWA (index.html) fonctionne en statique sans ce backend (mode "amont").
Ce backend sert le mode "api", le cache serveur et la doc Swagger.

Lancer :  uvicorn main:app --host 0.0.0.0 --port 8000
Docs   :  http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
import websockets
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
# SocialChess — client WebSocket (frame binaire "\x00" + JSON)
# ---------------------------------------------------------------------------

SC_WS_URL = "wss://api.socialchess.com?x=ws"
SC_WS_BASE = {
    "anonymous": "true",
    "build": "856",
    "version": "2026.04.2.856.web",
    "deviceId": "chesslive-proxy",
    "language": "fr",
    "platform": "web",
}
# Catégories de classement SocialChess (7 modes)
SOCIAL_TYPES = ["Bullet", "Blitz", "Rapid", "Chess960", "Classical", "Fast", "Slow"]


class SocialChessClient:
    """Connexion WS persistante + requêtes {command, wsId} avec ping préalable."""

    def __init__(self) -> None:
        self._ws = None
        self._ws_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._listener: Optional[asyncio.Task] = None

    async def ensure(self) -> None:
        if self._ws is not None:
            return
        self._ws = await websockets.connect(SC_WS_URL, max_size=16 * 2**20)
        self._listener = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if not raw or raw == ".":
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                wid = msg.get("wsId")
                if wid in self._pending:
                    fut = self._pending.pop(wid)
                    if not fut.done():
                        fut.set_result(msg)
        except Exception:
            pass
        finally:
            self._ws = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"error": "closed"})
            self._pending.clear()
            self._listener = None

    async def request(self, command: str, extra: Optional[dict] = None,
                      timeout: float = 12.0) -> dict:
        async with self._lock:
            await self.ensure()
            self._ws_id += 1
            wid = self._ws_id
            payload = {"command": command, "wsId": wid, **SC_WS_BASE, **(extra or {})}
            frame = ("\x00" + json.dumps(payload)).encode("utf-8")
            fut = asyncio.get_event_loop().create_future()
            self._pending[wid] = fut
            try:
                await self._ws.send(frame)
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                self._pending.pop(wid, None)
                return {"error": "timeout"}
            finally:
                self._pending.pop(wid, None) if fut.done() else None

    async def ask(self, command: str, extra: Optional[dict] = None) -> dict:
        # le serveur exige un "ping" avant la commande
        await self.request("ping")
        return await self.request(command, extra)

    async def leaderboard(self, category: str) -> list[dict]:
        cat = category if category in SOCIAL_TYPES else "Rapid"
        j = await self.ask("usersByRank", {"nearElo": "3000", "ascending": "false", "category": cat})
        if j.get("error") or "usersByRank" not in j:
            raise HTTPException(status_code=502, detail=f"SocialChess : {j.get('error') or 'réponse vide'}")
        users = j["usersByRank"] or []
        rows = []
        for i, u in enumerate(users):
            s = u.get("stats" + cat) or {}
            rows.append({
                "rank": int(s.get("rank") or i + 1),
                "username": u.get("username") or u.get("id"),
                "title": "",
                "country": u.get("countryCode") or "",
                "elo": int(s.get("e") or 0),
                "elo_best": int(s.get("he") or 0),
                "wins": int(s.get("w") or 0),
                "losses": int(s.get("l") or 0),
                "draws": int(s.get("d") or 0),
                "games": int(s.get("w") or 0) + int(s.get("l") or 0) + int(s.get("d") or 0),
                "_id": u.get("id"),
                "_raw": u,
            })
        return rows

    async def user(self, user_id: str) -> dict:
        j = await self.ask("getUser", {"getUserIds": [user_id], "includeStats": "true"})
        users = (j or {}).get("users") or []
        if not users:
            raise HTTPException(status_code=404, detail="Joueur SocialChess introuvable")
        return users[0]


social_client = SocialChessClient()


def social_normalize(user: dict) -> dict:
    """Profil SocialChess → forme commune JSON."""
    rated = ["Fast", "Slow", "Bullet", "Blitz", "Rapid", "Chess960", "Classical"]
    ratings = []
    for cat in rated:
        s = user.get("stats" + cat) or {}
        if s.get("e") is None:
            continue
        ratings.append({
            "type": {"Fast": "fast", "Slow": "slow"}.get(cat, cat.lower()),
            "elo": int(s.get("e") or 0),
            "elo_best": int(s.get("he") or 0),
            "rank": int(s.get("rank") or 0),
            "games": int(s.get("w") or 0) + int(s.get("l") or 0) + int(s.get("d") or 0),
            "wins": int(s.get("w") or 0),
            "losses": int(s.get("l") or 0),
            "draws": int(s.get("d") or 0),
        })
    return {
        "username": user.get("username") or user.get("id"),
        "country": user.get("countryCode") or "",
        "city": user.get("city") or "",
        "created": (user.get("created") or "")[:10],
        "avatar_url": ("https://s3.amazonaws.com/chess-profile-images/lrg-"
                       + user["profileImageName"]) if user.get("profileImageName") else None,
        "ratings": ratings,
    }


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
    title="ChessLive API — Classements échecs en direct",
    description=(
        "API non officielle des classements en direct SimpleChess et SocialChess : "
        "bullet, blitz, rapide, chess960, classique… + profils joueurs et "
        "historique d'évolution Elo (SimpleChess)."
    ),
    version="2.0.0",
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
    provider: str = Query("simplechess", description="'simplechess' ou 'socialchess'"),
    limit: int = Query(100, ge=1, le=100, description="Nombre de joueurs à renvoyer"),
    country: Optional[str] = Query(None, description="Filtrer par code pays (ex. FRA / FR)"),
    search: Optional[str] = Query(None, description="Filtrer par pseudo (insensible à la casse)"),
    refresh: bool = Query(False, description="Forcer le rafraîchissement de l'API amont"),
) -> JSONResponse:
    provider = provider.lower()
    if provider not in ("simplechess", "socialchess"):
        raise HTTPException(status_code=404, detail="Provider inconnu (simplechess|socialchess)")

    refresh_delay_min = None
    source = UPSTREAM_BASE if provider == "simplechess" else SC_WS_URL
    players = []

    if provider == "simplechess":
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
        refresh_delay_min = to_int(data.get("1")) if data.get("0") == "refreshDelayMin" else None
        clean = [
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
            for p in players
        ]
        # filtres
        if country:
            clean = [p for p in clean if p["country"].upper() == country.upper()]
        if search:
            needle = search.strip().lower()
            clean = [p for p in clean if needle in p["username"].lower()]
        top, mode_label = clean[:limit], MODES[mode_id]["label"]

    else:  # socialchess
        if mode not in SOCIAL_TYPES and mode not in [m.lower() for m in SOCIAL_TYPES]:
            raise HTTPException(status_code=404,
                                detail="Catégorie inconnue. Choisir parmi : " + ", ".join(SOCIAL_TYPES))
        cat = next((c for c in SOCIAL_TYPES if c.lower() == mode.lower()), mode)
        key = f"sctop:{cat}"
        rows = await social_client.leaderboard(cat)
        clean = [
            {
                "rank": r["rank"],
                "username": r["username"],
                "title": r["title"],
                "country": r["country"],
                "elo": r["elo"],
                "elo_best": r["elo_best"],
                "wins": r["wins"],
                "losses": r["losses"],
                "draws": r["draws"],
                "games": r["games"],
                "_id": r["_id"],
            }
            for r in rows
        ]
        if country:
            clean = [p for p in clean if p["country"].upper() == country.upper()]
        if search:
            needle = search.strip().lower()
            clean = [p for p in clean if needle in p["username"].lower()]
        top, mode_label = clean[:limit], cat

    return JSONResponse({
        "provider": provider,
        "mode": mode,
        "mode_label": mode_label,
        "generated_at": int(time.time()),
        "upstream_refresh_delay_min": refresh_delay_min,
        "count": len(top),
        "source": source,
        "unofficial": True,
        "top": top,
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


@app.get("/api/player/{username}/history/{mode}", tags=["Joueur"])
async def player_history(username: str, mode: str) -> JSONResponse:
    """Évolution Elo (SimpleChess) : graphique mensuel, période 1Y."""
    mode_id = resolve_mode(mode)
    uname = username.strip()
    raw = await upstream.post(
        "/public/player/profile/stats/advanced",
        {"username": uname, "type": UPSTREAM_TYPE[mode_id], "period": "1Y",
         "withEloChart": True, "withOpeningsChart": False, "withOpeningsV2": False},
        ttl=600.0,
        key=f"history:{uname.lower()}:{mode_id}",
    )
    data = raw.get("data") or {}
    chart = data.get("eloChartData") or {}
    stats = data.get("stats") or {}
    return JSONResponse({
        "username": uname,
        "mode": mode_id,
        "period": "1Y",
        "labels": chart.get("labels") or [],
        "values": [to_int(v) for v in (chart.get("values") or [])],
        "trend": data.get("eloTrend") or "",
        "games": to_int(stats.get("games")),
        "wins": to_int(stats.get("wins")),
        "losses": to_int(stats.get("losses")),
        "draws": to_int(stats.get("draws")),
        "elo_min": to_int(stats.get("eloMin")),
        "elo_max": to_int(stats.get("eloMax")),
        "opp_elo_avg": to_float(stats.get("oppEloAvg")),
    })


@app.get("/api/player/social/{user_id}", tags=["Joueur"])
async def social_player(user_id: str) -> JSONResponse:
    """Profil SocialChess via WebSocket (getUser + includeStats)."""
    user = await social_client.user(user_id)
    return JSONResponse(social_normalize(user))

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
