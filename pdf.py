"""Generation du PDF d'un devis avec ReportLab (mise en page pro)."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Flowable,
)

from calculs import calcul_totaux, ligne_total_ht, fmt_euro
from config import ARTISAN, CONDITIONS

# Charte graphique
BLEU = colors.HexColor("#1f4e79")
BLEU_CLAIR = colors.HexColor("#dbe5f1")
GRIS = colors.HexColor("#6b7280")
GRIS_LIGNE = colors.HexColor("#d1d5db")


class LogoPlaceholder(Flowable):
    """Carre logo placeholder (a remplacer par une image plus tard)."""

    def __init__(self, size=22 * mm):
        super().__init__()
        self.size = size
        self.width = size
        self.height = size

    def draw(self):
        c = self.canv
        c.setFillColor(BLEU)
        c.roundRect(0, 0, self.size, self.size, 3 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(self.size / 2, self.size / 2 + 4, "LOGO")
        c.setFont("Helvetica", 6)
        c.drawCentredString(self.size / 2, self.size / 2 - 6, "placeholder")


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8,
                                leading=11, textColor=GRIS),
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontSize=9,
                                 leading=12),
        "bold": ParagraphStyle("bold", parent=base["Normal"], fontSize=9,
                               leading=12, fontName="Helvetica-Bold"),
        "h_artisan": ParagraphStyle("h_artisan", parent=base["Normal"],
                                    fontSize=13, leading=15,
                                    fontName="Helvetica-Bold", textColor=BLEU),
        "titre": ParagraphStyle("titre", parent=base["Normal"], fontSize=20,
                                fontName="Helvetica-Bold", textColor=BLEU,
                                alignment=TA_RIGHT),
        "sous_titre": ParagraphStyle("sous_titre", parent=base["Normal"],
                                     fontSize=9, textColor=GRIS, alignment=TA_RIGHT),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=9, leading=12),
        "cell_r": ParagraphStyle("cell_r", parent=base["Normal"], fontSize=9,
                                 leading=12, alignment=TA_RIGHT),
        "th": ParagraphStyle("th", parent=base["Normal"], fontSize=9,
                             fontName="Helvetica-Bold", textColor=colors.white),
        "th_r": ParagraphStyle("th_r", parent=base["Normal"], fontSize=9,
                               fontName="Helvetica-Bold", textColor=colors.white,
                               alignment=TA_RIGHT),
    }
    return styles


def generer_pdf(devis, prestations):
    """Construit le PDF en memoire et renvoie un BytesIO positionne a 0.

    `devis` : mapping (numero, client_nom, client_adresse, client_email,
              date_creation, notes).
    `prestations` : liste de mappings (designation, quantite, prix_unitaire,
                    tva_taux).
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Devis {devis['numero']}",
    )
    st = _styles()
    story = []

    # --- En-tete : logo + artisan a gauche, titre devis a droite ---
    artisan_html = (
        f"<b><font size=13 color='#1f4e79'>{ARTISAN['nom']}</font></b><br/>"
        f"{ARTISAN['gerant']}<br/>"
        f"{ARTISAN['adresse']}<br/>{ARTISAN['code_postal']} {ARTISAN['ville']}<br/>"
        f"Tel : {ARTISAN['telephone']}<br/>{ARTISAN['email']}"
    )
    titre_html = (
        f"<para alignment='right'><font size=20 color='#1f4e79'><b>DEVIS</b></font><br/>"
        f"<font size=9 color='#6b7280'>N&deg; {devis['numero']}<br/>"
        f"Date : {devis['date_creation']}</font></para>"
    )
    entete = Table(
        [[
            Table([[LogoPlaceholder(), Paragraph(artisan_html, st["normal"])]],
                  colWidths=[26 * mm, 60 * mm],
                  style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (1, 0), (1, 0), 6)])),
            Paragraph(titre_html, st["normal"]),
        ]],
        colWidths=[92 * mm, 82 * mm],
    )
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(entete)
    story.append(Spacer(1, 8 * mm))

    # --- Bloc client ---
    adresse_client = (devis["client_adresse"] or "").replace("\n", "<br/>")
    client_html = (
        f"<b>CLIENT</b><br/>{devis['client_nom']}<br/>{adresse_client}"
    )
    if devis.get("client_email"):
        client_html += f"<br/>{devis['client_email']}"
    bloc_client = Table([[Paragraph(client_html, st["normal"])]],
                        colWidths=[82 * mm], hAlign="RIGHT")
    bloc_client.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, GRIS_LIGNE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f9fafb")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(bloc_client)
    story.append(Spacer(1, 8 * mm))

    # --- Tableau des prestations ---
    header = [
        Paragraph("Designation", st["th"]),
        Paragraph("Qte", st["th_r"]),
        Paragraph("P.U. HT", st["th_r"]),
        Paragraph("TVA", st["th_r"]),
        Paragraph("Total HT", st["th_r"]),
    ]
    data = [header]
    for p in prestations:
        ht = ligne_total_ht(p)
        data.append([
            Paragraph(p["designation"], st["cell"]),
            Paragraph(f"{p['quantite']:g}", st["cell_r"]),
            Paragraph(fmt_euro(p["prix_unitaire"]), st["cell_r"]),
            Paragraph(f"{float(p['tva_taux']):g} %", st["cell_r"]),
            Paragraph(fmt_euro(ht), st["cell_r"]),
        ])

    table = Table(data, colWidths=[84 * mm, 16 * mm, 26 * mm, 16 * mm, 32 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLEU),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, GRIS_LIGNE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6fb")]),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))

    # --- Totaux ---
    totaux = calcul_totaux(prestations)
    totaux_rows = [
        ["Total HT", fmt_euro(totaux["total_ht"])],
    ]
    for v in totaux["ventilation"]:
        totaux_rows.append([f"TVA {v['taux']:g} % (sur {fmt_euro(v['base'])})",
                            fmt_euro(v["montant"])])
    totaux_rows.append(["Total TTC", fmt_euro(totaux["total_ttc"])])

    cells = []
    for i, (label, val) in enumerate(totaux_rows):
        is_ttc = (i == len(totaux_rows) - 1)
        style = st["bold"] if is_ttc else st["normal"]
        cells.append([Paragraph(label, style),
                      Paragraph(val, st["cell_r"] if not is_ttc else
                                ParagraphStyle("ttc_r", parent=st["cell_r"],
                                               fontName="Helvetica-Bold",
                                               textColor=colors.white))])

    bloc_tot = Table(cells, colWidths=[52 * mm, 38 * mm], hAlign="RIGHT")
    n = len(totaux_rows)
    bloc_tot.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, GRIS_LIGNE),
        ("LINEBELOW", (0, 0), (-1, n - 2), 0.4, GRIS_LIGNE),
        ("BACKGROUND", (0, n - 1), (-1, n - 1), BLEU),
        ("TEXTCOLOR", (1, n - 1), (1, n - 1), colors.white),
    ]))
    story.append(bloc_tot)
    story.append(Spacer(1, 10 * mm))

    # --- Notes eventuelles ---
    if devis.get("notes"):
        notes_html = devis["notes"].replace("\n", "<br/>")
        story.append(Paragraph("<b>Notes :</b>", st["normal"]))
        story.append(Paragraph(notes_html, st["small"]))
        story.append(Spacer(1, 6 * mm))

    # --- Conditions de paiement ---
    acompte = round(totaux["total_ttc"] * CONDITIONS["acompte_pct"] / 100.0, 2)
    cond_html = (
        f"<b>Conditions de paiement</b><br/>"
        f"Validite du devis : {CONDITIONS['validite_jours']} jours.<br/>"
        f"Acompte de {CONDITIONS['acompte_pct']} % a la commande, soit "
        f"{fmt_euro(acompte)}.<br/>"
        f"{CONDITIONS['paiement']}<br/>"
        f"IBAN : {ARTISAN['iban']}"
    )
    bloc_cond = Table([[Paragraph(cond_html, st["small"])]], colWidths=[174 * mm])
    bloc_cond.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLEU_CLAIR),
        ("BOX", (0, 0), (-1, -1), 0.5, BLEU),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(bloc_cond)
    story.append(Spacer(1, 6 * mm))

    # --- Bon pour accord ---
    accord = Table([[
        Paragraph("Date et signature du client<br/>"
                  "<font size=7 color='#6b7280'>precedee de la mention "
                  "&laquo; Bon pour accord &raquo;</font>", st["small"]),
        Paragraph("", st["small"]),
    ]], colWidths=[100 * mm, 74 * mm])
    accord.setStyle(TableStyle([
        ("BOX", (1, 0), (1, 0), 0.5, GRIS_LIGNE),
        ("TOPPADDING", (0, 0), (-1, -1), 18),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(accord)

    doc.build(story, onFirstPage=_pied, onLaterPages=_pied)
    buf.seek(0)
    return buf


def _pied(canvas, doc):
    """Pied de page : mentions legales de l'artisan."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRIS)
    pied = (
        f"{ARTISAN['nom']} - SIRET {ARTISAN['siret']} - "
        f"TVA {ARTISAN['tva_intra']} - APE {ARTISAN['ape']} - "
        f"{ARTISAN['adresse']}, {ARTISAN['code_postal']} {ARTISAN['ville']}"
    )
    canvas.drawCentredString(A4[0] / 2, 10 * mm, pied)
    canvas.drawCentredString(A4[0] / 2, 6 * mm, CONDITIONS["mentions"][:120])
    canvas.restoreState()
