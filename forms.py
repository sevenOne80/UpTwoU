from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, SubmitField, RadioField, HiddenField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, Regexp, ValidationError


class EmptyForm(FlaskForm):
    """Token CSRF uniquement, sans champ."""
    pass


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Mot de passe', validators=[DataRequired()])
    submit = SubmitField('Se connecter')


class RegisterForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired()])
    prenom = StringField('Prénom', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Mot de passe', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Confirmer le mot de passe', validators=[
        DataRequired(), EqualTo('password', message='Les mots de passe ne correspondent pas.')
    ])
    submit = SubmitField('Créer mon compte')


class DonneesForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired()])
    prenom = StringField('Prénom', validators=[DataRequired()])
    date_naissance = StringField('Date de naissance', validators=[
        Optional(),
        Regexp(r'^\d{2}/\d{2}/\d{4}$', message='Format attendu : JJ/MM/AAAA')
    ])
    sexe = SelectField('Sexe', choices=[('', '—'), ('F', 'Femme'), ('M', 'Homme')],
                       validators=[Optional()])
    adresse = StringField('Adresse', validators=[Optional()])
    ville = StringField('Ville', validators=[Optional()])
    code_postal = StringField('Code postal', validators=[Optional()])
    pays = SelectField('Pays', validators=[Optional()], choices=[
        ('Belgique', 'Belgique'),
        ('France', 'France'),
        ('Luxembourg', 'Luxembourg'),
        ('Pays-Bas', 'Pays-Bas'),
        ('Allemagne', 'Allemagne'),
        ('Suisse', 'Suisse'),
        ('Autriche', 'Autriche'),
        ('Espagne', 'Espagne'),
        ('Italie', 'Italie'),
        ('Portugal', 'Portugal'),
        ('Royaume-Uni', 'Royaume-Uni'),
        ('Irlande', 'Irlande'),
        ('Danemark', 'Danemark'),
        ('Suède', 'Suède'),
        ('Norvège', 'Norvège'),
        ('Finlande', 'Finlande'),
        ('Pologne', 'Pologne'),
        ('République tchèque', 'République tchèque'),
        ('Hongrie', 'Hongrie'),
        ('Roumanie', 'Roumanie'),
        ('Bulgarie', 'Bulgarie'),
        ('Grèce', 'Grèce'),
        ('Croatie', 'Croatie'),
        ('Autre', 'Autre'),
    ], default='Belgique')
    pays_libre = StringField('Précisez le pays', validators=[Optional()])
    telephone = StringField('Téléphone fixe', validators=[Optional()])
    telephone_gsm = StringField('GSM', validators=[Optional()])
    iban = StringField('IBAN', validators=[Optional()])
    beneficiaire_1 = StringField('Bénéficiaire 1 (en cas de décès)', validators=[Optional()])
    beneficiaire_2 = StringField('Bénéficiaire 2 (en cas de décès)', validators=[Optional()])
    submit = SubmitField('Enregistrer les modifications')

    def validate_iban(self, field):
        if not field.data:
            return
        iban = field.data.strip().replace(' ', '').upper()
        if len(iban) < 15 or len(iban) > 34:
            raise ValidationError('IBAN invalide — longueur incorrecte.')
        if not iban[:2].isalpha() or not iban[2:4].isdigit():
            raise ValidationError('IBAN invalide — format incorrect (ex. BE68 5390 0754 7034).')
        rearranged = iban[4:] + iban[:4]
        numeric = ''
        for c in rearranged:
            if c.isdigit():
                numeric += c
            elif c.isalpha():
                numeric += str(ord(c) - ord('A') + 10)
            else:
                raise ValidationError('IBAN invalide — caractères non autorisés.')
        if int(numeric) % 97 != 1:
            raise ValidationError('IBAN invalide — numéro de contrôle incorrect.')

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        if not self.telephone.data and not self.telephone_gsm.data:
            self.telephone.errors.append('Indiquez au moins un numéro (fixe ou GSM).')
            ok = False
        return ok


class ProfilForm(FlaskForm):
    profil = SelectField('Profil', choices=[
        ('prudent', 'Prudent'),
        ('equilibre', 'Équilibré'),
        ('dynamique', 'Dynamique'),
        ('conviction', 'Conviction'),
    ], validators=[DataRequired()])
    submit = SubmitField('Enregistrer')


class CourtierRegisterForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired()])
    prenom = StringField('Prénom', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    code_postal = StringField('Code postal', validators=[])
    ville = StringField('Ville', validators=[])
    telephone = StringField('Téléphone', validators=[])
    password = PasswordField('Mot de passe', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Confirmer', validators=[DataRequired(), EqualTo('password')])
    code_secret = StringField('Code secret', validators=[DataRequired()])
    submit = SubmitField('Créer le compte courtier')


class OnboardingKYCForm(FlaskForm):
    nom = StringField('Nom', validators=[DataRequired()])
    prenom = StringField('Prénom', validators=[DataRequired()])
    date_naissance = StringField('Date de naissance (JJ/MM/AAAA)', validators=[
        DataRequired(), Regexp(r'^\d{2}/\d{2}/\d{4}$', message='Format JJ/MM/AAAA requis')
    ])
    sexe = SelectField('Sexe', choices=[('F', 'Femme'), ('M', 'Homme'), ('X', 'Je ne souhaite pas partager')], validators=[DataRequired()])
    niss = StringField('Numéro national', validators=[DataRequired()])
    adresse = StringField('Adresse', validators=[DataRequired()])
    code_postal = StringField('Code postal', validators=[DataRequired()])
    ville = StringField('Ville', validators=[DataRequired()])
    pays = StringField('Pays', validators=[DataRequired()], default='Belgique')
    est_ppe = RadioField(
        'PPE',
        choices=[('non', 'Non'), ('oui', 'Oui')],
        validators=[DataRequired(message='Veuillez répondre à la question PPE.')],
    )
    ppe_details = StringField('Fonction ou lien avec la PPE', validators=[Optional()])
    submit = SubmitField('Confirmer mes données')


class OnboardingContratForm(FlaskForm):
    iban = StringField('Numéro de compte (IBAN)', validators=[DataRequired(message='L\'IBAN est requis.')])
    beneficiaires_json = HiddenField('Bénéficiaires', validators=[DataRequired(message='Veuillez définir au moins un bénéficiaire.')])
    courtier_id = HiddenField('Courtier sélectionné', validators=[Optional()])
    submit = SubmitField('Continuer')

    def validate_iban(self, field):
        if not field.data:
            return
        iban = field.data.strip().replace(' ', '').upper()
        if len(iban) < 15 or len(iban) > 34:
            raise ValidationError('IBAN invalide — longueur incorrecte.')
        if not iban[:2].isalpha() or not iban[2:4].isdigit():
            raise ValidationError('IBAN invalide — format incorrect (ex. BE68 5390 0754 7034).')
        rearranged = iban[4:] + iban[:4]
        numeric = ''
        for c in rearranged:
            if c.isdigit():
                numeric += c
            elif c.isalpha():
                numeric += str(ord(c) - ord('A') + 10)
            else:
                raise ValidationError('IBAN invalide — caractères non autorisés.')
        if int(numeric) % 97 != 1:
            raise ValidationError('IBAN invalide — numéro de contrôle incorrect.')


class QuestionnaireForm(FlaskForm):
    q1 = RadioField('Horizon de placement', choices=[
        ('0', 'Moins de 5 ans'), ('1', '5 à 10 ans'), ('2', '10 à 20 ans'), ('3', 'Plus de 20 ans')
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    q2 = RadioField('Réaction si votre portefeuille perd 20% en 3 mois', choices=[
        ('0', 'Vendre pour limiter les pertes'), ('1', 'Envisager de réduire mon exposition'),
        ('2', 'Attendre sereinement le redressement'), ('3', "Profiter de l'occasion pour renforcer")
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    q3 = RadioField('Votre objectif principal', choices=[
        ('0', 'Préserver mon capital avant tout'), ('1', 'Rendement régulier avec peu de volatilité'),
        ('2', 'Faire croître mon patrimoine long terme'), ('3', 'Maximiser le rendement, forte volatilité acceptée')
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    q4 = RadioField('Ces réserves représentent quelle part de votre patrimoine total ?', choices=[
        ('0', 'Plus de 75%'), ('1', '50% à 75%'), ('2', '25% à 50%'), ('3', 'Moins de 25%')
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    q5 = RadioField('Expérience en placements financiers', choices=[
        ('0', 'Aucune, je découvre'), ('1', 'Limitée (livrets, obligations)'),
        ('2', 'Modérée (fonds, assurance-vie)'), ('3', 'Avancée (actions, ETF, gestion active)')
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    q6 = RadioField('Face à une perte de 30% sur 6 mois, vous...', choices=[
        ('0', "Sortez : c'est insupportable"), ('1', 'Réduisez prudemment votre exposition'),
        ('2', 'Gardez le cap sans stress'), ('3', 'Renforcez votre position')
    ], validators=[DataRequired(message='Répondez à toutes les questions')])
    submit = SubmitField('Valider')
