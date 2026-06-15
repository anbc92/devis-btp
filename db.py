"""Couche d'acces a la base SQLite."""

import os
import sqlite3
from pathlib import Path

# Chemin de la base : surchargeable via DB_PATH (utile en conteneur/volume).
DB_PATH = Path(os.environ.get("DB_PATH") or (Path(__file__).parent / "devis.db"))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Schema de la table profil (rattachee a un utilisateur via user_id).
PROFIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS profil (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER UNIQUE,
    nom_entreprise       TEXT NOT NULL DEFAULT '',
    gerant               TEXT NOT NULL DEFAULT '',
    adresse              TEXT NOT NULL DEFAULT '',
    telephone            TEXT NOT NULL DEFAULT '',
    email                TEXT NOT NULL DEFAULT '',
    siret                TEXT NOT NULL DEFAULT '',
    iban                 TEXT NOT NULL DEFAULT '',
    conditions_paiement  TEXT NOT NULL DEFAULT '',
    assurance_decennale  TEXT NOT NULL DEFAULT '',
    assureur_nom         TEXT NOT NULL DEFAULT '',
    auto_entrepreneur    INTEGER NOT NULL DEFAULT 0,
    logo_path            TEXT NOT NULL DEFAULT '',
    mail_server          TEXT NOT NULL DEFAULT '',
    mail_port            TEXT NOT NULL DEFAULT '',
    mail_username        TEXT NOT NULL DEFAULT '',
    mail_password        TEXT NOT NULL DEFAULT '',
    mail_from            TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

# Colonnes de profil susceptibles d'etre copiees lors d'une migration.
_PROFIL_COLONNES = [
    "nom_entreprise", "gerant", "adresse", "telephone", "email", "siret",
    "iban", "conditions_paiement", "assurance_decennale", "assureur_nom",
    "auto_entrepreneur", "logo_path", "mail_server", "mail_port",
    "mail_username", "mail_password", "mail_from",
]


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS devis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            numero          TEXT    NOT NULL UNIQUE,
            client_nom      TEXT    NOT NULL,
            client_adresse  TEXT    NOT NULL DEFAULT '',
            client_email    TEXT    NOT NULL DEFAULT '',
            statut          TEXT    NOT NULL DEFAULT 'brouillon',
            notes           TEXT    NOT NULL DEFAULT '',
            date_creation   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prestations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            devis_id        INTEGER NOT NULL,
            designation     TEXT    NOT NULL,
            quantite        REAL    NOT NULL DEFAULT 1,
            prix_unitaire   REAL    NOT NULL DEFAULT 0,
            tva_taux        REAL    NOT NULL DEFAULT 20,
            position        INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (devis_id) REFERENCES devis(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT    NOT NULL UNIQUE,
            password_hash   TEXT    NOT NULL,
            nom_entreprise  TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash  TEXT    PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            expires_at  TEXT    NOT NULL,
            used        INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS compteurs (
            annee    INTEGER PRIMARY KEY,
            dernier  INTEGER NOT NULL DEFAULT 0
        );
        """
    )

    # Profil : creation (nouveau schema) ou migration depuis l'ancien (id=1).
    _migrer_profil(conn)

    # Migration douce : colonnes de conformite reglementaire sur profil
    # (assurance decennale, auto-entrepreneur) pour les bases existantes.
    _ensure_columns(conn, "profil", {
        "assurance_decennale": "TEXT NOT NULL DEFAULT ''",
        "assureur_nom": "TEXT NOT NULL DEFAULT ''",
        "auto_entrepreneur": "INTEGER NOT NULL DEFAULT 0",
    })

    # Migration douce : colonne user_id sur devis (les anciens devis -> NULL)
    # et champs de conformite (validite, dates/delai de chantier).
    _ensure_columns(conn, "devis", {
        "user_id": "INTEGER",
        "validite_jours": "INTEGER NOT NULL DEFAULT 30",
        "date_debut_travaux": "TEXT NOT NULL DEFAULT ''",
        "delai_execution": "TEXT NOT NULL DEFAULT ''",
    })

    # Initialise les compteurs a partir des numeros de devis existants.
    _init_compteurs(conn)

    conn.commit()
    conn.close()


def _init_compteurs(conn):
    """Aligne le compteur de chaque annee sur le plus grand numero existant.

    Garantit que la prochaine sequence ne reutilisera jamais un numero deja
    attribue (evite les collisions avec les devis existants).
    """
    maxima = {}
    for r in conn.execute("SELECT numero FROM devis"):
        try:
            _, annee, seq = (r["numero"] or "").split("-")
            annee, seq = int(annee), int(seq)
        except (ValueError, AttributeError):
            continue
        maxima[annee] = max(maxima.get(annee, 0), seq)
    for annee, m in maxima.items():
        conn.execute(
            "INSERT INTO compteurs (annee, dernier) VALUES (?, ?) "
            "ON CONFLICT(annee) DO UPDATE SET dernier = MAX(dernier, excluded.dernier)",
            (annee, m),
        )


def _migrer_profil(conn):
    """Cree la table profil au nouveau schema, ou migre l'ancien (sans user_id).

    L'ancienne ligne unique (id=1) est conservee en tant que profil "legacy"
    (user_id NULL) : aucune donnee n'est perdue.
    """
    colonnes = {r["name"] for r in conn.execute("PRAGMA table_info(profil)")}

    if not colonnes:
        conn.executescript(PROFIL_SCHEMA)
        return
    if "user_id" in colonnes:
        return  # deja au nouveau schema

    # Ancien schema -> reconstruction en preservant les donnees existantes.
    conn.execute("ALTER TABLE profil RENAME TO profil_old")
    conn.executescript(PROFIL_SCHEMA)
    communes = [c for c in _PROFIL_COLONNES if c in colonnes]
    if communes:
        liste = ", ".join(communes)
        conn.execute(
            f"INSERT INTO profil (user_id, {liste}) "
            f"SELECT NULL, {liste} FROM profil_old"
        )
    conn.execute("DROP TABLE profil_old")


def _ensure_columns(conn, table, colonnes):
    """Ajoute via ALTER TABLE les colonnes manquantes (migration legere)."""
    existantes = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for nom, ddl in colonnes.items():
        if nom not in existantes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom} {ddl}")


def next_numero(conn):
    """Genere un numero de devis : DEV-AAAA-NNNN (annee fixee a la creation)."""
    row = conn.execute("SELECT COUNT(*) AS n FROM devis").fetchone()
    seq = row["n"] + 1
    # L'annee est passee par l'appelant pour eviter l'usage direct d'une date ici.
    return seq
