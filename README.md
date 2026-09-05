# ♟️ ChessLive

**Classements échecs en direct** — PWA installable (installable, hors-ligne après 1ère visite, graphique d'évolution Elo).

Deux jeux supportés, avec **switch intégré** :

| Jeu | Classements | Source (découverte) |
|---|---|---|
| ♟️ **SimpleChess** (Europe Echecs) | Bullet · Blitz · Rapid · Chess960 · Puzzle Battle | `POST https://api.echecs.com/public/liveplay/top` (REST, CORS ouvert, sans clé) |
| ⚔️ **SocialChess** (Woodchop Software) | Bullet · Blitz · Rapid · Chess960 · Classical · Fast · Slow | `wss://api.socialchess.com?x=ws` (frame binaire `\x00` + JSON, commandes `usersByRank` / `getUser`) |

Fonctionnalités : classement en direct par mode, **filtre par pays**, recherche,
**profil joueur** (tous ses Elo + rang mondial), **graphique d'évolution Elo**
(SimpleChess – API officielle `eloChartData`), et un **panneau « Mon compte »**
(préconfiguré sur votre pseudo : ILoveKaroline 🚀).

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
  MY_USERNAME: "ILoveKaroline", // votre compte SimpleChess (panneau stats)
  MY_USERNAME_SC: "",           // votre pseudo SocialChess (optionnel)
  AUTO_REFRESH_SEC: 60,
};
```

## 🛠️ La FastAPI incluse (optionnelle : cache serveur, filtres, Swagger)

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000   # doc : /docs
```

| Endpoint | Description |
|---|---|
| `GET /api/leaderboard/{mode}?provider=simplechess` | Top SimpleChess (`bullet`, `blitz`, `standard`, `chess960`, `puzzlesbatl`) |
| `GET /api/leaderboard/{mode}?provider=socialchess` | Top SocialChess (`Bullet`, `Blitz`, `Rapid`, `Chess960`, `Classical`, `Fast`, `Slow`) |
| `GET /api/player/{username}` · `.../rating/{mode}` | Profil + Elo SimpleChess |
| `GET /api/player/{username}/history/{mode}` | **Graphique d'évolution Elo** (mensuel, période 1Y) |
| `GET /api/player/social/{user_id}` | Profil SocialChess (via WebSocket) |
| `GET /api/modes` · `/api/health` | Métadonnées |

Filtres communs : `limit` (1-100), `country`, `search`, `refresh`.
Cache mémoire TTL 60 s + anti-stampede + anti-abus.

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
  constantes dans `index.html` / `main.py`).
- **Usage raisonnable** : ne pas poller les API amont à haute fréquence.
- SocialChess ne fournit pas d'historique Elo public → graphique uniquement
  pour SimpleChess (données officielles du jeu : mensuel, période 1Y).
- Le panneau « Mon compte » est lié à votre profil SimpleChess
  (`config.js` → `MY_USERNAME`).

## 📁 Fichiers

- `index.html` — **la PWA** (tout-en-un : UI + providers SimpleChess/SocialChess + graphique SVG)
- `config.js` — configuration (mode amont/api, compte, auto-refresh)
- `manifest.webmanifest` + `sw.js` + `static/icons/*` — PWA installable, hors-ligne
- `main.py` + `requirements.txt` — l'API FastAPI optionnelle (cache, Swagger, proxy WS)
- `README.md` — ce fichier
