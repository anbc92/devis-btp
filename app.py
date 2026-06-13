"""Application web de generation de devis pour artisans BTP (V1)."""

from datetime import date

from flask import (
    Flask, render_template, request, redirect, url_for, send_file, abort, flash
)

from db import get_db, init_db
from calculs import calcul_totaux, ligne_total_ht, fmt_euro
from pdf import generer_pdf
from config import ARTISAN, CONDITIONS, TAUX_TVA, STATUTS

app = Flask(__name__)
app.secret_key = "devis-btp-v1-change-me"

# Filtres Jinja
app.jinja_env.filters["euro"] = fmt_euro
app.jinja_env.globals.update(STATUTS=STATUTS, ARTISAN=ARTISAN)


def _statut_label(statut):
    return {"brouillon": "Brouillon", "envoye": "Envoye",
            "accepte": "Accepte", "refuse": "Refuse"}.get(statut, statut)


app.jinja_env.filters["statut_label"] = _statut_label


def _charger_prestations(devis_id, conn):
    rows = conn.execute(
        "SELECT * FROM prestations WHERE devis_id = ? ORDER BY position, id",
        (devis_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _parse_lignes(form):
    """Extrait les lignes de prestation du formulaire (champs en tableaux)."""
    designations = form.getlist("designation[]")
    quantites = form.getlist("quantite[]")
    prix = form.getlist("prix_unitaire[]")
    tva = form.getlist("tva_taux[]")
    lignes = []
    for i, des in enumerate(designations):
        des = (des or "").strip()
        if not des:
            continue
        try:
            q = float((quantites[i] or "0").replace(",", "."))
            pu = float((prix[i] or "0").replace(",", "."))
            t = float((tva[i] or "0").replace(",", "."))
        except (ValueError, IndexError):
            continue
        lignes.append({"designation": des, "quantite": q,
                       "prix_unitaire": pu, "tva_taux": t})
    return lignes


@app.route("/")
def index():
    conn = get_db()
    rows = conn.execute("SELECT * FROM devis ORDER BY id DESC").fetchall()
    devis_list = []
    for r in rows:
        d = dict(r)
        prestations = _charger_prestations(d["id"], conn)
        d["totaux"] = calcul_totaux(prestations)
        d["nb_lignes"] = len(prestations)
        devis_list.append(d)
    conn.close()
    return render_template("index.html", devis_list=devis_list)


@app.route("/devis/nouveau", methods=["GET", "POST"])
def nouveau():
    if request.method == "POST":
        client_nom = request.form.get("client_nom", "").strip()
        if not client_nom:
            flash("Le nom du client est obligatoire.", "error")
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form, mode="nouveau")

        lignes = _parse_lignes(request.form)
        if not lignes:
            flash("Ajoutez au moins une prestation.", "error")
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form, mode="nouveau")

        conn = get_db()
        annee = date.today().year
        n = conn.execute("SELECT COUNT(*) AS c FROM devis").fetchone()["c"] + 1
        numero = f"DEV-{annee}-{n:04d}"
        cur = conn.execute(
            "INSERT INTO devis (numero, client_nom, client_adresse, "
            "client_email, statut, notes, date_creation) "
            "VALUES (?, ?, ?, ?, 'brouillon', ?, ?)",
            (numero, client_nom,
             request.form.get("client_adresse", "").strip(),
             request.form.get("client_email", "").strip(),
             request.form.get("notes", "").strip(),
             date.today().strftime("%d/%m/%Y")),
        )
        devis_id = cur.lastrowid
        for pos, lg in enumerate(lignes):
            conn.execute(
                "INSERT INTO prestations (devis_id, designation, quantite, "
                "prix_unitaire, tva_taux, position) VALUES (?, ?, ?, ?, ?, ?)",
                (devis_id, lg["designation"], lg["quantite"],
                 lg["prix_unitaire"], lg["tva_taux"], pos),
            )
        conn.commit()
        conn.close()
        flash(f"Devis {numero} cree.", "ok")
        return redirect(url_for("voir", devis_id=devis_id))

    return render_template("form.html", taux_tva=TAUX_TVA, form={}, mode="nouveau")


@app.route("/devis/<int:devis_id>")
def voir(devis_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM devis WHERE id = ?", (devis_id,)).fetchone()
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    prestations = _charger_prestations(devis_id, conn)
    conn.close()
    for p in prestations:
        p["total_ht"] = ligne_total_ht(p)
    totaux = calcul_totaux(prestations)
    return render_template("voir.html", devis=devis, prestations=prestations,
                           totaux=totaux, conditions=CONDITIONS)


@app.route("/devis/<int:devis_id>/statut", methods=["POST"])
def changer_statut(devis_id):
    statut = request.form.get("statut")
    if statut not in STATUTS:
        abort(400)
    conn = get_db()
    conn.execute("UPDATE devis SET statut = ? WHERE id = ?", (statut, devis_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))


@app.route("/devis/<int:devis_id>/supprimer", methods=["POST"])
def supprimer(devis_id):
    conn = get_db()
    conn.execute("DELETE FROM devis WHERE id = ?", (devis_id,))
    conn.commit()
    conn.close()
    flash("Devis supprime.", "ok")
    return redirect(url_for("index"))


@app.route("/devis/<int:devis_id>/pdf")
def pdf_devis(devis_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM devis WHERE id = ?", (devis_id,)).fetchone()
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    prestations = _charger_prestations(devis_id, conn)
    conn.close()
    buf = generer_pdf(devis, prestations)
    return send_file(buf, mimetype="application/pdf",
                     download_name=f"{devis['numero']}.pdf", as_attachment=False)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
