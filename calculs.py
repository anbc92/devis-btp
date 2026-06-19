"""Calculs des totaux d'un devis (HT, TVA, TTC)."""


def ligne_total_ht(prestation):
    return round(prestation["quantite"] * prestation["prix_unitaire"], 2)


def calcul_totaux(prestations):
    """Retourne un dict avec total_ht, total_tva, total_ttc et la
    ventilation de TVA par taux.

    `prestations` : iterable de mappings ayant quantite, prix_unitaire, tva_taux.
    """
    total_ht = 0.0
    tva_par_taux = {}

    for p in prestations:
        ht = ligne_total_ht(p)
        total_ht += ht
        taux = float(p["tva_taux"])
        tva_par_taux.setdefault(taux, 0.0)
        tva_par_taux[taux] += ht

    ventilation = []
    total_tva = 0.0
    for taux in sorted(tva_par_taux.keys(), reverse=True):
        base = round(tva_par_taux[taux], 2)
        montant = round(base * taux / 100.0, 2)
        total_tva += montant
        if base > 0:
            ventilation.append({"taux": taux, "base": base, "montant": montant})

    total_ht = round(total_ht, 2)
    total_tva = round(total_tva, 2)
    total_ttc = round(total_ht + total_tva, 2)

    return {
        "total_ht": total_ht,
        "total_tva": total_tva,
        "total_ttc": total_ttc,
        "ventilation": ventilation,
    }


def fmt_euro(montant):
    """Formate un montant a la francaise : 1 234,56 €."""
    s = f"{montant:,.2f}"
    s = s.replace(",", " ").replace(".", ",")
    return f"{s} €"
