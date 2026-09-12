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
     - MY_USERNAME_CM   : pseudo Checkmate / « Chess Online & Offline » (Splend Apps)
     - MY_USERNAME_SC   : pseudo SocialChess (optionnel, pour l'import)
     (Ancien compte : "ILoveKaroline" — remplacer ici pour y revenir.) */
  MY_USERNAME: "Miyukipa",
  MY_USERNAME_CM: "ChessMiyuki",
  MY_USERNAME_SC: "",

  /* Jeu Checkmate (Splend Apps) :
     CHECKMATE_LIMIT = nombre de premiers du classement officiel chargé
     (1000 par défaut ; augmenter = plus de joueurs par pays mais plus lourd). 
     le classement est lu en LECTURE SEULE dans la base publique de l'app
     (session anonyme, aucune donnée personnelle). Mettre false pour le
     retirer complètement de l'interface si l'éditeur ferme l'accès. */
  CHECKMATE_ENABLED: true,
  CHECKMATE_LIMIT: 1000,

  /* Rafraîchissement automatique du classement (secondes) */
  AUTO_REFRESH_SEC: 60,
};
