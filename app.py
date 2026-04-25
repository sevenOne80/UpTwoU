import os
import re
import json
import base64
import pdfplumber
import anthropic
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, Client, Contrat, Analyse, AssureurRef, seed_assureurs
from forms import LoginForm, RegisterForm, DonneesForm, ProfilForm, CourtierRegisterForm, OnboardingKYCForm, QuestionnaireForm

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
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.config['SQLALCHEMY_DATABASE_URI'] = (
    'sqlite:///' + os.path.join(os.path.dirname(__file__), 'uptwou.db')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page.'
login_manager.login_message_category = 'error'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


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

ALLOWED_KYC_EXT = {'jpg', 'jpeg', 'png', 'pdf'}

KYC_PROMPT = """Analyse cette pièce d'identité et extrais les données au format JSON uniquement, sans texte avant ni après :
{
  "nom": "NOM EN MAJUSCULES",
  "prenom": "Prénom(s)",
  "date_naissance": "JJ/MM/AAAA",
  "niss": "XX.XX.XX-XXX.XX ou null",
  "adresse": "rue et numéro ou null",
  "code_postal": "code postal ou null",
  "ville": "localité ou null",
  "pays": "pays ou Belgique",
  "sexe": "F ou M ou null"
}
Pour le NISS belge (numéro national) : il apparaît au verso de la carte d'identité belge au format XX.XX.XX-XXX.XX. Si absent, utilise null."""

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
                return json.loads(raw)
    except Exception:
        pass
    return {}


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
  "resume": "Phrase synthétique en 1-2 phrases sur la situation de l'assuré.",
  "details": [
    "Point clé 1 — réserves trouvées, montants, assureurs",
    "Point clé 2 — conditions confirmées ou à vérifier",
    "Point clé 3 — prochaine étape recommandée"
  ],
  "raisons_refus": ["Raison détaillée — uniquement si eligible = false"],
  "montant_total": "XX XXX,XX €" ou null,
  "couverture_deces": "XX XXX,XX €" ou null,
  "nb_contrats": nombre entier ou null,
  "contrats": [
    {{
      "assureur": "Nom exact de l'assureur tel qu'il apparaît dans le document",
      "numero": "numéro de contrat ou police, vide si absent",
      "type_branche": "Branche 21" ou "Branche 23" ou "Inconnu",
      "reserve": "XX XXX,XX" ou null,
      "date_valeur": "JJ/MM/AAAA" ou null,
      "statut": "dormant" ou "actif" ou "inconnu"
    }}
  ],
  "personne": {{
    "prenom": "prénom extrait de la section 0 ou null",
    "nom": "nom extrait de la section 0 ou null",
    "niss": "numéro NISS format XX.XX.XX-XXX.XX ou null",
    "adresse": "adresse complète ou null"
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

**Condition 3 — Montant minimum**
Le total des réserves transférables doit être **strictement supérieur à 10 000 €**.
Si le total est ≤ 10 000 €, eligible = false avec mention du montant insuffisant.

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

5. Réserves présentes mais ≤ 10 000 € au total → eligible = false avec mention du montant

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
            texte = extract_pdf_text(filepath)
            if not texte.strip():
                flash("Le PDF semble vide ou illisible.", "error")
                return redirect(url_for("analyser"))

            json_brut = analyse_avec_claude(texte)
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
        except anthropic.APIError as e:
            flash(f"Erreur lors de l'analyse IA : {e}", "error")
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

        for c in analyse.get('contrats', []):
            db.session.add(Contrat(
                client_id=client_profile.id,
                assureur=c.get('assureur'),
                numero=c.get('numero'),
                type_branche=c.get('type_branche'),
                statut=c.get('statut', 'inconnu'),
                reserve=c.get('reserve'),
                date_valeur=c.get('date_valeur')
            ))

        db.session.add(Analyse(
            client_id=client_profile.id,
            filename=session.get('analyse_filename', ''),
            resultat_json=analyse_json
        ))

        db.session.commit()
        login_user(user)
        flash("Compte créé. Complétez votre dossier KYC.", "success")
        return redirect(url_for('onboarding_kyc'))

    return render_template("register.html", form=form, analyse=analyse)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        dest = 'admin_dashboard' if current_user.role == 'courtier' else 'client_dashboard'
        return redirect(url_for(dest))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            if not next_page:
                next_page = url_for('admin_dashboard' if user.role == 'courtier' else 'client_dashboard')
            return redirect(next_page)
        flash("Email ou mot de passe incorrect.", "error")

    return render_template("login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


# ── Client routes ──────────────────────────────────────────────────────────────
@app.route("/client/dashboard")
@login_required
@client_required
def client_dashboard():
    return render_template("client/dashboard.html", client=current_user.client_profile)


@app.route("/client/contrats")
@login_required
@client_required
def client_contrats():
    return render_template("client/contrats.html", client=current_user.client_profile)


@app.route("/client/profil", methods=["GET", "POST"])
@login_required
@client_required
def client_profil():
    client = current_user.client_profile
    form = ProfilForm()
    if request.method == 'GET':
        form.profil.data = client.profil_risque

    if form.validate_on_submit():
        client.profil_risque = form.profil.data
        client.profil_choisi_par = 'client'
        client.profil_date = datetime.utcnow()
        db.session.commit()
        flash("Profil d'investissement mis à jour.", "success")
        return redirect(url_for('client_profil'))

    return render_template("client/profil.html", client=client, form=form)


@app.route("/client/donnees", methods=["GET", "POST"])
@login_required
@client_required
def client_donnees():
    client = current_user.client_profile
    form = DonneesForm(obj=client)

    if form.validate_on_submit():
        form.populate_obj(client)
        db.session.commit()
        flash("Vos données ont été mises à jour.", "success")
        return redirect(url_for('client_donnees'))

    return render_template("client/donnees.html", client=client, form=form)


# ── Transfert routes ──────────────────────────────────────────────────────────
@app.route("/client/transfert")
@login_required
@client_required
def client_transfert():
    client = current_user.client_profile
    return render_template("client/transfert.html", client=client,
                           contrats=client.contrats_dormants)


@app.route("/client/transfert/<int:contrat_id>/pdf")
@login_required
@client_required
def client_transfert_pdf(contrat_id):
    from io import BytesIO
    from pdf_utils import generer_annexe1
    contrat = Contrat.query.get_or_404(contrat_id)
    if contrat.client_id != current_user.client_profile.id:
        flash("Accès non autorisé.", "error")
        return redirect(url_for('client_transfert'))
    pdf_bytes = generer_annexe1(current_user.client_profile, contrat)
    slug = re.sub(r'[^a-z0-9]+', '_', (contrat.assureur or 'assureur').lower())[:30]
    return send_file(BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True,
                     download_name=f"annexe1_transfert_{slug}.pdf")


# ── Onboarding routes ─────────────────────────────────────────────────────────

def _onboarding_guard(required_step):
    """Decorator factory: redirect if client hasn't reached required onboarding step."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            client = current_user.client_profile
            step_order = ['kyc', 'profil', 'frais', 'complet']
            current = client.onboarding_step
            required_idx = step_order.index(required_step)
            current_idx = step_order.index(current) if current in step_order else 0
            if current_idx < required_idx:
                targets = {'kyc': 'onboarding_kyc', 'profil': 'onboarding_profil',
                           'frais': 'onboarding_frais'}
                return redirect(url_for(targets.get(current, 'onboarding_kyc')))
            return f(*args, **kwargs)
        return wrapper
    return decorator


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
                    extracted = analyse_piece_identite(fpath)
                    session['kyc_data'] = extracted
                    kyc_data = extracted
                    flash("Document analysé. Vérifiez et complétez vos données.", "success")
                else:
                    flash("Format non supporté (JPG, PNG ou PDF uniquement).", "error")
            return redirect(url_for('onboarding_kyc'))

        # ── Phase 2 : form confirmation ───────────────────────────────────
        if action == 'confirm' and form.validate_on_submit():
            client.nom = form.nom.data
            client.prenom = form.prenom.data
            client.date_naissance = form.date_naissance.data
            client.sexe = form.sexe.data
            client.niss = form.niss.data
            client.adresse = form.adresse.data
            client.code_postal = form.code_postal.data
            client.ville = form.ville.data
            client.pays = form.pays.data
            client.kyc_verifie = True
            db.session.commit()
            session.pop('kyc_data', None)
            return redirect(url_for('onboarding_profil'))

    # Pre-fill form from AI extraction or existing client data
    if request.method == 'GET':
        form.nom.data          = kyc_data.get('nom') or client.nom or ''
        form.prenom.data       = kyc_data.get('prenom') or client.prenom or ''
        form.date_naissance.data = kyc_data.get('date_naissance') or client.date_naissance or ''
        form.sexe.data         = kyc_data.get('sexe') or client.sexe or ''
        form.niss.data         = kyc_data.get('niss') or client.niss or ''
        form.adresse.data      = kyc_data.get('adresse') or client.adresse or ''
        form.code_postal.data  = kyc_data.get('code_postal') or client.code_postal or ''
        form.ville.data        = kyc_data.get('ville') or client.ville or ''
        form.pays.data         = kyc_data.get('pays') or client.pays or 'Belgique'

    has_document = bool(client.kyc_document)
    return render_template("onboarding/kyc.html", form=form,
                           kyc_data=kyc_data, has_document=has_document,
                           current_step=1)


@app.route("/onboarding/profil", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_profil():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))

    if request.method == 'POST':
        profil = request.form.get('profil')
        if profil in ('prudent', 'equilibre', 'dynamique', 'conviction'):
            client.profil_risque = profil
            client.profil_choisi_par = 'client'
            client.profil_date = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('onboarding_frais'))

    return render_template("onboarding/profil.html", current_step=2,
                           profil_actuel=client.profil_risque)


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
        client.profil_risque = profil
        client.profil_choisi_par = 'client'
        client.profil_date = datetime.utcnow()
        client.questionnaire_score = score
        db.session.commit()
        return redirect(url_for('onboarding_frais'))

    if q1_prefill is not None:
        form.q1.data = q1_prefill

    return render_template("onboarding/questionnaire.html", form=form, current_step=2,
                           q1_prefill=q1_prefill, q1_label=q1_label)


@app.route("/onboarding/frais", methods=["GET", "POST"])
@login_required
@client_required
def onboarding_frais():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))

    if request.method == 'POST' and request.form.get('frais_ok') == '1':
        client.frais_acceptes = True
        db.session.commit()
        return redirect(url_for('onboarding_synthese'))

    return render_template("onboarding/frais.html", current_step=3, client=client)


@app.route("/onboarding/synthese")
@login_required
@client_required
def onboarding_synthese():
    client = current_user.client_profile
    if not client.kyc_verifie:
        return redirect(url_for('onboarding_kyc'))
    if not client.profil_risque:
        return redirect(url_for('onboarding_profil'))
    if not client.frais_acceptes:
        return redirect(url_for('onboarding_frais'))
    dormants = client.contrats_dormants
    return render_template("onboarding/synthese.html", client=client,
                           contrats=dormants, current_step=4)


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

    # Replace contrats with new analysis
    for c in client.contrats:
        db.session.delete(c)
    db.session.flush()

    for c in analyse.get('contrats', []):
        db.session.add(Contrat(
            client_id=client.id,
            assureur=c.get('assureur'),
            numero=c.get('numero'),
            type_branche=c.get('type_branche'),
            statut=c.get('statut', 'inconnu'),
            reserve=c.get('reserve'),
            date_valeur=c.get('date_valeur')
        ))

    db.session.add(Analyse(
        client_id=client.id,
        filename=session.get('analyse_filename', ''),
        resultat_json=analyse_json
    ))
    db.session.commit()
    session.pop('analyse_json', None)
    session.pop('analyse_filename', None)
    flash("Votre dossier a été mis à jour.", "success")
    return redirect(url_for('onboarding_synthese'))


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
        client.profil_risque = profil
        client.profil_choisi_par = 'courtier'
        client.profil_date = datetime.utcnow()
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
            prenom=form.prenom.data
        ))
        db.session.commit()

        login_user(user)
        return redirect(url_for('admin_dashboard'))

    return render_template("admin/register.html", form=form)


# Jinja2 filter for admin template
app.jinja_env.filters['fromjson'] = json.loads

with app.app_context():
    db.create_all()
    seed_assureurs(app)

if __name__ == "__main__":
    app.run(debug=True)
