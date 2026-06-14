"""Couche d'acces a la base SQLite."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "devis.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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

        CREATE TABLE IF NOT EXISTS profil (
            id                   INTEGER PRIMARY KEY CHECK (id = 1),
            nom_entreprise       TEXT NOT NULL DEFAULT '',
            gerant               TEXT NOT NULL DEFAULT '',
            adresse              TEXT NOT NULL DEFAULT '',
            telephone            TEXT NOT NULL DEFAULT '',
            email                TEXT NOT NULL DEFAULT '',
            siret                TEXT NOT NULL DEFAULT '',
            iban                 TEXT NOT NULL DEFAULT '',
            conditions_paiement  TEXT NOT NULL DEFAULT '',
            logo_path            TEXT NOT NULL DEFAULT '',
            mail_server          TEXT NOT NULL DEFAULT '',
            mail_port            TEXT NOT NULL DEFAULT '',
            mail_username        TEXT NOT NULL DEFAULT '',
            mail_password        TEXT NOT NULL DEFAULT '',
            mail_from            TEXT NOT NULL DEFAULT ''
        );
        """
    )

    # Migration : ajoute les colonnes SMTP aux bases profil deja existantes.
    _ensure_columns(conn, "profil", {
        "mail_server": "TEXT NOT NULL DEFAULT ''",
        "mail_port": "TEXT NOT NULL DEFAULT ''",
        "mail_username": "TEXT NOT NULL DEFAULT ''",
        "mail_password": "TEXT NOT NULL DEFAULT ''",
        "mail_from": "TEXT NOT NULL DEFAULT ''",
    })

    conn.commit()
    conn.close()


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
