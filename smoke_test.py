"""Test rapide : init DB, seed, generation PDF, calculs, routes Flask, auth."""

from werkzeug.security import generate_password_hash

import seed
from db import get_db
from pdf import generer_pdf
from calculs import calcul_totaux
import app as appmod

# Desactive la verification CSRF pour les POST du client de test.
appmod.app.config["WTF_CSRF_ENABLED"] = False

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

print("\nTous les tests passent.")
