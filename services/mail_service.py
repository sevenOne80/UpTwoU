"""
UpTwoU — Service d'envoi d'emails
Centralise la logique smtplib / Brevo utilisée par plusieurs blueprints.
"""
import logging
import os
import smtplib
from email import encoders as email_encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


def _smtp_send(msg: MIMEMultipart, recipients: list[str]):
    """Envoie le message via SMTP Brevo. No-op si MAIL_SERVER absent (dev)."""
    mail_server = os.getenv('MAIL_SERVER')
    mail_from = msg['From']
    if not mail_server:
        logger.info("DEV — email non envoyé (MAIL_SERVER absent) : %s → %s",
                    msg['Subject'], recipients)
        return
    try:
        with smtplib.SMTP(mail_server, int(os.getenv('MAIL_PORT', 587))) as server:
            server.starttls()
            username = os.getenv('MAIL_USERNAME')
            password = os.getenv('MAIL_PASSWORD')
            if username and password:
                server.login(username, password)
            server.sendmail(mail_from, recipients, msg.as_bytes())
        logger.info("Email envoyé : %s → %s", msg['Subject'], recipients)
    except Exception:
        logger.exception("Échec envoi email : %s", msg['Subject'])


def send_signed_transfer_email(
    *,
    client_email: str,
    client_nom: str,
    insurer_email: str,
    insurer_nom: str,
    reference: str,
    signed_pdf_path,
):
    """
    Envoie l'Annexe 1 signée à l'assureur cédant (To) avec CC au client.
    Appelé depuis le webhook handler après PackageFinished.
    """
    mail_from = os.getenv('MAIL_FROM', 'noreply@uptwou.be')

    subject = f"Demande de transfert de réserves — Réf. {reference}"

    body_html = f"""\
<p>Madame, Monsieur,</p>
<p>Veuillez trouver ci-joint la demande de transfert de réserves de pension
complémentaire signée électroniquement par notre affilié(e)
<strong>{client_nom}</strong>.</p>
<p><strong>Référence du dossier UpTwoU :</strong> {reference}</p>
<p>Conformément à la Convention Assuralia du 22 septembre 2015, nous vous remercions
de procéder au transfert dans un délai de 30 jours calendrier à compter de la
réception du présent formulaire.</p>
<p>Pour toute question, contactez-nous à
<a href="mailto:operations@uptwou.be">operations@uptwou.be</a>.</p>
<p>Cordialement,<br>L'équipe UpTwoU</p>
"""

    msg = MIMEMultipart()
    msg['From'] = mail_from
    msg['To'] = insurer_email
    msg['Cc'] = client_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    pdf_path = Path(signed_pdf_path) if signed_pdf_path else None
    if pdf_path and pdf_path.exists():
        with pdf_path.open('rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
        email_encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment',
                        filename=f"Annexe1_{reference}_signe.pdf")
        msg.attach(part)
    else:
        logger.warning("PDF signé introuvable pour réf. %s — envoi sans pièce jointe", reference)

    _smtp_send(msg, [insurer_email, client_email])
