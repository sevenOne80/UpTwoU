"""
PDF generation for Annexe 1 - Convention du 22 septembre 2015
Demande de transfert individuel de réserves de pensions complémentaires.
One form per ceding insurer (contrat dormant).
"""
import os
from fpdf import FPDF

# UpTwoU's receiving B23 insurer - configure via .env
NOUVEL_NOM  = os.environ.get('NOUVEL_ASSUREUR_NOM',  'Contrat de pension extra-légal individuel salarié')
NOUVEL_BCE  = os.environ.get('NOUVEL_ASSUREUR_BCE',  '')
NOUVEL_IBAN = os.environ.get('NOUVEL_ASSUREUR_IBAN', '')
NOUVEL_REF  = os.environ.get('NOUVEL_ASSUREUR_REF',  '')

LM    = 20     # left margin (mm)
W     = 170    # usable width (mm)
LW    = 50     # label column width in 3-col sections
CW    = 60     # each value column = (W - LW) / 2
TEAL  = (200, 220, 218)
GREY  = (248, 248, 248)


def _v(val, default=''):
    return str(val).strip() if val else default


def generer_annexe1(client, contrat, nouveau_contrat_ref=None, dest=None):
    """Return PDF bytes for one Annexe 1 transfer request form.

    dest: optional AssureurDestinataire instance - overrides global NOUVEL_* constants.
    """
    nom_dest  = dest.nom  if dest else NOUVEL_NOM
    bce_dest  = dest.bce  if dest else NOUVEL_BCE
    iban_dest = dest.iban if dest else NOUVEL_IBAN
    ref_pdf   = nouveau_contrat_ref or (dest.ref if dest else None) or NOUVEL_REF

    # Resolve ceding insurer reference
    ref     = contrat.assureur_ref
    bce_ced = ref.numero_bce if ref else ''

    adresse_client = _v(client.adresse)
    if client.code_postal or client.ville:
        adresse_client += f", {_v(client.code_postal)} {_v(client.ville)}"

    sexe_f = '(X) Femme' if client.sexe == 'F' else '( ) Femme'
    sexe_m = '(X) Homme' if client.sexe == 'M' else '( ) Homme'

    pdf = FPDF('P', 'mm', 'A4')
    pdf.set_margins(LM, 15, LM)
    pdf.set_auto_page_break(True, 22)
    pdf.add_page()

    # ── Helpers ────────────────────────────────────────────────────────────

    def sec_header(text):
        pdf.set_fill_color(*TEAL)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(20, 70, 65)
        pdf.set_draw_color(100, 140, 135)
        pdf.set_xy(LM, pdf.get_y())
        pdf.cell(W, 7, f'  {text}', border=1, fill=True)
        pdf.ln(7)
        pdf.set_draw_color(0, 0, 0)

    def labeled_cell(x, y, w, h, label, value):
        """A cell with a small grey label on top and bold value below."""
        lh, vh = 4, h - 4
        pdf.set_xy(x, y)
        pdf.set_font('Helvetica', '', 7)
        pdf.set_text_color(130, 130, 130)
        pdf.cell(w, lh, f'   {label}', border='LTR')
        pdf.set_xy(x, y + lh)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(25, 25, 25)
        pdf.cell(w, vh, f'   {_v(value)}', border='LBR')

    def row2(ll, lv, rl, rv, h=11):
        """Two labeled cells side by side."""
        y, hw = pdf.get_y(), W / 2
        labeled_cell(LM,      y, hw, h, ll, lv)
        labeled_cell(LM + hw, y, hw, h, rl, rv)
        pdf.set_xy(LM, y + h)

    def subheader3(h1, h2):
        """Column headers for 3-column sections."""
        y = pdf.get_y()
        pdf.set_xy(LM, y)
        pdf.set_fill_color(*GREY)
        pdf.set_font('Helvetica', 'BI', 8)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(LW, 7, '', border=1, fill=True)
        pdf.cell(CW, 7, f'   {h1}', border=1, fill=True)
        pdf.cell(CW, 7, f'   {h2}', border=1, fill=True)
        pdf.ln(7)

    def row3(label, pv, nv, h=10):
        """3-column row: label | previous value | new value."""
        y = pdf.get_y()
        pdf.set_xy(LM, y)
        pdf.set_font('Helvetica', 'I', 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(LW, h, f'   {label}', border=1)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(25, 25, 25)
        pdf.cell(CW, h, f'   {_v(pv)}', border=1)
        pdf.cell(CW, h, f'   {_v(nv)}', border=1)
        pdf.ln(h)

    def row3_check(label, checked_prev, h=10):
        """3-column row with checkboxes (statut professionnel)."""
        y = pdf.get_y()
        pdf.set_xy(LM, y)
        pdf.set_font('Helvetica', 'I', 8)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(LW, h, f'   {label}', border=1)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(25, 25, 25)
        p = '(X) Salarie    ( ) Independant' if checked_prev else '( ) Salarie    ( ) Independant'
        pdf.cell(CW, h, f'   {p}', border=1)
        pdf.cell(CW, h,  '   ( ) Salarie    ( ) Independant', border=1)
        pdf.ln(h)

    # ── Title ──────────────────────────────────────────────────────────────
    pdf.set_fill_color(*TEAL)
    pdf.set_draw_color(80, 130, 125)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_text_color(15, 70, 65)
    pdf.set_xy(LM, 15)
    pdf.multi_cell(W, 6,
        'DEMANDE DE TRANSFERT DE RESERVES DE PENSIONS COMPLEMENTAIRES\n'
        'Convention du 22 septembre 2015 entre entreprises d\'assurances',
        border=1, fill=True, align='C')

    pdf.ln(3)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(W, 5,
        "Le soussigne demande de transferer les reserves acquises, constituees aupres du "
        "precedent organisme de pension, au nouvel organisme de pension.",
        align='J')
    pdf.ln(5)

    # ── AFFILIÉ ────────────────────────────────────────────────────────────
    sec_header('AFFILIE')
    row2('Nom',     client.nom,     'N° de registre national', client.niss)
    row2('Prenom',  client.prenom,  'Date de naissance',       client.date_naissance)
    row2('Adresse', adresse_client, 'Sexe', f'{sexe_f}      {sexe_m}')
    pdf.ln(6)

    # ── ORGANISME DE PENSION ───────────────────────────────────────────────
    sec_header('ORGANISME DE PENSION')
    subheader3('Precedent', 'Nouveau (UpTwoU)')
    row3('Nom',                  contrat.assureur, nom_dest)
    row3('Numero de reference',  contrat.numero,   ref_pdf)
    row3('Numero BCE',           bce_ced,          bce_dest)
    row3("Numero de compte",     "pas d'application", iban_dest)
    pdf.ln(6)

    # ── ORGANISATEUR ───────────────────────────────────────────────────────
    sec_header('ORGANISATEUR  (EMPLOYEUR, SOCIETE OU ORGANISATEUR SECTORIEL)')
    subheader3('Precedent', 'Nouveau (1)')
    row3('Nom / Forme juridique', _v(contrat.organisateur), '')
    row3('Numero BCE',            _v(contrat.organisateur_bce), '')
    row3_check('Statut professionnel', checked_prev=True)
    row3('Date de depart',        _v(contrat.date_valeur), '')
    row3("Date d'affiliation (1)", '', '')
    pdf.ln(5)

    # ── Footnote & conditions ──────────────────────────────────────────────
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(W, 4,
        "(1) Uniquement d'application dans le cas d'un transfert de reserves a l'organisme "
        "de pension du nouvel organisateur (= employeur, societe ou organisateur sectoriel).")

    pdf.ln(4)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(55, 55, 55)
    pdf.multi_cell(W, 4,
        "Les reserves transferees sont soumises aux conditions d'application aupres du nouvel "
        "organisme de pension. Une fois les reserves effectivement transferees, l'affilie ne "
        "peut plus faire valoir de droits sur le montant de reserves transfere a l'egard du "
        "precedent organisme de pension. Les prestations resultant des reserves transferees "
        "sont calculees suivant les bases techniques applicables aupres du nouvel organisme "
        "de pension a partir du moment du transfert.")

    # ── Signature ──────────────────────────────────────────────────────────
    pdf.ln(10)
    y_sig = pdf.get_y()
    hw = W / 2
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(55, 55, 55)
    pdf.set_xy(LM, y_sig)
    pdf.cell(hw, 5, "Fait a ___________________,  le ____/____/________")
    pdf.set_xy(LM + hw, y_sig)
    pdf.cell(hw, 5, "Signature de l'affilie", align='C')
    pdf.ln(14)
    pdf.set_draw_color(140, 140, 140)
    pdf.line(LM + hw + 15, pdf.get_y(), LM + W - 15, pdf.get_y())

    # ── Footer line ────────────────────────────────────────────────────────
    pdf.set_y(-14)
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 5,
        "Convention du 22 septembre 2015 entre entreprises d'assurances"
        " - Transfert individuel de reserves de pensions complementaires - Annexe 1",
        align='C')

    return bytes(pdf.output())


# ── Contract extract ───────────────────────────────────────────────────────────

TEAL_DARK  = (15,  80,  70)
TEAL_MED   = (25, 120, 105)
TEAL_LIGHT = (205, 232, 228)
GREY_BG    = (248, 249, 250)
TEXT_MAIN  = (25,  35,  40)
TEXT_MUTED = (110, 120, 125)


def generer_extrait_contrat(client, contract):
    """Return PDF bytes for a client-facing contract summary extract."""
    from datetime import date as _date

    today_str = _date.today().strftime('%d/%m/%Y')

    pdf = FPDF('P', 'mm', 'A4')
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(False)
    pdf.add_page()

    # ── Header band ───────────────────────────────────────────────────────────
    pdf.set_fill_color(*TEAL_DARK)
    pdf.rect(0, 0, 210, 30, 'F')

    pdf.set_xy(20, 9)
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(85, 9, 'UpTwoU')

    pdf.set_xy(20, 19)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(180, 215, 210)
    pdf.cell(85, 5, 'Retraite complementaire individuelle')

    pdf.set_xy(110, 11)
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(200, 228, 223)
    pdf.cell(60, 5, f'Extrait du {today_str}', align='R')
    pdf.set_xy(110, 17)
    pdf.set_font('Helvetica', 'B', 8)
    pdf.cell(60, 5, f'Ref. {contract.numero or "-"}', align='R')

    # ── Document title ────────────────────────────────────────────────────────
    pdf.set_y(38)
    pdf.set_x(20)
    pdf.set_font('Helvetica', 'B', 15)
    pdf.set_text_color(*TEAL_DARK)
    pdf.cell(170, 8, 'Extrait de contrat de pension')
    pdf.ln(8)
    pdf.set_x(20)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(170, 5, 'Contrat de pension extra-legal individuel  -  Branche 23')
    pdf.ln(6)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def section(title):
        pdf.set_fill_color(*TEAL_LIGHT)
        pdf.set_draw_color(*TEAL_MED)
        pdf.set_x(20)
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(170, 5.5, f'  {title}', fill=True, border='B', ln=True)
        pdf.ln(0.5)

    def kv(label, value, italic_value=False):
        pdf.set_x(23)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(58, 5.5, label)
        style = 'I' if italic_value else 'B'
        pdf.set_font('Helvetica', style, 9)
        pdf.set_text_color(*TEXT_MAIN)
        pdf.multi_cell(109, 5.5, _v(value, '-'))

    def spacer():
        pdf.ln(2.5)

    # ── 1. Titulaire ──────────────────────────────────────────────────────────
    section('TITULAIRE DU CONTRAT')
    kv('Nom et prenom',             f'{_v(client.prenom)} {_v(client.nom)}')
    kv('Registre national (NISS)',  client.niss)
    kv('Date de naissance',         client.date_naissance)
    addr = _v(client.adresse)
    if client.code_postal or client.ville:
        addr += f', {_v(client.code_postal)} {_v(client.ville)}'
    kv('Adresse',                   addr or None)
    spacer()

    # ── 2. Contrat ────────────────────────────────────────────────────────────
    section('CONTRAT')
    insurer = contract.pension_insurer.nom_legal if contract.pension_insurer else 'OneLife SA'
    kv('Organisme de pension',  insurer)
    kv('Type de contrat',       contract.type_branche or 'Branche 23')
    kv('Numero de reference',   contract.numero)
    kv('Date de creation',      contract.created_at.strftime('%d/%m/%Y') if contract.created_at else None)
    if contract.date_terme:
        kv('Date de terme',     contract.date_terme)
    spacer()

    # ── 3. Profil d'investissement ────────────────────────────────────────────
    section("PROFIL D'INVESTISSEMENT")
    profil_labels = {
        'prudent':    'Prudent  -  capital garanti, rendement stable',
        'equilibre':  'Equilibre  -  mix actions / obligations',
        'dynamique':  'Dynamique  -  majorite en actions mondiales',
        'conviction': 'Conviction  -  portefeuille concentre haute conviction',
    }
    profil = profil_labels.get(client.profil_risque or '', client.profil_risque or '-')
    kv('Profil de risque',  profil)
    kv('Gestionnaire',      'Wealtheon')
    spacer()

    # ── 4. Structure des frais ────────────────────────────────────────────────
    section('STRUCTURE DES FRAIS')
    y0 = pdf.get_y()
    col_w = 170 / 4
    for i, (label, val, note) in enumerate([
        ("Entree",      "0 %",      "transfert"),
        ("Gestion",     "0,10 %",   "par an"),
        ("Sous-jacents","1,85 %",   "par an"),
        ("Sortie",      "0 %",      "transfert"),
    ]):
        x = 20 + i * col_w
        # box
        fill_col = TEAL_LIGHT if val == '0 %' else (235, 245, 243)
        pdf.set_fill_color(*fill_col)
        pdf.set_draw_color(210, 228, 225)
        pdf.rect(x + 1, y0, col_w - 2, 13, 'FD')
        # label
        pdf.set_xy(x + 1, y0 + 1)
        pdf.set_font('Helvetica', '', 7)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(col_w - 2, 3.5, label, align='C')
        # value
        pdf.set_xy(x + 1, y0 + 4.5)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.set_text_color(*TEAL_DARK)
        pdf.cell(col_w - 2, 5, val, align='C')
        # note
        pdf.set_xy(x + 1, y0 + 9.5)
        pdf.set_font('Helvetica', '', 6.5)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(col_w - 2, 3, note, align='C')
    pdf.set_y(y0 + 16)
    spacer()

    # ── 5. État des réserves ──────────────────────────────────────────────────
    section('ETAT DES RESERVES')
    if contract.reserves_recues:
        for rr in contract.reserves_recues:
            source = rr.transfert.assureur if rr.transfert else '-'
            date_r = f'recue le {rr.date_reception}' if rr.date_reception else 'date inconnue'
            kv(f'Reserve ({date_r})',  f'{_v(rr.montant)} EUR  -  {source}')
        if contract.total_reserve:
            pdf.set_x(23)
            pdf.set_font('Helvetica', 'B', 9)
            pdf.set_text_color(*TEAL_DARK)
            pdf.cell(58, 7, 'Total reserves recues')
            pdf.set_font('Helvetica', 'B', 11)
            pdf.cell(109, 7, f'{contract.total_reserve} EUR')
            pdf.ln(7)
    else:
        pdf.set_x(23)
        pdf.set_font('Helvetica', 'I', 8.5)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(170, 5.5, 'Aucune reserve recue a ce jour - transfert en cours ou a initier.')
        pdf.ln(3)
    spacer()

    # ── 6. Bénéficiaires ──────────────────────────────────────────────────────
    section('BENEFICIAIRES')
    kv("A l'echeance",          f'{_v(client.prenom)} {_v(client.nom)}')
    if client.beneficiaire_1:
        kv('En cas de deces - 1er', client.beneficiaire_1)
    if client.beneficiaire_2:
        kv('En cas de deces - 2e',  client.beneficiaire_2)
    if not client.beneficiaire_1 and not client.beneficiaire_2:
        pdf.set_x(23)
        pdf.set_font('Helvetica', 'I', 8)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(170, 5.5, 'Aucun beneficiaire designe en cas de deces.')
        pdf.ln(3)
    spacer()

    # ── Disclaimer ────────────────────────────────────────────────────────────
    pdf.set_fill_color(*GREY_BG)
    pdf.set_draw_color(225, 230, 228)
    pdf.set_x(20)
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.multi_cell(170, 4,
        "Ce document est un extrait informatif de votre contrat de pension extra-legal individuel. "
        "Il ne constitue pas un document contractuel au sens strict. "
        "En cas de divergence, les conditions generales du contrat font foi.",
        fill=True, border=1, align='J')

    # ── Footer ────────────────────────────────────────────────────────────────
    pdf.set_y(-16)
    pdf.set_draw_color(*TEAL_MED)
    pdf.set_line_width(0.4)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(2)
    pdf.set_font('Helvetica', '', 7)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.set_x(20)
    pdf.cell(85, 4, 'UpTwoU  -  contact@uptwou.be  -  www.uptwou.be')
    pdf.cell(85, 4, f'Edite le {today_str}', align='R')

    return bytes(pdf.output())
