from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='client')  # 'client' | 'courtier'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client_profile = db.relationship('Client', foreign_keys='Client.user_id', backref='user', uselist=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = 'client'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nom = db.Column(db.String(100))
    prenom = db.Column(db.String(100))
    date_naissance = db.Column(db.String(10))   # JJ/MM/AAAA
    sexe = db.Column(db.String(1))              # 'F' | 'M'
    niss = db.Column(db.String(20))
    adresse = db.Column(db.String(200))
    ville = db.Column(db.String(100))
    code_postal = db.Column(db.String(10))
    telephone = db.Column(db.String(20))
    iban = db.Column(db.String(34))
    beneficiaire_vie = db.Column(db.String(200))    # en cas de vie
    beneficiaire_1 = db.Column(db.String(200))      # en cas de décès — 1er
    beneficiaire_2 = db.Column(db.String(200))      # en cas de décès — 2ème
    profil_risque = db.Column(db.String(20))    # prudent | equilibre | dynamique | conviction
    profil_choisi_par = db.Column(db.String(20))  # client | courtier
    profil_date = db.Column(db.DateTime)
    pays = db.Column(db.String(100), default='Belgique')
    kyc_verifie = db.Column(db.Boolean, default=False)
    kyc_document = db.Column(db.String(200))      # filename of uploaded ID
    frais_acceptes = db.Column(db.Boolean, default=False)
    questionnaire_score = db.Column(db.Integer)   # raw score 0-18
    courtier_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    courtier = db.relationship('User', foreign_keys='Client.courtier_id', backref='clients_assignes')
    contrats = db.relationship('Contrat', backref='client', lazy=True)
    analyses = db.relationship('Analyse', backref='client', lazy=True)

    @property
    def onboarding_step(self):
        if not self.kyc_verifie:
            return 'kyc'
        if not self.profil_risque:
            return 'profil'
        if not self.frais_acceptes:
            return 'frais'
        return 'complet'

    @property
    def latest_analyse(self):
        if not self.analyses:
            return None
        return max(self.analyses, key=lambda a: a.created_at)

    @property
    def montant_total(self):
        latest = self.latest_analyse
        if latest:
            try:
                data = json.loads(latest.resultat_json)
                return data.get('montant_total')
            except Exception:
                pass
        return None

    @property
    def contrats_dormants(self):
        """Dormant contracts from the most recent analysis only."""
        latest = self.latest_analyse
        if latest:
            return [c for c in latest.contrats if c.statut == 'dormant']
        # Fallback for legacy records without analyse_id
        return [c for c in self.contrats if c.statut == 'dormant' and c.analyse_id is None]

    @property
    def contrats_actifs(self):
        """Active (non-dormant) contracts from the most recent analysis only."""
        latest = self.latest_analyse
        if latest:
            return [c for c in latest.contrats if c.statut == 'actif']
        return [c for c in self.contrats if c.statut == 'actif' and c.analyse_id is None]


class Contrat(db.Model):
    __tablename__ = 'contrat'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    analyse_id = db.Column(db.Integer, db.ForeignKey('analyse.id'), nullable=True)
    assureur = db.Column(db.String(150))
    numero = db.Column(db.String(100))
    type_branche = db.Column(db.String(30))     # Branche 21 | Branche 23 | Inconnu
    statut = db.Column(db.String(20), default='inconnu')  # dormant | actif | inconnu
    reserve = db.Column(db.String(30))
    date_valeur = db.Column(db.String(20))
    organisateur = db.Column(db.String(200))    # "Organisé par" in section 2.2
    organisateur_bce = db.Column(db.String(30)) # BCE number of the organiser
    date_terme = db.Column(db.String(10))        # JJ/MM/AAAA — contract maturity date
    date_transfert = db.Column(db.String(10))    # JJ/MM/AAAA — date de réception des fonds cédants
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def assureur_ref(self):
        """Lookup the reference entry for this insurer (best-effort name match)."""
        if not self.assureur:
            return None
        nom = self.assureur.lower()
        return AssureurRef.query.filter(
            db.func.lower(AssureurRef.nom_court).contains(nom[:10])
        ).first()


class Analyse(db.Model):
    __tablename__ = 'analyse'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    filename = db.Column(db.String(200))
    resultat_json = db.Column(db.Text)
    date_extrait = db.Column(db.String(10))   # "01/01/YYYY" — reference date on mypension.be statement
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    contrats = db.relationship('Contrat', backref='analyse', lazy=True)


class AssureurRef(db.Model):
    """
    Reference table for Belgian pension insurers.
    Used to pre-fill Annexe 1 transfer request forms.
    BCE numbers are official identifiers required on the form.
    """
    __tablename__ = 'assureur_ref'
    id = db.Column(db.Integer, primary_key=True)
    nom_court = db.Column(db.String(100), unique=True, nullable=False)  # name used in matching
    nom_legal = db.Column(db.String(200))                               # full legal name for the form
    numero_bce = db.Column(db.String(20))                               # e.g. "0404.494.849"
    adresse = db.Column(db.String(200))
    code_postal = db.Column(db.String(10))
    ville = db.Column(db.String(100))
    email_transfert = db.Column(db.String(150))   # contact address for sending Annexe 1
    iban = db.Column(db.String(34))               # receiving account for the reserve transfer

    def __repr__(self):
        return f'<AssureurRef {self.nom_court}>'


# ── Seed data ──────────────────────────────────────────────────────────────────
ASSUREURS_SEED = [
    dict(nom_court='AG Insurance',      nom_legal='AG Insurance SA',
         numero_bce='0404.494.849',     adresse='Boulevard Émile Jacqmain 53',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='AXA',               nom_legal='AXA Belgium SA',
         numero_bce='0404.483.367',     adresse='Place du Trône 1',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='Belfius Insurance', nom_legal='Belfius Insurance SA',
         numero_bce='0405.764.064',     adresse='Galerie de la Toison d\'Or 29',
         code_postal='1050',            ville='Bruxelles'),
    dict(nom_court='Ethias',            nom_legal='Ethias SA',
         numero_bce='0404.484.654',     adresse='Rue des Croisiers 24',
         code_postal='4000',            ville='Liège'),
    dict(nom_court='P&V Assurances',    nom_legal='P&V Assurances SCRL',
         numero_bce='0402.236.531',     adresse='Rue Royale 151',
         code_postal='1210',            ville='Bruxelles'),
    dict(nom_court='KBC Insurance',     nom_legal='KBC Assurances SA',
         numero_bce='0403.552.563',     adresse='Professor Roger Van Overstraetenplein 2',
         code_postal='3000',            ville='Leuven'),
    dict(nom_court='NN Insurance',      nom_legal='NN Insurance Belgium SA',
         numero_bce='0890.270.057',     adresse='Avenue Fonsny 38',
         code_postal='1060',            ville='Bruxelles'),
    dict(nom_court='Allianz',           nom_legal='Allianz Benelux SA',
         numero_bce='0403.258.197',     adresse='Boulevard du Roi Albert II 32',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='Federale Assurance', nom_legal='Federale Assurance SC',
         numero_bce='0407.039.583',     adresse='Rue de l\'Étuve 12',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='Vivium',            nom_legal='Vivium SA',
         numero_bce='0448.020.024',     adresse='Rue Royale 151',
         code_postal='1210',            ville='Bruxelles'),
    dict(nom_court='Integrale',         nom_legal='Integrale SA',
         numero_bce='0400.098.660',     adresse='Avenue du Douaire 40',
         code_postal='1348',            ville='Louvain-la-Neuve'),
    dict(nom_court='Argenta',           nom_legal='Argenta Assurances SA',
         numero_bce='0452.191.422',     adresse='Belgiëlei 49–53',
         code_postal='2018',            ville='Antwerpen'),
    dict(nom_court='Athora',            nom_legal='Athora Belgium SA',
         numero_bce='0405.764.008',     adresse='Avenue Louise 331',
         code_postal='1050',            ville='Bruxelles'),
    dict(nom_court='Generali',          nom_legal='Generali Belgium SA',
         numero_bce='0401.848.680',     adresse='Avenue Louise 149',
         code_postal='1050',            ville='Bruxelles'),
    dict(nom_court='OCA',               nom_legal='OCA (Organisme pour le Financement de Pensions)',
         numero_bce='0421.387.497',     adresse='Rue Montoyer 24',
         code_postal='1000',            ville='Bruxelles'),
]


class ContactMessage(db.Model):
    __tablename__ = 'contact_message'
    id         = db.Column(db.Integer, primary_key=True)
    nom        = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), nullable=False)
    sujet      = db.Column(db.String(100))
    message    = db.Column(db.Text, nullable=False)
    lu         = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def seed_assureurs(app):
    """Insert reference insurers if the table is empty. Call once after db.create_all()."""
    with app.app_context():
        if AssureurRef.query.count() == 0:
            for data in ASSUREURS_SEED:
                db.session.add(AssureurRef(**data))
            db.session.commit()
