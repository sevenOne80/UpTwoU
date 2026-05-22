from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

db = SQLAlchemy()


class PensionInsurer(db.Model):
    """B23 product carriers used by UpTwoU (OneLife SA, Vitis Life SA, …)."""
    __tablename__ = 'pension_insurer'
    id           = db.Column(db.Integer, primary_key=True)
    nom_legal    = db.Column(db.String(200), nullable=False)
    nom_court    = db.Column(db.String(100), unique=True)
    numero_rcs   = db.Column(db.String(30))   # RCS Luxembourg or BCE Belgium
    numero_bce   = db.Column(db.String(12))
    numero_fsma  = db.Column(db.String(30))
    adresse      = db.Column(db.String(200))
    code_postal  = db.Column(db.String(10))
    ville        = db.Column(db.String(100))
    pays         = db.Column(db.String(50), default='Luxembourg')
    email        = db.Column(db.String(150))
    telephone    = db.Column(db.String(30))
    iban         = db.Column(db.String(34))
    actif        = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<PensionInsurer {self.nom_legal}>'


PENSION_INSURERS_SEED = [
    dict(
        nom_legal='OneLife SA',
        nom_court='OneLife',
        numero_rcs='B 54390',
        adresse='Rue de la Liberté 1',
        code_postal='L-8399',
        ville='Windhof',
        pays='Luxembourg',
    ),
    dict(
        nom_legal='Vitis Life SA',
        nom_court='Vitis Life',
        numero_rcs='B 84360',
        adresse='Rue de la Liberté 1',
        code_postal='L-8399',
        ville='Windhof',
        pays='Luxembourg',
    ),
]


def seed_pension_insurers(app):
    """Insert UpTwoU pension insurers if the table is empty."""
    with app.app_context():
        if PensionInsurer.query.count() == 0:
            for data in PENSION_INSURERS_SEED:
                db.session.add(PensionInsurer(**data))
            db.session.commit()



class BrokerCabinet(db.Model):
    __tablename__ = 'broker_cabinet'
    id           = db.Column(db.Integer, primary_key=True)
    nom          = db.Column(db.String(200), nullable=False)
    bce          = db.Column(db.String(30))
    fsma         = db.Column(db.String(30))
    adresse      = db.Column(db.String(200))
    code_postal  = db.Column(db.String(10))
    ville        = db.Column(db.String(100))
    telephone    = db.Column(db.String(20))
    email        = db.Column(db.String(150))
    site_web     = db.Column(db.String(200))
    actif        = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    courtiers    = db.relationship('User', back_populates='cabinet', lazy=True)

    def __repr__(self):
        return f'<BrokerCabinet {self.nom}>'


# Keep old name as alias
CabinetCourtage = BrokerCabinet


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='client')  # 'client' | 'courtier'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    email_confirmed = db.Column(db.Boolean, default=False)
    email_token = db.Column(db.String(64))
    cabinet_id = db.Column(db.Integer, db.ForeignKey('broker_cabinet.id'), nullable=True)
    cabinet = db.relationship('BrokerCabinet', back_populates='courtiers')
    client_profile = db.relationship('Client', foreign_keys='Client.user_id', backref='user', uselist=False)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_locked(self):
        if self.locked_until and datetime.utcnow() < self.locked_until:
            return True
        return False

    def record_failed_login(self):
        self.failed_login_attempts = (self.failed_login_attempts or 0) + 1
        if self.failed_login_attempts >= 5:
            from datetime import timedelta
            self.locked_until = datetime.utcnow() + timedelta(minutes=15)

    def reset_login_attempts(self):
        self.failed_login_attempts = 0
        self.locked_until = None


class Client(db.Model):
    __tablename__ = 'client'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nom = db.Column(db.String(100))
    prenom = db.Column(db.String(100))
    date_naissance = db.Column(db.String(10))   # DD/MM/YYYY
    sexe = db.Column(db.String(1))              # 'F' | 'M'
    niss = db.Column(db.String(20))
    adresse = db.Column(db.String(200))
    ville = db.Column(db.String(100))
    code_postal = db.Column(db.String(10))
    telephone = db.Column(db.String(20))
    telephone_gsm = db.Column(db.String(20))
    iban = db.Column(db.String(34))
    beneficiaire_vie = db.Column(db.String(200))
    beneficiaire_1 = db.Column(db.String(200))
    beneficiaire_2 = db.Column(db.String(200))
    beneficiaires_json = db.Column(db.Text)
    profil_risque = db.Column(db.String(20))    # prudent | equilibre | dynamique | conviction
    profil_choisi_par = db.Column(db.String(20))
    profil_date = db.Column(db.DateTime)
    pays = db.Column(db.String(100), default='Belgique')
    kyc_verifie = db.Column(db.Boolean, default=False)
    kyc_document = db.Column(db.String(200))
    est_ppe = db.Column(db.Boolean, nullable=True)
    ppe_details = db.Column(db.String(500), nullable=True)
    ci_date_validite = db.Column(db.String(10), nullable=True)   # DD/MM/YYYY expiry of eID card
    frais_acceptes = db.Column(db.Boolean, default=False)
    questionnaire_score = db.Column(db.Integer)
    courtier_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    courtier = db.relationship('User', foreign_keys='Client.courtier_id', backref='clients_assignes')
    cabinet_id = db.Column(db.Integer, db.ForeignKey('broker_cabinet.id'), nullable=True)
    cabinet = db.relationship('BrokerCabinet', foreign_keys='Client.cabinet_id')
    contrats = db.relationship('Contract', backref='client', lazy=True)
    analyses = db.relationship('Analysis', backref='client', lazy=True)
    # transfer_requests: backref from TransferRequest.client

    @property
    def onboarding_step(self):
        if not self.kyc_verifie:
            return 'kyc'
        if not self.profil_risque:
            return 'profil'
        if not self.iban:
            return 'contrat'
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
        """Dormant transfer requests from the most recent analysis."""
        latest = self.latest_analyse
        if latest:
            return [t for t in self.transfer_requests
                    if t.analyse_id == latest.id and t.statut == 'dormant']
        return [t for t in self.transfer_requests if t.statut == 'dormant']

    @property
    def contrats_actifs(self):
        """All UpTwoU B23 contracts for this client."""
        return list(self.contrats)


class Contract(db.Model):
    """UpTwoU B23 contract — issued by UpTwoU, not a source/dormant contract."""
    __tablename__ = 'contract'
    id               = db.Column(db.Integer, primary_key=True)
    client_id        = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    pension_insurer_id  = db.Column(db.Integer, db.ForeignKey('pension_insurer.id'), nullable=True)
    pension_insurer     = db.relationship('PensionInsurer', foreign_keys='Contract.pension_insurer_id')
    cabinet_id       = db.Column(db.Integer, db.ForeignKey('broker_cabinet.id'), nullable=True)
    cabinet          = db.relationship('BrokerCabinet', foreign_keys='Contract.cabinet_id')
    numero           = db.Column(db.String(100))
    type_branche     = db.Column(db.String(30), default='Branche 23')
    statut           = db.Column(db.String(20), default='actif')  # actif | liquide
    date_terme       = db.Column(db.String(10))
    beneficiaires_json = db.Column(db.Text)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    reserves_recues  = db.relationship('ReceivedReserve', back_populates='contract', lazy=True)

    @property
    def total_reserve(self):
        """Sum of all received reserves in euros."""
        total = sum(rr.montant or 0.0 for rr in self.reserves_recues)
        return f"{total:,.2f}".replace(',', ' ').replace('.', ',') if total else None

    @property
    def derniere_reception(self):
        """Date of the most recent received reserve."""
        dates = [rr.date_reception for rr in self.reserves_recues if rr.date_reception]
        return max(dates) if dates else None


# Keep Contrat as an alias so existing code using the old name still works during transition
Contrat = Contract


class TransferRequest(db.Model):
    """Dormant pension contract from a previous employer — source of a reserve transfer."""
    __tablename__ = 'transfer_request'
    id               = db.Column(db.Integer, primary_key=True)
    client_id        = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    analyse_id       = db.Column(db.Integer, db.ForeignKey('analysis.id'), nullable=True)
    assureur         = db.Column(db.String(150))
    numero           = db.Column(db.String(100))
    type_branche     = db.Column(db.String(30))
    reserve          = db.Column(db.String(30))
    date_valeur      = db.Column(db.String(20))
    organisateur     = db.Column(db.String(200))
    organisateur_bce = db.Column(db.String(30))
    date_terme       = db.Column(db.String(10))
    statut           = db.Column(db.String(20), default='dormant')  # dormant | en_cours | recu
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    client     = db.relationship('Client', backref=db.backref('transfer_requests', lazy=True))
    analyse    = db.relationship('Analysis', backref=db.backref('transfer_requests', lazy=True))
    signatures = db.relationship('TransferSignature', back_populates='transfer_request', lazy=True)

    @property
    def assureur_ref(self):
        if not self.assureur:
            return None
        nom = self.assureur.lower()
        return InsurerRef.query.filter(
            db.func.lower(InsurerRef.nom_court).contains(nom[:10])
        ).first()


# Keep TransfertReserve as alias for existing code
TransfertReserve = TransferRequest


class AccountScan(db.Model):
    """Log of each automatic bank account scan (PSD2)."""
    __tablename__ = 'account_scan'
    id           = db.Column(db.Integer, primary_key=True)
    started_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at  = db.Column(db.DateTime, nullable=True)
    statut       = db.Column(db.String(20), default='en_cours')  # en_cours | succes | erreur
    nb_trouvees  = db.Column(db.Integer, default=0)   # total transactions crédit dans la période
    nb_nouvelles = db.Column(db.Integer, default=0)   # non déjà présentes en DB
    nb_matchees  = db.Column(db.Integer, default=0)   # matchées à un TransferRequest
    iban_compte  = db.Column(db.String(34))
    date_debut   = db.Column(db.Date)
    date_fin     = db.Column(db.Date)
    erreur       = db.Column(db.Text, nullable=True)

    reserves = db.relationship('ReceivedReserve', back_populates='scan', lazy=True)


class ReceivedReserve(db.Model):
    """Reserves actually received into an UpTwoU contract from a transfer."""
    __tablename__ = 'received_reserve'
    id                       = db.Column(db.Integer, primary_key=True)
    # contract_id nullable : les entrées auto-scannées peuvent arriver sans contrat assigné
    contract_id              = db.Column(db.Integer, db.ForeignKey('contract.id'), nullable=True)
    transfer_id              = db.Column(db.Integer, db.ForeignKey('transfer_request.id'), nullable=True)
    scan_id                  = db.Column(db.Integer, db.ForeignKey('account_scan.id'), nullable=True)
    montant                  = db.Column(db.Float)
    currency                 = db.Column(db.String(3), nullable=False, default='EUR')
    date_reception           = db.Column(db.String(10))          # DD/MM/YYYY (saisie manuelle)
    date_valeur_banque       = db.Column(db.Date, nullable=True) # date valeur PSD2
    communication            = db.Column(db.String(500))
    communication_structuree = db.Column(db.String(60))
    iban_source              = db.Column(db.String(34))
    nom_emetteur             = db.Column(db.String(200))
    ref_transaction          = db.Column(db.String(100), unique=True, nullable=True)
    source                   = db.Column(db.String(20), default='manuel')  # auto | manuel
    note                     = db.Column(db.String(500))
    created_at               = db.Column(db.DateTime, default=datetime.utcnow)

    contract  = db.relationship('Contract', back_populates='reserves_recues')
    transfert = db.relationship('TransferRequest', backref=db.backref('reserve_recue', uselist=False))
    scan      = db.relationship('AccountScan', back_populates='reserves')


class Analysis(db.Model):
    __tablename__ = 'analysis'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    filename = db.Column(db.String(200))
    resultat_json = db.Column(db.Text)
    date_extrait = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # transfer_requests: backref from TransferRequest.analyse


# Keep Analyse as alias
Analyse = Analysis


class InsurerRef(db.Model):
    """
    Reference table for Belgian pension insurers.
    Used to pre-fill Annexe 1 transfer request forms.
    BCE numbers are official identifiers required on the form.
    """
    __tablename__ = 'insurer_ref'
    id = db.Column(db.Integer, primary_key=True)
    nom_court = db.Column(db.String(100), unique=True, nullable=False)
    nom_legal = db.Column(db.String(200))
    numero_bce = db.Column(db.String(20))
    adresse = db.Column(db.String(200))
    code_postal = db.Column(db.String(10))
    ville = db.Column(db.String(100))
    email_transfert = db.Column(db.String(150))
    iban = db.Column(db.String(34))

    def __repr__(self):
        return f'<InsurerRef {self.nom_court}>'


# ── Seed data ──────────────────────────────────────────────────────────────────
ASSUREURS_SEED = [
    dict(nom_court='AG Insurance',      nom_legal='AG Insurance SA',
         numero_bce='0404.494.849',     adresse='Boulevard Emile Jacqmain 53',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='AXA',               nom_legal='AXA Belgium SA',
         numero_bce='0404.483.367',     adresse='Place du Trone 1',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='Belfius Insurance', nom_legal='Belfius Insurance SA',
         numero_bce='0405.764.064',     adresse='Galerie de la Toison d\'Or 29',
         code_postal='1050',            ville='Bruxelles'),
    dict(nom_court='Ethias',            nom_legal='Ethias SA',
         numero_bce='0404.484.654',     adresse='Rue des Croisiers 24',
         code_postal='4000',            ville='Liege'),
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
         numero_bce='0407.039.583',     adresse='Rue de l\'Etuve 12',
         code_postal='1000',            ville='Bruxelles'),
    dict(nom_court='Vivium',            nom_legal='Vivium SA',
         numero_bce='0448.020.024',     adresse='Rue Royale 151',
         code_postal='1210',            ville='Bruxelles'),
    dict(nom_court='Integrale',         nom_legal='Integrale SA',
         numero_bce='0400.098.660',     adresse='Avenue du Douaire 40',
         code_postal='1348',            ville='Louvain-la-Neuve'),
    dict(nom_court='Argenta',           nom_legal='Argenta Assurances SA',
         numero_bce='0452.191.422',     adresse='Belgiëlei 49-53',
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


class ProfileChangeLog(db.Model):
    __tablename__ = 'profile_change_log'
    id                   = db.Column(db.Integer, primary_key=True)
    client_id            = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    profil_ancien        = db.Column(db.String(20))
    profil_nouveau       = db.Column(db.String(20), nullable=False)
    choisi_par           = db.Column(db.String(20))
    score_questionnaire  = db.Column(db.Integer)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    envoye_wealtheon     = db.Column(db.Boolean, default=False)
    date_envoi_wealtheon = db.Column(db.DateTime)
    envoye_assureur      = db.Column(db.Boolean, default=False)
    date_envoi_assureur  = db.Column(db.DateTime)

    client = db.relationship('Client', backref=db.backref('profil_logs', lazy=True,
                                                           order_by='ProfileChangeLog.created_at'))

    def __repr__(self):
        return f'<ProfileChangeLog client={self.client_id} {self.profil_ancien}->{self.profil_nouveau}>'


# Keep old name as alias
ProfilChangeLog = ProfileChangeLog


class NavHistory(db.Model):
    """Monthly NAV (Net Asset Value) per risk profile per fund manager."""
    __tablename__ = 'nav_history'
    id            = db.Column(db.Integer, primary_key=True)
    gestionnaire  = db.Column(db.String(100), nullable=False)
    profil        = db.Column(db.String(20),  nullable=False)
    date          = db.Column(db.String(7),   nullable=False)   # YYYY-MM
    vni           = db.Column(db.Float,       nullable=False)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('gestionnaire', 'profil', 'date', name='uq_nav_gestionnaire_profil_date'),
    )

    def __repr__(self):
        return f'<NavHistory {self.gestionnaire} {self.profil} {self.date} {self.vni}>'


class ContactMessage(db.Model):
    __tablename__ = 'contact_message'
    id         = db.Column(db.Integer, primary_key=True)
    client_id  = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    nom        = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(150), nullable=False)
    sujet      = db.Column(db.String(100))
    message    = db.Column(db.Text, nullable=False)
    lu         = db.Column(db.Boolean, default=False)
    reponse    = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship('Client', backref=db.backref('contact_messages', lazy=True))


class TransferSignature(db.Model):
    """Electronic signature tracking for one reserve transfer request."""
    __tablename__ = 'transfer_signature'

    id                     = db.Column(db.Integer, primary_key=True)
    reference              = db.Column(db.String(32), unique=True, nullable=False)

    # Source dormant contract being transferred
    transfert_id           = db.Column(db.Integer, db.ForeignKey('transfer_request.id'), nullable=True)

    # UpTwoU destination contract — DB column kept as 'nouveau_contrat_id' for migration compat
    contrat_id             = db.Column('nouveau_contrat_id', db.Integer, db.ForeignKey('contract.id'), nullable=True)

    connective_package_id  = db.Column(db.String(100))
    connective_document_id = db.Column(db.String(100))
    statut_signature       = db.Column(db.String(20), default='non_initie')
    # non_initie | en_attente | signe | expire | revoque

    chemin_pdf_signe       = db.Column(db.String(500))
    chemin_audit_trail     = db.Column(db.String(500))
    signing_url            = db.Column(db.String(500))

    cree_le                = db.Column(db.DateTime, default=datetime.utcnow)
    mis_a_jour_le          = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    transfer_request  = db.relationship('TransferRequest', back_populates='signatures')
    contrat           = db.relationship('Contract', foreign_keys='TransferSignature.contrat_id',
                                        backref=db.backref('transfert_signatures', lazy=True))
    evenements        = db.relationship('SignatureEvent', back_populates='transfert_sig',
                                        cascade='all, delete-orphan')

    @property
    def transfert(self):
        """Alias for transfer_request — used in older code."""
        return self.transfer_request

    @property
    def est_signe(self):
        return self.statut_signature == 'signe'

    @property
    def dossier_complet(self):
        return bool(self.chemin_pdf_signe and self.chemin_audit_trail)

    def __repr__(self):
        return f'<TransferSignature {self.reference} [{self.statut_signature}]>'


# Keep old name as alias
TransfertSignature = TransferSignature


class SignatureEvent(db.Model):
    """Audit log for Connective signature lifecycle events."""
    __tablename__ = 'signature_event'

    id           = db.Column(db.Integer, primary_key=True)
    transfert_id = db.Column(db.Integer, db.ForeignKey('transfer_signature.id'), nullable=False)
    event_type   = db.Column(db.String(50), nullable=False)
    package_id   = db.Column(db.String(100))
    details      = db.Column(db.Text)
    cree_le      = db.Column(db.DateTime, default=datetime.utcnow)

    transfert_sig = db.relationship('TransferSignature', back_populates='evenements')

    def __repr__(self):
        return f'<SignatureEvent {self.event_type} [{self.package_id}]>'


class OutgoingTransfer(db.Model):
    """Reserve transfer initiated by the client FROM an UpTwoU contract TO another insurer."""
    __tablename__ = 'outgoing_transfer'

    id              = db.Column(db.Integer, primary_key=True)
    client_id       = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    contract_id     = db.Column(db.Integer, db.ForeignKey('contract.id'), nullable=True)
    assureur_dest   = db.Column(db.String(150))
    numero_dest     = db.Column(db.String(100))    # policy number at destination insurer
    montant         = db.Column(db.String(30))
    motif           = db.Column(db.String(500))
    statut          = db.Column(db.String(20), default='en_attente')
    # en_attente | en_cours | transmis | refuse | annule
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client   = db.relationship('Client', backref=db.backref('outgoing_transfers', lazy=True))
    contract = db.relationship('Contract', backref=db.backref('outgoing_transfers', lazy=True))

    def __repr__(self):
        return f'<OutgoingTransfer {self.id} {self.statut}>'


def seed_assureurs(app):
    """Insert reference insurers if the table is empty."""
    with app.app_context():
        if InsurerRef.query.count() == 0:
            for data in ASSUREURS_SEED:
                db.session.add(InsurerRef(**data))
            db.session.commit()
