from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional, Regexp


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
    profil = SelectField('Profil d\'investissement', choices=[
        ('prudent', 'Prudent'),
        ('equilibre', 'Équilibré'),
        ('dynamique', 'Dynamique'),
        ('conviction', 'Conviction'),
    ], validators=[DataRequired()])
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
    telephone = StringField('Téléphone', validators=[Optional()])
    iban = StringField('IBAN', validators=[Optional()])
    beneficiaire_1 = StringField('Bénéficiaire 1 (en cas de décès)', validators=[Optional()])
    beneficiaire_2 = StringField('Bénéficiaire 2 (en cas de décès)', validators=[Optional()])
    submit = SubmitField('Enregistrer les modifications')


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
    password = PasswordField('Mot de passe', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Confirmer', validators=[DataRequired(), EqualTo('password')])
    code_secret = StringField('Code secret', validators=[DataRequired()])
    submit = SubmitField('Créer le compte courtier')
