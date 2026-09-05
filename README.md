# ♞ SimpleChess Leaderboard API

API **non officielle** qui expose le **classement mondial en direct** de
[SimpleChess](https://www.simplechess.com) (Europe Echecs) pour chaque mode de
jeu : **Bullet 🚀, Blitz ⚡, Rapid (standard) 🕒, Chess960 🎲, Puzzle Battle 🧩**.

Il n'existe aucun classement public consultable en ligne pour SimpleChess
(l'app iOS l'affiche en interne seulement). Cette API le rend accessible en
rejouant proprement l'appel que la zone de jeu web fait elle-même :
`POST https://api.echecs.com/public/liveplay/top` avec les en-têtes applicatifs
(`App-Site: SIM`, etc.) — **aucune authentification n'est requise** pour cet
endpoint.

---

## 📱 Version PWA installable

L'app est une **PWA** : manifest + service worker + icônes. Utilisable dans le
navigateur, installable sur l'écran d'accueil (bouton « Installer l'app ») et
**consultable hors connexion** (la dernière copie du classement est mise en
cache). `config.js` permet deux modes :

| Mode | Description |
|---|---|
| `amont` (défaut) | La PWA appelle **directement** `api.echecs.com` (CORS ouvert, aucune clé) — **aucun backend requis**, hébergeable en statique |
| `api` | La PWA passe par la FastAPI incluse (cache serveur + filtres) — renseigner `API_BASE` |

### Déploiement GitHub Pages

1. Créez un dépôt public (ex. `simplechess-live`) sur GitHub (sans README).
2. Poussez le contenu de ce dossier sur la branche `main`.
3. Settings → Pages → **Deploy from a branch** → `main` / racine.
4. L'app est en ligne sur `https://<user>.github.io/simplechess-live/` — et
   installable en PWA (⚠️ mode `amont` uniquement ; le mode `api` n'a pas de
   vrai serveur sur Pages).

### Backend optionnel

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Puis dans `config.js` : `API_MODE: "api"`, `API_BASE: "https://votre-api.url"`.

## 📡 Endpoints de l'API

### `GET /api/leaderboard/{mode}` — Top 100 mondial d'un mode

| Paramètre | Type | Défaut | Description |
|---|---|---|---|
| `mode` | path | — | `bullet` · `blitz` · `standard` (alias : `rapid`, `s`) · `chess960` (alias : `960`) · `puzzlesbatl` |
| `limit` | query | `100` | 1 à 100 |
| `country` | query | — | filtre code pays (ex. `FRA`) |
| `search` | query | — | filtre pseudo |
| `refresh` | query | `false` | force un re-fetch de l'API amont (min. 10 s entre deux forcages) |

```bash
curl "http://localhost:8000/api/leaderboard/bullet"
curl "http://localhost:8000/api/leaderboard/blitz?country=FRA&limit=20"
curl "http://localhost:8000/api/leaderboard/standard?search=Gilles"
```

Réponse :

```json
{
  "mode": "bullet",
  "generated_at": 1757070000,
  "upstream_refresh_delay_min": 60,
  "count": 100,
  "top": [
    {
      "rank": 1, "username": "Better67", "title": "", "country": "VAT",
      "elo": 2416, "elo_best": 2416,
      "wins": 80, "losses": 4, "draws": 0, "games": 84
    }
  ]
}
```

### `GET /api/player/{username}` — Profil + tous les Elo d'un joueur

```bash
curl "http://localhost:8000/api/player/GillesLeGrand"
```

```json
{
  "username": "GillesLeGrand", "country": "USA", "seniority": 1,
  "registration_date": "20200602", "last_connect_date": "20260904230439",
  "avatar_url": "https://api.echecs.com/public/player/avatar/GillesLeGrand",
  "ratings": [
    { "type": "bullet", "elo": 2210, "elo_best": 2484, "rank": 5,
      "rd": 50, "games": 26010, "wins": 22792, "losses": 2608, "draws": 610 }
  ]
}
```

### `GET /api/player/{username}/rating/{mode}` — Elo d'un joueur dans un mode

### `GET /api/modes` — Modes disponibles · `GET /api/health` — État

## 🏗️ Architecture

```
Navigateur / votre script
        │  HTTP (JSON, CORS *)
        ▼
  main.py  (FastAPI)
   ├── cache mémoire TTL 60 s par mode + anti-stampede (single-flight)
   ├── anti-abus : forcage manuel limité à 1 / 10 s
   └── httpx (async) ──► POST https://api.echecs.com/public/liveplay/top
                          headers App-Site=SIM, App-Name=simplechessWeb, …
```

Les données amont ne changent en général que **toutes les ~60 min**
(`refreshDelayMin` renvoyé par l'API), ce qui rend le cache très efficace.

## ⚠️ À savoir

- **Projet non officiel** : non affilié à Europe Echecs / SimpleChess. Le
  contrat de l'API amont peut changer sans préavis (endpoints, en-têtes,
  fréquences). Si un jour ça casse, c'est qu'Europe Echecs a modifié sa zone
  de jeu — mettez à jour les constantes dans `main.py`.
- **Soyez raisonnables** : ce projet est pensé pour un usage léger/consultatif.
  Ne construisez pas un poller à haute fréquence ; le paramètre `refresh` est
  volontairement limité.
- Le classement est celui de la **compte global** (toutes périodes
  confondues) — l'API amont ne permet pas de filtrer par période (7 jours,
  mois, année).
- L'app iOS et le web partagent le même backend : les Elo affichés ici sont
  bien ceux du jeu SimpleChess.

## 🧭 Comment ça a été trouvé (résumé de l'analyse)

1. `https://www.europe-echecs.com/simplechess.html` charge un bundle JS
   (`/common/applets/gamingzone.ts/dist/assets/index-*.js`).
2. Le bundle contient `WebApi` avec `https://api.echecs.com` et la liste des
   chemins (`public/liveplay/top`, `public/player/profile/stats/ratings`, …).
3. Le client poste en JSON avec les en-têtes `App-Lang/App-Site/App-Platform/
   App-Name/App-Version/App-Impl` + `Authorization: Bearer` (seulement si
   connecté — inutile pour les endpoints publics).
4. `public/liveplay/top` avec `{"type": "bullet"}` renvoie la flatList du top
   100 mondial (`refreshDelayMin: 60`).

## 📁 Fichiers

- `main.py` — l'API (FastAPI) : client amont, cache, endpoints.
- `static/index.html` — tableau de bord de démonstration (auto-référentiel).
- `requirements.txt` — dépendances.
