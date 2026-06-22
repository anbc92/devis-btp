"""Generation du PDF d'un devis ou d'une facture avec ReportLab."""

import base64
import binascii
from datetime import datetime, timedelta
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
    Image,
)

from calculs import calcul_totaux, ligne_total_ht, fmt_euro
from profil import get_profil, logo_fs_path

# Charte graphique — alignee sur le site (bleu confiance, plat, aere).
PRIMARY = colors.HexColor("#2563EB")
PRIMARY_DARK = colors.HexColor("#1D4ED8")
PRIMARY_LIGHT = colors.HexColor("#EFF4FF")
TEXT = colors.HexColor("#1E293B")
MUTED = colors.HexColor("#64748B")
BORDER = colors.HexColor("#E2E8F0")
BG_ALT = colors.HexColor("#F8FAFC")
SUCCESS = colors.HexColor("#16A34A")

# Hauteur du filet d'accent en haut de page.
BAR_H = 3 * mm


def _logo_flowable(prof, size=22 * mm):
    """Renvoie le logo de l'artisan, ou None s'il n'en a pas configure.

    Pas de placeholder : un document client ne doit pas afficher de cartouche
    « LOGO ». En l'absence de logo, l'en-tete affiche simplement le bloc texte
    de l'entreprise sur toute la largeur.
    """
    path = logo_fs_path(prof)
    if path:
        return Image(path, width=size, height=size, kind="proportional")
    return None


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8,
                                leading=11, textColor=MUTED),
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontSize=9.5,
                                 leading=13, textColor=TEXT),
        "bold": ParagraphStyle("bold", parent=base["Normal"], fontSize=9.5,
                               leading=13, fontName="Helvetica-Bold",
                               textColor=TEXT),
        # Etiquette discrete (ex: "DEVIS POUR")
        "label": ParagraphStyle("label", parent=base["Normal"], fontSize=7.5,
                                 leading=11, fontName="Helvetica-Bold",
                                 textColor=MUTED),
        "h_artisan": ParagraphStyle("h_artisan", parent=base["Normal"],
                                    fontSize=13, leading=16,
                                    fontName="Helvetica-Bold", textColor=TEXT),
        "titre": ParagraphStyle("titre", parent=base["Normal"], fontSize=26,
                                fontName="Helvetica-Bold", textColor=PRIMARY,
                                alignment=TA_RIGHT, leading=28),
        "sous_titre": ParagraphStyle("sous_titre", parent=base["Normal"],
                                     fontSize=9, leading=13, textColor=MUTED,
                                     alignment=TA_RIGHT),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=9.5,
                               leading=13, textColor=TEXT),
        "cell_r": ParagraphStyle("cell_r", parent=base["Normal"], fontSize=9.5,
                                 leading=13, alignment=TA_RIGHT, textColor=TEXT),
        "th": ParagraphStyle("th", parent=base["Normal"], fontSize=8.5,
                             fontName="Helvetica-Bold", textColor=colors.white),
        "th_r": ParagraphStyle("th_r", parent=base["Normal"], fontSize=8.5,
                               fontName="Helvetica-Bold", textColor=colors.white,
                               alignment=TA_RIGHT),
    }
    return styles


def _fmt_date_fr(valeur):
    """Formate une date ISO (AAAA-MM-JJ) au format francais JJ/MM/AAAA.

    Renvoie la valeur telle quelle si elle n'est pas une date ISO reconnue.
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return ""
    try:
        return datetime.strptime(valeur, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valeur


def _date_limite_validite(date_creation, jours):
    """Calcule la date limite de validite : date_creation (JJ/MM/AAAA) + jours."""
    try:
        base = datetime.strptime((date_creation or "").strip(), "%d/%m/%Y")
    except (ValueError, TypeError):
        return None
    try:
        jours = int(jours)
    except (ValueError, TypeError):
        return None
    return (base + timedelta(days=jours)).strftime("%d/%m/%Y")


def _signature_flowable(signature, width=52 * mm, height=20 * mm):
    """Decode l'image base64 d'une signature et renvoie un Image ReportLab.

    Renvoie None si la donnee est absente ou illisible (rendu degrade sans
    interrompre la generation du PDF).
    """
    data = (signature or {}).get("signature_base64") or ""
    if "," in data:  # retire le prefixe "data:image/png;base64,"
        data = data.split(",", 1)[1]
    if not data:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return Image(BytesIO(raw), width=width, height=height, kind="proportional")
    except Exception:
        return None


def generer_pdf(devis, prestations, prof=None, signature=None, facture=None):
    """Construit le PDF en memoire et renvoie un BytesIO positionne a 0.

    `devis` : mapping (numero, client_nom, client_adresse, client_email,
              date_creation, notes, validite_jours, date_debut_travaux,
              delai_execution).
    `prestations` : liste de mappings (designation, quantite, prix_unitaire,
                    tva_taux).
    `prof` : profil de l'artisan emetteur. Si None, profil legacy (defauts).
    `signature` : mapping de signature electronique (nom_signataire,
                  signature_base64, date_signature) a afficher sur le devis,
                  ou None.
    `facture` : si fourni (mapping numero_facture, date_emission,
                date_echeance, statut), le document est genere en FACTURE et
                non en devis.
    """
    is_facture = facture is not None
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title=(facture["numero_facture"] if is_facture
               else f"Devis {devis['numero']}"),
    )
    if prof is None:
        prof = get_profil()
    st = _styles()
    story = []

    # --- En-tete : logo + artisan a gauche, titre du document a droite ---
    adresse_artisan = (prof["adresse"] or "").replace("\n", "<br/>")
    artisan_html = (
        f"<b><font size=13 color='#1E293B'>{prof['nom_entreprise']}</font></b><br/>"
        f"<font color='#64748B'>{prof['gerant']}<br/>"
        f"{adresse_artisan}<br/>"
        f"Tél : {prof['telephone']}<br/>{prof['email']}</font>"
    )
    if is_facture:
        titre_html = (
            f"<para alignment='right'><font size=26 color='#2563EB'><b>FACTURE</b>"
            f"</font><br/><font size=9 color='#64748B'>"
            f"N&deg; {facture['numero_facture']}<br/>"
            f"Date d'émission : {facture['date_emission']}<br/>"
            f"Échéance : {facture['date_echeance']}<br/>"
            f"Réf. devis : {devis['numero']}</font></para>"
        )
    else:
        titre_html = (
            f"<para alignment='right'><font size=26 color='#2563EB'><b>DEVIS</b></font><br/>"
            f"<font size=9 color='#64748B'>N&deg; {devis['numero']}<br/>"
            f"Date : {devis['date_creation']}</font></para>"
        )
    logo = _logo_flowable(prof)
    artisan_par = Paragraph(artisan_html, st["normal"])
    if logo is not None:
        bloc_artisan = Table(
            [[logo, artisan_par]],
            colWidths=[26 * mm, 64 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (1, 0), (1, 0), 8)]))
    else:
        bloc_artisan = artisan_par
    entete = Table(
        [[bloc_artisan, Paragraph(titre_html, st["normal"])]],
        colWidths=[92 * mm, 82 * mm],
    )
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(entete)
    story.append(Spacer(1, 9 * mm))

    # --- Bloc client (etiquette + coordonnees, carte legere a gauche) ---
    label_client = "FACTURÉ À" if is_facture else "DEVIS POUR"
    adresse_client = (devis["client_adresse"] or "").replace("\n", "<br/>")
    client_html = (
        f"<font size=7.5 color='#64748B'><b>{label_client}</b></font><br/>"
        f"<font size=11 color='#1E293B'><b>{devis['client_nom']}</b></font>"
    )
    if adresse_client:
        client_html += f"<br/><font color='#64748B'>{adresse_client}</font>"
    if devis.get("client_email"):
        client_html += f"<br/><font color='#64748B'>{devis['client_email']}</font>"
    bloc_client = Table([[Paragraph(client_html, st["normal"])]],
                        colWidths=[100 * mm], hAlign="LEFT")
    bloc_client.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_ALT),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, PRIMARY),  # filet d'accent a gauche
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(bloc_client)
    story.append(Spacer(1, 6 * mm))

    # --- Informations chantier (validite, debut des travaux, delai) ---
    # Bloc specifique au devis ; pour une facture, la date d'echeance figure
    # deja dans l'en-tete.
    validite_jours = devis.get("validite_jours") or prof.get("validite_jours") or 30
    date_limite = _date_limite_validite(devis.get("date_creation"), validite_jours)
    date_debut = _fmt_date_fr(devis.get("date_debut_travaux"))
    delai = (devis.get("delai_execution") or "").strip()

    if not is_facture:
        infos = [("Validité du devis", f"{validite_jours} jours")]
        if date_debut:
            infos.append(("Début des travaux (estimé)", date_debut))
        if delai:
            infos.append(("Délai d'exécution", delai))

        infos_html = "&nbsp;&nbsp;&nbsp;".join(
            f"<font color='#64748B'><b>{label} :</b> {valeur}</font>"
            for label, valeur in infos
        )
        bloc_infos = Table([[Paragraph(infos_html, st["small"])]],
                           colWidths=[174 * mm])
        bloc_infos.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(bloc_infos)
        story.append(Spacer(1, 5 * mm))

    # --- Tableau des prestations ---
    header = [
        Paragraph("DÉSIGNATION", st["th"]),
        Paragraph("QTÉ", st["th_r"]),
        Paragraph("P.U. HT", st["th_r"]),
        Paragraph("TVA", st["th_r"]),
        Paragraph("TOTAL HT", st["th_r"]),
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
        # En-tete
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        # Corps
        ("TOPPADDING", (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BG_ALT]),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # --- Totaux : HT + ventilation TVA (discrets), puis bloc TTC bleu ---
    totaux = calcul_totaux(prestations)
    petites_rows = [["Total HT", fmt_euro(totaux["total_ht"])]]
    for v in totaux["ventilation"]:
        petites_rows.append([f"TVA {v['taux']:g} % (sur {fmt_euro(v['base'])})",
                             fmt_euro(v["montant"])])

    cells = [[Paragraph(f"<font color='#64748B'>{label}</font>", st["normal"]),
              Paragraph(val, st["cell_r"])] for label, val in petites_rows]
    bloc_petits = Table(cells, colWidths=[55 * mm, 35 * mm], hAlign="RIGHT")
    bloc_petits.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, BORDER),
    ]))
    story.append(bloc_petits)

    # Bloc Total TTC mis en avant (fond bleu, texte blanc, plus grand).
    ttc_style = ParagraphStyle("ttc_l", parent=st["bold"], fontSize=12,
                               textColor=colors.white)
    ttc_style_r = ParagraphStyle("ttc_r2", parent=st["bold"], fontSize=13,
                                 textColor=colors.white, alignment=TA_RIGHT)
    bloc_ttc = Table([[Paragraph("TOTAL TTC", ttc_style),
                       Paragraph(fmt_euro(totaux["total_ttc"]), ttc_style_r)]],
                     colWidths=[55 * mm, 35 * mm], hAlign="RIGHT")
    bloc_ttc.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(bloc_ttc)
    story.append(Spacer(1, 8 * mm))

    # --- Notes eventuelles ---
    if devis.get("notes"):
        notes_html = devis["notes"].replace("\n", "<br/>")
        story.append(Paragraph("<b>Notes</b>", st["bold"]))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"<font color='#64748B'>{notes_html}</font>",
                               st["small"]))
        story.append(Spacer(1, 6 * mm))

    # --- Conditions de paiement / reglement ---
    if is_facture:
        cond_lignes = ["<b>Conditions de règlement</b>"]
        if facture.get("date_echeance"):
            cond_lignes.append(
                f"Facture payable au plus tard le {facture['date_echeance']}.")
        cond_lignes.append(prof["conditions_paiement"])
        cond_lignes.append(f"IBAN : {prof['iban']}")
    else:
        acompte = round(totaux["total_ttc"] * prof["acompte_pct"] / 100.0, 2)
        validite_txt = f"Validité du devis : {validite_jours} jours"
        if date_limite:
            validite_txt += f" (valable jusqu'au {date_limite})"
        cond_lignes = [
            "<b>Conditions de paiement</b>",
            f"{validite_txt}.",
            f"Acompte de {prof['acompte_pct']} % à la commande, soit "
            f"{fmt_euro(acompte)}.",
            prof["conditions_paiement"],
            f"IBAN : {prof['iban']}",
        ]
    # Assurance decennale (mention obligatoire BTP si souscrite).
    decennale = (prof.get("assurance_decennale") or "").strip()
    if decennale:
        assureur = (prof.get("assureur_nom") or "").strip()
        mention_dec = f"Assurance décennale : {decennale}"
        if assureur:
            mention_dec += f" (assureur : {assureur})"
        cond_lignes.append(mention_dec)
    # Franchise en base de TVA (auto-entrepreneur).
    if prof.get("auto_entrepreneur"):
        cond_lignes.append(
            "<b>TVA non applicable, article 293 B du CGI.</b>")
    cond_html = "<br/>".join(cond_lignes)
    bloc_cond = Table([[Paragraph(cond_html, st["small"])]], colWidths=[174 * mm])
    bloc_cond.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIMARY_LIGHT),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(bloc_cond)
    story.append(Spacer(1, 6 * mm))

    if is_facture:
        # --- Mention "Facture acquittee" si la facture est payee ---
        if facture.get("statut") == "payee":
            story.append(Paragraph(
                "<para alignment='center'><font size=13 color='#16A34A'>"
                "<b>FACTURE ACQUITTÉE</b></font></para>", st["normal"]))
            story.append(Spacer(1, 4 * mm))
    else:
        # --- Mention "Devis valable jusqu'au ..." en bas de page ---
        if date_limite:
            story.append(Paragraph(
                f"<b>Devis valable jusqu'au {date_limite}.</b>", st["small"]))
            story.append(Spacer(1, 4 * mm))

        # --- Bon pour accord : signature electronique si presente ---
        if signature:
            sig_img = _signature_flowable(signature)
            droite = []
            if sig_img is not None:
                droite.append(sig_img)
            droite.append(Paragraph(
                f"<b>{signature.get('nom_signataire', '')}</b><br/>"
                f"<font size=7 color='#64748B'>Signé électroniquement le "
                f"{signature.get('date_signature', '')}</font>", st["small"]))
            accord = Table([[
                Paragraph(
                    "<b>Bon pour accord</b><br/>"
                    "<font size=7 color='#64748B'>Devis accepté et signé "
                    "électroniquement par le client.</font>", st["small"]),
                droite,
            ]], colWidths=[100 * mm, 74 * mm])
            accord.setStyle(TableStyle([
                ("BOX", (1, 0), (1, 0), 0.6, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
        else:
            accord = Table([[
                Paragraph("Date et signature du client<br/>"
                          "<font size=7 color='#64748B'>précédée de la mention "
                          "&laquo; Bon pour accord &raquo;</font>", st["small"]),
                Paragraph("", st["small"]),
            ]], colWidths=[100 * mm, 74 * mm])
            accord.setStyle(TableStyle([
                ("BOX", (1, 0), (1, 0), 0.6, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 18),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
        story.append(accord)

    pied = _make_page_decoration(prof)
    doc.build(story, onFirstPage=pied, onLaterPages=pied)
    buf.seek(0)
    return buf


def _make_page_decoration(prof):
    """Construit le callback de decoration de page : filet d'accent en haut +
    pied de page (mentions legales de l'artisan)."""
    adresse_ligne = (prof["adresse"] or "").replace("\n", ", ")

    def _decor(canvas, doc):
        canvas.saveState()
        # Filet d'accent bleu en haut de page (toute la largeur).
        canvas.setFillColor(PRIMARY)
        canvas.rect(0, A4[1] - BAR_H, A4[0], BAR_H, stroke=0, fill=1)

        # Pied de page : mentions legales centrees.
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        ligne = (
            f"{prof['nom_entreprise']} - SIRET {prof['siret']} - "
            f"TVA {prof['tva_intra']} - APE {prof['ape']} - {adresse_ligne}"
        )
        # Mention de franchise de TVA : uniquement pour les auto-entrepreneurs.
        if prof.get("auto_entrepreneur") and prof.get("mention_tva"):
            canvas.drawCentredString(A4[0] / 2, 13 * mm, prof["mention_tva"])
        canvas.drawCentredString(A4[0] / 2, 10 * mm, ligne)
        canvas.drawCentredString(A4[0] / 2, 6 * mm, prof["mentions"][:140])
        canvas.restoreState()

    return _decor
