"""
PDF generation for Annexe 1 — Convention du 22 septembre 2015
Demande de transfert individuel de réserves de pensions complémentaires.
One form per ceding insurer (contrat dormant).
"""
import os
from fpdf import FPDF

# UpTwoU's receiving B23 insurer — configure via .env
NOUVEL_NOM  = os.environ.get('NOUVEL_ASSUREUR_NOM',  'À compléter par UpTwoU')
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


def generer_annexe1(client, contrat):
    """Return PDF bytes for one Annexe 1 transfer request form."""

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
    row3('Nom',                  contrat.assureur, NOUVEL_NOM)
    row3('Numero de reference',  contrat.numero,   NOUVEL_REF)
    row3('Numero BCE',           bce_ced,          NOUVEL_BCE)
    row3("Numero de compte",     "pas d'application", NOUVEL_IBAN)
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
