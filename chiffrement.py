"""Chiffrement symetrique des secrets stockes en base (Fernet).

Sert a ne pas stocker le mot de passe SMTP en clair dans SQLite. La cle est
derivee de ENCRYPTION_KEY (ou a defaut SECRET_KEY). Les anciennes valeurs en
clair restent lisibles (retro-compatibilite) tant qu'on ne les re-enregistre
pas.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:"  # marque une valeur chiffree (vs une ancienne valeur en clair)


def _fernet():
    base = (os.environ.get("ENCRYPTION_KEY")
            or os.environ.get("SECRET_KEY")
            or "dev-only-change-me")
    cle = base64.urlsafe_b64encode(hashlib.sha256(base.encode()).digest())
    return Fernet(cle)


def chiffrer(valeur):
    """Chiffre une chaine ; renvoie '' si vide."""
    valeur = valeur or ""
    if not valeur:
        return ""
    return _PREFIX + _fernet().encrypt(valeur.encode()).decode()


def dechiffrer(valeur):
    """Dechiffre une valeur. Les valeurs sans prefixe (legacy en clair) sont
    renvoyees telles quelles. Renvoie '' si le jeton est invalide."""
    valeur = valeur or ""
    if not valeur.startswith(_PREFIX):
        return valeur
    try:
        return _fernet().decrypt(valeur[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""
