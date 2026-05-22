"""
Local parser for Belgian eID cards.
Replaces the Claude API call — no personal data leaves the machine.

Strategy: OCR the document with PyMuPDF + pytesseract, then find and
parse the TD1 MRZ (3 × 30-char lines at the bottom of the back side).

MRZ layout for Belgian eID (TD1):
  Line 1: IDBEL + card_number + optional_data
  Line 2: YYMMDD(dob) + check + sex + YYMMDD(expiry) + check + BEL
          + personal_number(11 = NISS digits) + composite_check
  Line 3: SURNAME<<GIVEN_NAMES<<padding

Address is NOT printed on Belgian eID cards (stored in electronic chip).
"""
import re
import io
from datetime import datetime


_MRZ_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<')

# NISS as printed on the back: XX.XX.XX-XXX.XX
_RE_NISS_PRINTED = re.compile(r'\b(\d{2}\.\d{2}\.\d{2}-\d{3}\.\d{2})\b')


# ── MRZ field helpers ─────────────────────────────────────────────────────────

def _mrz_date(yymmdd: str, is_birth: bool = True) -> str | None:
    """'730227' → '27/02/1973'."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if is_birth:
        cur = datetime.now().year % 100
        # Cannot be born in 2000s and already be 18+ (pension clients are adults)
        century = 1900 if yy > (cur - 18) else 2000
    else:
        # Belgian IDs expire within 10 years — always in 2000s
        century = 2000
    try:
        return f"{dd:02d}/{mm:02d}/{century + yy}"
    except Exception:
        return None


def _format_niss(raw11: str) -> str | None:
    """'73022724705' → '73.02.27-247.05'."""
    if len(raw11) != 11 or not raw11.isdigit():
        return None
    return (f"{raw11[0:2]}.{raw11[2:4]}.{raw11[4:6]}"
            f"-{raw11[6:9]}.{raw11[9:11]}")


def _parse_td1(l1: str, l2: str, l3: str) -> dict:
    """Parse 3×30 TD1 MRZ for Belgian eID."""
    result: dict = {}

    # Line 3: SURNAME<<GIVEN_NAMES<<padding
    parts = l3.rstrip('<').split('<<')
    if parts:
        # <single> inside surname = hyphen; inside given names = space
        result['nom'] = parts[0].replace('<', '-').strip('-')
    if len(parts) > 1:
        result['prenom'] = parts[1].replace('<', ' ').strip()

    # Line 2 positions (0-based):
    # 0-5   DOB YYMMDD   6 check
    # 7     sex
    # 8-13  expiry YYMMDD  14 check
    # 15-17 nationality
    # 18-28 personal number (11 chars = NISS without separators)
    # 29    composite check
    result['date_naissance'] = _mrz_date(l2[0:6], is_birth=True)
    result['sexe'] = l2[7] if l2[7] in ('M', 'F') else None
    result['date_validite'] = _mrz_date(l2[8:14], is_birth=False)
    result['niss'] = _format_niss(l2[18:29])
    result['pays'] = 'Belgique'

    return result


# ── MRZ detection ─────────────────────────────────────────────────────────────

def _find_mrz(text: str) -> tuple[str, str, str] | None:
    """Scan OCR text for 3 consecutive 30-char MRZ lines starting with IDB."""
    # Normalise: strip each line, collapse embedded spaces into <
    lines = [ln.strip().replace(' ', '<').upper() for ln in text.splitlines()]
    for i in range(len(lines) - 2):
        l1, l2, l3 = lines[i], lines[i + 1], lines[i + 2]
        if (len(l1) == 30 and len(l2) == 30 and len(l3) == 30
                and all(c in _MRZ_CHARS for c in l1 + l2 + l3)
                and l1.startswith('IDB')):
            return l1, l2, l3
    return None


# ── OCR pipeline ──────────────────────────────────────────────────────────────

def _configure_tesseract():
    """Set pytesseract.tesseract_cmd to common Windows install paths if not in PATH."""
    import shutil
    import pytesseract

    if shutil.which('tesseract'):
        return  # already on PATH

    import os
    candidates = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        os.path.expanduser(r'~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'),
        os.path.expanduser(r'~\AppData\Local\Tesseract-OCR\tesseract.exe'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return

    raise FileNotFoundError(
        "Tesseract introuvable. Installez-le depuis "
        "https://github.com/UB-Mannheim/tesseract/wiki "
        "puis relancez l'application."
    )


def _ocr_file(file_obj) -> str:
    """
    Convert a PDF or image file to text using PyMuPDF + pytesseract.
    For text-based PDFs, native extraction is used (faster, no OCR).
    For image-based pages, render at 2× resolution then OCR.
    """
    try:
        import fitz          # PyMuPDF
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "KYC local parsing requires: pip install pymupdf pytesseract pillow. "
            "Tesseract must also be installed on the system."
        ) from exc

    _configure_tesseract()

    # Read raw bytes and detect format
    if hasattr(file_obj, 'read'):
        raw = file_obj.read()
        ext = 'pdf'
    else:
        path = str(file_obj)
        with open(path, 'rb') as f:
            raw = f.read()
        ext = path.rsplit('.', 1)[-1].lower()

    # Tesseract config: LSTM engine, block layout; French + English
    OCR_CONFIG = '--oem 1 --psm 6'

    pages: list[str] = []

    if ext in ('jpg', 'jpeg', 'png'):
        img = Image.open(io.BytesIO(raw))
        pages.append(pytesseract.image_to_string(img, lang='fra+eng', config=OCR_CONFIG))
    else:  # PDF
        doc = fitz.open(stream=raw, filetype='pdf')
        for page in doc:
            native = page.get_text().strip()
            if native:
                pages.append(native)
            else:
                mat = fitz.Matrix(2, 2)  # 2× zoom for sharper OCR
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
                pages.append(pytesseract.image_to_string(img, lang='fra+eng', config=OCR_CONFIG))

    return '\n'.join(pages)


# ── Public entry point ────────────────────────────────────────────────────────

def parse_belgian_eid(file_obj) -> dict:
    """
    Parse a Belgian eID card (JPG, PNG, or scanned PDF).
    Returns a dict matching the schema from analyse_piece_identite().

    Fields extracted from MRZ: nom, prenom, date_naissance, sexe,
    date_validite, niss.
    Fields always null (not on card): adresse, code_postal, ville.
    """
    text = _ocr_file(file_obj)

    result: dict = {
        'nom': None, 'prenom': None,
        'date_naissance': None, 'niss': None, 'sexe': None,
        'date_validite': None,
        'adresse': None, 'code_postal': None, 'ville': None,
        'pays': 'Belgique',
    }

    # Primary: parse MRZ (structured, reliable)
    mrz = _find_mrz(text)
    if mrz:
        result.update(_parse_td1(*mrz))

    # Fallback: NISS printed on back if MRZ failed or gave no NISS
    if not result.get('niss'):
        m = _RE_NISS_PRINTED.search(text)
        if m:
            result['niss'] = m.group(1)

    return result
