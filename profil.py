"""Profil de l'artisan : valeurs editables stockees en base SQLite.

Ces valeurs remplacent celles codees en dur dans config.py. Les champs non
proposes dans le formulaire /profil (TVA intra, APE, validite, acompte,
mentions legales) restent fournis par config.py comme valeurs par defaut.
"""

import os
from pathlib import Path

from db import get_db
from config import ARTISAN, CONDITIONS

# Champs editables via le formulaire /profil (= colonnes de la table profil)
CHAMPS_PROFIL = [
    "nom_entreprise", "gerant", "adresse", "telephone", "email",
    "siret", "iban", "conditions_paiement",
]

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

STATIC_DIR = Path(__file__).parent / "static"
LOGO_REL = "uploads/logo.png"  # chemin relatif a /static


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
        "logo_path": "",
    }


def get_profil():
    """Renvoie le profil complet (base + defauts config) sous forme de dict.

    Inclut aussi les champs non editables encore lus depuis config.py
    (tva_intra, ape, validite_jours, acompte_pct, mentions).
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM profil WHERE id = 1").fetchone()
    conn.close()
    data = dict(row) if row else {}

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

    # Config SMTP : valeur en base sinon variable d'environnement
    for cle, env in _SMTP_ENV.items():
        valeur = (data.get(cle) or "").strip()
        data[cle] = valeur or os.environ.get(env, "").strip()
    if not data["mail_from"]:
        data["mail_from"] = data["mail_username"]
    return data


def save_profil(valeurs, logo_path=None):
    """Enregistre (upsert) le profil. `valeurs` : mapping des CHAMPS_PROFIL.

    Si `logo_path` est fourni (chemin relatif), il est mis a jour ; sinon
    le logo existant est conserve.
    """
    conn = get_db()
    row = conn.execute("SELECT logo_path FROM profil WHERE id = 1").fetchone()
    logo = logo_path if logo_path is not None else (row["logo_path"] if row else "")
    conn.execute(
        """
        INSERT INTO profil (id, nom_entreprise, gerant, adresse, telephone,
                            email, siret, iban, conditions_paiement, logo_path)
        VALUES (1, :nom_entreprise, :gerant, :adresse, :telephone, :email,
                :siret, :iban, :conditions_paiement, :logo_path)
        ON CONFLICT(id) DO UPDATE SET
            nom_entreprise = excluded.nom_entreprise,
            gerant = excluded.gerant,
            adresse = excluded.adresse,
            telephone = excluded.telephone,
            email = excluded.email,
            siret = excluded.siret,
            iban = excluded.iban,
            conditions_paiement = excluded.conditions_paiement,
            logo_path = excluded.logo_path
        """,
        {**{c: (valeurs.get(c) or "").strip() for c in CHAMPS_PROFIL},
         "logo_path": logo},
    )
    conn.commit()
    conn.close()


def get_profil_raw():
    """Renvoie les valeurs BRUTES stockees en base (sans fusion/defaut/env).

    Utile pour diagnostiquer ce qui est reellement enregistre.
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM profil WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def save_smtp(valeurs):
    """Enregistre (upsert) la config SMTP. `valeurs` : mapping des CHAMPS_SMTP.

    Ne touche pas aux autres colonnes du profil (en cas de conflit sur id=1).
    """
    conn = get_db()
    conn.execute(
        """
        INSERT INTO profil (id, mail_server, mail_port, mail_username,
                            mail_password, mail_from)
        VALUES (1, :mail_server, :mail_port, :mail_username, :mail_password,
                :mail_from)
        ON CONFLICT(id) DO UPDATE SET
            mail_server = excluded.mail_server,
            mail_port = excluded.mail_port,
            mail_username = excluded.mail_username,
            mail_password = excluded.mail_password,
            mail_from = excluded.mail_from
        """,
        {c: (valeurs.get(c) or "").strip() for c in CHAMPS_SMTP},
    )
    conn.commit()
    conn.close()


def logo_fs_path(profil):
    """Chemin systeme du logo si present, sinon None."""
    rel = (profil or {}).get("logo_path") or ""
    if not rel:
        return None
    p = STATIC_DIR / rel
    return str(p) if p.exists() else None
