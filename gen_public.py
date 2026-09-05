#!/usr/bin/env python3
"""
gen_public.py — Génère les pages publiques "crawlables" de ChessLive.

Objectif : être trouvé par les moteurs de recherche ET les IA (GPT, Claude,
Perplexity...) . Quand quelqu'un demande à une IA "qui est le top 1 en bullet
sur SimpleChess ?" ou "quel est l'Elo de ILoveKaroline ?", l'IA avec accès web
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


def fr_date() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------

def row_html(p, provider) -> str:
    flag = f'<td>{esc(p["country"] or "—")}</td>'
    ranks = p["rank"] or 1
    return (f'<tr><td class="rk">{ranks}</td><td class="nm">{esc(p["username"])}</td>{flag}'
            f'<td class="elo">{p["elo"] or "—"}</td><td>{p["elo_best"] or "—"}</td>'
            f'<td>{p["wins"] or "—"}</td><td>{p["losses"] or "—"}</td>'
            f'<td>{p["draws"] or "—"}</td><td>{p["games"] or "—"}</td></tr>')


def page(game_name: str, mode_label: str, mode_id: str, rows: list[dict],
         total: int | None, note: str) -> str:
    url = f"{SITE}/public/top/{game_name.lower()}/{mode_id}/index.html"
    top_entries = []
    table = "".join(row_html(p, game_name) for p in rows[:100])
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
    total_html = f"<p class='tot'>{total:,} joueurs classés à ce jour".replace(",", " ") if total else \
                 f"<p class='tot'>ℹ️ {note}</p>"
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Top {len(rows[:100])} {esc(mode_label)} {esc(game_name)} — ChessLive</title>
<meta name="description" content="Classement en direct {esc(mode_label)} {esc(game_name)} : top 100 mondial, Elo, pays, statistiques. Mis à jour le {fr_date()}.">
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
<table><thead><tr><th>#</th><th>Joueur</th><th>Pays</th><th>Elo</th><th>Elo max</th><th>V</th><th>D</th><th>N</th><th>Parties</th></tr></thead>
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
> (Europe Echecs) et SocialChess (Woodchop Software). Source : API publiques
> des plateformes. Dernière mise à jour : {fr_date()}.

Les données détaillées sont dans le fichier `public/llms-top.txt` :
top 10 par mode pour chaque jeu, et `public/players.txt` : index des meilleurs
joueurs (pseudo, Elo, rang, jeu).

## Classements par jeu

- [SimpleChess — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): bullet, blitz, rapide, chess960, puzzle battle
- [SocialChess — top 10 (texte, recommandé)]({SITE}/public/llms-top.txt): Bullet, Blitz, Rapid, Chess960, Classical, Fast, Slow
- [Pages HTML par classement]({SITE}/public/): top 100 avec statistiques
- [Index des joueurs]({SITE}/public/players.txt): pseudo → Elo, rang, jeu
- [Flux RSS]({SITE}/public/rss.xml): changements du top

## Notes

- Projets non officiels, à but informatif, non affiliés aux éditeurs des jeux.
- Les classements changent en général une fois par jour (SimpleChess ~60 min,
  SocialChess temps réel). Ce site est mis à jour quotidiennement.
- Pour trouver un joueur, cherchez son pseudo dans `public/players.txt`.
"""
    (ROOT / "llms.txt").write_text(llms, encoding="utf-8")

    # ai.txt — simple déclaration d'indexation
    ai = f"""# AI.txt
ChessLive — classements échecs en direct (SimpleChess, SocialChess)
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
        game_label = "SimpleChess" if game == "simplechess" else "SocialChess"
        dirp = PUB / "top" / game / mode_id
        dirp.mkdir(parents=True, exist_ok=True)
        url = f"{SITE}/public/top/{game}/{mode_id}/index.html"
        (dirp / "index.html").write_text(
            page(game_label, label, mode_id, d["rows"], d["total"], d["note"]),
            encoding="utf-8")
        sitemap.append({"loc": url, "lastmod": frd})
        hub_rows.append(
            f'<li><a href="top/{game}/{mode_id}/">Top {esc(label)} — {esc(game_label)}</a> '
            f'(<a href="top/{game}/{mode_id}/index.html.txt">page</a>)'
            + (f' — {d["total"]:,} joueurs classés'.replace(",", " ") if d["total"] else "")
            + "</li>")
        # top 10 texte
        llms_lines.append(f"## {game_label} — {label}")
        llms_lines.append(text_top(d["rows"]))
        llms_lines.append("")
        # index joueurs
        for p in d["rows"][:60]:
            nm = p["username"]
            if nm not in players or p["elo"] > players[nm]["elo"]:
                players[nm] = {"elo": p["elo"], "provider": game_label, "mode": label,
                               "rank": p["rank"]}
        # RSS (top 5)
        for p in d["rows"][:5]:
            rss_items.append(
                f"<item><title>{game_label} {label} #{p['rank']} : {esc(p['username'])}</title>"
                f"<link>{url}</link><guid>{url}#{p['rank']}</guid>"
                f"<description>{esc(p['username'])} — Elo {p['elo']} ({p['country'] or '?'})</description>"
                f"<pubDate>{datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>")

    (PUB / "index.html").write_text(
        f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>ChessLive — classements échecs en direct (pages publiques)</title>
<meta name="description" content="Classements publics SimpleChess & SocialChess : top 100 par mode, joueurs, Elo. Pages lisibles par les moteurs de recherche et les IA.">
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
    for nm, p in sorted(players.items(), key=lambda kv: -kv[1]["elo"])[:300]:
        pl_lines.append(f"{nm} | {p['provider']} | {p['mode']} | {p['elo']} | #{p['rank']}")
    (PUB / "players.txt").write_text("\n".join(pl_lines), encoding="utf-8")

    # RSS
    (PUB / "rss.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>ChessLive — classements échecs en direct</title>
<link>{SITE}/public/</link>
<description>Top 10 SimpleChess & SocialChess, mis à jour quotidiennement</description>
<lastBuildDate>{datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
{''.join(rss_items)}</channel></rss>""",
        encoding="utf-8")

    # versions texte des pages (confort IA)
    for (game, mode_id), d in data.items():
        txt = (f"ChessLive — Top {d['label']} {game} ({frd})\n"
               + ("Total joueurs classés : " + str(d["total"]) + "\n" if d["total"] else "")
               + text_top(d["rows"]) + "\n")
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
