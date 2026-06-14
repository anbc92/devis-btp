"""Test rapide : init DB, seed, generation PDF, calculs, routes Flask, auth."""

from werkzeug.security import generate_password_hash

import seed
from db import get_db
from pdf import generer_pdf
from calculs import calcul_totaux
import app as appmod

# Desactive CSRF et rate-limiting pour les POST du client de test.
appmod.app.config["WTF_CSRF_ENABLED"] = False
appmod.app.config["RATELIMIT_ENABLED"] = False
appmod.limiter.enabled = False

seed.run()

# Utilisateur de test (cree si absent)
EMAIL, MDP = "smoke@test.local", "smoketest"
conn = get_db()
if not conn.execute("SELECT 1 FROM users WHERE email = ?", (EMAIL,)).fetchone():
    conn.execute(
        "INSERT INTO users (email, password_hash, nom_entreprise, created_at) "
        "VALUES (?, ?, ?, ?)",
        (EMAIL, generate_password_hash(MDP), "Smoke SARL", "2026-01-01 00:00:00"),
    )
    conn.commit()
conn.close()

conn = get_db()
row = conn.execute("SELECT * FROM devis LIMIT 1").fetchone()
prest = [dict(r) for r in conn.execute(
    "SELECT * FROM prestations WHERE devis_id=? ORDER BY position",
    (row["id"],)).fetchall()]
conn.close()

# PDF
buf = generer_pdf(dict(row), prest)
data = buf.getvalue()
with open("test_devis.pdf", "wb") as f:
    f.write(data)
assert data[:4] == b"%PDF", "PDF invalide"
print(f"[OK] PDF genere : {len(data)} octets -> test_devis.pdf")

# Calculs
t = calcul_totaux(prest)
print(f"[OK] Totaux : HT={t['total_ht']} TVA={t['total_tva']} TTC={t['total_ttc']}")
assert abs(t["total_ht"] + t["total_tva"] - t["total_ttc"]) < 0.01

# Routes via test client
client = appmod.app.test_client()

# Pages publiques
for url in ["/", "/connexion", "/inscription"]:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    print(f"[OK] GET {url} -> 200 (public)")

# Protection : sans session -> redirection vers /connexion
r = client.get("/devis")
assert r.status_code == 302 and "/connexion" in r.headers["Location"], \
    f"/devis non protege -> {r.status_code}"
print("[OK] /devis protege (302 -> /connexion)")

# Connexion
r = client.post("/connexion", data={"email": EMAIL, "password": MDP})
assert r.status_code == 302, f"connexion -> {r.status_code}"
print("[OK] connexion reussie")

# Routes protegees (devis legacy user_id NULL accessible)
for url in ["/devis", f"/devis/{row['id']}", f"/devis/{row['id']}/pdf",
            "/devis/nouveau", "/profil", "/parametres"]:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    print(f"[OK] GET {url} -> 200 ({len(r.data)} octets)")

# Page d'erreur 404 personnalisee
r = client.get("/page-qui-nexiste-pas")
assert r.status_code == 404 and "introuvable" in r.get_data(as_text=True), \
    "404 personnalisee absente"
print("[OK] page d'erreur 404 personnalisee")

# Pagination presente dans le contexte
r = client.get("/devis?page=1")
assert r.status_code == 200
print("[OK] /devis?page=1 -> 200 (pagination)")

# Reinitialisation de mot de passe (jeton cree directement en base)
import hashlib as _hl
import secrets as _sec
from datetime import datetime as _dt, timedelta as _td

c2 = appmod.app.test_client()  # client non authentifie
assert c2.get("/mot-de-passe-oublie").status_code == 200
conn = get_db()
uid = conn.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()["id"]
tok = _sec.token_urlsafe(16)
conn.execute(
    "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
    (_hl.sha256(tok.encode()).hexdigest(), uid,
     (_dt.now() + _td(hours=1)).strftime("%Y-%m-%d %H:%M:%S")),
)
conn.commit()
conn.close()
assert c2.get(f"/reinitialiser/{tok}").status_code == 200
assert c2.post(f"/reinitialiser/{tok}", data={"password": "nouveaumdp"}).status_code == 302
assert c2.post("/connexion", data={"email": EMAIL, "password": "nouveaumdp"}).status_code == 302
print("[OK] reinitialisation de mot de passe")

print("\nTous les tests passent.")
