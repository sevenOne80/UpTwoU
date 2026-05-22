import os
import re
import json
import base64
import secrets
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders as email_encoders
import pdfplumber
import anthropic
from datetime import datetime, date, timezone, timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, send_from_directory, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, Client, Contrat, Analyse, ContactMessage, ProfilChangeLog, CabinetCourtage, TransfertSignature, SignatureEvent, TransfertReserve, seed_assureurs
from pdf_utils import generer_annexe1, generer_extrait_contrat, NOUVEL_NOM
from pdf_parser import parse_mypension_pdf
from kyc_parser import parse_belgian_eid
from services.connective_service import initiate_batch_transfer_signature
from routes.signature import signature_bp
import itsme_auth
from forms import LoginForm, RegisterForm, DonneesForm, ProfilForm, CourtierRegisterForm, OnboardingKYCForm, OnboardingContratForm, QuestionnaireForm, EmptyForm, ClientContactForm

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── App setup ──────────────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'uptowu-dev-secret')

@app.template_filter('from_json')
def from_json_filter(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'sqlite:///' + os.path.join(os.path.dirname(__file__), 'uptwou.db')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ── Session security ──────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)
app.config['SESSION_COOKIE_HTTPONLY']    = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'
app.config['SESSION_COOKIE_SECURE']     = os.environ.get('FLASK_ENV') == 'production'

db.init_app(app)
app.register_blueprint(signature_bp)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
login_manager.login_message_category = 'error'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.before_request
def enforce_session_timeout():
    """Force logout after 30 min of inactivity for authenticated users."""
    if not current_user.is_authenticated:
        return
    last_str = session.get('_last_activity')
    if last_str:
        try:
            last = datetime.fromisoformat(last_str)
            if (datetime.now(timezone.utc).replace(tzinfo=None) - last).total_seconds() > 1800:
                logout_user()
                session.clear()
                flash("Votre session a expiré. Veuillez vous reconnecter.", "error")
                return redirect(url_for('login'))
        except (ValueError, TypeError):
            pass
    session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _parse_reserve(val) -> float:
    """Parse a reserve string (e.g. '12 345,67' or '12345.67') to float."""
    if not val:
        return 0.0
    s = str(val).strip().replace('€', '').replace('\xa0', '').replace(' ', '')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        parts = s.split(',')
        if len(parts[-1]) == 3 and parts[-1].isdigit():
            s = s.replace(',', '')
        else:
            s = s.replace(',', '.')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ── Email helper ───────────────────────────────────────────────────────────────
def _send_verification_email(user_email: str, token: str):
    verify_url = url_for('verifier_email', token=token, _external=True)
    mail_server = os.getenv('MAIL_SERVER')
    if not mail_server:
        app.logger.info("DEV — lien de vérification email : %s", verify_url)
        return

    html_body = f"""
    <html><body style="font-family:sans-serif;color:#1a1a1a;max-width:560px;margin:auto;padding:32px;">
      <img src="https://app.uptwoU.be/static/img/uptwou-logo.svg" height="36" alt="UpTwoU" style="margin-bottom:24px;">
      <h2 style="color:#0d5c4e;font-size:22px;margin-bottom:8px;">Confirmez votre adresse e-mail</h2>
      <p style="color:#555;line-height:1.6;">
        Merci de vous être inscrit sur UpTwoU. Cliquez sur le bouton ci-dessous pour
        activer votre compte et accéder à votre espace personnel.
      </p>
      <a href="{verify_url}"
         style="display:inline-block;margin:24px 0;padding:14px 32px;background:#0d5c4e;
                color:#fff;text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;">
        Confirmer mon adresse e-mail
      </a>
      <p style="color:#888;font-size:13px;">
        Ce lien est valable 24 heures. Si vous n'êtes pas à l'origine de cette inscription, ignorez cet email.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">UpTwoU — Rue Belliard 40, 1040 Bruxelles</p>
    </body></html>
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Confirmez votre adresse e-mail — UpTwoU'
    msg['From'] = os.getenv('MAIL_FROM', 'noreply@uptwou.be')
    msg['To'] = user_email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(mail_server, int(os.getenv('MAIL_PORT', 587))) as server:
            server.starttls()
            username = os.getenv('MAIL_USERNAME')
            password = os.getenv('MAIL_PASSWORD')
            if username and password:
                server.login(username, password)
            server.send_message(msg)
        app.logger.info("Email de vérification envoyé à %s", user_email)
    except Exception:
        app.logger.exception("Échec de l'envoi de l'email de vérification à %s", user_email)


def _attach_file(msg: MIMEMultipart, filepath: str, filename: str):
    """Attach a file to a MIMEMultipart message."""
    try:
        with open(filepath, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
        email_encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(part)
        return True
    except OSError:
        return False


def _send_kyc_mismatch_emails(client, user_email: str):
    """
    Send mypension PDF + ID card to support, and a waiting confirmation to the client.
    Falls back to logging if MAIL_SERVER is not configured.
    """
    support = 'support@uptwou.be'
    mail_from = os.getenv('MAIL_FROM', 'noreply@uptwou.be')
    nom_complet = f"{client.prenom or ''} {client.nom or ''}".strip() or user_email

    # ── Email 1 : alerte support ─────────────────────────────────────────────
    msg_support = MIMEMultipart()
    msg_support['Subject'] = f"Vérification manuelle KYC — {nom_complet}"
    msg_support['From'] = mail_from
    msg_support['To'] = support

    body_support = f"""
    <html><body style="font-family:sans-serif;color:#1a1a1a;max-width:600px;margin:auto;padding:32px;">
      <h2 style="color:#c0392b;">Vérification manuelle KYC requise</h2>
      <p>Le numéro national extrait de la carte d'identité ne correspond pas
         à celui de l'extrait mypension.be pour le client suivant :</p>
      <table style="border-collapse:collapse;width:100%;margin:16px 0;">
        <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600;">Nom</td>
            <td style="padding:6px 12px;">{nom_complet}</td></tr>
        <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600;">Email</td>
            <td style="padding:6px 12px;">{user_email}</td></tr>
        <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600;">NISS mypension</td>
            <td style="padding:6px 12px;">{client.niss or '—'}</td></tr>
        <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600;">Client ID</td>
            <td style="padding:6px 12px;">{client.id}</td></tr>
      </table>
      <p>Les documents sont joints à cet email. Après vérification, activez manuellement
         le compte via l'interface admin et contactez le client.</p>
    </body></html>
    """
    msg_support.attach(MIMEText(body_support, 'html', 'utf-8'))

    # Pièces jointes
    upload_dir = UPLOAD_FOLDER
    if client.latest_analyse and client.latest_analyse.filename:
        _attach_file(
            msg_support,
            os.path.join(upload_dir, client.latest_analyse.filename),
            f"mypension_{nom_complet.replace(' ', '_')}.pdf",
        )
    if client.kyc_document:
        _attach_file(
            msg_support,
            os.path.join(upload_dir, client.kyc_document),
            f"carte_identite_{nom_complet.replace(' ', '_')}.pdf",
        )

    # ── Email 2 : confirmation client ────────────────────────────────────────
    msg_client = MIMEMultipart('alternative')
    msg_client['Subject'] = 'Votre dossier est en cours de vérification — UpTwoU'
    msg_client['From'] = mail_from
    msg_client['To'] = user_email

    body_client = f"""
    <html><body style="font-family:sans-serif;color:#1a1a1a;max-width:560px;margin:auto;padding:32px;">
      <img src="https://app.uptwoU.be/static/img/uptwou-logo.svg" height="36" alt="UpTwoU" style="margin-bottom:24px;">
      <h2 style="color:#0d5c4e;font-size:22px;margin-bottom:8px;">Votre dossier est en cours de vérification</h2>
      <p style="color:#555;line-height:1.6;">
        Bonjour {client.prenom or ''},<br><br>
        Nous avons bien reçu vos documents et notre équipe va les examiner dans les plus brefs délais.
      </p>
      <p style="color:#555;line-height:1.6;">
        Vous recevrez un email dès que votre identité aura été confirmée,
        afin de poursuivre votre inscription sur UpTwoU.
      </p>
      <p style="color:#555;line-height:1.6;">
        Pour toute question, contactez-nous à
        <a href="mailto:support@uptwou.be" style="color:#0d5c4e;">support@uptwou.be</a>.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">UpTwoU — Rue Belliard 40, 1040 Bruxelles</p>
    </body></html>
    """
    msg_client.attach(MIMEText(body_client, 'html', 'utf-8'))

    # ── Envoi ────────────────────────────────────────────────────────────────
    mail_server = os.getenv('MAIL_SERVER')
    if not mail_server:
        app.logger.info("DEV — KYC mismatch pour %s (client_id=%s)", user_email, client.id)
        return

    try:
        with smtplib.SMTP(mail_server, int(os.getenv('MAIL_PORT', 587))) as server:
            server.starttls()
            username = os.getenv('MAIL_USERNAME')
            password = os.getenv('MAIL_PASSWORD')
            if username and password:
                server.login(username, password)
            server.send_message(msg_support)
            server.send_message(msg_client)
        app.logger.info("Emails KYC mismatch envoyés — client %s", client.id)
    except Exception:
        app.logger.exception("Échec envoi emails KYC mismatch — client %s", client.id)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ── Role decorators ────────────────────────────────────────────────────────────
def courtier_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'courtier':
            flash("Accès réservé aux courtiers.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'client':
            flash("Accès réservé aux clients.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


# ── Pension age helpers ────────────────────────────────────────────────────────
def _add_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:          # Feb 29 in non-leap year
        return d.replace(year=d.year + years, day=28)


def birthdate_from_niss(niss):
    """Extract date of birth from Belgian NISS (YY.MM.DD-XXX.CC).
    For births from 2000, the month is encoded as MM+20."""
    digits = re.sub(r'\D', '', niss or '')
    if len(digits) != 11:
        return None
    yy, mm, dd = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    if mm > 20:         # 2000+
        mm -= 20
        year = 2000 + yy
    else:
        year = 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def pension_date_for(bd):
    """Return earliest Belgian statutory pension date.
    Age 66 if reached before 01/01/2030, else 67."""
    at_66 = _add_years(bd, 66)
    return at_66 if at_66 < date(2030, 1, 1) else _add_years(bd, 67)


_MOIS_FR = ['janvier','février','mars','avril','mai','juin',
            'juillet','août','septembre','octobre','novembre','décembre']


def retirement_date_for(bd):
    """1st of the month following the statutory pension birthday."""
    pd = pension_date_for(bd)
    if pd.month == 12:
        return date(pd.year + 1, 1, 1)
    return date(pd.year, pd.month + 1, 1)


def remaining_to_pension(bd):
    """Return (years, months) remaining until pension, or None if already past."""
    today = date.today()
    pd = pension_date_for(bd)
    if pd <= today:
        return None
    y = pd.year - today.year
    m = pd.month - today.month
    if m < 0:
        y -= 1
        m += 12
    return y, m


# ── Onboarding / KYC helpers ──────────────────────────────────────────────────
ASSUREURS_EXCLUS = ['vitis', 'onelife', 'one life', 'lombard international']

def is_assureur_exclu(assureur):
    if not assureur:
        return False
    return any(ex in assureur.lower() for ex in ASSUREURS_EXCLUS)

PROFIL_THRESHOLDS = [(0, 4, 'prudent'), (5, 9, 'equilibre'), (10, 14, 'dynamique'), (15, 18, 'conviction')]

def score_to_profil(score):
    for lo, hi, p in PROFIL_THRESHOLDS:
        if lo <= score <= hi:
            return p
    return 'equilibre'

def _log_profil_change(client, nouveau_profil, choisi_par, score=None):
    """Record a profile change and flag it as pending delivery to Wealtheon + insurer."""
    entry = ProfilChangeLog(
        client_id=client.id,
        profil_ancien=client.profil_risque,
        profil_nouveau=nouveau_profil,
        choisi_par=choisi_par,
        score_questionnaire=score,
    )
    db.session.add(entry)


ALLOWED_KYC_EXT = {'jpg', 'jpeg', 'png', 'pdf'}

KYC_PROMPT = """Analyse cette pièce d'identité belge et extrais les données au format JSON uniquement, sans texte avant ni après.

Règles importantes :
- "adresse" = rue et numéro UNIQUEMENT (ex: "Rue de la Loi 42")
- "code_postal" = code postal 4 chiffres UNIQUEMENT (ex: "1040")
- "ville" = localité UNIQUEMENT (ex: "Bruxelles")
- Ne mélange pas ces trois champs : si l'adresse imprimée est "Rue de la Loi 42, 1040 Bruxelles", décompose-la correctement
- Pour le NISS belge : verso de la carte, format XX.XX.XX-XXX.XX
- Si une valeur est absente, utilise null

{
  "nom": "NOM EN MAJUSCULES",
  "prenom": "Prénom(s)",
  "date_naissance": "JJ/MM/AAAA",
  "niss": "XX.XX.XX-XXX.XX ou null",
  "adresse": "rue et numéro uniquement ou null",
  "code_postal": "4 chiffres ou null",
  "ville": "localité uniquement ou null",
  "pays": "pays ou Belgique",
  "sexe": "F ou M ou null"
}"""

def analyse_piece_identite(filepath):
    """Analyse an ID card (image or PDF) with Claude vision. Returns dict."""
    ext = filepath.rsplit('.', 1)[-1].lower()
    with open(filepath, 'rb') as f:
        data = base64.b64encode(f.read()).decode()
    if ext in ('jpg', 'jpeg'):
        item = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
    elif ext == 'png':
        item = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
    else:  # pdf
        item = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    try:
        client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            messages=[{"role": "user", "content": [item, {"type": "text", "text": KYC_PROMPT}]}]
        )
        for block in msg.content:
            if block.type == "text":
                raw = re.sub(r'^```json\s*', '', block.text.strip())
                raw = re.sub(r'\s*```$', '', raw)
                data = json.loads(raw)
                return _split_adresse(data)
    except Exception:
        pass
    return {}


def _split_adresse(data: dict) -> dict:
    """
    Fallback: if Claude returned the full address in the 'adresse' field
    (e.g. "Rue de la Loi 42, 1040 Bruxelles"), extract postal code and city.
    Only fills missing fields — never overwrites values Claude already separated.
    """
    adresse = data.get('adresse') or ''
    if not adresse:
        return data
    if data.get('code_postal') and data.get('ville'):
        return data

    # Look for a 4-digit Belgian postal code inside the address string
    m = re.search(r'\b(\d{4})\s+([A-Za-zÀ-ÿ\- ]+)$', adresse)
    if m:
        cp, ville = m.group(1), m.group(2).strip()
        rue = adresse[:m.start()].strip().rstrip(',').strip()
        if not data.get('code_postal'):
            data['code_postal'] = cp
        if not data.get('ville'):
            data['ville'] = ville
        if rue:
            data['adresse'] = rue
    return data


# ── Claude analysis ────────────────────────────────────────────────────────────
ANALYSIS_PROMPT = """Tu es un expert en pension complémentaire belge (2e pilier), spécialisé dans l'optimisation des réserves en Branche 21 vers Branche 23.

## Structure des extraits mypension.be

Les documents mypension.be suivent toujours ce format :
- En-tête : nom, prénom et NISS de l'assuré, date de l'extrait
- Section "Pension complémentaire" ou "Aanvullend pensioen" contenant un ou plusieurs tableaux :
  Colonnes typiques : Organisme de pension / Assureur | Numéro de contrat / Police | Type de contrat (Branche 21 / Branche 23 / Tak 21 / Tak 23) | Réserves acquises / Verworven reserves | Date de valeur / Waarderingsdatum
- Le total des réserves peut apparaître comme "Total", "Totaal" ou être la somme des lignes
- Les montants sont en euros, format belge : 12 345,67 ou 12.345,67
- Les documents peuvent être en français, néerlandais ou les deux

## Exemple réel de section 2.1 telle qu'elle apparaît dans un PDF mypension.be

Voici comment la section 2.1 est typiquement extraite d'un PDF (format tabulaire aplati) :

```
2.1 Plans de pension comme travailleur salarié au 01/01/2024
Organisateur | Organisme de pension | Plan de pension | Statut d'affiliation au 01/01/2024 | Réserve de pension le 01/01/2024 | Evènement après 01/01/2024
Pas d'application | OCA - CVV 50148 0421.387.497 | Plan de pension de l'employeur EUCL/DB/BA 2012-0000-0000-0006-8090-8460 | Affilié non actif | 39.391,76 € | -
Pas d'application | P & V Assurances 0402.236.531 | Plan de pension de l'employeur VIC53010125L00 2011-0000-0000-0001-8629-8903 | Affilié actif | 20.463,44 € | Sortie le 01/04/2024
Pas d'application | AG INSURANCE 0404.494.849 | Plan de pension de l'employeur RG0415622333000000000010992 | Affilié non actif | 14.743,75 € | -
Total de la réserve de pension comme travailleur salarié: 166.807,83 €
```

**Interprétation des statuts :**
- "Affilié non actif" → plan dormant, transférable si réserve > 10 000 €
- "Affilié actif" AVEC "Sortie le JJ/MM/AAAA" dans la colonne Evènement → l'affilié a quitté l'employeur après la date de référence. Ce plan EST devenu dormant et est transférable si réserve > 10 000 €
- "Affilié actif" SANS mention de sortie → plan actif, non transférable

**Règle pour les plans "Sortie le" :** un plan avec statut "Affilié actif" au 01/01/2024 mais portant la mention "Sortie le XX/XX/XXXX" est traité comme dormant car l'affilié a quitté l'employeur. Ces plans sont éligibles au transfert.

## Mots-clés à rechercher (FR / NL / EN / DE)

Assureurs fréquents : AG Insurance, Allianz, Athora, AXA, Belfius Insurance, Ethias, Federale, Generali, ING Life, KBC Insurance, NN Insurance, P&V, Vivium, Integrale, Argenta, Fidelity, Equitable Life
Réserves : "réserves acquises", "verworven reserves", "acquired reserves", "erworbene Reserven", "valeur de rachat", "afkoopwaarde", "surrender value", "Rückkaufswert"
Statut dormant : "non-actif", "niet-actief", "inactive", "inaktiv", "dormant", "slapend", "sleeping", "ruhend", "Sortie le", "Uittreding op", "Left on", "Ausgetreten am"

## Document à analyser

<extrait_pension>
{texte_pdf}
</extrait_pension>

## Structure exacte des extraits mypension.be

**Section 0 — En-tête du dossier**
Intitulé : "Dossier « ma pension complémentaire »" suivi de l'année (ex. 2024)
Contient :
- "Mes données" : prénom, nom, numéro NISS (format XX.XX.XX-XXX.XX), adresse complète

**Section 2 — Ma pension complémentaire comme travailleur salarié**
Intitulé : "Ma pension complémentaire comme travailleur salarié" / "Mijn aanvullend pensioen als werknemer"
La présence de cette section CONFIRME que l'assuré a des droits en tant que salarié.
Contient : Couverture décès au JJ/MM/AAAA : XX XXX,XX €

**Section 2.1 — Plans de pension comme travailleur salarié** ← SECTION CLÉ
Intitulé : "Plans de pension comme travailleur salarié" / "Pensioenplannen als werknemer"
Contient le tableau détaillé des contrats avec pour chaque plan :
- Nom de l'organisme / assureur
- Numéro de plan ou contrat
- Statut : actif, non-actif, dormant, "Sortie le JJ/MM/AAAA"
- Type (Branche 21 / Branche 23 / Tak 21 / Tak 23)
- Réserves acquises en € à une date de valeur donnée

**Section 2.2 — Fiches de détail individuelles** ← SOURCE DE L'ORGANISATEUR
Chaque fiche de détail (une par plan) contient :
- "Référence : XXXXXXX" → numéro de contrat (confirme ou complète le numéro de la section 2.1)
- "Organisé par NOM_ORGANISME (numéro d'entreprise 0XXX.XXX.XXX)" → **organisateur** du plan
- "Géré par NOM_ORGANISME (numéro d'entreprise 0XXX.XXX.XXX)" → **organisme de pension** (assureur gérant)
Pour chaque contrat, croise la référence de la section 2.1 avec la fiche 2.2 correspondante pour extraire organisateur et organisme_gestion.

**IMPORTANT — ordre des sections non garanti :**
Les sections ne suivent PAS nécessairement l'ordre numérique dans le document PDF.
La section 2.1 peut apparaître APRÈS les sections 3, 3.1, 3.2, etc.
Tu dois parcourir l'intégralité du document avant de conclure qu'une section est absente.
Ne déclare eligible = "a_verifier" QUE si, après avoir lu tout le document, aucun tableau de plans avec statuts n'est trouvé nulle part.

La section 2.1 est la source principale des données de plan. La section 2.2 (fiches individuelles) est optionnelle et son absence n'affecte pas l'éligibilité.
Si la section 2.1 est présente (quel que soit son emplacement dans le PDF) → les plans sont identifiables, c'est suffisant pour conclure.

## Instructions

Analyse ce document et réponds UNIQUEMENT au format JSON suivant, sans texte avant ou après :

{{
  "eligible": true, false, ou "a_verifier",
  "titre": une des trois valeurs ci-dessous,
  "resume": "Message personnalisé s'adressant directement à la personne. Commence par 'Cher Monsieur [prénom] [nom],' ou 'Chère Madame [prénom] [nom],' selon le sexe extrait, puis utilise 'vous' tout au long. Ex : 'Cher Monsieur Jean Dupont, vous disposez d'une réserve totale de ...'",
  "details": [
    "Point clé 1 — réserves trouvées, montants, assureurs",
    "Point clé 2 — statut des plans (dormant/actif) et conditions confirmées ou à vérifier"
  ],
  "raisons_refus": ["Raison détaillée — uniquement si eligible = false"],
  "montant_total": "XX XXX,XX €" ou null,
  "couverture_deces": "XX XXX,XX €" ou null,
  "nb_contrats": nombre entier ou null,
  "contrats": [
    {{
      "assureur": "Nom exact de l'organisme gérant tel qu'il apparaît dans 'Géré par' (section 2.2) ou dans le tableau 2.1",
      "numero": "numéro de contrat ou police, vide si absent",
      "type_branche": "Branche 21" ou "Branche 23" ou "Inconnu",
      "reserve": "XX XXX,XX" ou null,
      "date_valeur": "JJ/MM/AAAA" ou null,
      "statut": "dormant" ou "actif" ou "inconnu",
      "organisateur": "Nom exact extrait de 'Organisé par' dans la fiche 2.2 correspondante, sans le numéro BCE. Null si absent ou 'Pas d'application'.",
      "organisateur_bce": "Numéro BCE de l'organisateur au format 0XXX.XXX.XXX, extrait de la même ligne 'Organisé par'. Null si absent."
    }}
  ],
  "personne": {{
    "prenom": "prénom extrait de la section 0 ou null",
    "nom": "nom extrait de la section 0 ou null",
    "niss": "numéro NISS format XX.XX.XX-XXX.XX ou null",
    "adresse": "adresse complète ou null",
    "sexe": "M si masculin, F si féminin — déduit du NISS (chiffres 7-9 impairs = M, pairs = F) ou du prénom si NISS absent. null si indéterminable."
  }}
}}

Valeurs possibles pour "titre" selon eligible :
- eligible = true       → "Oui, un transfert vers la Branche 23 est possible"
- eligible = "a_verifier" → "Un transfert est probablement possible — une vérification est nécessaire"
- eligible = false      → "Non, un transfert vers la Branche 23 n'est pas possible"

## Règles d'éligibilité — toutes les conditions doivent être réunies

**Condition 1 — Statut salarié**
Seules les réserves constituées en tant que **salarié** sont transférables.
Les réserves constituées en tant qu'indépendant (EIP, PLCI, pension libre complémentaire) ne sont PAS transférables.
Mots-clés indiquant le statut salarié : "salarié", "werknemer", "employee", "Arbeitnehmer", "assurance de groupe", "groepsverzekering", "pension sectorielle", "sectoraal pensioen".

**Condition 2 — Type de contrat : plan non-actif / dormant**
Seuls les plans dans lesquels l'assuré n'est plus en activité chez l'employeur concerné sont transférables.
Recherche impérativement ces mentions dans le document :
- "non-actif", "non actif", "niet-actief", "niet actief", "inactif"
- "dormant", "slapend"
- "Sortie le", "Uittreding op", "Date de sortie", "Left on", "Ausgetreten am"
- "ex-employeur", "vorige werkgever", "ancien employeur"
Si aucune de ces mentions n'est présente, le plan est considéré actif et non transférable.

**Condition 3 — Montant minimum (condition commerciale UpTwoU, non légale)**
Le total des réserves transférables doit être **strictement supérieur à 10 000 €**.
Il s'agit d'un seuil fixé par UpTwoU pour des raisons de viabilité économique, et non d'une obligation légale.
Si le total est ≤ 10 000 €, eligible = false. Dans raisons_refus, précise explicitement que ce seuil est une condition imposée par UpTwoU et non par la loi.

Note : le type de branche (Branche 21 ou Branche 23) n'est PAS un critère d'éligibilité. Renseigne le champ "type_branche" à titre informatif uniquement, mais ne l'utilise pas pour déterminer eligible.

**Arbre de décision :**

1. Le document est illisible ou clairement hors-sujet (pas un extrait mypension.be) → eligible = false

2. Le document contient uniquement des réserves d'indépendant (EIP/PLCI confirmés, aucune mention salarié/assurance de groupe) → eligible = false

3. Le document contient des réserves de salarié (ou statut ambigu) ET le tableau des plans (section 2.1 ou équivalent) EST présent :
   - Plan dormant confirmé + > 10 000 € → eligible = true
   - Plan actif uniquement (aucun dormant) → eligible = false
   - Mix actif/dormant → eligible = true si total des plans dormants > 10 000 €

4. Le document contient des réserves > 10 000 € MAIS aucun tableau de plans avec statuts n'est trouvé (impossible de distinguer actif/dormant) :
   → eligible = "a_verifier"
   → Expliquer dans details qu'il n'est pas possible de déterminer le statut des plans et inviter à télécharger l'extrait complet depuis mypension.be

5. Réserves présentes mais ≤ 10 000 € au total → eligible = false.
   Dans raisons_refus, formuler ainsi : "Le total de vos réserves transférables est inférieur au seuil de 10 000 € requis par UpTwoU. Il ne s'agit pas d'une limite légale mais d'une condition commerciale fixée par UpTwoU pour assurer la viabilité économique de la gestion."

**Langues supportées : français, néerlandais, anglais, allemand**"""


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_pdf_text(filepath):
    parts = []
    with pdfplumber.open(filepath) as pdf:
        for i, page in enumerate(pdf.pages):
            # Texte courant
            t = page.extract_text(x_tolerance=2, y_tolerance=2)
            if t:
                parts.append(t)
            # Tableaux (réserves, contrats — souvent en grille sur mypension.be)
            tables = page.extract_tables({
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 5,
            })
            for table in tables:
                rows = []
                for row in table:
                    cleaned = [str(c or '').strip().replace('\n', ' ') for c in row]
                    if any(cleaned):
                        rows.append(' | '.join(cleaned))
                if rows:
                    parts.append(f"[Tableau page {i+1}]\n" + '\n'.join(rows))
    return "\n\n".join(parts)


def analyse_avec_claude(texte_pdf):
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user",
                   "content": ANALYSIS_PROMPT.format(texte_pdf=texte_pdf[:30000])}]
    )
    for block in message.content:
        if block.type == "text":
            raw = block.text.strip()
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return raw
    return None


# ── Public routes ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyser", methods=["GET", "POST"])
def analyser():
    if request.method == "POST":
        file = request.files.get("extrait")
        if not file or file.filename == "":
            flash("Veuillez sélectionner un fichier.", "error")
            return redirect(url_for("analyser"))
        if not allowed_file(file.filename):
            flash("Format invalide. Veuillez uploader un fichier PDF.", "error")
            return redirect(url_for("analyser"))

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            json_brut = parse_mypension_pdf(filepath)
            resultat = json.loads(json_brut)

            # ── Post-process: exclude Vitis / Onelife ──────────────────────
            if 'contrats' in resultat:
                exclus = [c for c in resultat['contrats'] if is_assureur_exclu(c.get('assureur'))]
                resultat['contrats'] = [c for c in resultat['contrats'] if not is_assureur_exclu(c.get('assureur'))]
                if exclus:
                    resultat.setdefault('details', []).append(
                        f"{len(exclus)} contrat(s) Vitis Life / Onelife exclus de l'analyse (déjà en gestion Branche 23)."
                    )

            # ── Post-process: pension age eligibility ──────────────────────
            niss = (resultat.get('personne') or {}).get('niss')
            if niss:
                bd = birthdate_from_niss(niss)
                if bd:
                    rem = remaining_to_pension(bd)
                    age_legale = 66 if _add_years(bd, 66) < date(2030, 1, 1) else 67

                    if rem is None:
                        # Already at or past pension age
                        resultat['eligible'] = False
                        resultat['titre'] = "Non, un transfert vers la Branche 23 n'est pas possible"
                        resultat.setdefault('raisons_refus', []).append(
                            f"Vous avez atteint ou dépassé l'âge légal de la pension ({age_legale} ans). "
                            "Un transfert de réserves vers la Branche 23 n'est plus pertinent."
                        )
                    else:
                        years, months = rem
                        total_months = years * 12 + months
                        duree = f"{years} an{'s' if years > 1 else ''} et {months} mois"

                        if total_months < 24:
                            # Less than 2 years → refuse
                            resultat['eligible'] = False
                            resultat['titre'] = "Non, un transfert vers la Branche 23 n'est pas possible"
                            resultat.setdefault('raisons_refus', []).append(
                                f"Durée restante avant la pension insuffisante : {duree} "
                                f"(âge légal : {age_legale} ans). Un minimum de 2 ans est requis "
                                "pour qu'un transfert en Branche 23 soit pertinent."
                            )
                        else:
                            # Enrich résumé and details with remaining time
                            resultat['resume'] = (
                                (resultat.get('resume') or '') +
                                f" Durée restante avant la pension légale ({age_legale} ans) : {duree}."
                            )
                            resultat.setdefault('details', []).append(
                                f"Horizon de placement : {duree} avant l'âge légal de la pension "
                                f"({age_legale} ans) — compatible avec un investissement en Branche 23."
                            )

            # Re-serialize after post-processing so session stays coherent
            json_brut = json.dumps(resultat, ensure_ascii=False)
            session['analyse_json'] = json_brut
            session['analyse_filename'] = filename

            return render_template("resultat.html", r=resultat, filename=filename)

        except json.JSONDecodeError:
            flash("L'analyse n'a pas pu être interprétée. Veuillez réessayer.", "error")
            return redirect(url_for("analyser"))
        except Exception as e:
            flash(f"Une erreur inattendue s'est produite : {e}", "error")
            return redirect(url_for("analyser"))

    return render_template("analyser.html")


# ── Auth routes ────────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('client_dashboard'))

    analyse_json = session.get('analyse_json')
    if not analyse_json:
        flash("Veuillez d'abord analyser votre extrait de pension.", "error")
        return redirect(url_for('analyser'))

    analyse = json.loads(analyse_json)
    personne = analyse.get('personne') or {}
    form = RegisterForm()

    if request.method == 'GET':
        form.prenom.data = personne.get('prenom') or ''
        form.nom.data = personne.get('nom') or ''

    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash("Un compte existe déjà avec cette adresse e-mail.", "error")
            return render_template("register.html", form=form, analyse=analyse)

        # Check NISS uniqueness
        niss = personne.get('niss') or ''
        if niss:
            existing = Client.query.filter_by(niss=niss).first()
            if existing:
                flash("Un compte existe déjà pour ce numéro national. Connectez-vous.", "error")
                return redirect(url_for('login'))

        user = User(email=form.email.data, role='client')
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        adresse_raw = personne.get('adresse') or ''
        client_profile = Client(
            user_id=user.id,
            nom=form.nom.data,
            prenom=form.prenom.data,
            niss=niss,
            adresse=adresse_raw,
        )
        db.session.add(client_profile)
        db.session.flush()

        analyse_record = Analyse(
            client_id=client_profile.id,
            filename=session.get('analyse_filename', ''),
            resultat_json=analyse_json
        )
        db.session.add(analyse_record)
        db.session.flush()

        for c in analyse.get('contrats', []):
            db.session.add(TransfertReserve(
                client_id=client_profile.id,
                analyse_id=analyse_record.id,
                assureur=c.get('assureur'),
                numero=c.get('numero'),
                type_branche=c.get('type_branche'),
                statut=c.get('statut', 'dormant'),
                reserve=c.get('reserve'),
                date_valeur=c.get('date_valeur'),
                organisateur=c.get('organisateur'),
                organisateur_bce=c.get('organisateur_bce'),
            ))

        token = secrets.token_urlsafe(32)
        user.email_token = token
        user.email_confirmed = False
        db.session.commit()

        _send_verification_email(user.email, token)
        return redirect(url_for('email_en_attente', email=user.email))

    return render_template("register.html", form=form, analyse=analyse)


@app.route("/email-en-attente")
def email_en_attente():
    email = request.args.get('email', '')
    return render_template("email_en_attente.html", email=email)


@app.route("/verifier-email/<token>")
def verifier_email(token):
    user = User.query.filter_by(email_token=token).first()
    if not user:
        flash("Ce lien de confirmation est invalide ou a déjà été utilisé.", "error")
        return redirect(url_for('login'))
    user.email_confirmed = True
    user.email_token = None
    db.session.commit()
    login_user(user)
    session.permanent = True
    session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    flash("Votre adresse e-mail est confirmée. Bienvenue sur UpTwoU !", "success")
    return redirect(url_for('onboarding_kyc'))


@app.route("/renvoyer-confirmation", methods=["POST"])
def renvoyer_confirmation():
    email = request.form.get('email', '').strip()
    user = User.query.filter_by(email=email).first()
    if user and not user.email_confirmed:
        token = secrets.token_urlsafe(32)
        user.email_token = token
        db.session.commit()
        _send_verification_email(email, token)
    flash("Si un compte non confirmé existe pour cette adresse, un nouvel email vient d'être envoyé.", "success")
    return redirect(url_for('email_en_attente', email=email))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        dest = 'admin_dashboard' if current_user.role == 'courtier' else 'client_dashboard'
        return redirect(url_for(dest))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()

        # ── Lockout check ────────────────────────────────────────────────────
        if user and user.is_locked():
            remaining = int((user.locked_until - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 60) + 1
            flash(f"Compte temporairement bloqué suite à trop de tentatives. "
                  f"Réessayez dans {remaining} minute{'s' if remaining > 1 else ''}.", "error")
            return render_template("login.html", form=form)

        if user and user.check_password(form.password.data):
            if not user.email_confirmed:
                return redirect(url_for('email_en_attente', email=user.email))
            user.reset_login_attempts()
            db.session.commit()
            login_user(user)
            session.permanent = True
            session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            next_page = request.args.get('next')
            if not next_page:
                next_page = url_for('admin_dashboard' if user.role == 'courtier' else 'client_dashboard')
            return redirect(next_page)

        # ── Failed attempt ───────────────────────────────────────────────────
        if user:
            user.record_failed_login()
            db.session.commit()
            remaining_attempts = max(0, 5 - (user.failed_login_attempts or 0))
            if user.is_locked():
                flash("Compte bloqué pendant 15 minutes après 5 tentatives échouées.", "error")
            else:
                flash(f"Email ou mot de passe incorrect. "
                      f"Il vous reste {remaining_attempts} tentative{'s' if remaining_attempts != 1 else ''}.", "error")
        else:
            flash("Email ou mot de passe incorrect.", "error")

    return render_template("login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── itsme OIDC ────────────────────────────────────────────────────────────────
@app.route("/auth/itsme")
def auth_itsme():
    if not itsme_auth.is_configured():
        flash("La connexion via itsme n'est pas encore configurée.", "error")
        return redirect(url_for('login'))

    next_url = request.args.get('next', '')
    redirect_uri = url_for('auth_itsme_callback', _external=True)
    auth_url = itsme_auth.build_auth_url(session, redirect_uri, next_url)
    if not auth_url:
        flash("Impossible de joindre le service itsme. Réessayez dans quelques instants.", "error")
        return redirect(url_for('login'))

    return redirect(auth_url)


@app.route("/auth/itsme/callback")
def auth_itsme_callback():
    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', error)
        flash(f"Connexion itsme annulée : {desc}", "error")
        return redirect(url_for('login'))

    state = request.args.get('state', '')
    if state != session.get('itsme_state', ''):
        flash("Session itsme invalide. Veuillez réessayer.", "error")
        return redirect(url_for('login'))

    code = request.args.get('code', '')
    redirect_uri = url_for('auth_itsme_callback', _external=True)
    claims = itsme_auth.exchange_code(code, session, redirect_uri)

    if not claims:
        flash("L'authentification itsme a échoué. Veuillez réessayer.", "error")
        return redirect(url_for('login'))

    # Clean itsme session keys
    next_url = session.pop('itsme_next', '')
    session.pop('itsme_state', None)
    session.pop('itsme_nonce', None)
    session.pop('itsme_code_verifier', None)

    # Find or create user from itsme sub / email
    sub   = claims.get('sub', '')
    email = claims.get('email', '')

    user = None
    if email:
        user = User.query.filter_by(email=email).first()

    if user is None:
        # Auto-create a client account from itsme identity
        given  = claims.get('given_name', '')
        family = claims.get('family_name', '')
        phone  = claims.get('phone_number', '')

        if not email:
            flash("itsme n'a pas transmis d'adresse e-mail. Veuillez vous connecter avec vos identifiants.", "error")
            return redirect(url_for('login'))

        user = User(email=email, role='client')
        user.password_hash = 'itsme:' + sub   # unusable password — itsme only
        db.session.add(user)
        db.session.flush()

        client_profile = Client(
            user_id=user.id,
            nom=family.upper() if family else '',
            prenom=given,
            telephone=phone,
        )
        db.session.add(client_profile)
        db.session.commit()
        flash(f"Compte créé via itsme. Bienvenue, {given} !", "success")
    else:
        flash("Connexion itsme réussie.", "success")

    login_user(user)
    session.permanent = True
    session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    if next_url:
        return redirect(next_url)
    dest = 'admin_dashboard' if user.role == 'courtier' else 'client_dashboard'
    return redirect(url_for(dest))


# ── Client routes ──────────────────────────────────────────────────────────────
@app.route("/client/dashboard")
@login_required
@client_required
def client_dashboard():
    return redirect(url_for('client_contrats'))


@app.route("/client/contrats")
@login_required
@client_required
def client_contrats():
    from datetime import date
    client = current_user.client_profile
    actifs = client.contrats_actifs
    new_ids = [c.id for c in actifs]
    ts_rows = TransfertSignature.query.filter(
        TransfertSignature.contrat_id.in_(new_ids)
    ).all() if new_ids else []
    ts_by_contrat = {}
    for ts in ts_rows:
        ts_by_contrat.setdefault(ts.contrat_id, []).append(ts)
    ts_statuts_by_contrat = {
        cid: {t.statut_signature for t in lst}
        for cid, lst in ts_by_contrat.items()
    }
    date_retraite_str = None
    age_retraite = None
    if client.niss:
        bd = birthdate_from_niss(client.niss)
        if bd:
            at_66 = _add_years(bd, 66)
            age_retraite = 66 if at_66 < date(2030, 1, 1) else 67
            rd = retirement_date_for(bd)
            date_retraite_str = f"1er {_MOIS_FR[rd.month - 1]} {rd.year}"

    profil_locked = False
    next_profil_change = None
    if client.profil_date:
        days_since = (datetime.now(timezone.utc).replace(tzinfo=None) - client.profil_date).days
        if days_since < 90:
            profil_locked = True
            next_profil_change = (client.profil_date + timedelta(days=90)).strftime('%d/%m/%Y')

    return render_template("client/contrats.html",
                           client=client,
                           ts_by_contrat=ts_by_contrat,
                           ts_statuts_by_contrat=ts_statuts_by_contrat,
                           today=date.today(),
                           date_retraite_str=date_retraite_str,
                           age_retraite=age_retraite,
                           profil_locked=profil_locked,
                           next_profil_change=next_profil_change)


@app.route("/client/contrat/<int:contract_id>/extrait")
@login_required
@client_required
def client_extrait_contrat(contract_id):
    client = current_user.client_profile
    contract = db.session.get(Contrat, contract_id)
    if not contract or contract.client_id != client.id:
        abort(404)
    pdf_bytes = generer_extrait_contrat(client, contract)
    from flask import Response
    filename = f"extrait-contrat-{contract.numero or contract_id}.pdf"
    return Response(pdf_bytes, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="{filename}"'
    })


@app.route("/api/courtiers/search")
@login_required
def api_courtiers_search():
    from flask import jsonify
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify([])
    cabinets = [c for c in CabinetCourtage.query.all() if c.actif and str(c.actif).lower() not in ('0', 'false', '')]
    results = []
    for cab in cabinets:
        haystack = ' '.join(filter(None, [cab.nom, cab.code_postal])).lower()
        if q in haystack:
            nom = cab.nom or ''
            results.append({
                'id': cab.id,
                'nom': nom,
                'code_postal': cab.code_postal or '',
                'ville': cab.ville or '',
                'initiales': (nom[:2]).upper() if nom else '??',
            })
    return jsonify(results)


@app.route("/client/demande-changement-courtier", methods=["POST"])
@login_required
@client_required
def client_demande_changement_courtier():
    client = current_user.client_profile
    nouveau_courtier_id = request.form.get("courtier_id", "").strip()
    message_text = request.form.get("message", "").strip()

    ancien_nom = None
    if client.courtier and client.courtier.client_profile:
        cp = client.courtier.client_profile
        ancien_nom = f"{cp.prenom} {cp.nom}"
    elif client.cabinet:
        ancien_nom = client.cabinet.nom

    # Suppression du conseiller
    if nouveau_courtier_id == "none":
        client.courtier_id = None
        client.cabinet_id = None
        note = f"Suppression du conseiller : {ancien_nom or '(aucun)'}."
        if message_text:
            note += f"\nMessage client : {message_text}"
        db.session.add(ContactMessage(
            nom=f"{client.prenom} {client.nom}",
            email=current_user.email,
            sujet="Suppression de conseiller",
            message=note,
        ))
        db.session.commit()
        flash("Votre conseiller a été retiré.", "success")
        return redirect(url_for("client_contrats"))

    if not nouveau_courtier_id:
        flash("Veuillez sélectionner un conseiller.", "error")
        return redirect(url_for("client_contrats"))

    cabinet = CabinetCourtage.query.get(int(nouveau_courtier_id))
    if not cabinet:
        flash("Conseiller introuvable.", "error")
        return redirect(url_for("client_contrats"))

    client.cabinet_id = cabinet.id
    client.courtier_id = None  # reset individual courtier — cabinet takes over

    nouveau_nom = cabinet.nom
    note = f"Changement de conseiller : {ancien_nom or '(aucun)'} → {nouveau_nom}."
    db.session.add(ContactMessage(
        nom=f"{client.prenom} {client.nom}",
        email=current_user.email,
        sujet="Changement de conseiller",
        message=note,
    ))
    db.session.commit()
    flash(f"Votre conseiller a été mis à jour : {nouveau_nom}.", "success")
    return redirect(url_for("client_contrats"))


@app.route("/client/modifier-profil", methods=["POST"])
@login_required
@client_required
def client_modifier_profil():
    client = current_user.client_profile
    if client.profil_date:
        days_since = (datetime.now(timezone.utc).replace(tzinfo=None) - client.profil_date).days
        if days_since < 90:
            next_date = (client.profil_date + timedelta(days=90)).strftime('%d/%m/%Y')
            flash(f"Profil modifiable une fois par trimestre. Prochain changement possible le {next_date}.", "error")
            return redirect(url_for('client_contrats'))
    profil = request.form.get("profil_risque", "").strip()
    if profil in ("prudent", "equilibre", "dynamique", "conviction"):
        _log_profil_change(client, profil, "client")
        client.profil_risque = profil
        client.profil_choisi_par = "client"
        client.profil_date = datetime.now(timezone.utc)
        db.session.commit()
        flash("Profil de risque mis à jour.", "success")
    else:
        flash("Profil invalide.", "error")
    return redirect(url_for("client_contrats"))


@app.route("/client/modifier-beneficiaires", methods=["POST"])
@login_required
@client_required
def client_modifier_beneficiaires():
    client = current_user.client_profile
    client.beneficiaire_1 = request.form.get("beneficiaire_1", "").strip() or None
    client.beneficiaire_2 = request.form.get("beneficiaire_2", "").strip() or None
    db.session.commit()
    flash("Bénéficiaires mis à jour.", "success")
    return redirect(url_for("client_contrats"))


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        nom     = request.form.get("nom", "").strip()
        email   = request.form.get("email", "").strip()
        sujet   = request.form.get("sujet", "").strip()
        message = request.form.get("message", "").strip()
        if nom and email and message:
            msg = ContactMessage(nom=nom, email=email, sujet=sujet, message=message)
            db.session.add(msg)
            db.session.commit()
            flash("Votre message a bien été envoyé. Nous vous répondrons dans les meilleurs délais.", "success")
            return redirect(url_for("contact"))
        flash("Merci de remplir tous les champs obligatoires.", "error")
    return render_template("contact.html")


@app.route("/comment-ca-marche")
def comment_ca_marche():
    return render_template("comment-ca-marche.html")


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/gestion")
def gestion_publique():
    client = None
    if current_user.is_authenticated and current_user.role == 'client':
        client = current_user.client_profile
    return render_template("gestion.html", client=client)


@app.route("/client/gestion")
@login_required
@client_required
def client_gestion():
    client = current_user.client_profile
    today = date.today()

    def _parse_reserve(s):
        if not s:
            return 0.0
        s = str(s).strip().replace('€', '').replace('\xa0', '').replace(' ', '')
        if ',' in s and '.' in s:
            if s.rfind(',') > s.rfind('.'):
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_date_fr(s):
        if not s:
            return None
        parts = s.split('/')
        if len(parts) == 3:
            try:
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            except (ValueError, IndexError):
                pass
        return None

    def _add_month(d):
        m, y = d.month + 1, d.year
        if m > 12:
            m, y = 1, y + 1
        day = min(d.day, 28)
        return date(y, m, day)

    # Valeur des réserves reçues sur les contrats UpTwoU
    reserve_actuelle = sum(
        (rr.montant or 0.0) for c in client.contrats_actifs for rr in c.reserves_recues
    )

    date_terme = None
    for c in client.contrats_actifs:
        dt = _parse_date_fr(c.date_terme)
        if dt and (date_terme is None or dt < date_terme):
            date_terme = dt

    pension_dt = None
    if client.niss:
        bd = birthdate_from_niss(client.niss)
        if bd:
            pension_dt = pension_date_for(bd)

    end_date = date_terme or pension_dt
    if end_date is None or end_date <= today:
        end_date = date(today.year + 20, today.month, today.day)

    injections = []
    for c in client.contrats_actifs:
        for rr in c.reserves_recues:
            inj = _parse_date_fr(rr.date_reception)
            if inj:
                label = rr.transfert.assureur if rr.transfert else 'Injection'
                injections.append({'date': inj.isoformat(), 'label': label})

    # Scenario parameters: base return + annual volatility spread (σ)
    # Spread decays as σ/√t → band is wide near term, converges over time
    SCENARIO_PROFILES = {
        'prudent':    {'r': 0.035, 'sigma': 0.025},
        'equilibre':  {'r': 0.050, 'sigma': 0.035},
        'dynamique':  {'r': 0.070, 'sigma': 0.050},
        'conviction': {'r': 0.090, 'sigma': 0.070},
    }
    sp = SCENARIO_PROFILES.get(client.profil_risque or '', {'r': 0.050, 'sigma': 0.035})
    r_base, sigma = sp['r'], sp['sigma']

    proj_dates, proj_base, proj_opt, proj_pess = [], [], [], []
    if reserve_actuelle > 0:
        cur, m = today, 0
        while cur <= end_date:
            t = m / 12                          # years elapsed
            spread = sigma / max(t, 1.0) ** 0.5 # cap at t=1 → max spread = sigma
            proj_dates.append(cur.isoformat())
            proj_base.append(round(reserve_actuelle * (1 + r_base) ** t, 2))
            proj_opt.append(round(reserve_actuelle * (1 + r_base + spread) ** t, 2))
            proj_pess.append(round(max(reserve_actuelle * (1 + r_base - spread) ** t, 0), 2))
            cur = _add_month(cur)
            m += 1

    chart_data = json.dumps({
        'dates':       proj_dates,
        'base':        proj_base,
        'opt':         proj_opt,
        'pess':        proj_pess,
        'injections':  injections,
        'date_terme':  date_terme.isoformat() if date_terme else None,
        'pension_date': pension_dt.isoformat() if pension_dt else None,
        'reserve_actuelle': reserve_actuelle,
    })

    return render_template("client/gestion.html", client=client, chart_data=chart_data)


@app.route("/client/profil", methods=["GET", "POST"])
@login_required
@client_required
def client_profil():
    client = current_user.client_profile
    form = ProfilForm()
    if request.method == 'GET':
        form.profil.data = client.profil_risque

    if form.validate_on_submit():
        _log_profil_change(client, form.profil.data, 'client')
        client.profil_risque = form.profil.data
        client.profil_choisi_par = 'client'
        client.profil_date = datetime.now(timezone.utc)
        db.session.commit()
        flash("Profil d'investissement mis à jour.", "success")
        return redirect(url_for('client_profil'))

    logs = sorted(client.profil_logs, key=lambda l: l.created_at, reverse=True)
    return render_template("client/profil.html", client=client, form=form, logs=logs)


@app.route("/client/questionnaire", methods=["GET", "POST"])
@login_required
@client_required
def client_questionnaire():
    client = current_user.client_profile

    q1_prefill = q1_label = None
    if client.niss:
        bd = birthdate_from_niss(client.niss)
        if bd:
            rem = remaining_to_pension(bd)
            if rem is None:
                q1_prefill, q1_label = '0', 'Moins de 5 ans'
            else:
                total_months = rem[0] * 12 + rem[1]
                if total_months < 60:
                    q1_prefill, q1_label = '0', 'Moins de 5 ans'
                elif total_months < 120:
                    q1_prefill, q1_label = '1', '5 à 10 ans'
                elif total_months < 240:
                    q1_prefill, q1_label = '2', '10 à 20 ans'
                else:
                    q1_prefill, q1_label = '3', 'Plus de 20 ans'

    form = QuestionnaireForm()
    if form.validate_on_submit():
        q1_val = q1_prefill if q1_prefill is not None else form.q1.data
        score = int(q1_val) + sum(int(getattr(form, f'q{i}').data) for i in range(2, 7))
        profil = score_to_profil(score)
        _log_profil_change(client, profil, 'client', score=score)
        client.profil_risque = profil
        client.profil_choisi_par = 'client'
        client.profil_date = datetime.now(timezone.utc)
        client.questionnaire_score = score
        db.session.commit()
        flash(f"Profil <strong class='capitalize'>{profil}</strong> déterminé par le questionnaire.", "success")
        return redirect(url_for('client_profil'))

    if q1_prefill is not None:
        form.q1.data = q1_prefill

    return render_template("client/questionnaire.html", client=client, form=form,
                           q1_prefill=q1_prefill, q1_label=q1_label)


@app.route("/client/donnees", methods=["GET", "POST"])
@login_required
@client_required
def client_donnees():
    client = current_user.client_profile
    form = DonneesForm(obj=client)

    standard_pays = [c[0] for c in form.pays.choices]
    if request.method == 'GET' and client.pays and client.pays not in standard_pays:
        form.pays.data = 'Autre'
        form.pays_libre.data = client.pays

    if form.validate_on_submit():
        form.populate_obj(client)
        if form.pays.data == 'Autre':
            client.pays = form.pays_libre.data.strip() or 'Autre'
        db.session.commit()
        flash("Vos données ont été mises à jour.", "success")
        return redirect(url_for('client_donnees'))

    return render_template("client/donnees.html", client=client, form=form)


@app.route("/client/contact", methods=["GET", "POST"])
@login_required
@client_required
def client_contact():
    client = current_user.client_profile
    form = ClientContactForm()
    if form.validate_on_submit():
        msg = ContactMessage(
            client_id=client.id,
            nom=f"{client.prenom or ''} {client.nom or ''}".strip() or current_user.email,
            email=current_user.email,
            sujet=form.sujet.data,
            message=form.message.data,
        )
        db.session.add(msg)
        db.session.commit()
        flash("Votre message a bien été envoyé. Notre équipe vous répondra dans les plus brefs délais.", "success")
        return redirect(url_for('client_contact'))
    messages = ContactMessage.query.filter_by(client_id=client.id)\
                                   .order_by(ContactMessage.created_at.desc()).all()
    return render_template("client/contact.html", client=client, form=form, messages=messages)


@app.route("/client/conditions-generales")
@login_required
@client_required
def client_conditions_generales():
    return render_template("client/conditions_generales.html")


# ── Transfert routes ──────────────────────────────────────────────────────────
@app.route("/client/extrait")
@login_required
@client_required
def client_extrait():
    """Serve the client's latest uploaded mypension.be PDF."""
    client = current_user.client_profile
    analyse = client.latest_analyse
    if not analyse or not analyse.filename:
        flash("Aucun extrait disponible.", "error")
        return redirect(url_for('client_contrats'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], analyse.filename,
                               as_attachment=False)


@app.route("/client/transfert")
@login_required
@client_required
def client_transfert():
    client = current_user.client_profile
    latest = client.latest_analyse
    if latest:
        entrants = [t for t in client.transfer_requests
                    if t.analyse_id == latest.id and t.statut in ('dormant', 'en_cours', 'recu')]
    else:
        entrants = [t for t in client.transfer_requests if t.statut in ('dormant', 'en_cours', 'recu')]
    ts_map = {}
    for t in client.transfer_requests:
        sigs = sorted(t.signatures, key=lambda x: x.cree_le or datetime.min, reverse=True)
        ts_map[t.id] = sigs[0] if sigs else None
    transferts_out = sorted(client.outgoing_transfers,
                            key=lambda t: t.created_at or datetime.min,
                            reverse=True)
    return render_template("client/transfert.html", client=client,
                           entrants=entrants, ts_map=ts_map,
                           transferts_out=transferts_out)


@app.route("/client/transfert/<int:transfert_id>/pdf")
@login_required
@client_required
def client_transfert_pdf(transfert_id):
    from io import BytesIO
    transfert = db.session.get(TransfertReserve, transfert_id)
    if not transfert or transfert.client_id != current_user.client_profile.id:
        flash("Accès non autorisé.", "error")
        return redirect(url_for('client_transfert'))
    ts = next((s for s in transfert.signatures), None)
    b23_num = ts.contrat.numero if ts and ts.contrat else None
    pdf_bytes = generer_annexe1(current_user.client_profile, transfert,
                                nouveau_contrat_ref=b23_num)
    slug = re.sub(r'[^a-z0-9]+', '_', (transfert.assureur or 'assureur').lower())[:30]
    return send_file(BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f"annexe1_transfert_{slug}.pdf")


# ── Onboarding routes ─────────────────────────────────────────────────────────


@app.route("/onboarding/kyc", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_kyc():
    client = current_user.client_profile
    form = OnboardingKYCForm()
    kyc_data = session.get('kyc_data', {})

    if request.method == 'POST':
        action = request.form.get('action')

        # ── Phase 1 : file upload → Claude analysis ──────────────────────
        if action == 'upload':
            file = request.files.get('kyc_file')
            if file and file.filename:
                ext = file.filename.rsplit('.', 1)[-1].lower()
                if ext in ALLOWED_KYC_EXT:
                    fname = secure_filename(f"kyc_{client.id}_{file.filename}")
                    fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                    file.save(fpath)
                    client.kyc_document = fname
                    db.session.commit()
                    try:
                        extracted = parse_belgian_eid(fpath)
                        session['kyc_data'] = extracted

                        # ── Validité de la carte d'identité ──────────────
                        date_val = extracted.get('date_validite')
                        if date_val:
                            try:
                                d, m, y = date_val.split('/')
                                if date(int(y), int(m), int(d)) < date.today():
                                    flash(
                                        f"Carte d'identité expirée le {date_val}. "
                                        "Veuillez fournir un document en cours de validité.",
                                        "error"
                                    )
                            except (ValueError, AttributeError):
                                pass

                        # ── Concordance NISS mypension ↔ carte d'identité ──
                        niss_mp = re.sub(r'\D', '', client.niss or '')
                        niss_ci = re.sub(r'\D', '', extracted.get('niss') or '')
                        if niss_mp and niss_ci and niss_mp != niss_ci:
                            flash(
                                f"Attention : le numéro national de la carte ({extracted.get('niss')}) "
                                f"ne correspond pas à celui de l'extrait mypension.be ({client.niss}). "
                                "Vérifiez et corrigez avant de confirmer.",
                                "error"
                            )
                        else:
                            flash("Document analysé. Vérifiez et complétez vos données.", "success")

                        kyc_data = extracted
                    except FileNotFoundError as e:
                        flash(str(e), "error")
                    except Exception as e:
                        flash(f"Lecture du document impossible : {e}. Remplissez le formulaire manuellement.", "error")
                else:
                    flash("Format non supporté (JPG, PNG ou PDF uniquement).", "error")
            return redirect(url_for('onboarding_kyc'))

        # ── Phase 2 : form confirmation ───────────────────────────────────
        if action == 'confirm' and form.validate_on_submit():
            niss_mypension = re.sub(r'\D', '', client.niss or '')
            niss_carte = re.sub(r'\D', '', form.niss.data or '')
            if niss_mypension and niss_carte and niss_mypension != niss_carte:
                if app.debug:
                    flash(
                        f"[DEV] NISS mismatch — mypension : {niss_mypension} / carte : {niss_carte}. "
                        "Validation forcée en mode développement.",
                        "error"
                    )
                else:
                    _send_kyc_mismatch_emails(client, current_user.email)
                    return redirect(url_for('kyc_en_verification'))

            client.nom = form.nom.data
            client.prenom = form.prenom.data
            client.date_naissance = form.date_naissance.data
            client.sexe = form.sexe.data
            client.niss = form.niss.data
            client.adresse = form.adresse.data
            client.code_postal = form.code_postal.data
            client.ville = form.ville.data
            client.pays = form.pays.data
            client.est_ppe = (form.est_ppe.data == 'oui')
            client.ppe_details = form.ppe_details.data.strip() if client.est_ppe else None
            client.ci_date_validite = session.get('kyc_data', {}).get('date_validite')
            client.kyc_verifie = True
            db.session.commit()
            session.pop('kyc_data', None)
            return redirect(url_for('onboarding_questionnaire'))

    # Pre-fill form from AI extraction or existing client data
    if request.method == 'GET':
        form.nom.data          = kyc_data.get('nom') or client.nom or ''
        form.prenom.data       = kyc_data.get('prenom') or client.prenom or ''
        niss_val = kyc_data.get('niss') or client.niss or ''
        form.niss.data = niss_val
        # Derive sex from NISS digits 7-9 (birth order): odd = M, even = F
        sexe_val = kyc_data.get('sexe') or client.sexe or ''
        if not sexe_val and niss_val:
            digits = re.sub(r'\D', '', niss_val)
            if len(digits) == 11:
                sexe_val = 'M' if int(digits[6:9]) % 2 == 1 else 'F'
        form.sexe.data = sexe_val
        # Derive date of birth from NISS if not already extracted
        ddn = kyc_data.get('date_naissance') or client.date_naissance or ''
        if not ddn and niss_val:
            bd = birthdate_from_niss(niss_val)
            if bd:
                ddn = bd.strftime('%d/%m/%Y')
        form.date_naissance.data = ddn
        # On first visit: only Claude extraction (adresse voulue vide par défaut).
        # On return (kyc already confirmed): also pre-fill from DB.
        db_adresse = client.adresse if client.kyc_verifie else ''
        form.adresse.data     = kyc_data.get('adresse') or db_adresse or ''
        form.code_postal.data = kyc_data.get('code_postal') or (client.code_postal if client.kyc_verifie else '') or ''
        form.ville.data       = kyc_data.get('ville') or (client.ville if client.kyc_verifie else '') or ''
        form.pays.data        = kyc_data.get('pays') or (client.pays if client.kyc_verifie else '') or 'Belgique'
        if client.est_ppe is not None:
            form.est_ppe.data    = 'oui' if client.est_ppe else 'non'
            form.ppe_details.data = client.ppe_details or ''

    has_document = bool(client.kyc_document)
    return render_template("onboarding/kyc.html", form=form,
                           kyc_data=kyc_data, has_document=has_document,
                           current_step=1, prev_url=None)


@app.route("/onboarding/kyc-en-verification")
@login_required
@client_required
def kyc_en_verification():
    return render_template("onboarding/kyc_en_verification.html")


@app.route("/onboarding/profil", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_profil():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if client.questionnaire_score is None:
        return redirect(url_for('onboarding_questionnaire'))

    questionnaire_profil = score_to_profil(client.questionnaire_score)

    if request.method == 'POST':
        profil = request.form.get('profil')
        if profil in ('prudent', 'equilibre', 'dynamique', 'conviction'):
            _log_profil_change(client, profil, 'client')
            client.profil_risque = profil
            client.profil_choisi_par = 'client'
            client.profil_date = datetime.now(timezone.utc)
            db.session.commit()
            return redirect(url_for('onboarding_contrat'))

    return render_template("onboarding/profil.html", current_step=2,
                           profil_actuel=client.profil_risque,
                           questionnaire_profil=questionnaire_profil,
                           questionnaire_score=client.questionnaire_score,
                           prev_url=url_for('onboarding_questionnaire'))


@app.route("/onboarding/questionnaire", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_questionnaire():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))

    # Pre-fill Q1 from NISS pension horizon
    q1_prefill = None
    q1_label = None
    if client.niss:
        bd = birthdate_from_niss(client.niss)
        if bd:
            rem = remaining_to_pension(bd)
            if rem is None:
                q1_prefill, q1_label = '0', 'Moins de 5 ans'
            else:
                total_months = rem[0] * 12 + rem[1]
                if total_months < 60:
                    q1_prefill, q1_label = '0', 'Moins de 5 ans'
                elif total_months < 120:
                    q1_prefill, q1_label = '1', '5 à 10 ans'
                elif total_months < 240:
                    q1_prefill, q1_label = '2', '10 à 20 ans'
                else:
                    q1_prefill, q1_label = '3', 'Plus de 20 ans'

    form = QuestionnaireForm()
    if form.validate_on_submit():
        # If Q1 was locked (pre-filled), use the server-side value to prevent tampering
        q1_val = q1_prefill if q1_prefill is not None else form.q1.data
        score = int(q1_val) + sum(int(getattr(form, f'q{i}').data) for i in range(2, 7))
        profil = score_to_profil(score)
        _log_profil_change(client, profil, 'client', score=score)
        client.profil_risque = profil
        client.profil_choisi_par = 'client'
        client.profil_date = datetime.now(timezone.utc)
        client.questionnaire_score = score
        db.session.commit()
        return redirect(url_for('onboarding_profil'))

    if q1_prefill is not None:
        form.q1.data = q1_prefill

    return render_template("onboarding/questionnaire.html", form=form, current_step=2,
                           q1_prefill=q1_prefill, q1_label=q1_label)


@app.route("/onboarding/contrat/brouillon", methods=["POST"])
@login_required
@client_required
def onboarding_contrat_brouillon():
    data = request.get_json(force=True, silent=True) or {}
    session['contrat_draft'] = {
        'iban':              data.get('iban', ''),
        'beneficiaires_json': data.get('beneficiaires_json', ''),
        'courtier_id':       data.get('courtier_id', ''),
    }
    return ('', 204)


@app.route("/onboarding/contrat", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_contrat():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))

    form = OnboardingContratForm()

    contrat_draft = session.get('contrat_draft', {})

    if form.validate_on_submit():
        client.iban = form.iban.data.strip().replace(' ', '').upper()

        # Parse and store beneficiary JSON
        try:
            benef_data = json.loads(form.beneficiaires_json.data)
        except (json.JSONDecodeError, TypeError):
            benef_data = {}
        client.beneficiaires_json = json.dumps(benef_data, ensure_ascii=False)

        # Derive compat fields beneficiaire_1 / beneficiaire_2
        if benef_data.get('type') != 'personnalise':
            client.beneficiaire_1 = benef_data.get('label', '')
            client.beneficiaire_2 = None
        else:
            benefs = benef_data.get('beneficiaires', [])
            def _fmt(b):
                return f"{b.get('prenom','')} {b.get('nom','')} ({b.get('pourcentage','')}%)".strip()
            client.beneficiaire_1 = _fmt(benefs[0]) if len(benefs) > 0 else None
            client.beneficiaire_2 = _fmt(benefs[1]) if len(benefs) > 1 else None

        cabinet_id = form.courtier_id.data
        if cabinet_id and str(cabinet_id).isdigit():
            cab = CabinetCourtage.query.get(int(cabinet_id))
            if cab:
                client.cabinet_id = cab.id
        db.session.commit()
        session.pop('contrat_draft', None)
        return redirect(url_for('onboarding_frais'))

    if request.method == 'GET':
        form.iban.data = client.iban or contrat_draft.get('iban', '')
        form.beneficiaires_json.data = (
            client.beneficiaires_json
            or contrat_draft.get('beneficiaires_json', '')
        )
        if contrat_draft.get('courtier_id'):
            form.courtier_id.data = contrat_draft['courtier_id']

    courtier_actuel = None
    cab_id = client.cabinet_id or (
        int(contrat_draft['courtier_id'])
        if contrat_draft.get('courtier_id') and str(contrat_draft['courtier_id']).isdigit()
        else None
    )
    if cab_id:
        cab = CabinetCourtage.query.get(cab_id)
        if cab:
            courtier_actuel = {
                'id': cab.id,
                'nom': cab.nom or '',
                'ville': cab.ville or '',
            }

    return render_template("onboarding/contrat.html", form=form,
                           courtier_actuel=courtier_actuel, current_step=3)


@app.route("/onboarding/frais", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_frais():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))
    if not client.iban:
        return redirect(url_for('onboarding_contrat'))

    if request.method == 'POST' and request.form.get('frais_ok') == '1':
        client.frais_acceptes = True
        db.session.commit()
        return redirect(url_for('onboarding_transfert'))

    return render_template("onboarding/frais.html", current_step=4, client=client)


@app.route("/onboarding/transfert", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_transfert():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))
    if not client.iban:
        return redirect(url_for('onboarding_contrat'))
    if not client.frais_acceptes:
        return redirect(url_for('onboarding_frais'))

    dormants = client.contrats_dormants

    if request.method == 'POST':
        # Compute retirement date once — used when creating the B23 contract
        _date_terme_str = None
        if client.niss:
            _bd = birthdate_from_niss(client.niss)
            if _bd:
                _date_terme_str = retirement_date_for(_bd).strftime('%d/%m/%Y')

        # ── Test bypass: skip validation and Connective ────────────────────────
        if request.form.get('skip_test') == '1':
            b23_contrat = next(iter(client.contrats), None)
            ts_count = TransfertSignature.query.count()
            year = datetime.now(timezone.utc).year
            for t in dormants:
                if TransfertSignature.query.filter_by(transfert_id=t.id).first():
                    continue
                ts_count += 1
                ts_ref = f"UTU-{year}-{ts_count:05d}"
                if not b23_contrat:
                    b23_contrat = Contrat(
                        client_id=client.id,
                        pension_insurer_id=1,
                        cabinet_id=client.cabinet_id,
                        numero=ts_ref, type_branche='Branche 23', statut='actif',
                        date_terme=_date_terme_str,
                        beneficiaires_json=client.beneficiaires_json,
                    )
                    db.session.add(b23_contrat)
                    db.session.flush()
                db.session.add(TransfertSignature(
                    reference=ts_ref, transfert_id=t.id,
                    statut_signature='non_initie', contrat_id=b23_contrat.id,
                ))
            db.session.commit()
            return redirect(url_for('onboarding_synthese'))

        selected_ids = [int(i) for i in request.form.getlist('contrat_ids') if i.isdigit()]
        dormants_by_id = {t.id: t for t in dormants}
        selected = [dormants_by_id[i] for i in selected_ids if i in dormants_by_id]

        if not selected:
            flash("Sélectionnez au moins un contrat à transférer.", "error")
            return redirect(url_for('onboarding_transfert'))

        total = sum(_parse_reserve(t.reserve) for t in selected)
        if total < 10_000:
            flash(
                f"Le total des réserves sélectionnées ({total:,.0f} €) est inférieur à 10 000 € "
                "— le transfert ne peut pas être initié.",
                "error"
            )
            return redirect(url_for('onboarding_transfert'))

        b23_contrat = next(iter(client.contrats), None)
        ts_count = TransfertSignature.query.count()
        year = datetime.now(timezone.utc).year
        ts_list = []
        transfers = []
        folder = Path(app.root_path) / 'transfer_pdfs'
        folder.mkdir(parents=True, exist_ok=True)

        for t in selected:
            existing = TransfertSignature.query.filter_by(transfert_id=t.id).first()
            if existing and existing.statut_signature == 'signe':
                continue
            ts = existing
            if not ts:
                ts_count += 1
                ts_ref = f"UTU-{year}-{ts_count:05d}"
                if not b23_contrat:
                    b23_contrat = Contrat(
                        client_id=client.id,
                        pension_insurer_id=1,
                        cabinet_id=client.cabinet_id,
                        numero=ts_ref,
                        type_branche='Branche 23',
                        statut='actif',
                        date_terme=_date_terme_str,
                        beneficiaires_json=client.beneficiaires_json,
                    )
                    db.session.add(b23_contrat)
                    db.session.flush()
                ts = TransfertSignature(
                    reference=ts_ref,
                    transfert_id=t.id,
                    statut_signature='non_initie',
                    contrat_id=b23_contrat.id,
                )
                db.session.add(ts)
                db.session.flush()
            ts_list.append(ts)
            b23_num = ts.contrat.numero if ts.contrat else ts.reference
            pdf_bytes = generer_annexe1(client, t, nouveau_contrat_ref=b23_num)
            pdf_path = folder / f"{ts.reference}.pdf"
            pdf_path.write_bytes(pdf_bytes)
            transfers.append((ts.reference, pdf_path))

        db.session.commit()

        if not transfers:
            return redirect(url_for('onboarding_synthese'))

        use_itsme = request.form.get('use_itsme', '1') == '1'
        affilie = {
            "prenom": client.prenom or "",
            "nom": client.nom or "",
            "email": client.user.email,
            "niss": client.niss or "",
            "langue": "fr",
        }
        try:
            result = initiate_batch_transfer_signature(
                affilie=affilie,
                transfers=transfers,
                use_itsme=use_itsme,
            )
            signing_url = result["signing_url"]
            package_id  = result["package_id"]
            doc_ids     = result["document_ids"]

            for i, ts in enumerate(ts_list):
                ts.connective_package_id  = package_id
                ts.connective_document_id = doc_ids[i] if i < len(doc_ids) else None
                ts.statut_signature       = "en_attente"
                ts.signing_url            = signing_url
            db.session.commit()

            evt = SignatureEvent(
                transfert_id=ts_list[0].id,
                event_type="signature_batch_initiee",
                package_id=package_id,
                details=json.dumps({"email": affilie["email"], "nb_contrats": len(ts_list)}),
            )
            db.session.add(evt)
            db.session.commit()

        except Exception:
            app.logger.exception("Erreur initiation signature batch onboarding")
            flash("Erreur lors de l'initiation de la signature électronique. Veuillez réessayer.", "error")

        return redirect(url_for('onboarding_transfert'))

    # ── GET ────────────────────────────────────────────────────────────────────
    ts_all = [ts for t in dormants for ts in t.signatures]
    phase = 'selection'
    signing_url = None

    if ts_all:
        statuts = [ts.statut_signature for ts in ts_all]
        if all(s == 'signe' for s in statuts):
            phase = 'signed'
        elif any(s == 'en_attente' for s in statuts):
            phase = 'signing'
            ts_with_url = next((ts for ts in ts_all if ts.signing_url), None)
            signing_url = ts_with_url.signing_url if ts_with_url else None

    ts_map = {}
    for t in dormants:
        sigs = sorted(t.signatures, key=lambda x: x.cree_le or datetime.min, reverse=True)
        ts_map[t.id] = sigs[0] if sigs else None

    reserves = {t.id: _parse_reserve(t.reserve) for t in dormants}

    return render_template("onboarding/transfert.html", client=client,
                           dormants=dormants, ts_map=ts_map,
                           phase=phase, signing_url=signing_url,
                           reserves=reserves, current_step=5)


@app.route("/onboarding/transfert/<int:transfert_id>/pdf")
@login_required
@client_required
def onboarding_annexe1_pdf(transfert_id):
    """Generate and serve the Annexe 1 PDF for a given transfer request (inline)."""
    from flask import Response
    client = current_user.client_profile
    transfert = db.session.get(TransfertReserve, transfert_id)
    if not transfert or transfert.client_id != client.id:
        abort(404)
    ts = next((s for s in transfert.signatures), None)
    b23_num = ts.contrat.numero if ts and ts.contrat else None
    pdf_bytes = generer_annexe1(client, transfert, nouveau_contrat_ref=b23_num)
    return Response(
        pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'inline; filename="annexe1_{transfert_id}.pdf"'},
    )


@app.route("/onboarding/synthese")
@login_required
@client_required
def onboarding_synthese():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))
    if not client.iban:
        return redirect(url_for('onboarding_contrat'))
    if not client.frais_acceptes:
        return redirect(url_for('onboarding_frais'))
    dormants = client.contrats_dormants
    if dormants and not any(
        TransfertSignature.query.filter_by(transfert_id=t.id).first() for t in dormants
    ):
        return redirect(url_for('onboarding_transfert'))
    ts_map = {}
    for t in dormants:
        sigs = sorted(t.signatures, key=lambda x: x.cree_le or datetime.min, reverse=True)
        ts_map[t.id] = sigs[0] if sigs else None

    contrats_selectionnes = [t for t in dormants if ts_map.get(t.id)]

    b23_contrat = next(iter(client.contrats), None)
    uptwou_refs = [b23_contrat.numero] if b23_contrat else []

    non_null_ts = [ts for ts in ts_map.values() if ts]
    if dormants and non_null_ts and all(ts.statut_signature == 'signe' for ts in non_null_ts) and len(non_null_ts) == len(dormants):
        batch_state = 'signe'
    elif any(ts.statut_signature == 'en_attente' for ts in non_null_ts):
        batch_state = 'en_attente'
    else:
        batch_state = 'not_started'

    signing_url = None
    if batch_state == 'en_attente':
        ts_with_url = next((ts for ts in non_null_ts if ts.signing_url), None)
        signing_url = ts_with_url.signing_url if ts_with_url else None

    total_reserves = sum(_parse_reserve(c.reserve) for c in contrats_selectionnes)

    return render_template("onboarding/synthese.html", client=client,
                           contrats=contrats_selectionnes, ts_map=ts_map,
                           uptwou_refs=uptwou_refs,
                           batch_state=batch_state,
                           signing_url=signing_url,
                           total_reserves=total_reserves,
                           current_step=6)


@app.route("/client/analyser", methods=["GET", "POST"])
@login_required
@client_required
def client_analyser():
    if request.method == "POST":
        file = request.files.get("extrait")
        if not file or file.filename == "":
            flash("Veuillez sélectionner un fichier PDF.", "error")
            return redirect(url_for("client_analyser"))
        if not allowed_file(file.filename):
            flash("Format invalide — fichier PDF uniquement.", "error")
            return redirect(url_for("client_analyser"))

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            json_brut = parse_mypension_pdf(filepath)
            resultat = json.loads(json_brut)

            if 'contrats' in resultat:
                exclus = [c for c in resultat['contrats'] if is_assureur_exclu(c.get('assureur'))]
                resultat['contrats'] = [c for c in resultat['contrats'] if not is_assureur_exclu(c.get('assureur'))]
                if exclus:
                    resultat.setdefault('details', []).append(
                        f"{len(exclus)} contrat(s) Vitis Life / Onelife exclus (déjà en gestion Branche 23)."
                    )

            niss = (resultat.get('personne') or {}).get('niss')
            if niss:
                bd = birthdate_from_niss(niss)
                if bd:
                    rem = remaining_to_pension(bd)
                    age_legale = 66 if _add_years(bd, 66) < date(2030, 1, 1) else 67
                    if rem is None:
                        resultat['eligible'] = False
                        resultat.setdefault('raisons_refus', []).append(
                            f"Âge légal de la pension ({age_legale} ans) atteint ou dépassé."
                        )
                    else:
                        years, months = rem
                        total_months = years * 12 + months
                        duree = f"{years} an{'s' if years > 1 else ''} et {months} mois"
                        if total_months < 24:
                            resultat['eligible'] = False
                            resultat.setdefault('raisons_refus', []).append(
                                f"Horizon insuffisant : {duree} avant la pension (minimum 2 ans requis)."
                            )
                        else:
                            resultat.setdefault('details', []).append(
                                f"Horizon de placement : {duree} avant l'âge légal ({age_legale} ans)."
                            )

            json_brut = json.dumps(resultat, ensure_ascii=False)
            session['analyse_json'] = json_brut
            session['analyse_filename'] = filename
            return redirect(url_for('client_analyse_resultat'))

        except json.JSONDecodeError:
            flash("L'analyse n'a pas pu être interprétée. Veuillez réessayer.", "error")
        except Exception as e:
            flash(f"Erreur inattendue : {e}", "error")
        return redirect(url_for("client_analyser"))

    return render_template("client/analyser.html", client=current_user.client_profile, form=EmptyForm())


@app.route("/client/analyse-resultat")
@login_required
@client_required
def client_analyse_resultat():
    analyse_json = session.get('analyse_json')
    if not analyse_json:
        flash("Aucune analyse disponible.", "error")
        return redirect(url_for('client_analyser'))
    resultat = json.loads(analyse_json)
    return render_template("client/analyse_resultat.html",
                           client=current_user.client_profile, r=resultat, form=EmptyForm())


@app.route("/client/maj-analyse", methods=["POST"])
@login_required
@client_required
def client_maj_analyse():
    """Update client contrats from a new mypension analysis (re-analysis flow)."""
    analyse_json = session.get('analyse_json')
    if not analyse_json:
        flash("Aucune analyse en cours.", "error")
        return redirect(url_for('client_dashboard'))

    analyse = json.loads(analyse_json)
    client = current_user.client_profile

    # Extract mypension.be reference date from first contract's date_valeur
    date_extrait = None
    for c in analyse.get('contrats', []):
        if c.get('date_valeur'):
            date_extrait = c['date_valeur']
            break

    # Create the Analyse record first to get its id
    new_analyse = Analyse(
        client_id=client.id,
        filename=session.get('analyse_filename', ''),
        resultat_json=analyse_json,
        date_extrait=date_extrait
    )
    db.session.add(new_analyse)
    db.session.flush()  # populate new_analyse.id

    # Add new transfer requests linked to this analysis
    for c in analyse.get('contrats', []):
        db.session.add(TransfertReserve(
            client_id=client.id,
            analyse_id=new_analyse.id,
            assureur=c.get('assureur'),
            numero=c.get('numero'),
            type_branche=c.get('type_branche'),
            statut=c.get('statut', 'dormant'),
            reserve=c.get('reserve'),
            date_valeur=c.get('date_valeur'),
            organisateur=c.get('organisateur'),
            organisateur_bce=c.get('organisateur_bce'),
        ))

    db.session.commit()
    session.pop('analyse_json', None)
    session.pop('analyse_filename', None)
    flash("Votre dossier a été mis à jour avec les données du nouvel extrait.", "success")
    return redirect(url_for('client_transfert'))


# ── Admin / Courtier routes ────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@courtier_required
def admin_dashboard():
    clients = Client.query.all()
    return render_template("admin/dashboard.html", clients=clients)


@app.route("/admin/client/<int:client_id>")
@login_required
@courtier_required
def admin_client_detail(client_id):
    client = Client.query.get_or_404(client_id)
    form = ProfilForm()
    form.profil.data = client.profil_risque
    return render_template("admin/client_detail.html", client=client, form=form)


@app.route("/admin/client/<int:client_id>/profil", methods=["POST"])
@login_required
@courtier_required
def admin_set_profil(client_id):
    client = Client.query.get_or_404(client_id)
    profil = request.form.get('profil')
    if profil in ('prudent', 'equilibre', 'dynamique'):
        _log_profil_change(client, profil, 'courtier')
        client.profil_risque = profil
        client.profil_choisi_par = 'courtier'
        client.profil_date = datetime.now(timezone.utc)
        db.session.commit()
        flash(f"Profil mis à jour pour {client.prenom} {client.nom}.", "success")
    return redirect(url_for('admin_client_detail', client_id=client_id))


@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    form = CourtierRegisterForm()
    if form.validate_on_submit():
        secret = os.environ.get('COURTIER_SECRET', '')
        if not secret or form.code_secret.data != secret:
            flash("Code secret invalide.", "error")
            return render_template("admin/register.html", form=form)

        if User.query.filter_by(email=form.email.data).first():
            flash("Un compte existe déjà avec cet email.", "error")
            return render_template("admin/register.html", form=form)

        user = User(email=form.email.data, role='courtier')
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        db.session.add(Client(
            user_id=user.id,
            nom=form.nom.data,
            prenom=form.prenom.data,
            code_postal=form.code_postal.data or None,
            ville=form.ville.data or None,
            telephone=form.telephone.data or None,
        ))
        db.session.commit()

        login_user(user)
        session.permanent = True
        session['_last_activity'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        return redirect(url_for('admin_dashboard'))

    return render_template("admin/register.html", form=form)


# Jinja2 filter for admin template
app.jinja_env.filters['fromjson'] = json.loads

with app.app_context():
    db.create_all()
    seed_assureurs(app)

# ── Dev helpers (debug only) ───────────────────────────────────────────────────
@app.route("/dev/fake-analyse")
def dev_fake_analyse():
    if not app.debug:
        abort(404)

    fake = {
        "eligible": True,
        "titre": "Oui, un transfert vers la Branche 23 est possible",
        "resume": (
            "Vous disposez de 2 contrats de pension complémentaire dormants "
            "transférables vers la Branche 23. Durée restante avant la pension légale (67 ans) : "
            "environ 21 ans — compatible avec un investissement en Branche 23."
        ),
        "montant_total": "18 450,00 €",
        "nb_contrats": 2,
        "details": [
            "Horizon de placement : ≈ 21 ans avant l'âge légal de la pension (67 ans) — compatible avec un investissement en Branche 23.",
            "Les réserves combinées dépassent le seuil minimum de 10 000 € requis pour le transfert.",
            "Statut salarié confirmé pour les deux contrats."
        ],
        "personne": {
            "prenom": "Tahsin",
            "nom":    "Bilgin",
            "niss":   "80031524705",
            "adresse": "Rue de la Loi 42, 1040 Bruxelles"
        },
        "contrats": [
            {
                "assureur":         "AG Insurance",
                "numero":           "056-1234567-89",
                "type_branche":     "Branche 21",
                "statut":           "dormant",
                "reserve":          12750.00,
                "date_valeur":      "31/12/2024",
                "organisateur":     "Acme SA",
                "organisateur_bce": "0412.345.678"
            },
            {
                "assureur":         "Ethias",
                "numero":           "ETH-9876543",
                "type_branche":     "Branche 21",
                "statut":           "dormant",
                "reserve":          5700.00,
                "date_valeur":      "31/12/2024",
                "organisateur":     "Beta NV",
                "organisateur_bce": "0456.789.012"
            }
        ]
    }

    session['analyse_json'] = json.dumps(fake, ensure_ascii=False)
    session['analyse_filename'] = 'fake_mypension.pdf'
    return redirect(url_for('register'))


if __name__ == "__main__":
    app.run(debug=True)
