# ♟️ ChessLive

**Classements échecs en direct** — PWA installable (hors-ligne après 1ère visite, graphique d'évolution Elo).

Deux jeux supportés, avec **switch intégré** :

| Jeu | Classements | Source (découverte) |
|---|---|---|
| ♟️ **SimpleChess** (Europe Echecs) | Bullet · Blitz · Rapid · Chess960 · Puzzle Battle | `POST https://api.echecs.com/public/liveplay/top` (REST, CORS ouvert, sans clé) |
| ⚔️ **SocialChess** (Woodchop Software) | Bullet · Blitz · Rapid · Chess960 · Classical · Fast · Slow | `wss://api.socialchess.com?x=ws` (frame binaire `\x00` + JSON, commandes `usersByRank` / `getUser`) |

## ✨ Fonctionnalités (v3)

- **Classement en direct** par mode + **filtre par pays**, recherche, auto-refresh
- **Profil joueur** : tous ses Elo + rang mondial, dans une **modale superposée** au classement
- **Carte de joueur partageable** (`PNG 1080×1350`) : gradient, cavalier, Elo par mode — partage natif ou téléchargement
- **Graphique d'évolution Elo** (SimpleChess – API officielle `eloChartData`) + **time-lapse local** : ChessLive mémorise le classement (1 mesure / 30 min) et trace l'évolution dans le profil et le compte
- **Movers & Shakers** : tri par rang gagné, ▲/▼/NEW, « X en mouvement · Y nouveaux »
- **Stats & totaux** : histogramme Elo du top + **nombre total de joueurs classés** (SocialChess : exact, via `eloRanking` du bas du classement ; SimpleChess : non publié par l'API amont → note honnête affichée)
- **Classement par pays** : nb de joueurs, Elo moyen, meilleur — clic pour filtrer
- **Exports** : CSV (BOM UTF-8) et JSON — dans l'app (chips) **et** côté API (`?format=csv`)
- **Panneau « Mon compte »** (préconfiguré sur votre pseudo : Miyukipa 🏆) : graphique + mesures ChessLive
- **☁️ Visibilité IA/SEO** : pages statiques générées (`public/`, `llms.txt`, `ai.txt`, `robots.txt`, `sitemap.xml`, `rss.xml`) pour que les moteurs de recherche **et les IA** puissent répondre « qui est le top 1 / top 10 » sur chaque jeu — **Miyukipa #1 bullet, blitz & rapid** 😉

---

## 🚀 La PWA (recommandée — aucun backend requis)

Les deux API amont acceptent les requêtes **directement depuis le navigateur**
(CORS ouvert REST pour SimpleChess, WebSocket pur pour SocialChess). Le projet
est donc **100 % statique** : déployable sur GitHub Pages, Netlify, etc.

```bash
python3 -m http.server 8000      # ou tout serveur statique
```

> ⚠️ Le WebSocket SocialChess nécessite HTTPS (wss) — sur `localhost` ça
> fonctionne, sur un statique il faut un domaine HTTPS (GitHub Pages okay).

### Configurer (`config.js`)

```js
window.SC_CONFIG = {
  API_MODE: "amont",            // "amont" (statique) | "api" (backend FastAPI)
  API_BASE: "",                 // ex. "https://mon-api.up.railway.app" (mode api)
  DEFAULT_GAME: "simplechess",  // jeu ouvert par défaut
  MY_USERNAME: "Miyukipa", // votre compte SimpleChess (panneau stats)
  MY_USERNAME_SC: "",           // votre pseudo SocialChess (optionnel)
  AUTO_REFRESH_SEC: 60,
};
```

## 🛠️ La FastAPI incluse (optionnelle : cache serveur, filtres, Swagger, exports)

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000   # doc : /docs
```

| Endpoint | Description |
|---|---|
| `GET /api/leaderboard/{mode}?provider=simplechess` | Top SimpleChess (`bullet`, `blitz`, `standard`, `chess960`, `puzzlesbatl`) — `&format=csv` pour l'export |
| `GET /api/leaderboard/{mode}?provider=socialchess` | Top SocialChess (`Bullet`, `Blitz`, `Rapid`, `Chess960`, `Classical`, `Fast`, `Slow`) |
| `GET /api/stats/{provider}` | **Total joueurs classés** par mode (SocialChess exact ; SimpleChess = note) |
| `GET /api/player/{username}` · `.../rating/{mode}` | Profil + Elo SimpleChess |
| `GET /api/player/{username}/history/{mode}` | **Graphique d'évolution Elo** (mensuel, période 1Y) |
| `GET /api/player/social/{user_id}` | Profil SocialChess (via WebSocket) |
| `GET /api/modes` · `/api/health` | Métadonnées |

Filtres communs : `limit` (1-100), `country`, `search`, `refresh`.
Cache mémoire TTL 60 s + anti-stampede + anti-abus. Sert aussi `public/`, `llms.txt`, `robots.txt`, `sitemap.xml`.

## ☁️ Pages publiques IA/SEO (générées)

`python3 gen_public.py` interroge les deux API et génère, **committé et rafraîchi
quotidiennement par GitHub Actions** (`.github/workflows/refresh-public.yml`) :

- `public/index.html` — hub des classements (liens top 100 par mode)
- `public/top/{jeu}/{mode}/index.html` — page SEO par classement (JSON-LD Dataset, table top 100)
- `public/llms-top.txt` — **top 10 par mode en texte brut** (le fichier que lisent les IA)
- `public/players.txt` — index des meilleurs joueurs (pseudo → Elo, rang, jeu)
- `public/rss.xml` — flux des changements du top
- `llms.txt`, `ai.txt`, `robots.txt`, `sitemap.xml` — conventions de découverte

Résultat : « Qui est le n°1 mondial de bullet ? » → les IA à accès web trouvent
`llms-top.txt` et répondent. ✅

> ℹ️ **Total joueurs SocialChess** : chaque utilisateur porte un champ `eloRanking`
> (rang global par catégorie) ; en scannant le bas du classement
> (`nearElo:"1", ascending:"true"`) le rang max = total exact (ex. Bullet 5 331,
> Blitz 12 908, Rapid 7 838 ; relevé 2026-09-05). SimpleChess ne le publie pas.

---

## 🔍 Comment ça a été découvert (résumé)

**SimpleChess** : le bundle de la zone de jeu (`europe-echecs.com/simplechess.html`
→ `gamingzone.ts/dist/assets/index-*.js`) contient le module WebApi sur
`https://api.echecs.com` avec les en-têtes applicatifs (`App-Site: SIM`,
`App-Name: simplechessWeb`) ; `public/liveplay/top` renvoie la flatList du
top 100 sans authentification (`refreshDelayMin: 60`).

**SocialChess** : `socialchess.com` (Flutter web) → `main.dart.js` contient
`wss://api.socialchess.com?x=ws` et les commandes (`usersByRank`, `getUser`,
`login`…). Protocole : **message binaire préfixé `\x00`** + JSON, avec
`wsId`, identif. `anonymous:"true"`, `build/version/deviceId/language/platform`,
et un **`ping` obligatoire avant chaque commande**. Les réponses sont en JSON
(`usersByRank` avec `statsBullet/statsBlitz/statsRapid/...`; `getUser` avec le
profil complet, `rankPct`, `aoe`…). Le projet Firebase
(`socialchess-4ea85.firebaseio.com`) est verrouillé (auth) — le WebSocket est
la voie publique.

## ⚠️ À savoir

- **Projet non officiel** : non affilié à Europe Echecs / Woodchop Software.
  Les API amont peuvent changer à tout moment (mettre alors à jour les
  constantes dans `index.html` / `main.py` / `gen_public.py`).
- **Usage raisonnable** : ne pas poller les API amont à haute fréquence.
- SocialChess ne fournit pas d'historique Elo public → graphique uniquement
  pour SimpleChess (données officielles du jeu : mensuel, période 1Y) ; le
  time-lapse ChessLive compense (mesures locales).
- Le panneau « Mon compte » est lié à votre profil SimpleChess
  (`config.js` → `MY_USERNAME`).
- Les snapshots locaux vivent dans `localStorage` (`chesslive-snapshots-v1`,
  1 mesure / 30 min par jeu+mode, 480 max) : ils ne sortent pas du navigateur.

## 📁 Fichiers

- `index.html` — **la PWA** (tout-en-un : UI + providers SimpleChess/SocialChess + graphique SVG + v3)
- `config.js` — configuration (mode amont/api, compte, auto-refresh)
- `manifest.webmanifest` + `sw.js` + `static/icons/*` — PWA installable, hors-ligne
- `main.py` + `requirements.txt` — l'API FastAPI optionnelle (cache, Swagger, proxy WS, stats, CSV)
- `gen_public.py` — générateur des pages publiques IA/SEO
- `.github/workflows/refresh-public.yml` — rafraîchit `public/` tous les jours
- `public/` · `llms.txt` · `ai.txt` · `robots.txt` · `sitemap.xml` — pages générées (committées)
- `IDEAS.md` — feuille de route des idées (60+)
- `README.md` — ce fichier
