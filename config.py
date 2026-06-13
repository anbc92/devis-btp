"""Configuration de l'artisan.

Modifiez ces valeurs avec vos propres coordonnees.
Elles apparaissent sur l'en-tete des devis PDF.
"""

ARTISAN = {
    "nom": "BTP Renov Pro",
    "gerant": "Jean Dupont",
    "adresse": "12 rue des Artisans",
    "code_postal": "75011",
    "ville": "Paris",
    "telephone": "01 23 45 67 89",
    "email": "contact@btp-renov-pro.fr",
    "siret": "123 456 789 00012",
    "tva_intra": "FR12345678900",
    "ape": "4334Z",
    "iban": "FR76 1234 5678 9012 3456 7890 123",
}

# Conditions affichees en bas de devis
CONDITIONS = {
    "validite_jours": 30,
    "acompte_pct": 30,
    "paiement": "Solde a la livraison. Reglement par cheque ou virement bancaire.",
    "mentions": "Devis gratuit. TVA non applicable, art. 293 B du CGI non concerne. "
                "Penalites de retard : taux legal en vigueur. "
                "Indemnite forfaitaire pour frais de recouvrement : 40 EUR.",
}

# Taux de TVA disponibles dans le formulaire (BTP France)
TAUX_TVA = [20.0, 10.0, 5.5, 0.0]

# Libelles des statuts de devis
STATUTS = ["brouillon", "envoye", "accepte", "refuse"]
