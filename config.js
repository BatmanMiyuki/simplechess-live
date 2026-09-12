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
  /* Classement national (onglet « Top France » + filtre pays) :
     CHECKMATE_COUNTRY        = code du pays mis en avant (FR, BE, CH…)
     CHECKMATE_COUNTRY_DEPTH  = profondeur du classement national chargé */
  CHECKMATE_COUNTRY: "FR",
  CHECKMATE_COUNTRY_DEPTH: 100,
  /* Pays proposés dans le filtre du jeu Checkmate : le classement national de
     chacun se charge à la demande (le classement mondial chargé ne contient que
     ~20 pays). Ajouter/retirer librement des codes ISO à 2 lettres. */
  CHECKMATE_COUNTRIES: ["FR","BE","CH","LU","MC","CA","US","GB","IE","DE","AT","NL",
                        "ES","PT","IT","GR","PL","RO","CZ","HU","SE","NO","DK","FI",
                        "RU","UA","TR","IL","EG","MA","DZ","TN","SN","CI","CM","NG",
                        "ZA","SA","AE","QA","IN","PK","BD","ID","PH","VN","TH","CN",
                        "JP","KR","AU","BR","AR","CL","CO","PE","MX"],

  /* Rafraîchissement automatique du classement (secondes) */
  AUTO_REFRESH_SEC: 60,
};
