/* =============================================================
   Configuration de la PWA SimpleChess Live
   =============================================================

   Deux modes possibles :

   1) "amont"  (par défaut, recommandé pour GitHub Pages / statique)
      La PWA appelle DIRECTEMENT l'API publique de SimpleChess :
      https://api.echecs.com (CORS ouvert, aucune clé requise).
      Aucun backend nécessaire — fonctionne hébergée en statique.

   2) "api"    (si vous déployez l'API FastAPI incluse dans le repo)
      Mettez l'URL de l'API, ex. : "https://mon-api.up.railway.app"
      La PWA utilisera GET /api/leaderboard/{mode}, /api/player/...
      (cache serveur, filtres, anti-abus).

   Changez la valeur puis rechargez l'app.
   ============================================================= */
window.SC_CONFIG = {
  API_MODE: "amont",          // "amont" | "api"
  API_BASE: "",               // ex. "https://mon-api.up.railway.app" (mode "api")
  AUTO_REFRESH_SEC: 60,       // rafraîchissement automatique du classement
};
