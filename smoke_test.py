"""Test rapide : init DB, seed, generation PDF, calculs, routes Flask."""

import seed
from db import get_db
from pdf import generer_pdf
from calculs import calcul_totaux
import app as appmod

seed.run()

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
for url in ["/", f"/devis/{row['id']}", f"/devis/{row['id']}/pdf", "/devis/nouveau"]:
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    print(f"[OK] GET {url} -> 200 ({len(r.data)} octets)")

print("\nTous les tests passent.")
