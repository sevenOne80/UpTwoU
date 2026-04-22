import os
import re
import json
import pdfplumber
import anthropic
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'uptowu-dev-secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ANALYSIS_PROMPT = """Tu es un expert en pension complémentaire belge (2e pilier), spécialisé dans l'optimisation des réserves en Branche 21 vers Branche 23.

Voici l'extrait de pension complémentaire d'un assuré belge, téléchargé depuis mypension.be :

<extrait_pension>
{texte_pdf}
</extrait_pension>

Analyse cet extrait et réponds UNIQUEMENT au format JSON suivant, sans aucun texte avant ou après :

{{
  "eligible": true ou false,
  "titre": "Oui, un transfert vers la Branche 23 est possible" ou "Non, un transfert vers la Branche 23 n'est pas possible",
  "resume": "Phrase d'accroche synthétique en 1-2 phrases.",
  "details": [
    "Point clé 1 (réserves trouvées, montants, contrats...)",
    "Point clé 2 (conditions d'éligibilité remplies ou non...)",
    "Point clé 3 (avantages potentiels ou raisons du refus...)"
  ],
  "raisons_refus": ["Raison 1", "Raison 2"] ,
  "montant_total": "XX XXX,XX €" ou null si non trouvé,
  "nb_contrats": nombre entier ou null
}}

Règles d'éligibilité pour un transfert Branche 21 → Branche 23 :
- L'assuré doit avoir des réserves acquises en Branche 21 (assurance vie à taux garanti)
- Les contrats doivent être des contrats de pension complémentaire du 2e pilier (EIP, PLCI, pension sectorielle, assurance de groupe)
- L'assuré doit être encore actif (pas encore à la retraite)
- Si ces conditions sont remplies, eligible = true
- Si l'extrait est illisible, vide, ou ne correspond pas à un extrait mypension.be, eligible = false avec explication claire"""


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_pdf_text(filepath):
    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def analyse_avec_claude(texte_pdf):
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
    message = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{
            "role": "user",
            "content": ANALYSIS_PROMPT.format(texte_pdf=texte_pdf[:12000])
        }]
    )
    # Extract text from response (skip thinking blocks)
    for block in message.content:
        if block.type == "text":
            raw = block.text.strip()
            # Strip markdown code fences if present
            raw = re.sub(r'^```json\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return raw
    return None


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
                flash("Le PDF semble vide ou illisible. Veuillez réessayer.", "error")
                return redirect(url_for("analyser"))

            json_brut = analyse_avec_claude(texte)
            resultat = json.loads(json_brut)
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


if __name__ == "__main__":
    app.run(debug=True)
