from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='client')  # 'client' | 'courtier'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client_profile = db.relationship('Client', backref='user', uselist=False)

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
    niss = db.Column(db.String(20))
    adresse = db.Column(db.String(200))
    ville = db.Column(db.String(100))
    code_postal = db.Column(db.String(10))
    telephone = db.Column(db.String(20))
    iban = db.Column(db.String(34))
    beneficiaire_1 = db.Column(db.String(200))
    beneficiaire_2 = db.Column(db.String(200))
    profil_risque = db.Column(db.String(20))   # prudent | equilibre | dynamique
    profil_choisi_par = db.Column(db.String(20))  # client | courtier
    profil_date = db.Column(db.DateTime)
    contrats = db.relationship('Contrat', backref='client', lazy=True)
    analyses = db.relationship('Analyse', backref='client', lazy=True)

    @property
    def montant_total(self):
        """Sum label from latest analysis JSON."""
        if self.analyses:
            import json
            try:
                data = json.loads(self.analyses[-1].resultat_json)
                return data.get('montant_total')
            except Exception:
                pass
        return None


class Contrat(db.Model):
    __tablename__ = 'contrat'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    assureur = db.Column(db.String(100))
    numero = db.Column(db.String(50))
    type_branche = db.Column(db.String(30))
    reserve = db.Column(db.String(30))
    date_valeur = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Analyse(db.Model):
    __tablename__ = 'analyse'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    filename = db.Column(db.String(200))
    resultat_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
