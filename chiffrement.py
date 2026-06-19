"""Chiffrement symetrique des secrets stockes en base (Fernet).

Sert a ne pas stocker le mot de passe SMTP en clair dans SQLite. La cle est
derivee de ENCRYPTION_KEY (ou a defaut SECRET_KEY). Les anciennes valeurs en
clair restent lisibles (retro-compatibilite) tant qu'on ne les re-enregistre
pas.
"""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "enc:"  # marque une valeur chiffree (vs une ancienne valeur en clair)
# Cle de repli utilisee uniquement si rien n'est configure (dev). Publique,
# donc non securisee : sert juste a ne pas planter en local.
_FALLBACK = "dev-only-change-me"


def _cle_base():
    """Cle source du chiffrement : ENCRYPTION_KEY de preference, sinon
    SECRET_KEY, sinon une cle de repli non securisee (dev)."""
    return (os.environ.get("ENCRYPTION_KEY")
            or os.environ.get("SECRET_KEY")
            or _FALLBACK)


def cle_non_securisee():
    """Vrai si aucune cle de chiffrement n'est configuree (repli dev public)."""
    return _cle_base() == _FALLBACK


def _fernet():
    cle = base64.urlsafe_b64encode(hashlib.sha256(_cle_base().encode()).digest())
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
        # Echec non silencieux : signale que la cle (ENCRYPTION_KEY/SECRET_KEY)
        # a probablement change depuis le chiffrement. Le secret est perdu et
        # doit etre re-enregistre.
        logger.warning(
            "Echec de dechiffrement d'un secret stocke : la cle de chiffrement "
            "a probablement change. Re-enregistrez le mot de passe SMTP concerne.")
        return ""
