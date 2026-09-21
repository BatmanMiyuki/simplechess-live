#!/usr/bin/env python3
"""
gen_public.py — Génère les pages publiques "crawlables" de ChessLive.

Objectif : être trouvé par les moteurs de recherche ET les IA (GPT, Claude,
Perplexity...) . Quand quelqu'un demande à une IA "qui est le top 1 en bullet
sur SimpleChess ?" ou "quel est l'Elo du n°1 mondial ?", l'IA avec accès web
trouve ces pages statiques et peut répondre.

Produit (commité, rafraîchi par GitHub Actions une fois par jour) :
  public/index.html                 — hub des classements
  public/top/{game}/{mode}/index.html — page SEO par classement (JSON-LD)
  public/llms-top.txt               — top 10 par mode, en texte brut (pour les IA)
  public/players.txt                — index des meilleurs joueurs (référencement)
  public/rss.xml                    — flux RSS des changements du top
  llms.txt / ai.txt / robots.txt / sitemap.xml — conventions de découverte IA/SEO

Usage : python3 gen_public.py   (nécessite httpx + websockets, voir requirements.txt)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
from pathlib import Path

import httpx
import websockets

SITE = "https://batmanmiyuki.github.io/simplechess-live"
ROOT = Path(__file__).resolve().parent
PUB = ROOT / "public"

# ------------------------- SimpleChess (REST) -------------------------
SC_UP = "https://api.echecs.com"
SC_H = {"Content-Type": "application/json", "Accept": "application/json",
        "App-Lang": "EN", "App-Site": "SIM", "App-Platform": "website",
        "App-Name": "simplechessWeb", "App-Version": "5.5.41", "App-Impl": "1"}
SC_MODES = [("bullet", "Bullet"), ("blitz", "Blitz"), ("standard", "Rapid"),
            ("chess960", "Chess960"), ("puzzlesbatl", "Puzzle Battle")]
SC_TYPE = {"bullet": "bullet", "blitz": "blitz", "standard": "standard",
           "chess960": "960", "puzzlesbatl": "puzzlesbatl"}
SC_TOTAL_NOTE = "SimpleChess ne publie pas le nombre total de joueurs classés (top 100 public)."

# ------------------------- SocialChess (WebSocket) -------------------------
SCo_WS = "wss://api.socialchess.com?x=ws"
SCo_BASE = {"anonymous": "true", "build": "856", "version": "2026.04.2.856.web",
            "deviceId": "chesslive-public", "language": "fr", "platform": "web"}
SCo_MODES = [("Bullet", "Bullet"), ("Blitz", "Blitz"), ("Rapid", "Rapid"),
             ("Chess960", "Chess960"), ("Classical", "Classical"),
             ("Fast", "Fast"), ("Slow", "Slow")]
SCo_KEY = "stats"

# ------------------- Checkmate / « Chess Online & Offline » (Splend Apps) ---
# Classement lu en LECTURE SEULE dans la base Firebase/Firestore de l'app
# (projet checkmate-17950) via une session ANONYME : même accès que l'écran
# « Classements » de l'application, aucune donnée personnelle, aucune écriture.
CM_KEY = "AIzaSyBRKClWWQ0u4Av4Ubq84-a-a3pRRU6-H70"      # clé publique embarquée dans l'APK
CM_PROJ = "checkmate-17950"
CM_FS = f"https://firestore.googleapis.com/v1/projects/{CM_PROJ}/databases/(default)/documents"
CM_RANKED = {"fieldFilter": {"field": {"fieldPath": "rankingGlobal"},
                            "op": "GREATER_THAN", "value": {"integerValue": "0"}}}
CM_NOTE = ("Classement officiel de l'app : seuls les joueurs qui portent un rang "
           "(rankingGlobal) y figurent — les comptes sans rang en sont absents, "
           "comme dans l'application.")


def _cm_dec(v):
    """Décode une valeur Firestore (REST) en Python."""
    if not v:
        return None
    for k, cast in (("stringValue", str), ("integerValue", int), ("doubleValue", float),
                    ("booleanValue", bool), ("timestampValue", str), ("referenceValue", str)):
        if k in v:
            return cast(v[k])
    if "nullValue" in v:
        return None
    if "mapValue" in v:
        return {a: _cm_dec(b) for a, b in (v["mapValue"].get("fields") or {}).items()}
    if "arrayValue" in v:
        return [_cm_dec(x) for x in (v["arrayValue"].get("values") or [])]
    return None


def _cm_doc(d):
    f = {k: _cm_dec(v) for k, v in (d.get("fields") or {}).items()}
    f["_id"] = d["name"].split("/")[-1]
    return f


def _cm_refresh_token() -> str:
    """Jeton de rafraîchissement d'une session anonyme existante, si l'on en a un.

    Priorité : $CM_REFRESH, puis $CM_SESSION (fichier JSON), puis anon.json local.
    Évite de créer une nouvelle session anonyme à chaque génération, ce que
    Firebase finit par refuser (TOO_MANY_ATTEMPTS_TRY_LATER)."""
    tok = os.environ.get("CM_REFRESH", "").strip()
    if tok:
        return tok
    cands = []
    if os.environ.get("CM_SESSION"):
        cands.append(pathlib.Path(os.environ["CM_SESSION"]))
    cands += [ROOT / "checkmate" / "anon2.json", ROOT.parent / "checkmate" / "anon2.json"]
    for p in cands:
        try:
            j = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
            if j.get("refreshToken"):
                return j["refreshToken"]
        except Exception:
            continue
    return ""


async def _cm_token(client) -> str:
    """Jeton d'accès Firestore : on rafraîchit la session existante si possible,
    sinon on crée une session anonyme (lecture seule)."""
    refresh = _cm_refresh_token()
    if refresh:
        try:
            r = await client.post(f"https://securetoken.googleapis.com/v1/token?key={CM_KEY}",
                                  data={"grant_type": "refresh_token", "refresh_token": refresh})
            r.raise_for_status()
            j = r.json()
            if j.get("id_token"):
                return j["id_token"]
        except Exception as e:      # jeton expiré/révoqué : on retombe sur une session neuve
            print(f"⚠️  Rafraîchissement de la session Checkmate impossible ({e}) — nouvelle session anonyme")
    r = await client.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={CM_KEY}",
                          json={"returnSecureToken": True})
    r.raise_for_status()
    return r.json()["idToken"]


async def _cm_run(client, tok, query) -> list:
    r = await client.post(f"{CM_FS}:runQuery", json={"structuredQuery": query},
                          headers={"Authorization": f"Bearer {tok}"})
    r.raise_for_status()
    return [_cm_doc(x["document"]) for x in r.json() if "document" in x]


async def _cm_count(client, tok, ranked=False, level_gt=None):
    sq = {"from": [{"collectionId": "users"}]}
    if ranked:
        sq["where"] = CM_RANKED
    elif level_gt is not None:
        sq["where"] = {"fieldFilter": {"field": {"fieldPath": "level"}, "op": "GREATER_THAN",
                                       "value": {"integerValue": str(level_gt)}}}
    r = await client.post(f"{CM_FS}:runAggregationQuery",
                          json={"structuredAggregationQuery": {
                              "structuredQuery": sq,
                              "aggregations": [{"count": {}, "alias": "n"}]}},
                          headers={"Authorization": f"Bearer {tok}"})
    if r.status_code != 200:
        return None
    try:
        return int(r.json()[0]["result"]["aggregateFields"]["n"]["integerValue"])
    except Exception:
        return None


async def fetch_checkmate_country(cc: str = "FR", depth: int = 100) -> dict:
    """Classement NATIONAL officiel : un appel par rang national
    (« country = XX ET rankingCountry = k ») — léger et sans index composite."""
    cc_eq = {"fieldFilter": {"field": {"fieldPath": "country"}, "op": "EQUAL",
                             "value": {"stringValue": cc}}}
    select = {"fields": [{"fieldPath": p} for p in
                         ("username", "country", "level", "rankingGlobal", "rankingCountry",
                          "stats", "puzzles")]}
    sem = asyncio.Semaphore(16)

    async with httpx.AsyncClient(timeout=60) as client:
        tok = await _cm_token(client)

        async def one(k):
            async with sem:
                try:
                    return await _cm_run(client, tok, {
                        "from": [{"collectionId": "users"}],
                        "where": {"compositeFilter": {"op": "AND", "filters": [
                            cc_eq, {"fieldFilter": {"field": {"fieldPath": "rankingCountry"},
                                                    "op": "EQUAL",
                                                    "value": {"integerValue": str(k)}}}]}},
                        "limit": 2, "select": select})
                except Exception:
                    return []
        got = await asyncio.gather(*(one(k) for k in range(1, depth + 1)))

    seen, rows = set(), []
    for lst in got:
        for f in lst:
            rc = int(f.get("rankingCountry") or 0)
            if rc <= 0 or f["_id"] in seen:
                continue
            seen.add(f["_id"])
            st, pz = f.get("stats") or {}, f.get("puzzles") or {}
            rows.append({"rank": rc, "username": f.get("username") or f["_id"],
                         "country": f.get("country") or cc, "elo": int(f.get("level") or 0),
                         "elo_best": None, "wins": int(st.get("gamesWins") or 0),
                         "losses": int(st.get("gamesLosses") or 0), "draws": int(st.get("gamesDraws") or 0),
                         "games": int(st.get("gamesAll") or 0),
                         "puzzles": int(pz.get("puzzlesPoints") or 0),
                         "rank_global": int(f.get("rankingGlobal") or 0)})
    rows.sort(key=lambda r: r["rank"])
    note = (f"Classement national officiel de l'app ({cc}) : joueurs portant un rang dans ce pays "
            f"(rang national + rang mondial). Profondeur analysée : {depth}.")
    return {"label": "France" if cc == "FR" else cc, "rows": rows, "total": None, "note": note}


async def fetch_checkmate(limit: int = 100) -> dict:
    """Top du classement OFFICIEL de l'app : joueurs classés (rang officiel),
    listés par rang. Lecture seule, session anonyme."""
    async with httpx.AsyncClient(timeout=90) as client:
        tok = await _cm_token(client)
        docs = await _cm_run(client, tok, {
            "from": [{"collectionId": "users"}],
            "where": CM_RANKED,
            "orderBy": [{"field": {"fieldPath": "rankingGlobal"}, "direction": "ASCENDING"}],
            "limit": limit})
        ranked = await _cm_count(client, tok, ranked=True)
        total = await _cm_count(client, tok)

    docs = [f for f in docs if int(f.get("rankingGlobal") or 0) > 0]
    rows = []
    for f in docs:
        st, pz = f.get("stats") or {}, f.get("puzzles") or {}
        rows.append({"rank": int(f.get("rankingGlobal") or 0), "username": f.get("username") or f["_id"],
                     "country": f.get("country") or "", "elo": int(f.get("level") or 0),
                     "elo_best": None, "wins": int(st.get("gamesWins") or 0),
                     "losses": int(st.get("gamesLosses") or 0), "draws": int(st.get("gamesDraws") or 0),
                     "games": int(st.get("gamesAll") or 0),
                     "puzzles": int(pz.get("puzzlesPoints") or 0),
                     "rank_country": int(f.get("rankingCountry") or 0)})
    note = CM_NOTE
    if ranked is not None and total is not None:
        note += (f" {ranked:,} joueurs classés sur {total:,} comptes au total."
                 .replace(",", " "))
    return {"label": "Classement (Elo)", "rows": rows, "total": ranked, "note": note}


def config_pseudos() -> tuple:
    """Pseudos suivis, lus dans config.js (My_USERNAME / MY_USERNAME_CM)."""
    try:
        txt = (ROOT / "config.js").read_text(encoding="utf-8")
    except Exception:
        return "", ""
    def grab(key):
        m = re.search(rf'{key}\s*:\s*"([^"]*)"', txt)
        return m.group(1) if m else ""
    return grab("MY_USERNAME"), grab("MY_USERNAME_CM")


async def fetch_checkmate_player(username: str):
    """Fiche d'un joueur Checkmate précis (Elo, rang mondial, rang pays)."""
    if not username:
        return None
    async with httpx.AsyncClient(timeout=45) as client:
        tok = await _cm_token(client)
        docs = await _cm_run(client, tok, {
            "from": [{"collectionId": "users"}], "limit": 10,
            "where": {"fieldFilter": {"field": {"fieldPath": "username"}, "op": "EQUAL",
                                      "value": {"stringValue": username}}}})
    if not docs:
        return None
    f = sorted(docs, key=lambda d: (not d.get("isAnonymous"), (d.get("stats") or {}).get("gamesAll") or 0),
               reverse=True)[0]
    return {"username": f.get("username"), "elo": int(f.get("level") or 0),
            "ranked": int(f.get("rankingGlobal") or 0),
            "rank": int(f.get("rankingGlobal") or 0), "rank_country": int(f.get("rankingCountry") or 0),
            "country": f.get("country") or "", "games": int((f.get("stats") or {}).get("gamesAll") or 0),
            "wins": int((f.get("stats") or {}).get("gamesWins") or 0),
            "puzzles": int((f.get("puzzles") or {}).get("puzzlesPoints") or 0)}


def esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Récupération des données
# ---------------------------------------------------------------------------

class SCoClient:
    def __init__(self):
        self.ws = None
        self.wid = 0
        self.pending: dict[int, asyncio.Future] = {}

    async def ensure(self):
        if self.ws is None:
            self.ws = await websockets.connect(SCo_WS, max_size=16 * 2**20)
            asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if not raw or raw == ".":
                    continue
                try:
                    m = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                wid = m.get("wsId")
                if wid in self.pending:
                    f = self.pending.pop(wid)
                    if not f.done():
                        f.set_result(m)
        except Exception:
            pass
        finally:
            self.ws = None

    async def ask(self, cmd, extra, timeout=15.0) -> dict:
        await self.ensure()
        out = {}
        for payload in [dict({"command": "ping"}), dict({"command": cmd, **extra})]:
            self.wid += 1
            p = {**payload, **SCo_BASE, "wsId": self.wid}
            fut = asyncio.get_event_loop().create_future()
            self.pending[self.wid] = fut
            await self.ws.send(("\x00" + json.dumps(p)).encode("utf-8"))
            try:
                out = await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                out = {"error": "timeout"}
        return out

    async def leaderboard(self, cat: str) -> list[dict]:
        j = await self.ask("usersByRank", {"nearElo": "3000", "ascending": "false", "category": cat})
        users = (j or {}).get("usersByRank") or []
        rows = []
        for i, u in enumerate(users):
            s = u.get("stats" + cat) or {}
            rows.append({
                "username": u.get("username") or u.get("id"),
                "country": u.get("countryCode") or "",
                "elo": int(s.get("e") or 0),
                "elo_best": int(s.get("he") or 0),
                "wins": int(s.get("w") or 0), "losses": int(s.get("l") or 0),
                "draws": int(s.get("d") or 0),
                "games": int(s.get("w") or 0) + int(s.get("l") or 0) + int(s.get("d") or 0),
                "rank": int(s.get("rank") or i + 1),
            })
        return rows

    async def total(self, cat: str) -> int | None:
        j = await self.ask("usersByRank", {"nearElo": "1", "ascending": "true", "category": cat})
        users = (j or {}).get("usersByRank") or []
        mx = 0
        for u in users:
            try:
                mx = max(mx, int(((u.get("eloRanking") or {}).get(cat)) or 0))
            except (TypeError, ValueError):
                continue
        return mx or None


# --- Chess Hotel (Foggy Media AB) -------------------------------------------
# Le jeu ne publie pas de classement mondial mais des LIGUES : pour chaque mode
# (Bullet, Blitz, Rapid, Chess960) et chaque niveau (Placement → Maître), les
# joueurs sont répartis en divisions d'environ 100 joueurs, classées aux POINTS.
# Chaque division est publique : GET /api/v1/division-scores/{id}.
CH_API = "https://www.chesshotel.com/api/v1"
CH_MODES = [("blitz", 4), ("rapid", 5), ("bullet", 3), ("chess960", 2)]
CH_LABELS = {"blitz": "Blitz", "rapid": "Rapid", "bullet": "Bullet", "chess960": "Chess960"}
CH_TIERS = [("master", "Maître"), ("diamond", "Diamant"), ("platinum", "Platine"),
            ("gold", "Or"), ("silver", "Argent"), ("bronze", "Bronze"), ("placement", "Placement")]
CH_TIER_LABELS = dict(CH_TIERS)
CH_KNOWN_DIVS = [1, 2, 3, 4, 5, 6, 8] + list(range(2486, 2552))   # divisions connues
CH_PROBE = 24                                                     # numéros sondés au-delà
CH_NOTE = ("Ligues Chess Hotel : chaque mode a son tableau, classé aux POINTS de la saison, "
           "du niveau Placement au niveau Maître (les joueurs sont répartis en divisions "
           "d'environ 100 joueurs). L'Elo est affiché à titre indicatif.")


async def _ch_division(client, div_id: int) -> list:
    """Tableau brut d'une division de ligue (API publique, lecture seule)."""
    r = await client.get(f"{CH_API}/division-scores/{div_id}")
    r.raise_for_status()
    return [x for x in (r.json().get("divisionScores") or []) if x.get("username")]


async def _ch_all_rows() -> list:
    """Toutes les lignes de toutes les divisions connues (+ sondage des nouvelles)."""
    ids = list(CH_KNOWN_DIVS)
    probe = list(range(CH_KNOWN_DIVS[-1] + 1, CH_KNOWN_DIVS[-1] + 1 + CH_PROBE))
    async with httpx.AsyncClient(timeout=90, headers={"Accept": "application/json"}) as client:
        lists = await asyncio.gather(*[_ch_division(client, i) for i in ids + probe],
                                     return_exceptions=True)
    rows = []
    for raw in lists:
        if isinstance(raw, Exception):
            continue
        for x in raw:
            if not x.get("username"):
                continue
            rows.append({
                "name": str(x["username"]).strip(), "user_id": x.get("userId"),
                "mode_id": int(x.get("modeId") or 0), "league": x.get("leagueName") or "",
                "tier": CH_TIER_LABELS.get(x.get("leagueName"), x.get("leagueName") or ""),
                "division": int(x.get("divisionId") or 0), "season": int(x.get("season") or 0),
                "elo": int(x.get("elo") or 0), "points": int(x.get("points") or 0),
                "wins": int(x.get("wins") or 0), "losses": int(x.get("losses") or 0),
                "draws": int(x.get("draws") or 0), "games": int(x.get("gameNr") or 0),
            })
    return rows


def _ch_rows(rows: list) -> list:
    """Normalise (mêmes champs que les autres jeux) et classe aux points."""
    rows = sorted(rows, key=lambda x: (-x["points"], -x["elo"], x["name"]))
    out = []
    for i, x in enumerate(rows, 1):
        out.append({"rank": i, "username": x["name"], "country": "", "tier": x["tier"],
                    "division": x["division"], "elo": x["elo"], "elo_best": None,
                    "points": x["points"], "wins": x["wins"], "losses": x["losses"],
                    "draws": x["draws"], "games": x["games"], "season": x["season"]})
    return out


async def fetch_chesshotel() -> dict:
    """Un classement par mode (toutes ligues) + un classement par mode et par ligue."""
    raw = await _ch_all_rows()
    if not raw:
        raise RuntimeError("aucune donnée de ligue")
    season = next((r["season"] for r in raw if r["season"]), 0)
    divs = len({r["division"] for r in raw})
    out = {}
    for mode, mode_id in CH_MODES:
        mine = [r for r in raw if r["mode_id"] == mode_id]
        if not mine:
            continue
        label = CH_LABELS[mode]
        out[("chesshotel", mode)] = {
            "label": label, "rows": _ch_rows(mine), "total": len(mine), "note": CH_NOTE,
            "extra": f"toutes ligues · saison {season}",
            "blurb": (f"Ligue {label} Chess Hotel (Foggy Media) : classement officiel de la saison "
                      f"{season}, tous niveaux confondus (Placement → Maître), aux points de ligue. "
                      f"Mis à jour le {fr_date()}."),
        }
        for key, tier_label in CH_TIERS:                    # un classement par ligue
            sub_rows = [r for r in mine if r["league"] == key]
            if not sub_rows:
                continue
            out[("chesshotel", f"{mode}-{key}")] = {
                "label": f"{label} · ligue {tier_label}", "rows": _ch_rows(sub_rows),
                "total": len(sub_rows), "note": CH_NOTE,
                "extra": f"ligue {tier_label} · saison {season}",
                "blurb": (f"Ligue {label} — niveau {tier_label} — Chess Hotel (Foggy Media) : classement "
                          f"officiel de la saison {season}, aux points de ligue. Mis à jour le {fr_date()}."),
            }
    return out


def text_chesshotel(rows) -> str:
    """Top 10 d'une ligue Chess Hotel en texte : points, Elo, niveau, statistiques."""
    lines = []
    for p in rows[:10]:
        lines.append(f"{p['rank']}. {p['username']} — {p['points']} points de ligue "
                     f"(Elo {p['elo']}, niveau {p['tier'] or '?'}, division {p.get('division') or '?'}, "
                     f"{p['games']} parties, {p['wins']}V/{p['losses']}D/{p['draws']}N, saison {p['season']})")
    return "\n".join(lines)


def fr_date() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------

def row_html(p, provider, col5="elo_best") -> str:
    flag = f'<td>{esc(p.get("tier") or p["country"] or "—")}</td>'
    ranks = p["rank"] or 1
    c5 = p.get(col5)
    return (f'<tr><td class="rk">{ranks}</td><td class="nm">{esc(p["username"])}</td>{flag}'
            f'<td class="elo">{p["elo"] or "—"}</td><td>{c5 if c5 is not None else "—"}</td>'
            f'<td>{p["wins"] or "—"}</td><td>{p["losses"] or "—"}</td>'
            f'<td>{p["draws"] or "—"}</td><td>{p["games"] or "—"}</td></tr>')


def page(game_name: str, mode_label: str, mode_id: str, rows: list[dict],
         total: int | None, note: str, col5_label: str = "Elo max", col5: str = "elo_best",
         col3_label: str = "Pays", blurb: str | None = None) -> str:
    url = f"{SITE}/public/top/{game_name.lower()}/{mode_id}/index.html"
    top_entries = []
    table = "".join(row_html(p, game_name, col5) for p in rows[:100])
    for p in rows[:10]:
        top_entries.append({
            "@type": "ListItem", "position": p["rank"] or 1,
            "name": p["username"], "description": f"{mode_label} Elo {p['elo']}"})
    jld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"ChessLive — Classement {game_name} {mode_label}",
        "description": f"Classement en direct (non officiel) des meilleurs joueurs "
                       f"{game_name} au {mode_label}, généré le {fr_date()}.",
        "url": url, "dateModified": fr_date(), "creator": {"@type": "Organization", "name": "ChessLive"},
        "variableMeasured": "Elo",
        "distribution": {"@type": "DataDownload", "contentUrl": url},
        "about": {"@type": "Thing", "name": "Jeu d'échecs en ligne"},
    }
    if blurb is None:
        blurb = (f"Classement en direct {esc(mode_label)} {esc(game_name)} : top 100 mondial, "
                 f"Elo, pays, statistiques. Mis à jour le {fr_date()}.")
    total_html = f"<p class='tot'>{total:,} joueurs classés".replace(",", " ") + \
                 ("</p>" if game_name != "Chess Hotel" else " (saison en cours)</p>") if total else \
                 f"<p class='tot'>ℹ️ {note}</p>"
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Top {len(rows[:100])} {esc(mode_label)} {esc(game_name)} — ChessLive</title>
<meta name="description" content="{blurb}">
<link rel="canonical" href="{url}">
<script type="application/ld+json">{json.dumps(jld, ensure_ascii=False)}</script>
<style>
 body{{font-family:system-ui,Arial,sans-serif;background:#0a0e13;color:#eef4f8;margin:0;padding:24px}}
 .wrap{{max-width:900px;margin:auto}} h1{{font-size:26px}} h1 span{{color:#7fdb6a}}
 a{{color:#3fa7d6}} .meta{{color:#8fa0b0;font-size:13px}} .tot{{color:#8fa0b0;font-size:14px}}
 table{{width:100%;border-collapse:collapse;background:#121a22;border-radius:14px;overflow:hidden;margin-top:14px}}
 th,td{{padding:9px 12px;text-align:left;font-size:14px;border-bottom:1px solid #22303f}}
 th{{background:#16202b;color:#8fa0b0;font-size:11px;text-transform:uppercase;letter-spacing:.07em}}
 .rk{{font-weight:800;color:#8fa0b0}} .nm{{font-weight:700}} .elo{{color:#7fdb6a;font-weight:800}}
 footer{{margin-top:20px;font-size:12px;color:#66788a}}
</style></head><body><div class="wrap">
<h1>Top {esc(mode_label)} <span>{esc(game_name)}</span> — ChessLive</h1>
<p class="meta">Classement en direct (projet non officiel) · généré le {fr_date()} · <a href="{SITE}/">ChessLive</a></p>
{total_html}
<table><thead><tr><th>#</th><th>Joueur</th><th>{esc(col3_label)}</th><th>Elo</th><th>{esc(col5_label)}</th><th>V</th><th>D</th><th>N</th><th>Parties</th></tr></thead>
<tbody>{table}</tbody></table>
<footer>ChessLive — non affilié à {esc(game_name)}. Données publiques, usage informatif.
<a href="{url.replace('/index.html','')}.txt">version texte</a> · <a href="{SITE}/public/llms-top.txt">top 10 texte (IA)</a></footer>
</div></body></html>
"""


def text_top(rows) -> str:
    lines = []
    for p in rows[:10]:
        lines.append(f"{p['rank'] or 1}. {p['username']} — Elo {p['elo']} "
                     f"(max {p['elo_best'] or p['elo']}, {p['games']} parties, {p['wins']}V/"
                     f"{p['losses']}D/{p['draws']}N, pays {p['country'] or '?'})")
    return "\n".join(lines)


def text_checkmate(rows) -> str:
    """Top 10 Checkmate en texte : Elo, victoires/défaites/nulles, points de puzzles."""
    lines = []
    for p in rows[:10]:
        rg = f", n°{p['rank_global']} mondial" if p.get("rank_global") else ""
        lines.append(f"{p['rank']}. {p['username']} — Elo {p['elo']} "
                     f"({p['games']} parties, {p['wins']}V/{p['losses']}D/{p['draws']}N, "
                     f"{p['puzzles']} points de puzzles, pays {p['country'] or '?'}{rg})")
    return "\n".join(lines)


async def fetch_all():
    data = {}   # data[ (game, mode_id) ] = {"rows": [...], "total": int|None, "note": ""}
    async with httpx.AsyncClient(base_url=SC_UP, headers=SC_H, timeout=20) as client:
        for mode_id, label in SC_MODES:
            r = await client.post("/public/liveplay/top", json={"type": SC_TYPE[mode_id]})
            j = r.json()
            fl = j["data"]["flatList"]
            fields, rows = fl["fields"], fl["values"]
            players = [dict(zip(fields, row)) for row in rows]
            data[("simplechess", mode_id)] = {
                "label": label,
                "rows": [{"rank": int(p.get("rank") or 1), "username": p.get("username", ""),
                          "country": p.get("country", ""), "elo": int(p.get("elo") or 0),
                          "elo_best": int(p.get("eloBest") or 0), "wins": int(p.get("wins") or 0),
                          "losses": int(p.get("losses") or 0), "draws": int(p.get("draws") or 0),
                          "games": int(p.get("total") or 0)} for p in players],
                "total": None,
                "note": SC_TOTAL_NOTE,
            }
    sco = SCoClient()
    for cat, label in SCo_MODES:
        rows = await sco.leaderboard(cat)
        total = await sco.total(cat)
        data[("socialchess", cat.lower())] = {
            "label": label, "rows": rows, "total": total, "note": "",
        }
    try:
        data.update(await fetch_chesshotel())
    except Exception as e:      # l'éditeur peut fermer l'accès : on n'échoue pas
        print(f"⚠️  Chess Hotel indisponible : {e}")
    try:
        cm = await fetch_checkmate(100)
        try:
            me = await fetch_checkmate_player(config_pseudos()[1])
            if me:
                cm["me"] = me
        except Exception:
            pass
        data[("checkmate", "rank")] = cm
        try:
            data[("checkmate", "france")] = await fetch_checkmate_country("FR", 100)
        except Exception as e:
            print(f"⚠️  Checkmate France indisponible : {e}")
    except Exception as e:      # l'éditeur peut fermer l'accès : on n'échoue pas
        print(f"⚠️  Checkmate indisponible : {e}")
    return data


def write_root_files():
    # robots.txt — autorise tout + bots IA explicitement
    robots = f"""User-agent: *
Allow: /

# Agents d'IA (lecture des classements ChessLive)
User-agent: GPTBot
Allow: /
User-agent: OAI-SearchBot
Allow: /
User-agent: ChatGPT-User
Allow: /
User-agent: ClaudeBot
Allow: /
User-agent: Claude-SearchBot
Allow: /
User-agent: PerplexityBot
Allow: /
User-agent: Google-Extended
Allow: /
User-agent: cohere-ai
Allow: /

Sitemap: {SITE}/sitemap.xml
"""
    (ROOT / "robots.txt").write_text(robots, encoding="utf-8")

    # llms.txt — convention de découverte pour les assistants IA
    llms = f"""# ChessLive

> Classements en direct (non officiels) des jeux d'échecs en ligne SimpleChess
> (Europe Echecs), SocialChess (Woodchop Software), Checkmate / « Chess Online
> & Offline » (Splend Apps) et Chess Hotel (Foggy Media). Sources : API publiques
> des plateformes et, pour Checkmate, la base de classement de l'app (lecture
> seule, session anonyme).
> Dernière mise à jour : {fr_date()}.

Les données détaillées sont dans le fichier `public/llms-top.txt` :
top 10 par mode pour chaque jeu, et `public/players.txt` : index des meilleurs
joueurs (pseudo, Elo, rang, jeu).

## Classements par jeu

- [SimpleChess — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): bullet, blitz, rapide, chess960, puzzle battle
- [SocialChess — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): Bullet, Blitz, Rapid, Chess960, Classical, Fast, Slow
- [Checkmate — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): classement mondial + classement France (rangs officiels)
- [Chess Hotel — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): ligues Blitz, Rapid, Bullet, Chess960 (points de la saison) + élite Diamant/Maître
- [Pages HTML par classement]({SITE}/public/): top 100 avec statistiques
- [Index des joueurs]({SITE}/public/players.txt): pseudo → Elo, rang, jeu
- [Flux RSS]({SITE}/public/rss.xml): changements du top

## Notes

- Projets non officiels, à but informatif, non affiliés aux éditeurs des jeux
  (Europe Echecs / SimpleChess, Woodchop Software / SocialChess, Splend Apps / Checkmate,
  Foggy Media / Chess Hotel).
- Classement Checkmate : ordre = Elo, puis victoires, puis points de puzzles (règle de l'app).
- Classement Chess Hotel : le jeu n'a pas de classement mondial — il organise des LIGUES
  par mode (Bullet, Blitz, Rapid, Chess960) et par niveau (Placement, Bronze, Argent, Or,
  Platine, Diamant, Maître), les joueurs étant répartis en divisions d'environ 100 joueurs.
  Chaque tableau est classé aux POINTS de la saison ; ChessLive publie le classement de
  chaque mode (toutes ligues) et de chaque ligue.
- Les classements changent en général une fois par jour (SimpleChess ~60 min,
  SocialChess temps réel). Ce site est mis à jour quotidiennement.
- Pour trouver un joueur, cherchez son pseudo dans `public/players.txt`.
"""
    (ROOT / "llms.txt").write_text(llms, encoding="utf-8")

    # ai.txt — simple déclaration d'indexation
    ai = f"""# AI.txt
ChessLive — classements échecs en direct (SimpleChess, SocialChess, Checkmate, Chess Hotel)
URL: {SITE}
Contenu utile pour les agents IA:
- {SITE}/public/llms-top.txt  (top 10 par mode, texte)
- {SITE}/public/players.txt   (index des meilleurs joueurs)
- {SITE}/public/              (pages HTML par classement)
Licence: usage informatif, données non officielles issues des API publiques des jeux.
Contact: ChessLive (projet communautaire).
Mise à jour: quotidienne.
"""
    (ROOT / "ai.txt").write_text(ai, encoding="utf-8")


def main():
    data = asyncio.run(fetch_all())
    frd = fr_date()

    # --- nettoie et crée les arborescences ---
    if PUB.exists():
        for f in PUB.rglob("*"):
            if f.is_file():
                f.unlink()
    (PUB / "top").mkdir(parents=True, exist_ok=True)

    # hub public/index.html
    hub_rows = []
    sitemap = []
    players = {}   # pseudo -> {best_elo, provider, mode, rank}
    llms_lines = [f"ChessLive — top 10 par mode (généré le {frd})",
                  "=" * 60, ""]
    rss_items = []

    for (game, mode_id), d in sorted(data.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        label = d["label"]
        game_label = {"simplechess": "SimpleChess", "socialchess": "SocialChess",
                      "checkmate": "Checkmate", "chesshotel": "Chess Hotel"}.get(game, game)
        dirp = PUB / "top" / game / mode_id
        dirp.mkdir(parents=True, exist_ok=True)
        url = f"{SITE}/public/top/{game}/{mode_id}/index.html"
        cm = game == "checkmate"
        ch = game == "chesshotel"
        (dirp / "index.html").write_text(
            page(game_label, label, mode_id, d["rows"], d["total"], d["note"],
                 col5_label="Points" if ch else ("Puzzles" if cm else "Elo max"),
                 col5="points" if ch else ("puzzles" if cm else "elo_best"),
                 col3_label="Ligue" if ch else "Pays",
                 blurb=(d.get("blurb") if ch else None)),
            encoding="utf-8")
        sitemap.append({"loc": url, "lastmod": frd})
        hub_rows.append(
            f'<li><a href="top/{game}/{mode_id}/">Top {esc(label)} — {esc(game_label)}</a> '
            f'(<a href="top/{game}/{mode_id}/index.html.txt">page</a>)'
            + (f' — {d["total"]:,} '.replace(",", " ")
               + "joueurs classés" if d["total"] else "")
            + "</li>")
        # top 10 texte
        llms_lines.append(f"## {game_label} — {label}")
        if game == "checkmate":
            llms_lines.append(text_checkmate(d["rows"]))
        elif ch:
            llms_lines.append(text_chesshotel(d["rows"]))
        else:
            llms_lines.append(text_top(d["rows"]))
        llms_lines.append("")
        # index joueurs
        for p in d["rows"][:60]:
            nm = p["username"]
            rank_txt = f"#{p['rank']}" + (f" (n°{p['rank_global']} mondial)" if p.get("rank_global") else "")
            if nm not in players or p["elo"] > players[nm]["elo"]:
                players[nm] = {"elo": p["elo"], "provider": game_label,
                               "mode": label if not p.get("rank_global") else f"{label}, {p['country']}",
                               "rank_txt": rank_txt}
        # RSS (top 5)
        for p in d["rows"][:5]:
            rss_items.append(
                f"<item><title>{game_label} {label} #{p['rank']} : {esc(p['username'])}</title>"
                f"<link>{url}</link><guid>{url}#{p['rank']}</guid>"
                f"<description>{esc(p['username'])} — Elo {p['elo']} "
                f"({p.get('tier') or p['country'] or '?'}"
                + (f", {p['points']} pts" if p.get('points') else "") + ")</description>"
                f"<pubDate>{datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>")

    (PUB / "index.html").write_text(
        f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>ChessLive — classements échecs en direct (pages publiques)</title>
<meta name="description" content="Classements publics SimpleChess, SocialChess, Checkmate (Chess Online & Offline) & Chess Hotel : top par mode, ligues, joueurs, Elo. Pages lisibles par les moteurs de recherche et les IA.">
<link rel="canonical" href="{SITE}/public/">
<style>body{{font-family:system-ui,Arial,sans-serif;background:#0a0e13;color:#eef4f8;padding:24px}}
.wrap{{max-width:860px;margin:auto}}h1{{font-size:26px}}h1 span{{color:#7fdb6a}}
a{{color:#3fa7d6;text-decoration:none}}a:hover{{text-decoration:underline}}
li{{margin:8px 0;font-size:15px}}.meta{{color:#8fa0b0;font-size:13px}}</style></head>
<body><div class="wrap"><h1>ChessLive — <span>classements échecs en direct</span></h1>
<p class="meta">Pages publiques (SEO + IA) · généré le {frd} · <a href="{SITE}/">Applier ChessLive</a> ·
<a href="llms-top.txt">Top 10 texte (IA)</a> · <a href="players.txt">Index joueurs</a> ·
<a href="../llms.txt">llms.txt</a> · <a href="rss.xml">RSS</a></p>
<ul>{''.join(hub_rows)}</ul>
<p class="meta">⚠️ Projet non officiel — données issues des API publiques des jeux ; ne pas utiliser pour des décisions officielles.</p>
</div></body></html>""",
        encoding="utf-8")

    # llms-top.txt
    (PUB / "llms-top.txt").write_text("\n".join(llms_lines), encoding="utf-8")

    # players.txt
    pl_lines = [f"# ChessLive — index des meilleurs joueurs (généré le {frd})",
                "# Format : pseudo | jeu | mode | elo | rang", ""]
    me = (data.get(("checkmate", "rank")) or {}).get("me")
    if me:
        rg = f"#{me['rank']}" if me["rank"] else f"non classé (compte hors classement, {me.get('ranked',0)})"
        rc = f" (n°{me['rank_country']} {me['country']})" if me["rank_country"] else ""
        pl_lines.append(f"{me['username']} | Checkmate | Classement (Elo) | {me['elo']} | {rg}{rc} "
                        f"· {me['games']} parties · {me['puzzles']} pts puzzles")
    seen_pl = set()
    for nm, p in sorted(players.items(), key=lambda kv: -kv[1]["elo"])[:300]:
        if nm.lower() in seen_pl:      # même joueur vu dans plusieurs classements
            continue
        seen_pl.add(nm.lower())
        pl_lines.append(f"{nm} | {p['provider']} | {p['mode']} | {p['elo']} | {p.get('rank_txt') or '#'+str(p['rank'])}")
    (PUB / "players.txt").write_text("\n".join(pl_lines), encoding="utf-8")

    # RSS
    (PUB / "rss.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>ChessLive — classements échecs en direct</title>
<link>{SITE}/public/</link>
<description>Top 10 SimpleChess, SocialChess, Checkmate & Chess Hotel (ligues), mis à jour quotidiennement</description>
<lastBuildDate>{datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
{''.join(rss_items)}</channel></rss>""",
        encoding="utf-8")

    # versions texte des pages (confort IA)
    for (game, mode_id), d in data.items():
        tot_lbl = ("Comptes au total : " if game == "checkmate"
                   else ("Joueurs classés dans ce tableau : " if game == "chesshotel"
                         else "Total joueurs classés : "))
        body = (text_checkmate(d["rows"]) if game == "checkmate"
                else (text_chesshotel(d["rows"]) if game == "chesshotel" else text_top(d["rows"])))
        txt = (f"ChessLive — Top {d['label']} {game} ({frd})\n"
               + (tot_lbl + str(d["total"]) + "\n" if d["total"] else "")
               + body + "\n")
        (PUB / "top" / game / mode_id / "index.html.txt").write_text(txt, encoding="utf-8")

    # sitemap.xml
    with open(ROOT / "sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for s in sitemap:
            f.write(f"  <url><loc>{s['loc']}</loc><lastmod>{s['lastmod']}</lastmod></url>\n")
        f.write("</urlset>\n")

    write_root_files()

    # résumé
    print(f"✅ Pages publiques générées le {frd} :")
    for (game, mode_id), d in data.items():
        tot = f" — {d['total']:,} joueurs classés".replace(",", " ") if d["total"] else " (total non publié)"
        print(f"   {game}/{mode_id}: top {len(d['rows'])} " + tot)


if __name__ == "__main__":
    main()
