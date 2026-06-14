"""Envoi d'un devis par e-mail via SMTP (smtplib, stdlib)."""

import smtplib
from email.message import EmailMessage


class MailError(Exception):
    """Erreur d'envoi ou de configuration SMTP (message destine a l'UI)."""


def config_smtp_ok(profil):
    """Vrai si le minimum requis pour envoyer est configure."""
    return bool((profil.get("mail_server") or "").strip()
                and (profil.get("mail_from") or profil.get("mail_username")))


def envoyer_devis(profil, destinataire, objet, corps, pdf_bytes, pdf_nom):
    """Envoie un mail avec le PDF du devis en piece jointe.

    Leve MailError en cas de config incomplete ou d'echec SMTP.
    """
    serveur = (profil.get("mail_server") or "").strip()
    expediteur = (profil.get("mail_from") or profil.get("mail_username") or "").strip()
    if not serveur or not expediteur:
        raise MailError("Configuration SMTP incomplète. Renseignez-la dans "
                        "Paramètres.")
    if not (destinataire or "").strip():
        raise MailError("Adresse e-mail du destinataire manquante.")

    try:
        port = int((profil.get("mail_port") or "587").strip())
    except ValueError:
        raise MailError("Port SMTP invalide.")

    utilisateur = (profil.get("mail_username") or "").strip()
    mot_de_passe = profil.get("mail_password") or ""

    msg = EmailMessage()
    msg["Subject"] = objet
    msg["From"] = expediteur
    msg["To"] = destinataire
    msg.set_content(corps)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename=pdf_nom)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(serveur, port, timeout=20) as s:
                if utilisateur:
                    s.login(utilisateur, mot_de_passe)
                s.send_message(msg)
        else:
            with smtplib.SMTP(serveur, port, timeout=20) as s:
                s.ehlo()
                try:
                    s.starttls()
                    s.ehlo()
                except smtplib.SMTPException:
                    pass  # serveur sans STARTTLS
                if utilisateur:
                    s.login(utilisateur, mot_de_passe)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError("Authentification SMTP refusée (identifiants invalides).")
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Échec de l'envoi : {exc}")
