"""Application web de generation de devis pour artisans BTP (V1)."""

import os
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (
    Flask, render_template, request, redirect, url_for, send_file, abort,
    flash, session,
)

from db import get_db, init_db
from calculs import calcul_totaux, ligne_total_ht, fmt_euro
from pdf import generer_pdf
from profil import (
    get_profil, get_profil_raw, save_profil, save_smtp, logo_rel,
    CHAMPS_PROFIL, CHAMPS_SMTP,
)
from mail import envoyer_devis, config_smtp_ok, debug_config_smtp, MailError
from config import ARTISAN, CONDITIONS, TAUX_TVA, STATUTS

app = Flask(__name__)
# Cle secrete des sessions : depuis l'environnement en production.
# La valeur de repli ne sert qu'au developpement local.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

# Cree les tables manquantes (dont users/profil) au demarrage, quel que soit
# le mode de lancement (python app.py, flask run, serveur WSGI...).
init_db()

# Upload logo
UPLOAD_DIR = Path(app.static_folder) / "uploads"
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 Mo
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Filtres Jinja
app.jinja_env.filters["euro"] = fmt_euro
app.jinja_env.globals.update(STATUTS=STATUTS, ARTISAN=ARTISAN)


def current_user_id():
    """ID de l'utilisateur connecte, ou None."""
    return session.get("user_id")


def login_required(view):
    """Protege une route : redirige vers /connexion si non authentifie."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user_id():
            return redirect(url_for("connexion", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def injecter_profil():
    """Rend le profil de l'utilisateur connecte disponible dans les templates."""
    return {"profil": get_profil(current_user_id())}


def _charger_devis_accessible(conn, devis_id):
    """Renvoie le devis si l'utilisateur courant peut y acceder, sinon None.

    Acces autorise pour ses propres devis et les devis legacy (user_id NULL).
    """
    row = conn.execute("SELECT * FROM devis WHERE id = ?", (devis_id,)).fetchone()
    if row is None:
        return None
    if row["user_id"] is not None and row["user_id"] != current_user_id():
        return None
    return row


def _statut_label(statut):
    return {"brouillon": "Brouillon", "envoye": "Envoyé",
            "accepte": "Accepté", "refuse": "Refusé"}.get(statut, statut)


app.jinja_env.filters["statut_label"] = _statut_label


def _charger_prestations(devis_id, conn):
    rows = conn.execute(
        "SELECT * FROM prestations WHERE devis_id = ? ORDER BY position, id",
        (devis_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _generer_numero(conn):
    """Numero de devis : DEV-AAAA-NNNN (annee courante, sequence globale)."""
    annee = date.today().year
    n = conn.execute("SELECT COUNT(*) AS c FROM devis").fetchone()["c"] + 1
    return f"DEV-{annee}-{n:04d}"


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


@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if current_user_id():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        mdp = request.form.get("password", "")
        nom = request.form.get("nom_entreprise", "").strip()

        if not email or not mdp or not nom:
            flash("Tous les champs sont obligatoires.", "error")
            return render_template("inscription.html", form=request.form)
        if len(mdp) < 6:
            flash("Le mot de passe doit faire au moins 6 caractères.", "error")
            return render_template("inscription.html", form=request.form)

        conn = get_db()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            conn.close()
            flash("Un compte existe déjà avec cet e-mail.", "error")
            return render_template("inscription.html", form=request.form)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, nom_entreprise, created_at) "
            "VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(mdp), nom,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        uid = cur.lastrowid
        conn.commit()
        conn.close()

        # Cree le profil de l'utilisateur avec son nom d'entreprise.
        save_profil(uid, {"nom_entreprise": nom})

        session.clear()
        session["user_id"] = uid
        session["email"] = email
        flash("Compte créé. Bienvenue !", "ok")
        return redirect(url_for("index"))

    return render_template("inscription.html", form={})


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if current_user_id():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        mdp = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user is None or not check_password_hash(user["password_hash"], mdp):
            flash("E-mail ou mot de passe incorrect.", "error")
            return render_template("connexion.html", form={"email": email})

        session.clear()
        session["user_id"] = user["id"]
        session["email"] = user["email"]
        flash("Connecté.", "ok")

        nxt = request.args.get("next") or ""
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = url_for("index")
        return redirect(nxt)

    return render_template("connexion.html", form={})


@app.route("/deconnexion")
def deconnexion():
    session.clear()
    flash("Déconnecté.", "ok")
    return redirect(url_for("connexion"))


@app.route("/")
def accueil():
    return render_template("accueil.html")


@app.route("/devis")
@login_required
def index():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM devis WHERE user_id = ? OR user_id IS NULL ORDER BY id DESC",
        (current_user_id(),),
    ).fetchall()
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
@login_required
def nouveau():
    if request.method == "POST":
        client_nom = request.form.get("client_nom", "").strip()
        if not client_nom:
            flash("Le nom du client est obligatoire.", "error")
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form,
                                   prestations=_parse_lignes(request.form),
                                   mode="nouveau")

        lignes = _parse_lignes(request.form)
        if not lignes:
            flash("Ajoutez au moins une prestation.", "error")
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form,
                                   prestations=_parse_lignes(request.form),
                                   mode="nouveau")

        conn = get_db()
        numero = _generer_numero(conn)
        cur = conn.execute(
            "INSERT INTO devis (numero, client_nom, client_adresse, "
            "client_email, statut, notes, date_creation, user_id) "
            "VALUES (?, ?, ?, ?, 'brouillon', ?, ?, ?)",
            (numero, client_nom,
             request.form.get("client_adresse", "").strip(),
             request.form.get("client_email", "").strip(),
             request.form.get("notes", "").strip(),
             date.today().strftime("%d/%m/%Y"),
             current_user_id()),
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
        flash(f"Devis {numero} créé.", "ok")
        return redirect(url_for("voir", devis_id=devis_id))

    return render_template("form.html", taux_tva=TAUX_TVA, form={}, mode="nouveau")


@app.route("/devis/<int:devis_id>")
@login_required
def voir(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
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
                           totaux=totaux, conditions=CONDITIONS,
                           smtp_ok=config_smtp_ok(get_profil(current_user_id())))


@app.route("/devis/<int:devis_id>/envoyer", methods=["POST"])
@login_required
def envoyer(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    prestations = _charger_prestations(devis_id, conn)

    destinataire = request.form.get("destinataire", "").strip()
    objet = request.form.get("objet", "").strip() or f"Devis {devis['numero']}"
    message = request.form.get("message", "").strip()

    # --- DEBUG : valeurs SMTP lues a la tentative d'envoi ---
    prof = get_profil(current_user_id())
    debug_config_smtp(get_profil_raw(current_user_id()),
                      f"valeurs BRUTES en base (devis {devis['numero']})")
    debug_config_smtp(prof,
                      f"valeurs RESOLUES base+env (devis {devis['numero']})")
    app.logger.warning("  destinataire saisi = %r", destinataire)

    pdf_buf = generer_pdf(devis, prestations)
    try:
        envoyer_devis(prof, destinataire, objet, message,
                      pdf_buf.getvalue(), f"{devis['numero']}.pdf")
    except MailError as exc:
        conn.close()
        flash(str(exc), "error")
        return redirect(url_for("voir", devis_id=devis_id))

    conn.execute("UPDATE devis SET statut = 'envoye' WHERE id = ?", (devis_id,))
    conn.commit()
    conn.close()
    flash(f"Devis envoyé à {destinataire}.", "ok")
    return redirect(url_for("voir", devis_id=devis_id))


@app.route("/devis/<int:devis_id>/dupliquer", methods=["POST"])
@login_required
def dupliquer(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)
    src = dict(row)
    prestations = _charger_prestations(devis_id, conn)

    numero = _generer_numero(conn)
    cur = conn.execute(
        "INSERT INTO devis (numero, client_nom, client_adresse, client_email, "
        "statut, notes, date_creation, user_id) "
        "VALUES (?, ?, ?, ?, 'brouillon', ?, ?, ?)",
        (numero, src["client_nom"], src["client_adresse"], src["client_email"],
         src["notes"], date.today().strftime("%d/%m/%Y"), current_user_id()),
    )
    nouveau_id = cur.lastrowid
    for pos, p in enumerate(prestations):
        conn.execute(
            "INSERT INTO prestations (devis_id, designation, quantite, "
            "prix_unitaire, tva_taux, position) VALUES (?, ?, ?, ?, ?, ?)",
            (nouveau_id, p["designation"], p["quantite"],
             p["prix_unitaire"], p["tva_taux"], pos),
        )
    conn.commit()
    conn.close()
    flash(f"Devis dupliqué : {numero}.", "ok")
    return redirect(url_for("voir", devis_id=nouveau_id))


@app.route("/devis/<int:devis_id>/modifier", methods=["GET", "POST"])
@login_required
def modifier(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        client_nom = request.form.get("client_nom", "").strip()
        if not client_nom:
            flash("Le nom du client est obligatoire.", "error")
            conn.close()
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form,
                                   prestations=_parse_lignes(request.form),
                                   mode="modifier", devis_id=devis_id)

        lignes = _parse_lignes(request.form)
        if not lignes:
            flash("Ajoutez au moins une prestation.", "error")
            conn.close()
            return render_template("form.html", taux_tva=TAUX_TVA,
                                   form=request.form,
                                   prestations=lignes,
                                   mode="modifier", devis_id=devis_id)

        conn.execute(
            "UPDATE devis SET client_nom = ?, client_adresse = ?, "
            "client_email = ?, notes = ? WHERE id = ?",
            (client_nom,
             request.form.get("client_adresse", "").strip(),
             request.form.get("client_email", "").strip(),
             request.form.get("notes", "").strip(),
             devis_id),
        )
        conn.execute("DELETE FROM prestations WHERE devis_id = ?", (devis_id,))
        for pos, lg in enumerate(lignes):
            conn.execute(
                "INSERT INTO prestations (devis_id, designation, quantite, "
                "prix_unitaire, tva_taux, position) VALUES (?, ?, ?, ?, ?, ?)",
                (devis_id, lg["designation"], lg["quantite"],
                 lg["prix_unitaire"], lg["tva_taux"], pos),
            )
        conn.commit()
        numero = row["numero"]
        conn.close()
        flash(f"Devis {numero} mis à jour.", "ok")
        return redirect(url_for("voir", devis_id=devis_id))

    devis = dict(row)
    prestations = _charger_prestations(devis_id, conn)
    conn.close()
    return render_template("form.html", taux_tva=TAUX_TVA, form=devis,
                           prestations=prestations, mode="modifier",
                           devis_id=devis_id)


@app.route("/devis/<int:devis_id>/statut", methods=["POST"])
@login_required
def changer_statut(devis_id):
    statut = request.form.get("statut")
    if statut not in STATUTS:
        abort(400)
    conn = get_db()
    if _charger_devis_accessible(conn, devis_id) is None:
        conn.close()
        abort(404)
    conn.execute("UPDATE devis SET statut = ? WHERE id = ?", (statut, devis_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("index"))


@app.route("/devis/<int:devis_id>/supprimer", methods=["POST"])
@login_required
def supprimer(devis_id):
    conn = get_db()
    if _charger_devis_accessible(conn, devis_id) is None:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM devis WHERE id = ?", (devis_id,))
    conn.commit()
    conn.close()
    flash("Devis supprimé.", "ok")
    return redirect(url_for("index"))


@app.route("/devis/<int:devis_id>/pdf")
@login_required
def pdf_devis(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    prestations = _charger_prestations(devis_id, conn)
    conn.close()
    buf = generer_pdf(devis, prestations)
    return send_file(buf, mimetype="application/pdf",
                     download_name=f"{devis['numero']}.pdf", as_attachment=False)


def _enregistrer_logo(fichier, user_id):
    """Valide et sauvegarde le logo de l'utilisateur en static/uploads/.

    Renvoie (logo_rel, erreur). logo_rel est None si aucun fichier valide
    n'a ete fourni (le logo existant doit alors etre conserve).
    """
    if fichier is None or not fichier.filename:
        return None, None

    ext = Path(fichier.filename).suffix.lower()
    if ext not in LOGO_EXTENSIONS:
        return None, "Format de logo invalide (PNG ou JPG uniquement)."

    fichier.seek(0, 2)  # fin
    taille = fichier.tell()
    fichier.seek(0)
    if taille > LOGO_MAX_BYTES:
        return None, "Logo trop volumineux (2 Mo maximum)."

    try:
        img = Image.open(fichier)
        img.verify()
        fichier.seek(0)
        img = Image.open(fichier).convert("RGBA")
    except (UnidentifiedImageError, OSError):
        return None, "Fichier image illisible ou corrompu."

    rel = logo_rel(user_id)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    img.save(Path(app.static_folder) / rel, format="PNG")
    return rel, None


@app.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    uid = current_user_id()
    if request.method == "POST":
        valeurs = {c: request.form.get(c, "") for c in CHAMPS_PROFIL}

        if not valeurs["nom_entreprise"].strip():
            flash("Le nom de l'entreprise est obligatoire.", "error")
            return render_template("profil.html", form=valeurs)

        logo, erreur = _enregistrer_logo(request.files.get("logo"), uid)
        if erreur:
            flash(erreur, "error")
            return render_template("profil.html", form=valeurs)

        save_profil(uid, valeurs, logo_path=logo)
        flash("Profil enregistré.", "ok")
        return redirect(url_for("profil"))

    return render_template("profil.html", form=get_profil(uid))


@app.route("/parametres", methods=["GET", "POST"])
@login_required
def parametres():
    uid = current_user_id()
    if request.method == "POST":
        # --- DEBUG : que recoit-on reellement du formulaire ? ---
        app.logger.warning("---- POST /parametres ----")
        app.logger.warning("  Content-Type : %s", request.content_type)
        app.logger.warning("  request.form (toutes cles) : %s",
                           dict(request.form))
        for c in CHAMPS_SMTP:
            app.logger.warning("  form[%s] = %r", c, request.form.get(c))

        valeurs = {c: request.form.get(c, "") for c in CHAMPS_SMTP}
        save_smtp(uid, valeurs)

        # Relecture immediate pour confirmer la persistance en base.
        apres = {k: v for k, v in get_profil_raw(uid).items()
                 if k.startswith("mail_")}
        app.logger.warning("  -> valeurs en base apres save : %s", apres)

        flash("Paramètres SMTP enregistrés.", "ok")
        return redirect(url_for("parametres"))

    return render_template("parametres.html", form=get_profil(uid))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
