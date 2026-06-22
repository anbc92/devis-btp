"""Application web de generation de devis pour artisans BTP (V1)."""

import os
import hashlib
import secrets
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (
    Flask, render_template, request, redirect, url_for, send_file,
    send_from_directory, abort, flash, session,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Charge les variables d'environnement depuis le fichier .env (si present).
load_dotenv()

from db import get_db, init_db
from calculs import calcul_totaux, ligne_total_ht, fmt_euro
from pdf import generer_pdf
from profil import (
    get_profil, save_profil, save_smtp, logo_rel, MEDIA_ROOT,
    CHAMPS_PROFIL, CHAMPS_SMTP,
)
from mail import envoyer_devis, envoyer_message, config_smtp_ok, MailError
from chiffrement import cle_non_securisee
from config import (
    ARTISAN, CONDITIONS, TAUX_TVA, STATUTS, STATUTS_FACTURE, ECHEANCE_JOURS,
)


def _flag_env(nom, defaut=False):
    """Lit un booleen depuis une variable d'environnement."""
    return os.environ.get(nom, str(defaut)).lower() in ("1", "true", "yes", "on")


app = Flask(__name__)

# Cle secrete des sessions : depuis l'environnement. Si absente, on genere une
# cle ephemere (jamais de cle previsible codee en dur).
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY absente de l'environnement : cle ephemere generee "
        "(les sessions ne survivront pas a un redemarrage).")
app.secret_key = _secret

# Avertit si les secrets en base (mot de passe SMTP) sont chiffres avec la cle
# de repli publique : c'est le cas quand ni ENCRYPTION_KEY ni SECRET_KEY ne sont
# definies. En production, definir au moins l'une des deux.
if cle_non_securisee():
    app.logger.warning(
        "Aucune cle de chiffrement configuree (ENCRYPTION_KEY ou SECRET_KEY) : "
        "les mots de passe SMTP sont chiffres avec une cle par defaut publique. "
        "Definissez SECRET_KEY (ou ENCRYPTION_KEY) en production.")

# Securite des cookies de session
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Mettre SESSION_COOKIE_SECURE=1 en production (HTTPS obligatoire).
    SESSION_COOKIE_SECURE=_flag_env("SESSION_COOKIE_SECURE"),
)

# Protection CSRF sur toutes les requetes mutantes (POST/PUT/PATCH/DELETE).
csrf = CSRFProtect(app)

# Anti-brute-force (limites par adresse IP). En production, utiliser un backend
# partage (ex: storage_uri="redis://...").
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Cree les tables manquantes (dont users/profil) au demarrage, quel que soit
# le mode de lancement (python app.py, flask run, serveur WSGI...).
init_db()


@app.after_request
def _entetes_securite(response):
    """Ajoute des en-tetes de securite a toutes les reponses."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

# Upload logo. Sous MEDIA_ROOT (= /app/data sur volume Railway, static/ en
# local) pour que les logos persistent aux redeploiements.
UPLOAD_DIR = MEDIA_ROOT / "uploads"
LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2 Mo
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Filtres Jinja
app.jinja_env.filters["euro"] = fmt_euro
app.jinja_env.globals.update(STATUTS=STATUTS, STATUTS_FACTURE=STATUTS_FACTURE,
                             ARTISAN=ARTISAN)


@app.route("/media/<path:filename>")
def media(filename):
    """Sert les fichiers media (logos) depuis MEDIA_ROOT.

    Quand MEDIA_ROOT pointe sur le volume (/app/data), les logos ne sont plus
    sous static/ : cette route les expose. On verifie que le chemin RESOLU
    reste dans UPLOAD_DIR (uploads/) : sinon "uploads/../devis.db" servirait la
    base SQLite voisine (le prefixe "uploads/" seul ne suffit pas a l'empecher).
    """
    cible = (MEDIA_ROOT / filename).resolve()
    try:
        cible.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        abort(404)
    return send_from_directory(MEDIA_ROOT, filename)


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
    try:
        return {"profil": get_profil(current_user_id())}
    except Exception:  # ne jamais casser le rendu (ex: page d'erreur)
        return {"profil": {}}


@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(429)
@app.errorhandler(500)
def _page_erreur(err):
    code = getattr(err, "code", 500)
    messages = {
        403: "Accès refusé.",
        404: "Page introuvable.",
        429: "Trop de tentatives. Réessayez dans quelques instants.",
        500: "Une erreur interne est survenue.",
    }
    return render_template("erreur.html", code=code,
                           message=messages.get(code, "Erreur.")), code


@app.errorhandler(CSRFError)
def _erreur_csrf(err):
    return render_template(
        "erreur.html", code=400,
        message="Session expirée ou requête invalide. Rechargez la page."), 400


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


def _statut_facture_label(statut):
    return {"impayee": "Impayée", "payee": "Payée",
            "en_retard": "En retard"}.get(statut, statut)


app.jinja_env.filters["statut_facture_label"] = _statut_facture_label


def _date_fr(valeur):
    """Formate une date ISO (AAAA-MM-JJ) en JJ/MM/AAAA, sinon renvoie tel quel."""
    valeur = (valeur or "").strip()
    if not valeur:
        return ""
    try:
        return datetime.strptime(valeur, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valeur


app.jinja_env.filters["date_fr"] = _date_fr


def _charger_prestations(devis_id, conn):
    rows = conn.execute(
        "SELECT * FROM prestations WHERE devis_id = ? ORDER BY position, id",
        (devis_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _generer_numero(conn):
    """Numero de devis : DEV-AAAA-NNNN via un compteur atomique par annee.

    Le compteur est monotone (jamais decremente) : aucune collision possible,
    meme apres suppression de devis ou en cas de creations concurrentes.
    """
    annee = date.today().year
    row = conn.execute(
        "INSERT INTO compteurs (annee, dernier) VALUES (?, 1) "
        "ON CONFLICT(annee) DO UPDATE SET dernier = dernier + 1 "
        "RETURNING dernier",
        (annee,),
    ).fetchone()
    return f"DEV-{annee}-{row['dernier']:04d}"


def _generer_numero_facture(conn):
    """Numero de facture : FAC-AAAA-NNNN via un compteur atomique par annee."""
    annee = date.today().year
    row = conn.execute(
        "INSERT INTO compteurs_factures (annee, dernier) VALUES (?, 1) "
        "ON CONFLICT(annee) DO UPDATE SET dernier = dernier + 1 "
        "RETURNING dernier",
        (annee,),
    ).fetchone()
    return f"FAC-{annee}-{row['dernier']:04d}"


def _charger_facture_accessible(conn, facture_id):
    """Renvoie la facture si l'utilisateur courant peut y acceder, sinon None.

    Acces autorise pour ses propres factures et les factures legacy (NULL).
    """
    row = conn.execute(
        "SELECT * FROM factures WHERE id = ?", (facture_id,)
    ).fetchone()
    if row is None:
        return None
    if row["user_id"] is not None and row["user_id"] != current_user_id():
        return None
    return row


def _charger_signature(conn, devis_id):
    """Renvoie la derniere signature enregistree pour un devis, ou None."""
    row = conn.execute(
        "SELECT * FROM signatures WHERE devis_id = ? ORDER BY id DESC LIMIT 1",
        (devis_id,),
    ).fetchone()
    return dict(row) if row else None


def _maj_factures_en_retard(conn, user_id):
    """Passe en 'en_retard' les factures impayees dont l'echeance est depassee.

    Appelee a la lecture des factures (liste/detail) : la date d'echeance etant
    stockee en JJ/MM/AAAA, la comparaison se fait en Python. Idempotent, et ne
    touche jamais les factures payees ou deja marquees en retard.
    """
    aujourdhui = date.today()
    a_modifier = []
    for row in conn.execute(
        "SELECT id, date_echeance FROM factures "
        "WHERE statut = 'impayee' AND (user_id = ? OR user_id IS NULL)",
        (user_id,),
    ):
        try:
            echeance = datetime.strptime(
                (row["date_echeance"] or "").strip(), "%d/%m/%Y").date()
        except ValueError:
            continue  # echeance absente ou illisible : on n'y touche pas
        if echeance < aujourdhui:
            a_modifier.append(row["id"])
    if a_modifier:
        marqueurs = ",".join("?" * len(a_modifier))
        conn.execute(
            f"UPDATE factures SET statut = 'en_retard' WHERE id IN ({marqueurs})",
            a_modifier,
        )
        conn.commit()


def _champs_conformite(form):
    """Extrait du formulaire les champs de conformite reglementaire du devis :
    validite (jours), date de debut des travaux et delai d'execution."""
    try:
        validite = int((form.get("validite_jours") or "").strip())
    except (ValueError, AttributeError):
        validite = 0
    if validite <= 0:
        validite = 30  # defaut reglementaire usuel
    return {
        "validite_jours": validite,
        "date_debut_travaux": (form.get("date_debut_travaux") or "").strip(),
        "delai_execution": (form.get("delai_execution") or "").strip(),
    }


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
@limiter.limit("10 per hour", methods=["POST"])
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
@limiter.limit("10 per minute; 50 per hour", methods=["POST"])
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


@app.route("/deconnexion", methods=["POST"])
def deconnexion():
    session.clear()
    flash("Déconnecté.", "ok")
    return redirect(url_for("connexion"))


def _smtp_systeme():
    """Config SMTP "systeme" (variables d'environnement) pour les mails de
    l'application (ex: reinitialisation de mot de passe)."""
    return {
        "mail_server": os.environ.get("MAIL_SERVER", ""),
        "mail_port": os.environ.get("MAIL_PORT", "587"),
        "mail_username": os.environ.get("MAIL_USERNAME", ""),
        "mail_password": os.environ.get("MAIL_PASSWORD", ""),
        "mail_from": os.environ.get("MAIL_FROM") or os.environ.get("MAIL_USERNAME", ""),
    }


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@app.route("/mot-de-passe-oublie", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def mot_de_passe_oublie():
    if current_user_id():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expire = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO password_resets (token_hash, user_id, expires_at) "
                "VALUES (?, ?, ?)",
                (_hash_token(token), user["id"], expire),
            )
            conn.commit()
            lien = url_for("reinitialiser", token=token, _external=True)
            corps = (
                "Bonjour,\n\nVous avez demandé la réinitialisation de votre "
                "mot de passe. Cliquez sur ce lien (valable 1 heure) :\n\n"
                f"{lien}\n\nSi vous n'êtes pas à l'origine de cette demande, "
                "ignorez cet e-mail.\n"
            )
            try:
                envoyer_message(_smtp_systeme(), email,
                                "Réinitialisation de votre mot de passe", corps)
            except MailError as exc:
                # Pas de SMTP systeme configure : on n'expose rien a l'UI.
                app.logger.warning("Reset mail non envoye (%s).", exc)
                if app.debug:
                    app.logger.warning("Lien de reinitialisation : %s", lien)
        conn.close()
        # Message generique (pas d'enumeration des comptes existants).
        flash("Si un compte existe pour cet e-mail, un lien de "
              "réinitialisation vient d'être envoyé.", "ok")
        return redirect(url_for("connexion"))

    return render_template("mot_de_passe_oublie.html")


@app.route("/reinitialiser/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reinitialiser(token):
    maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    ligne = conn.execute(
        "SELECT * FROM password_resets WHERE token_hash = ? AND used = 0 "
        "AND expires_at > ?",
        (_hash_token(token), maintenant),
    ).fetchone()

    if ligne is None:
        conn.close()
        flash("Lien invalide ou expiré. Refaites une demande.", "error")
        return redirect(url_for("mot_de_passe_oublie"))

    if request.method == "POST":
        mdp = request.form.get("password", "")
        if len(mdp) < 6:
            conn.close()
            flash("Le mot de passe doit faire au moins 6 caractères.", "error")
            return render_template("reinitialiser.html", token=token)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(mdp), ligne["user_id"]))
        conn.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?",
                     (_hash_token(token),))
        conn.commit()
        conn.close()
        flash("Mot de passe mis à jour. Vous pouvez vous connecter.", "ok")
        return redirect(url_for("connexion"))

    conn.close()
    return render_template("reinitialiser.html", token=token)


@app.route("/")
def accueil():
    return render_template("accueil.html")


PAR_PAGE = 15


@app.route("/devis")
@login_required
def index():
    uid = current_user_id()
    page = max(1, request.args.get("page", 1, type=int))
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM devis WHERE user_id = ? OR user_id IS NULL",
        (uid,),
    ).fetchone()["c"]
    rows = conn.execute(
        "SELECT * FROM devis WHERE user_id = ? OR user_id IS NULL "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        (uid, PAR_PAGE, (page - 1) * PAR_PAGE),
    ).fetchall()

    # Charge toutes les prestations de la page en une seule requete (anti N+1).
    ids = [r["id"] for r in rows]
    par_devis = {}
    if ids:
        marqueurs = ",".join("?" * len(ids))
        for p in conn.execute(
            f"SELECT * FROM prestations WHERE devis_id IN ({marqueurs})", ids
        ):
            par_devis.setdefault(p["devis_id"], []).append(dict(p))
    conn.close()

    devis_list = []
    for r in rows:
        d = dict(r)
        prestations = par_devis.get(d["id"], [])
        d["totaux"] = calcul_totaux(prestations)
        d["nb_lignes"] = len(prestations)
        devis_list.append(d)

    pages = max(1, (total + PAR_PAGE - 1) // PAR_PAGE)
    return render_template("index.html", devis_list=devis_list,
                           page=page, pages=pages, total=total)


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

        conf = _champs_conformite(request.form)
        conn = get_db()
        numero = _generer_numero(conn)
        cur = conn.execute(
            "INSERT INTO devis (numero, client_nom, client_adresse, "
            "client_email, statut, notes, date_creation, user_id, "
            "validite_jours, date_debut_travaux, delai_execution) "
            "VALUES (?, ?, ?, ?, 'brouillon', ?, ?, ?, ?, ?, ?)",
            (numero, client_nom,
             request.form.get("client_adresse", "").strip(),
             request.form.get("client_email", "").strip(),
             request.form.get("notes", "").strip(),
             date.today().strftime("%d/%m/%Y"),
             current_user_id(),
             conf["validite_jours"], conf["date_debut_travaux"],
             conf["delai_execution"]),
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
    facture = conn.execute(
        "SELECT * FROM factures WHERE devis_id = ?", (devis_id,)
    ).fetchone()
    signature = _charger_signature(conn, devis_id)
    conn.close()
    for p in prestations:
        p["total_ht"] = ligne_total_ht(p)
    totaux = calcul_totaux(prestations)
    # Lien public de signature (si un token a deja ete genere).
    lien_signature = None
    if devis.get("signature_token"):
        lien_signature = url_for("signer", token=devis["signature_token"],
                                 _external=True)
    return render_template("voir.html", devis=devis, prestations=prestations,
                           totaux=totaux, conditions=CONDITIONS,
                           facture=dict(facture) if facture else None,
                           signature=signature, lien_signature=lien_signature,
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
    signature = _charger_signature(conn, devis_id)
    conn.close()  # libere la connexion avant les operations lentes (PDF, SMTP)

    destinataire = request.form.get("destinataire", "").strip()
    objet = request.form.get("objet", "").strip() or f"Devis {devis['numero']}"
    message = request.form.get("message", "").strip()

    prof = get_profil(current_user_id())
    pdf_buf = generer_pdf(devis, prestations, prof, signature=signature)
    try:
        envoyer_devis(prof, destinataire, objet, message,
                      pdf_buf.getvalue(), f"{devis['numero']}.pdf")
    except MailError as exc:
        flash(str(exc), "error")
        return redirect(url_for("voir", devis_id=devis_id))

    conn = get_db()
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
        "statut, notes, date_creation, user_id, validite_jours, "
        "date_debut_travaux, delai_execution) "
        "VALUES (?, ?, ?, ?, 'brouillon', ?, ?, ?, ?, ?, ?)",
        (numero, src["client_nom"], src["client_adresse"], src["client_email"],
         src["notes"], date.today().strftime("%d/%m/%Y"), current_user_id(),
         src["validite_jours"], src["date_debut_travaux"],
         src["delai_execution"]),
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

        conf = _champs_conformite(request.form)
        conn.execute(
            "UPDATE devis SET client_nom = ?, client_adresse = ?, "
            "client_email = ?, notes = ?, validite_jours = ?, "
            "date_debut_travaux = ?, delai_execution = ? WHERE id = ?",
            (client_nom,
             request.form.get("client_adresse", "").strip(),
             request.form.get("client_email", "").strip(),
             request.form.get("notes", "").strip(),
             conf["validite_jours"], conf["date_debut_travaux"],
             conf["delai_execution"], devis_id),
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
    signature = _charger_signature(conn, devis_id)
    conn.close()
    buf = generer_pdf(devis, prestations, get_profil(current_user_id()),
                      signature=signature)
    return send_file(buf, mimetype="application/pdf",
                     download_name=f"{devis['numero']}.pdf", as_attachment=False)


# ---------------------------------------------------------------------------
# Factures (transformation d'un devis accepte en facture)
# ---------------------------------------------------------------------------

@app.route("/devis/<int:devis_id>/facturer", methods=["POST"])
@login_required
def facturer(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    if devis["statut"] != "accepte":
        conn.close()
        flash("Seul un devis accepté peut être transformé en facture.", "error")
        return redirect(url_for("voir", devis_id=devis_id))

    # Une seule facture par devis : si elle existe deja, on y redirige.
    existante = conn.execute(
        "SELECT id FROM factures WHERE devis_id = ?", (devis_id,)
    ).fetchone()
    if existante:
        conn.close()
        flash("Ce devis a déjà été facturé.", "ok")
        return redirect(url_for("voir_facture", facture_id=existante["id"]))

    numero = _generer_numero_facture(conn)
    emission = date.today()
    echeance = emission + timedelta(days=ECHEANCE_JOURS)
    cur = conn.execute(
        "INSERT INTO factures (user_id, devis_id, numero_facture, "
        "date_emission, date_echeance, statut) VALUES (?, ?, ?, ?, ?, 'impayee')",
        (current_user_id(), devis_id, numero,
         emission.strftime("%d/%m/%Y"), echeance.strftime("%d/%m/%Y")),
    )
    facture_id = cur.lastrowid
    conn.commit()
    conn.close()
    flash(f"Facture {numero} créée à partir du devis {devis['numero']}.", "ok")
    return redirect(url_for("voir_facture", facture_id=facture_id))


@app.route("/factures")
@login_required
def factures():
    uid = current_user_id()
    page = max(1, request.args.get("page", 1, type=int))
    conn = get_db()
    _maj_factures_en_retard(conn, uid)
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM factures WHERE user_id = ? OR user_id IS NULL",
        (uid,),
    ).fetchone()["c"]
    rows = conn.execute(
        "SELECT f.*, d.client_nom AS client_nom, d.numero AS devis_numero "
        "FROM factures f JOIN devis d ON d.id = f.devis_id "
        "WHERE f.user_id = ? OR f.user_id IS NULL "
        "ORDER BY f.id DESC LIMIT ? OFFSET ?",
        (uid, PAR_PAGE, (page - 1) * PAR_PAGE),
    ).fetchall()

    # Totaux TTC par facture (calcules depuis les prestations du devis lie).
    devis_ids = [r["devis_id"] for r in rows]
    par_devis = {}
    if devis_ids:
        marqueurs = ",".join("?" * len(devis_ids))
        for p in conn.execute(
            f"SELECT * FROM prestations WHERE devis_id IN ({marqueurs})", devis_ids
        ):
            par_devis.setdefault(p["devis_id"], []).append(dict(p))
    conn.close()

    factures_list = []
    for r in rows:
        f = dict(r)
        f["totaux"] = calcul_totaux(par_devis.get(f["devis_id"], []))
        factures_list.append(f)

    pages = max(1, (total + PAR_PAGE - 1) // PAR_PAGE)
    return render_template("factures.html", factures_list=factures_list,
                           page=page, pages=pages, total=total)


@app.route("/facture/<int:facture_id>")
@login_required
def voir_facture(facture_id):
    conn = get_db()
    _maj_factures_en_retard(conn, current_user_id())
    row = _charger_facture_accessible(conn, facture_id)
    if row is None:
        conn.close()
        abort(404)
    facture = dict(row)
    devis_row = conn.execute(
        "SELECT * FROM devis WHERE id = ?", (facture["devis_id"],)
    ).fetchone()
    devis = dict(devis_row) if devis_row else {}
    prestations = _charger_prestations(facture["devis_id"], conn)
    conn.close()
    for p in prestations:
        p["total_ht"] = ligne_total_ht(p)
    totaux = calcul_totaux(prestations)
    return render_template("facture.html", facture=facture, devis=devis,
                           prestations=prestations, totaux=totaux)


@app.route("/facture/<int:facture_id>/statut", methods=["POST"])
@login_required
def changer_statut_facture(facture_id):
    statut = request.form.get("statut")
    if statut not in STATUTS_FACTURE:
        abort(400)
    conn = get_db()
    if _charger_facture_accessible(conn, facture_id) is None:
        conn.close()
        abort(404)
    conn.execute("UPDATE factures SET statut = ? WHERE id = ?",
                 (statut, facture_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("factures"))


@app.route("/facture/<int:facture_id>/supprimer", methods=["POST"])
@login_required
def supprimer_facture(facture_id):
    conn = get_db()
    if _charger_facture_accessible(conn, facture_id) is None:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM factures WHERE id = ?", (facture_id,))
    conn.commit()
    conn.close()
    flash("Facture supprimée.", "ok")
    return redirect(url_for("factures"))


@app.route("/facture/<int:facture_id>/pdf")
@login_required
def pdf_facture(facture_id):
    conn = get_db()
    row = _charger_facture_accessible(conn, facture_id)
    if row is None:
        conn.close()
        abort(404)
    facture = dict(row)
    devis_row = conn.execute(
        "SELECT * FROM devis WHERE id = ?", (facture["devis_id"],)
    ).fetchone()
    devis = dict(devis_row) if devis_row else {}
    prestations = _charger_prestations(facture["devis_id"], conn)
    conn.close()
    buf = generer_pdf(devis, prestations, get_profil(current_user_id()),
                      facture=facture)
    return send_file(buf, mimetype="application/pdf",
                     download_name=f"{facture['numero_facture']}.pdf",
                     as_attachment=False)


# ---------------------------------------------------------------------------
# Signature electronique (lien public)
# ---------------------------------------------------------------------------

@app.route("/devis/<int:devis_id>/lien-signature", methods=["POST"])
@login_required
def lien_signature(devis_id):
    conn = get_db()
    row = _charger_devis_accessible(conn, devis_id)
    if row is None:
        conn.close()
        abort(404)
    token = row["signature_token"]
    if not token:
        token = secrets.token_urlsafe(9)  # ~12 caracteres, 72 bits d'entropie
        conn.execute("UPDATE devis SET signature_token = ? WHERE id = ?",
                     (token, devis_id))
        conn.commit()
    conn.close()
    flash("Lien de signature prêt. Transmettez-le à votre client.", "ok")
    return redirect(url_for("voir", devis_id=devis_id))


def _charger_devis_par_token(conn, token):
    if not token:
        return None
    return conn.execute(
        "SELECT * FROM devis WHERE signature_token = ?", (token,)
    ).fetchone()


@app.route("/signer/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def signer(token):
    conn = get_db()
    row = _charger_devis_par_token(conn, token)
    if row is None:
        conn.close()
        abort(404)
    devis = dict(row)
    signature = _charger_signature(conn, devis["id"])

    if request.method == "POST":
        if signature:  # deja signe : on ne re-signe pas
            conn.close()
            flash("Ce devis a déjà été signé.", "ok")
            return redirect(url_for("signer", token=token))

        nom = request.form.get("nom_signataire", "").strip()
        accepte = request.form.get("accepte")
        signature_data = request.form.get("signature_data", "").strip()

        erreurs = []
        if not nom:
            erreurs.append("Indiquez votre nom.")
        if not accepte:
            erreurs.append("Vous devez cocher « J'accepte le devis ».")
        if not signature_data.startswith("data:image/") or len(signature_data) < 100:
            erreurs.append("Votre signature manuscrite est requise.")
        if len(signature_data) > 1_000_000:
            erreurs.append("Signature trop volumineuse.")
        if erreurs:
            conn.close()
            for e in erreurs:
                flash(e, "error")
            return redirect(url_for("signer", token=token))

        ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")
              .split(",")[0].strip())
        conn.execute(
            "INSERT INTO signatures (devis_id, nom_signataire, signature_base64, "
            "date_signature, ip_adresse) VALUES (?, ?, ?, ?, ?)",
            (devis["id"], nom, signature_data,
             datetime.now().strftime("%d/%m/%Y %H:%M"), ip),
        )
        conn.execute("UPDATE devis SET statut = 'accepte' WHERE id = ?",
                     (devis["id"],))
        conn.commit()
        conn.close()
        flash("Merci ! Votre signature a bien été enregistrée. "
              "Le devis est désormais accepté.", "ok")
        return redirect(url_for("signer", token=token))

    prestations = _charger_prestations(devis["id"], conn)
    conn.close()
    for p in prestations:
        p["total_ht"] = ligne_total_ht(p)
    totaux = calcul_totaux(prestations)
    prof = get_profil(devis.get("user_id"))
    return render_template("signer.html", devis=devis, prestations=prestations,
                           totaux=totaux, profil=prof, signature=signature,
                           token=token)


def _enregistrer_logo(fichier, user_id):
    """Valide et sauvegarde le logo de l'utilisateur dans UPLOAD_DIR.

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
    img.save(MEDIA_ROOT / rel, format="PNG")
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
        valeurs = {c: request.form.get(c, "") for c in CHAMPS_SMTP}
        save_smtp(uid, valeurs)
        flash("Paramètres SMTP enregistrés.", "ok")
        return redirect(url_for("parametres"))

    return render_template("parametres.html", form=get_profil(uid))


if __name__ == "__main__":
    init_db()
    # Defauts surs : ecoute locale, debugger desactive. Surchargeables via env.
    # (NE JAMAIS activer FLASK_DEBUG en production : RCE via le debugger Werkzeug.)
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=_flag_env("FLASK_DEBUG"),
    )
