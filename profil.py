"""Profil de l'artisan : valeurs editables stockees en base SQLite.

Ces valeurs remplacent celles codees en dur dans config.py. Les champs non
proposes dans le formulaire /profil (TVA intra, APE, validite, acompte,
mentions legales) restent fournis par config.py comme valeurs par defaut.
"""

import os
from pathlib import Path

from db import get_db
from config import ARTISAN, CONDITIONS
from chiffrement import chiffrer, dechiffrer

# Champs texte editables via le formulaire /profil (= colonnes de la table profil)
CHAMPS_PROFIL_TEXTE = [
    "nom_entreprise", "gerant", "adresse", "telephone", "email",
    "siret", "iban", "conditions_paiement", "assurance_decennale",
    "assureur_nom",
]

# Case a cocher (auto-entrepreneur) : stockee en entier 0/1.
CHAMPS_PROFIL_BOOL = ["auto_entrepreneur"]

# Ensemble des champs lus depuis le formulaire /profil.
CHAMPS_PROFIL = CHAMPS_PROFIL_TEXTE + CHAMPS_PROFIL_BOOL

# Champs SMTP editables via le formulaire /parametres
CHAMPS_SMTP = [
    "mail_server", "mail_port", "mail_username", "mail_password", "mail_from",
]

# Valeurs SMTP par defaut : variables d'environnement
_SMTP_ENV = {
    "mail_server": "MAIL_SERVER",
    "mail_port": "MAIL_PORT",
    "mail_username": "MAIL_USERNAME",
    "mail_password": "MAIL_PASSWORD",
    "mail_from": "MAIL_FROM",
}

# Racine des fichiers media (logos). Sur un volume persistant (Railway),
# definir MEDIA_ROOT=/app/data pour que les logos survivent aux redeploiements.
# En local, defaut = dossier static/ (servi par Flask).
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT") or (Path(__file__).parent / "static"))


def _coerce_bool(valeur):
    """Convertit une valeur de formulaire (case a cocher) en entier 0/1."""
    return 1 if str(valeur or "").strip().lower() in ("1", "on", "true", "yes") else 0


def logo_rel(user_id):
    """Chemin relatif (a /static) du logo d'un utilisateur."""
    return f"uploads/logo_{user_id}.png"


def _defauts():
    """Valeurs par defaut issues de config.py."""
    return {
        "nom_entreprise": ARTISAN["nom"],
        "gerant": ARTISAN["gerant"],
        "adresse": f"{ARTISAN['adresse']}\n{ARTISAN['code_postal']} {ARTISAN['ville']}",
        "telephone": ARTISAN["telephone"],
        "email": ARTISAN["email"],
        "siret": ARTISAN["siret"],
        "iban": ARTISAN["iban"],
        "conditions_paiement": CONDITIONS["paiement"],
        "assurance_decennale": "",
        "assureur_nom": "",
        "auto_entrepreneur": 0,
        "logo_path": "",
    }


def _ligne_profil(user_id):
    """Ligne brute de profil pour un utilisateur (ou profil legacy si None)."""
    conn = get_db()
    if user_id is None:
        row = conn.execute(
            "SELECT * FROM profil WHERE user_id IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM profil WHERE user_id = ?", (user_id,)
        ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_profil(user_id=None):
    """Renvoie le profil complet (base + defauts config) pour un utilisateur.

    Inclut aussi les champs non editables encore lus depuis config.py
    (tva_intra, ape, validite_jours, acompte_pct, mentions).
    """
    data = _ligne_profil(user_id)

    for cle, defaut in _defauts().items():
        valeur = data.get(cle)
        if not (valeur or "").strip() if isinstance(valeur, str) else not valeur:
            data[cle] = defaut

    # Champs non editables : toujours depuis config.py
    data["tva_intra"] = ARTISAN["tva_intra"]
    data["ape"] = ARTISAN["ape"]
    data["validite_jours"] = CONDITIONS["validite_jours"]
    data["acompte_pct"] = CONDITIONS["acompte_pct"]
    data["mentions"] = CONDITIONS["mentions"]
    data["mention_tva"] = CONDITIONS["mention_tva"]

    # Config SMTP : valeur en base sinon variable d'environnement.
    # Le mot de passe est dechiffre (stocke chiffre au repos).
    for cle, env in _SMTP_ENV.items():
        if cle == "mail_password":
            valeur = dechiffrer(data.get(cle) or "")
        else:
            valeur = (data.get(cle) or "").strip()
        data[cle] = valeur or os.environ.get(env, "").strip()
    if not data["mail_from"]:
        data["mail_from"] = data["mail_username"]
    return data


def save_profil(user_id, valeurs, logo_path=None):
    """Enregistre (upsert) le profil de `user_id`. `valeurs` : CHAMPS_PROFIL.

    Si `logo_path` est fourni (chemin relatif), il est mis a jour ; sinon
    le logo existant est conserve.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT logo_path FROM profil WHERE user_id = ?", (user_id,)
    ).fetchone()
    logo = logo_path if logo_path is not None else (row["logo_path"] if row else "")
    params = {c: (valeurs.get(c) or "").strip() for c in CHAMPS_PROFIL_TEXTE}
    for c in CHAMPS_PROFIL_BOOL:
        params[c] = _coerce_bool(valeurs.get(c))
    params["user_id"] = user_id
    params["logo_path"] = logo
    conn.execute(
        """
        INSERT INTO profil (user_id, nom_entreprise, gerant, adresse, telephone,
                            email, siret, iban, conditions_paiement,
                            assurance_decennale, assureur_nom, auto_entrepreneur,
                            logo_path)
        VALUES (:user_id, :nom_entreprise, :gerant, :adresse, :telephone, :email,
                :siret, :iban, :conditions_paiement, :assurance_decennale,
                :assureur_nom, :auto_entrepreneur, :logo_path)
        ON CONFLICT(user_id) DO UPDATE SET
            nom_entreprise = excluded.nom_entreprise,
            gerant = excluded.gerant,
            adresse = excluded.adresse,
            telephone = excluded.telephone,
            email = excluded.email,
            siret = excluded.siret,
            iban = excluded.iban,
            conditions_paiement = excluded.conditions_paiement,
            assurance_decennale = excluded.assurance_decennale,
            assureur_nom = excluded.assureur_nom,
            auto_entrepreneur = excluded.auto_entrepreneur,
            logo_path = excluded.logo_path
        """,
        params,
    )
    conn.commit()
    conn.close()


def save_smtp(user_id, valeurs):
    """Enregistre (upsert) la config SMTP de `user_id`. `valeurs` : CHAMPS_SMTP.

    Le mot de passe est chiffre avant stockage. S'il est laisse vide, l'ancien
    mot de passe est conserve. Ne touche pas aux autres colonnes du profil.
    """
    conn = get_db()
    nouveau_mdp = (valeurs.get("mail_password") or "").strip()
    if nouveau_mdp:
        mdp_stocke = chiffrer(nouveau_mdp)
    else:
        row = conn.execute(
            "SELECT mail_password FROM profil WHERE user_id = ?", (user_id,)
        ).fetchone()
        mdp_stocke = row["mail_password"] if row else ""

    vals = {c: (valeurs.get(c) or "").strip() for c in CHAMPS_SMTP}
    vals["mail_password"] = mdp_stocke
    vals["user_id"] = user_id
    conn.execute(
        """
        INSERT INTO profil (user_id, mail_server, mail_port, mail_username,
                            mail_password, mail_from)
        VALUES (:user_id, :mail_server, :mail_port, :mail_username,
                :mail_password, :mail_from)
        ON CONFLICT(user_id) DO UPDATE SET
            mail_server = excluded.mail_server,
            mail_port = excluded.mail_port,
            mail_username = excluded.mail_username,
            mail_password = excluded.mail_password,
            mail_from = excluded.mail_from
        """,
        vals,
    )
    conn.commit()
    conn.close()


def logo_fs_path(profil):
    """Chemin systeme du logo si present, sinon None."""
    rel = (profil or {}).get("logo_path") or ""
    if not rel:
        return None
    p = MEDIA_ROOT / rel
    return str(p) if p.exists() else None
