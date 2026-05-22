"""
UpTwoU — Connective eSignatures integration
Gère l'intégralité du cycle de signature :
  1. Création du package (dossier de signature)
  2. Upload du PDF de demande de transfert
  3. Ajout du signataire (affilié) avec méthode itsme ou email OTP
  4. Initiation du flux (envoi du lien par email)
  5. Téléchargement du PDF signé + rapport d'audit

Variables d'environnement requises (.env) :
  CONNECTIVE_BASE_URL   = https://api.connective.eu/esig
  CONNECTIVE_API_KEY    = <clé API fournie par Connective>
  CONNECTIVE_SENDER     = info@uptwoU.be
  APP_BASE_URL          = https://app.uptwoU.be
  SIGNED_DOCS_FOLDER    = chemin absolu vers le dossier de stockage des docs signés
"""

import os
import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("CONNECTIVE_BASE_URL", "https://api.connective.eu/esig")
API_KEY  = os.getenv("CONNECTIVE_API_KEY", "")
SENDER   = os.getenv("CONNECTIVE_SENDER", "info@uptwoU.be")
DOCS_DIR = Path(os.getenv("SIGNED_DOCS_FOLDER", "signed_docs"))

DOCS_DIR.mkdir(parents=True, exist_ok=True)


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
    })
    return session


def create_signing_package(affilie: dict, transfert_id: str) -> dict:
    """
    Étape 1 — Crée un package Connective pour une demande de transfert.

    affilie : { prenom, nom, email, niss, langue }
    Retourne { package_id, status }
    """
    session = _make_session()
    payload = {
        "name": f"UpTwoU — Demande de transfert {transfert_id}",
        "status": "Draft",
        "callbackUrl": f"{os.getenv('APP_BASE_URL', '')}/signature/webhook",
        "expireAfterDays": 14,
        "language": affilie.get("langue", "fr"),
        "sender": {"name": "UpTwoU", "email": SENDER},
    }
    resp = session.post(f"{BASE_URL}/v4/packages", json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    logger.info("Package Connective créé : %s", data.get("id"))
    return {"package_id": data["id"], "status": data.get("status", "Draft")}


def upload_transfer_document(package_id: str, pdf_path: Path) -> str:
    """Étape 2 — Upload le PDF dans le package. Retourne document_id."""
    session = _make_session()
    with pdf_path.open("rb") as f:
        resp = session.post(
            f"{BASE_URL}/v4/packages/{package_id}/documents",
            headers={"Content-Type": "application/pdf"},
            data=f,
            params={"name": pdf_path.name},
            timeout=30,
        )
    resp.raise_for_status()
    doc_id = resp.json()["id"]
    logger.info("Document uploadé : %s (package %s)", doc_id, package_id)
    return doc_id


def add_signer(package_id: str, document_id: str, affilie: dict, use_itsme: bool = True) -> str:
    """
    Étape 3 — Ajoute l'affilié comme signataire.
    use_itsme=True → itsme Sign (SEQ) | False → email OTP (SEA)
    Retourne signer_id.
    """
    session = _make_session()
    auth_method = {"type": "itsme", "country": "BE"} if use_itsme else {"type": "EmailOtp"}
    payload = {
        "email": affilie["email"],
        "firstName": affilie["prenom"],
        "lastName": affilie["nom"],
        "language": affilie.get("langue", "fr"),
        "authenticationMethod": auth_method,
        "signingLocations": [{
            "documentId": document_id,
            "page": 1,
            "x": 60, "y": 720, "width": 200, "height": 50,
            "type": "Signature",
        }],
    }
    resp = session.post(f"{BASE_URL}/v4/packages/{package_id}/signers", json=payload, timeout=15)
    resp.raise_for_status()
    signer_id = resp.json()["id"]
    logger.info("Signataire ajouté : %s %s (signer_id=%s, itsme=%s)",
                affilie["prenom"], affilie["nom"], signer_id, use_itsme)
    return signer_id


def initiate_signing(package_id: str) -> str:
    """
    Étape 4 — Déclenche l'envoi du lien de signature à l'affilié.
    Retourne signing_url (utile pour un flux embedded/iframe).
    """
    session = _make_session()
    resp = session.put(
        f"{BASE_URL}/v4/packages/{package_id}/status",
        json={"status": "Active"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    signing_url = ""
    for signer in data.get("signers", []):
        signing_url = signer.get("signingUrl", "")
        break
    logger.info("Flux de signature initié pour package %s", package_id)
    return signing_url


def get_package_status(package_id: str) -> dict:
    """Polling de secours. Retourne { status, signers }."""
    session = _make_session()
    resp = session.get(f"{BASE_URL}/v4/packages/{package_id}", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {"status": data.get("status"), "signers": data.get("signers", [])}


def download_signed_documents(package_id: str, transfert_id: str) -> dict:
    """
    Étape 5 — Télécharge le PDF signé et le rapport d'audit après signature.
    Appelé depuis le webhook handler quand le package passe en 'Finished'.
    Retourne { signed_pdf_path, audit_trail_path }.
    """
    session = _make_session()

    resp = session.get(f"{BASE_URL}/v4/packages/{package_id}/documents", timeout=15)
    resp.raise_for_status()
    documents = resp.json()

    signed_pdf_path = None
    for doc in documents:
        doc_id = doc["id"]
        resp_dl = session.get(
            f"{BASE_URL}/v4/packages/{package_id}/documents/{doc_id}/download",
            timeout=30, stream=True,
        )
        resp_dl.raise_for_status()
        out_path = DOCS_DIR / f"{transfert_id}_signed.pdf"
        with out_path.open("wb") as f:
            for chunk in resp_dl.iter_content(chunk_size=8192):
                f.write(chunk)
        signed_pdf_path = out_path
        logger.info("PDF signé téléchargé : %s", out_path)
        break  # premier document = la demande de transfert

    resp_audit = session.get(f"{BASE_URL}/v4/packages/{package_id}/auditproof", timeout=15)
    audit_path = DOCS_DIR / f"{transfert_id}_audit_trail.pdf"
    if resp_audit.ok:
        with audit_path.open("wb") as f:
            f.write(resp_audit.content)
        logger.info("Rapport d'audit téléchargé : %s", audit_path)
    else:
        logger.warning("Rapport d'audit non disponible pour package %s", package_id)
        audit_path = None

    return {"signed_pdf_path": signed_pdf_path, "audit_trail_path": audit_path}


def initiate_transfer_signature(affilie: dict, pdf_path: Path, transfert_id: str, use_itsme: bool = True) -> dict:
    """
    Orchestre les étapes 1 à 4 en une seule fonction.
    Retourne { package_id, document_id, signer_id, signing_url, status }.
    """
    pkg = create_signing_package(affilie, transfert_id)
    package_id = pkg["package_id"]
    document_id = upload_transfer_document(package_id, pdf_path)
    signer_id = add_signer(package_id, document_id, affilie, use_itsme)
    signing_url = initiate_signing(package_id)
    return {
        "package_id": package_id,
        "document_id": document_id,
        "signer_id": signer_id,
        "signing_url": signing_url,
        "status": "Active",
    }


def download_signed_document_by_id(package_id: str, document_id: str, transfert_id: str) -> dict:
    """Downloads a specific signed document by document_id (for multi-doc packages)."""
    session = _make_session()
    resp = session.get(
        f"{BASE_URL}/v4/packages/{package_id}/documents/{document_id}/download",
        timeout=30, stream=True,
    )
    resp.raise_for_status()
    out_path = DOCS_DIR / f"{transfert_id}_signed.pdf"
    with out_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    logger.info("PDF signé (doc %s) téléchargé : %s", document_id, out_path)

    resp_audit = session.get(f"{BASE_URL}/v4/packages/{package_id}/auditproof", timeout=15)
    audit_path = DOCS_DIR / f"{transfert_id}_audit_trail.pdf"
    if resp_audit.ok:
        with audit_path.open("wb") as f:
            f.write(resp_audit.content)
    else:
        logger.warning("Rapport d'audit non disponible pour package %s", package_id)
        audit_path = None

    return {"signed_pdf_path": out_path, "audit_trail_path": audit_path}


def initiate_batch_transfer_signature(
    affilie: dict,
    transfers: list,
    use_itsme: bool = True,
) -> dict:
    """
    Creates one Connective package with multiple documents (one per transfer).
    transfers: list of (reference: str, pdf_path: Path)
    Returns { package_id, document_ids: [id1, id2, ...], signing_url }.
    """
    session = _make_session()
    refs_label = ", ".join(ref for ref, _ in transfers)
    pkg_payload = {
        "name": f"UpTwoU — Transferts {refs_label}",
        "status": "Draft",
        "callbackUrl": f"{os.getenv('APP_BASE_URL', '')}/signature/webhook",
        "expireAfterDays": 14,
        "language": affilie.get("langue", "fr"),
        "sender": {"name": "UpTwoU", "email": SENDER},
    }
    resp = session.post(f"{BASE_URL}/v4/packages", json=pkg_payload, timeout=15)
    resp.raise_for_status()
    package_id = resp.json()["id"]
    logger.info("Package batch créé : %s (%d docs)", package_id, len(transfers))

    document_ids = []
    for _, pdf_path in transfers:
        with pdf_path.open("rb") as f:
            resp = session.post(
                f"{BASE_URL}/v4/packages/{package_id}/documents",
                headers={"Content-Type": "application/pdf"},
                data=f,
                params={"name": pdf_path.name},
                timeout=30,
            )
        resp.raise_for_status()
        doc_id = resp.json()["id"]
        document_ids.append(doc_id)
        logger.info("Document uploadé : %s (package %s)", doc_id, package_id)

    signing_locations = [
        {"documentId": doc_id, "page": 1, "x": 60, "y": 720,
         "width": 200, "height": 50, "type": "Signature"}
        for doc_id in document_ids
    ]
    auth_method = {"type": "itsme", "country": "BE"} if use_itsme else {"type": "EmailOtp"}
    signer_payload = {
        "email": affilie["email"],
        "firstName": affilie["prenom"],
        "lastName": affilie["nom"],
        "language": affilie.get("langue", "fr"),
        "authenticationMethod": auth_method,
        "signingLocations": signing_locations,
    }
    resp = session.post(f"{BASE_URL}/v4/packages/{package_id}/signers",
                        json=signer_payload, timeout=15)
    resp.raise_for_status()
    logger.info("Signataire batch ajouté (package %s)", package_id)

    signing_url = initiate_signing(package_id)
    return {"package_id": package_id, "document_ids": document_ids, "signing_url": signing_url}
