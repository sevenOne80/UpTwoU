import os
import re
import json
import pdfplumber
import anthropic
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from models import db, User, Client, Contrat, Analyse, AssureurRef, seed_assureurs
from forms import LoginForm, RegisterForm, DonneesForm, ProfilForm, CourtierRegisterForm

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
Types B21 : "Branche 21", "Tak 21", "Branch 21", "Zweig 21", "taux garanti", "gegarandeerde rente", "guaranteed rate", "Garantiezins"
Types B23 : "Branche 23", "Tak 23", "Branch 23", "Zweig 23", "fonds d'investissement", "beleggingsfonds", "investment fund", "Investmentfonds"
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
La section 2.1 ou 2.2 peut apparaître APRÈS les sections 3, 3.1, 3.2, etc.
Tu dois parcourir l'intégralité du document avant de conclure qu'une section est absente.
Ne déclare eligible = "a_verifier" QUE si, après avoir lu tout le document, aucun détail par plan n'est trouvé.

Si la section 2.1 est présente (quel que soit son emplacement dans le PDF) → statut salarié confirmé, plans identifiables.
Si seule la section 2 apparaît et qu'aucun détail par plan n'est trouvé nulle part dans le document → eligible = "a_verifier".

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

**Condition 3 — Réserves en Branche 21**
Le contrat doit être en Branche 21 (taux garanti). Les contrats en Branche 23 uniquement ne sont pas transférables.

**Condition 4 — Montant minimum**
Le total des réserves transférables doit être **strictement supérieur à 10 000 €**.
Si le total est ≤ 10 000 €, eligible = false avec mention du montant insuffisant.

**Arbre de décision :**

1. Le document est illisible ou clairement hors-sujet (pas un extrait mypension.be) → eligible = false

2. Le document contient uniquement des réserves d'indépendant (EIP/PLCI confirmés, aucune mention salarié/assurance de groupe) → eligible = false

3. Le document contient des réserves de salarié (ou statut ambigu) ET le détail par plan EST présent (section 2.2) :
   - Plan dormant confirmé + B21 + > 10 000 € → eligible = true
   - Plan actif confirmé → eligible = false
   - Plan dormant + B23 uniquement → eligible = false
   - Plan dormant + mix B21/B23 → eligible = true si part B21 > 10 000 €

4. Le document contient des réserves > 10 000 € MAIS le détail par plan est absent (pas de section 2.2, pas de mention explicite dormant/actif) :
   → eligible = "a_verifier"
   → Expliquer dans details que l'extrait ne contient pas la section 2.2 et inviter à télécharger l'extrait complet depuis mypension.be (vue détaillée par plan)

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

    # Pré-remplir le formulaire avec les données extraites du PDF
    if request.method == 'GET':
        form.prenom.data = personne.get('prenom') or ''
        form.nom.data = personne.get('nom') or ''

    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash("Un compte existe déjà avec cette adresse e-mail.", "error")
            return render_template("register.html", form=form, analyse=analyse)

        user = User(email=form.email.data, role='client')
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()

        client_profile = Client(
            user_id=user.id,
            nom=form.nom.data,
            prenom=form.prenom.data,
            niss=personne.get('niss') or '',
            adresse=personne.get('adresse') or '',
            profil_risque=form.profil.data,
            profil_choisi_par='client',
            profil_date=datetime.utcnow()
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
        session.pop('analyse_json', None)
        session.pop('analyse_filename', None)

        login_user(user)
        flash("Bienvenue sur votre espace UpTwoU !", "success")
        return redirect(url_for('client_dashboard'))

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
