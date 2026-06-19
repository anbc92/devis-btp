"""Insere un devis d'exemple pour la demo (idempotent)."""

from datetime import date

from db import init_db, get_db


def run():
    init_db()
    conn = get_db()
    if conn.execute("SELECT COUNT(*) c FROM devis").fetchone()["c"] > 0:
        print("Base deja peuplee, rien a faire.")
        conn.close()
        return

    cur = conn.execute(
        "INSERT INTO devis (numero, client_nom, client_adresse, client_email, "
        "statut, notes, date_creation) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("DEV-2026-0001", "SARL Dubois",
         "15 avenue de la République\n75011 Paris",
         "contact@dubois.fr", "envoye",
         "Chantier rénovation salle de bain - 2ème étage.",
         date.today().strftime("%d/%m/%Y")),
    )
    did = cur.lastrowid
    rows = [
        ("Dépose ancien carrelage et évacuation", 12, 18.0, 10.0),
        ("Pose carrelage sol 60x60 (fourni et posé)", 12, 45.0, 10.0),
        ("Fourniture et pose faïence murale", 8, 32.5, 10.0),
        ("Plomberie - raccordements et robinetterie", 1, 350.0, 20.0),
    ]
    for i, (d, q, pu, t) in enumerate(rows):
        conn.execute(
            "INSERT INTO prestations (devis_id, designation, quantite, "
            "prix_unitaire, tva_taux, position) VALUES (?, ?, ?, ?, ?, ?)",
            (did, d, q, pu, t, i),
        )
    conn.commit()
    conn.close()
    print(f"Devis d'exemple cree (id={did}).")


if __name__ == "__main__":
    run()
