# Devis BTP — Générateur de devis pour artisans

Application web simple pour créer des devis, calculer automatiquement HT/TVA/TTC,
générer un PDF professionnel et suivre le statut de chaque devis.

**Stack :** Python Flask + SQLite + HTML/CSS vanilla + ReportLab (PDF).

## Fonctionnalités (V1)

- Formulaire de création : client (nom, adresse, email) + lignes de prestations
  (désignation, quantité, prix unitaire, TVA) avec ajout/suppression dynamique.
- Calcul automatique en temps réel : total HT, ventilation de la TVA par taux, total TTC.
- Génération PDF pro : logo placeholder, coordonnées artisan, bloc client,
  tableau des prestations, totaux, conditions de paiement, bon pour accord,
  mentions légales en pied de page.
- Liste des devis avec statut modifiable : brouillon, envoyé, accepté, refusé.
- Interface propre et responsive (les tableaux deviennent des cartes sur mobile).

## Installation

```powershell
cd C:\Users\Anas\devis-btp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancement

```powershell
# Option 1 : script
.\run.ps1

# Option 2 : manuel
.\.venv\Scripts\python.exe app.py
```

Puis ouvrir http://localhost:5000

Pour charger un devis d'exemple : `.\.venv\Scripts\python.exe seed.py`

## Personnalisation

Modifiez `config.py` pour renseigner **vos** coordonnées d'artisan (nom, SIRET,
adresse, IBAN), les conditions de paiement et les taux de TVA disponibles.
Le logo est un placeholder dessiné dans `pdf.py` (`LogoPlaceholder`) — remplaçable
par une vraie image plus tard.

## Structure

| Fichier            | Rôle                                              |
|--------------------|---------------------------------------------------|
| `app.py`           | Routes Flask (liste, création, vue, PDF, statut)  |
| `db.py`            | Schéma et accès SQLite                             |
| `calculs.py`       | Calculs HT/TVA/TTC et formatage des montants      |
| `pdf.py`           | Génération du PDF (ReportLab)                      |
| `config.py`        | Coordonnées artisan et conditions                 |
| `templates/`       | Vues HTML (base, liste, formulaire, détail)       |
| `static/style.css` | Styles (responsive)                               |
| `seed.py`          | Devis d'exemple                                   |
| `smoke_test.py`    | Test rapide bout-en-bout                          |

La base `devis.db` est créée automatiquement au premier lancement.
