/* =============================================================
   Configuration de ChessLive
   ============================================================= */
window.SC_CONFIG = {
  /* Mode de données :
     "amont" : la PWA interroge DIRECTEMENT l'API des jeux
               (api.echecs.com en REST + api.socialchess.com en WebSocket).
               Aucun backend requis — fonctionne sur GitHub Pages.
     "api"   : passe par l'API FastAPI incluse (cache serveur, filtres).
               Renseigner API_BASE, ex. "https://mon-api.up.railway.app". */
  API_MODE: "amont",
  API_BASE: "",

  /* Jeu ouvert par défaut : "simplechess" | "socialchess" */
  DEFAULT_GAME: "simplechess",

  /* Ton compte :
     - MY_USERNAME      : pseudo SimpleChess (profil + graphique d'évolution)
     - MY_USERNAME_SC   : pseudo SocialChess (optionnel, pour l'import) */
  MY_USERNAME: "ILoveKaroline",
  MY_USERNAME_SC: "",

  /* Rafraîchissement automatique du classement (secondes) */
  AUTO_REFRESH_SEC: 60,
};
