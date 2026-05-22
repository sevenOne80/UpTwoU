"""
UpTwoU — Routes Flask : flux de signature Connective

Endpoints :
  POST /signature/initier            — Lance la signature d'un contrat dormant
  GET  /signature/statut/<reference> — Interroge le statut (polling de secours)
  POST /signature/webhook            — Reçoit les notifications Connective
  GET  /signature/telecharger/<ref>  — Télécharge le PDF signé (courtier)
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, abort, current_app, send_file
from flask_login import login_required, current_user

from models import db, Contrat, TransfertReserve, TransfertSignature, SignatureEvent
from pdf_utils import generer_annexe1, NOUVEL_NOM, NOUVEL_BCE, NOUVEL_IBAN
from services.connective_service import (
    initiate_transfer_signature,
    initiate_batch_transfer_signature,
    get_package_status,
    download_signed_documents,
    download_signed_document_by_id,
)
from services.mail_service import send_signed_transfer_email

logger = logging.getLogger(__name__)

signature_bp = Blueprint("signature", __name__, url_prefix="/signature")

WEBHOOK_SECRET = os.getenv("CONNECTIVE_WEBHOOK_SECRET", "")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _verify_webhook_signature(req) -> bool:
    if not WEBHOOK_SECRET:
        logger.warning("CONNECTIVE_WEBHOOK_SECRET non configuré — vérification ignorée")
        return True
    sig_header = req.headers.get("X-Connective-Signature", "")
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        req.get_data(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", sig_header)


def _generate_reference() -> str:
    year = datetime.utcnow().year
    count = TransfertSignature.query.count() + 1
    return f"UTU-{year}-{count:05d}"


def _save_draft_pdf(reference: str, pdf_bytes: bytes) -> Path:
    folder = Path(current_app.config.get(
        'TRANSFER_PDFS_FOLDER',
        os.path.join(current_app.root_path, 'transfer_pdfs'),
    ))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{reference}.pdf"
    path.write_bytes(pdf_bytes)
    return path


def _traiter_signature_complete(transfert_sig: TransfertSignature):
    try:
        if transfert_sig.connective_document_id:
            docs = download_signed_document_by_id(
                package_id=transfert_sig.connective_package_id,
                document_id=transfert_sig.connective_document_id,
                transfert_id=transfert_sig.reference,
            )
        else:
            docs = download_signed_documents(
                package_id=transfert_sig.connective_package_id,
                transfert_id=transfert_sig.reference,
            )
        transfert_sig.statut_signature = "signe"
        transfert_sig.chemin_pdf_signe = str(docs["signed_pdf_path"])
        transfert_sig.chemin_audit_trail = str(docs.get("audit_trail_path") or "")
        db.session.commit()

        evt = SignatureEvent(
            transfert_id=transfert_sig.id,
            event_type="signature_complete",
            package_id=transfert_sig.connective_package_id,
            details=json.dumps({
                "pdf_signe": str(docs["signed_pdf_path"]),
                "audit_trail": str(docs.get("audit_trail_path")),
            }),
        )
        db.session.add(evt)
        db.session.commit()
        logger.info("Signature complète — transfert %s", transfert_sig.reference)

        # Envoi email à l'assureur cédant + CC client
        try:
            transfert = transfert_sig.transfert   # TransfertReserve (source dormant)
            client    = transfert.client if transfert else None
            ref_ass   = transfert.assureur_ref if transfert else None
            if client and ref_ass and ref_ass.email_transfert:
                send_signed_transfer_email(
                    client_email=client.user.email,
                    client_nom=f"{client.prenom or ''} {client.nom or ''}".strip(),
                    insurer_email=ref_ass.email_transfert,
                    insurer_nom=ref_ass.nom_legal or (transfert.assureur if transfert else '') or '',
                    reference=transfert_sig.reference,
                    signed_pdf_path=docs["signed_pdf_path"],
                )
            else:
                logger.warning(
                    "Pas d'email_transfert pour %s — envoi ignoré (réf. %s)",
                    transfert.assureur if transfert else '?', transfert_sig.reference,
                )
        except Exception:
            logger.exception("Erreur envoi email post-signature (réf. %s)", transfert_sig.reference)

    except Exception:
        logger.exception("Erreur traitement signature complète (package %s)", transfert_sig.connective_package_id)


# ── Route 1 : Initier la signature ────────────────────────────────────────────

@signature_bp.route("/initier", methods=["POST"])
@login_required
def initier_signature():
    """
    Lance le processus de signature pour un contrat dormant.

    Body JSON : { "contrat_id": 42, "use_itsme": true }
    Response  : { "ok": true, "reference": "UTU-2026-00001", "package_id": "...", "signing_url": "..." }
    """
    data = request.get_json(force=True)
    transfert_id = data.get("transfert_id") or data.get("contrat_id")
    use_itsme = data.get("use_itsme", True)

    if not transfert_id:
        abort(400, description="transfert_id est requis")

    transfert = db.session.get(TransfertReserve, transfert_id)
    if not transfert:
        abort(404, description=f"TransfertReserve {transfert_id} introuvable")

    client = transfert.client
    if current_user.role == 'client' and client.user_id != current_user.id:
        abort(403)

    ts = TransfertSignature.query.filter_by(transfert_id=transfert_id).first()
    if not ts:
        ts = TransfertSignature(
            reference=_generate_reference(),
            transfert_id=transfert_id,
            statut_signature='non_initie',
        )
        db.session.add(ts)
        db.session.flush()

    if not ts.contrat_id:
        b23_contrat = next(iter(client.contrats), None)
        if not b23_contrat:
            b23_contrat = Contrat(
                client_id=client.id,
                numero=ts.reference,
                type_branche='Branche 23',
                statut='actif',
            )
            db.session.add(b23_contrat)
            db.session.flush()
        ts.contrat_id = b23_contrat.id
        db.session.flush()

    b23_num = ts.contrat.numero if ts.contrat else ts.reference
    pdf_bytes = generer_annexe1(client, transfert, nouveau_contrat_ref=b23_num)
    pdf_path = _save_draft_pdf(ts.reference, pdf_bytes)

    affilie = {
        "prenom": client.prenom or "",
        "nom": client.nom or "",
        "email": client.user.email,
        "niss": client.niss or "",
        "langue": "fr",
    }

    result = initiate_transfer_signature(
        affilie=affilie,
        pdf_path=pdf_path,
        transfert_id=ts.reference,
        use_itsme=use_itsme,
    )

    ts.connective_package_id = result["package_id"]
    ts.connective_document_id = result["document_id"]
    ts.statut_signature = "en_attente"
    db.session.commit()

    evt = SignatureEvent(
        transfert_id=ts.id,
        event_type="signature_initiee",
        package_id=result["package_id"],
        details=json.dumps({"use_itsme": use_itsme, "email": affilie["email"]}),
    )
    db.session.add(evt)
    db.session.commit()

    logger.info("Signature initiée — transfert %s — email : %s", ts.reference, affilie["email"])
    return jsonify({
        "ok": True,
        "reference": ts.reference,
        "package_id": result["package_id"],
        "signing_url": result.get("signing_url", ""),
    })


# ── Route 1b : Initier la signature de TOUS les contrats dormants ─────────────

@signature_bp.route("/initier-tout", methods=["POST"])
@login_required
def initier_signature_tout():
    """
    Lance la signature de tous les contrats dormants en un seul package Connective.
    Body JSON : { "use_itsme": true }
    Response  : { "ok": true, "signing_url": "...", "package_id": "..." }
    """
    data = request.get_json(force=True)
    use_itsme = data.get("use_itsme", True)

    client = current_user.client_profile
    dormants = client.contrats_dormants

    if not dormants:
        return jsonify({"ok": False, "description": "Aucun contrat dormant"}), 400

    ts_list = []
    transfers = []

    from pdf_utils import NOUVEL_NOM
    b23_contrat = next(iter(client.contrats), None)

    for transfert in dormants:
        ts = TransfertSignature.query.filter_by(transfert_id=transfert.id).first()
        if not ts:
            ref = _generate_reference()
            if not b23_contrat:
                b23_contrat = Contrat(
                    client_id=client.id,
                    numero=ref,
                    type_branche='Branche 23',
                    statut='actif',
                )
                db.session.add(b23_contrat)
                db.session.flush()
            ts = TransfertSignature(
                reference=ref,
                transfert_id=transfert.id,
                statut_signature='non_initie',
                contrat_id=b23_contrat.id,
            )
            db.session.add(ts)
            db.session.flush()
        elif not ts.contrat_id:
            if not b23_contrat:
                b23_contrat = Contrat(
                    client_id=client.id,
                    numero=ts.reference,
                    type_branche='Branche 23',
                    statut='actif',
                )
                db.session.add(b23_contrat)
                db.session.flush()
            ts.contrat_id = b23_contrat.id
            db.session.flush()

        b23_num = ts.contrat.numero if ts.contrat else ts.reference
        pdf_bytes = generer_annexe1(client, transfert, nouveau_contrat_ref=b23_num)
        pdf_path = _save_draft_pdf(ts.reference, pdf_bytes)

        ts_list.append(ts)
        transfers.append((ts.reference, pdf_path))

    affilie = {
        "prenom": client.prenom or "",
        "nom": client.nom or "",
        "email": client.user.email,
        "niss": client.niss or "",
        "langue": "fr",
    }

    result = initiate_batch_transfer_signature(
        affilie=affilie,
        transfers=transfers,
        use_itsme=use_itsme,
    )

    for i, ts in enumerate(ts_list):
        ts.connective_package_id = result["package_id"]
        ts.connective_document_id = result["document_ids"][i]
        ts.statut_signature = "en_attente"

    db.session.commit()

    evt = SignatureEvent(
        transfert_id=ts_list[0].id,
        event_type="signature_batch_initiee",
        package_id=result["package_id"],
        details=json.dumps({
            "use_itsme": use_itsme,
            "email": affilie["email"],
            "nb_contrats": len(ts_list),
        }),
    )
    db.session.add(evt)
    db.session.commit()

    logger.info("Signature batch initiée — %d contrats — email : %s",
                len(ts_list), affilie["email"])

    return jsonify({
        "ok": True,
        "signing_url": result["signing_url"],
        "package_id": result["package_id"],
    })


# ── Route 2 : Statut (polling de secours) ─────────────────────────────────────

@signature_bp.route("/statut/<reference>", methods=["GET"])
@login_required
def statut_signature(reference):
    """Interroge le statut d'un package Connective (fallback si webhook absent)."""
    ts = TransfertSignature.query.filter_by(reference=reference).first_or_404()
    if not ts.connective_package_id:
        abort(409, description="Aucun package Connective associé à ce transfert")

    status = get_package_status(ts.connective_package_id)
    return jsonify({
        "reference": reference,
        "package_id": ts.connective_package_id,
        "statut_local": ts.statut_signature,
        "statut_connective": status["status"],
        "signers": status["signers"],
    })


# ── Route 3 : Webhook Connective ──────────────────────────────────────────────

@signature_bp.route("/webhook", methods=["POST"])
def webhook_connective():
    """
    Reçoit les notifications de Connective (pas d'auth requise, protégé par HMAC).
    Connective attend HTTP 200 dans les 5 secondes.
    """
    if not _verify_webhook_signature(request):
        logger.warning("Webhook Connective rejeté : signature invalide")
        abort(401)

    payload = request.get_json(force=True, silent=True) or {}
    event_type = payload.get("type", "")
    package_id = payload.get("packageId") or payload.get("id", "")

    logger.info("Webhook Connective reçu : type=%s package=%s", event_type, package_id)

    if not package_id:
        return jsonify({"ok": True, "ignored": True})

    ts_records = TransfertSignature.query.filter_by(connective_package_id=package_id).all()
    if not ts_records:
        logger.warning("Package Connective %s non trouvé en base", package_id)
        return jsonify({"ok": True, "warning": "package_non_trouve"})

    if event_type in ("PackageStatusChanged", "PackageFinished"):
        status = payload.get("status", "")
        if status == "Finished":
            for ts in ts_records:
                _traiter_signature_complete(ts)
        elif status == "Expired":
            for ts in ts_records:
                ts.statut_signature = "expire"
            db.session.commit()
        elif status == "Revoked":
            for ts in ts_records:
                ts.statut_signature = "revoque"
            db.session.commit()

    elif event_type == "SignerSigned":
        signer = payload.get("signer", {})
        evt = SignatureEvent(
            transfert_id=ts_records[0].id,
            event_type="signataire_a_signe",
            package_id=package_id,
            details=json.dumps({"email": signer.get("email"), "signe_le": signer.get("signedOn")}),
        )
        db.session.add(evt)
        db.session.commit()

    return jsonify({"ok": True})


# ── Route 4 : Téléchargement (courtier) ───────────────────────────────────────

@signature_bp.route("/telecharger/<reference>", methods=["GET"])
@login_required
def telecharger_document_signe(reference):
    """
    Télécharge le PDF signé ou le rapport d'audit.
    Accessible aux courtiers uniquement.
    Query params : ?type=signed (défaut) | ?type=audit
    """
    if current_user.role != 'courtier':
        abort(403)

    ts = TransfertSignature.query.filter_by(reference=reference).first_or_404()
    doc_type = request.args.get("type", "signed")

    if doc_type == "audit":
        path = ts.chemin_audit_trail
        filename = f"{reference}_audit_trail.pdf"
    else:
        path = ts.chemin_pdf_signe
        filename = f"{reference}_signed.pdf"

    if not path or not Path(path).exists():
        abort(404, description="Document non disponible")

    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=filename)
