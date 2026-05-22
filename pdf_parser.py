"""
Local parser for mypension.be PDF extracts.
Replaces the Claude API call — no personal data leaves the machine.

Parsing strategy: section 2.2 fiche pages are the primary source per contract.
Section 2.1 table and page 3 overview are used for totals only.
Section 3.x (indépendant) pages are ignored entirely.
"""
import re
import json
import pdfplumber


# ── Regex patterns ────────────────────────────────────────────────────────────
# Note: accented chars like é/è appear as \xe9/\xe8 in pdfplumber output for
# this PDF encoding, so we use `.` (any char) in patterns where accents appear.

_RE_NISS = re.compile(
    r'\(Num.ro NISS\s*:\s*(\d{2}\.\d{2}\.\d{2}-\d{3}\.\d{2})\)'
)
_RE_DATE_SITUATION = re.compile(r'Situation au (\d{2}/\d{2}/\d{4})')

# Company name is on the line BEFORE "Organisé par / Géré par"
_RE_ORGANISE_PAR = re.compile(
    r'(.+)\nOrganis. par\s+\(num.ro d.entreprise\s+([\d.]+)\)'
)
_RE_GERE_PAR = re.compile(
    r'(.+)\nG.r. par\s+\(num.ro d.entreprise\s+([\d.]+)\)'
)

# "Référence : V0639270001 - 2011-0000-..."
_RE_REFERENCE = re.compile(r'R.f.rence\s*:\s*([A-Z0-9][\w\-]+)\s*-\s*[\d\-]+')

# Statut appears on the same line as the street address
_RE_STATUT = re.compile(
    r'Statut d.affiliation au \d{2}/\d{2}/\d{4}\s*:\s*(.+?)(?:\n|$)'
)

# Reserve and death coverage — don't match € symbol (encoding is unreliable)
_RE_RESERVE_TOTALE = re.compile(
    r'R.serve de pension totale au \d{2}/\d{2}/\d{4}\s+([\d][\d.]*,\d{2})'
)
_RE_COUVERTURE_TOTALE = re.compile(
    r'Couverture d.c.s totale au \d{2}/\d{2}/\d{4}\s+([\d][\d.]*,\d{2})'
)
# Salarié death coverage from page 3 two-column layout:
# "Couverture décès au 22.218,40 <€> Couverture décès ..."
_RE_COUVERTURE_SAL_P3 = re.compile(
    r'Couverture d.c.s au\s+([\d][\d.]*,\d{2})'
)

_RE_BRANCHE = re.compile(r'branche\s+(\d{2})', re.I)

_RE_FICHE_SALARIE = re.compile(
    r'Fiche de d.tail comme travailleur salari.'
)
_RE_FICHE_INDEPENDANT = re.compile(
    r'Fiche de d.tail comme (?:dirigeant|ind.pendant)'
)

_DORMANT_KW = [
    'non actif', 'niet-actief', 'non-actief',
    'dormant', 'slapend', 'sortie le', 'uitgetreden',
]
_ACTIF_KW = ['actif', 'actief']

ASSUREURS_EXCLUS = ['vitis', 'onelife', 'one life', 'lombard']


# ── Amount helpers ────────────────────────────────────────────────────────────

def _parse_float(amount_str: str) -> float | None:
    """'21.803,59' or '21 803,59' → 21803.59"""
    if not amount_str:
        return None
    s = amount_str.strip().replace('\xa0', '').replace(' ', '')
    # Remove period used as thousands separator (period + 3 digits + comma)
    s = re.sub(r'\.(?=\d{3},)', '', s)
    s = s.replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def _format_amount(v: float) -> str:
    """21803.59 → '21 803,59'"""
    s = f"{v:,.2f}"                        # '21,803.59' (Python default)
    s = s.replace(',', 'X').replace('.', ',').replace('X', ' ')
    return s                               # '21 803,59'


def _normalize_reserve(raw: str) -> str | None:
    """Convert raw amount string to storage format 'XX XXX,XX'."""
    v = _parse_float(raw)
    return _format_amount(v) if v is not None else None


# ── Personal data ─────────────────────────────────────────────────────────────

def _extract_personne(pages_text: list[str]) -> dict:
    """Extract name, NISS, address from page 1 (cleanest structure)."""
    text = pages_text[0] if pages_text else ''
    lines = text.splitlines()

    niss = prenom = nom = adresse = None

    for i, line in enumerate(lines):
        m = _RE_NISS.search(line)
        if not m:
            continue
        niss = m.group(1)

        # Name: previous non-empty uppercase-only line
        for j in range(i - 1, -1, -1):
            prev = lines[j].strip()
            if prev and re.match(r'^[A-Z\xc0-\xd6\xd8-\xde][\w\s\-\xc0-\xff]+$', prev, re.I):
                # Check it looks like an all-caps name (contains only letters/spaces/hyphens)
                if re.match(r'^[A-Z\s\-\xc0-\xd6\xd8-\xde]+$', prev):
                    parts = prev.strip().split()
                    prenom = parts[0].capitalize() if parts else None
                    nom = ' '.join(p.capitalize() for p in parts[1:]) if len(parts) > 1 else None
                    break

        # Address: next non-empty lines until a section header
        addr = []
        for j in range(i + 1, min(i + 5, len(lines))):
            l = lines[j].strip()
            if not l:
                continue
            if re.match(r'^(Mon |Dans |Dossier |Information|Pour |Vous |1\.|2\.|3\.)', l):
                break
            if 'Affili' in l or 'Statut' in l:
                break
            addr.append(l)
            if len(addr) >= 2:
                break
        adresse = ', '.join(addr) if addr else None
        break

    sexe = None
    if niss:
        digits = re.sub(r'\D', '', niss)
        if len(digits) >= 9:
            sexe = 'M' if int(digits[6:9]) % 2 == 1 else 'F'

    return {
        'prenom': prenom,
        'nom': nom,
        'niss': niss,
        'adresse': adresse,
        'sexe': sexe,
    }


# ── Contract extraction from section 2.2 fiches ───────────────────────────────

def _parse_fiche_page(page_text: str, date_valeur: str | None) -> dict:
    """Parse one salarié fiche header page into a contract dict."""
    c: dict = {
        'assureur': None,
        'numero': None,
        'type_branche': 'Inconnu',
        'reserve': None,
        'date_valeur': date_valeur,
        'statut': 'inconnu',
        'organisateur': None,
        'organisateur_bce': None,
    }

    # Reference / contract number
    m = _RE_REFERENCE.search(page_text)
    if m:
        c['numero'] = m.group(1).strip()

    # "AXA BELGIUM\nGéré par (numéro d'entreprise 0404.483.367)"
    m = _RE_GERE_PAR.search(page_text)
    if m:
        c['assureur'] = m.group(1).strip()

    # "AXA BELGIUM\nOrganisé par (numéro d'entreprise 0404.483.367)"
    m = _RE_ORGANISE_PAR.search(page_text)
    if m:
        c['organisateur'] = m.group(1).strip()
        c['organisateur_bce'] = m.group(2).strip()

    # Statut (on same line as street address)
    m = _RE_STATUT.search(page_text)
    if m:
        statut_raw = m.group(1).strip().lower()
        if any(k in statut_raw for k in _DORMANT_KW):
            c['statut'] = 'dormant'
        elif any(k in statut_raw for k in _ACTIF_KW):
            c['statut'] = 'actif'

    # Fallback: "Compte dormant" appears in the plan-type heading (no Statut line)
    if c['statut'] == 'inconnu' and re.search(r'compte dormant', page_text, re.I):
        c['statut'] = 'dormant'

    # Reserve total
    m = _RE_RESERVE_TOTALE.search(page_text)
    if m:
        c['reserve'] = _normalize_reserve(m.group(1))

    return c


def _extract_salarie_contrats(pages_text: list[str], date_valeur: str | None) -> list[dict]:
    """
    Walk pages looking for salarié fiche headers (section 2.2).
    Stop when we hit an indépendant fiche (section 3.x).
    Accumulate constitution-de-pension pages for branche detection.
    """
    contrats: list[dict] = []
    current: dict | None = None
    current_chunks: list[str] = []

    for page_text in pages_text:
        if _RE_FICHE_INDEPENDANT.search(page_text):
            # Finalize any open salarié fiche and stop
            if current is not None:
                _finalize(current, '\n'.join(current_chunks))
                contrats.append(current)
                current = None
            break

        if _RE_FICHE_SALARIE.search(page_text):
            # Finalize previous fiche
            if current is not None:
                _finalize(current, '\n'.join(current_chunks))
                contrats.append(current)
            current = _parse_fiche_page(page_text, date_valeur)
            current_chunks = [page_text]
        elif current is not None:
            # Constitution de pension page belonging to current fiche
            current_chunks.append(page_text)

    # Don't forget the last fiche
    if current is not None:
        _finalize(current, '\n'.join(current_chunks))
        contrats.append(current)

    return contrats


def _finalize(contract: dict, combined_text: str):
    """Enrich with branche from the constitution pages."""
    m = _RE_BRANCHE.search(combined_text)
    if m:
        contract['type_branche'] = f'Branche {m.group(1)}'


# ── Totals ────────────────────────────────────────────────────────────────────

def _extract_date_situation(full_text: str) -> str | None:
    m = _RE_DATE_SITUATION.search(full_text)
    return m.group(1) if m else None


def _extract_couverture_deces_salarie(page3_text: str) -> str | None:
    """
    Extract salarié death coverage from page 3 two-column layout.
    pdfplumber merges columns, producing:
      "Couverture décès au 22.218,40 <€> Couverture décès 12.374,47 <€>"
    The first match (after "au") is the salarié total.
    """
    m = _RE_COUVERTURE_SAL_P3.search(page3_text)
    if m:
        v = _parse_float(m.group(1))
        return f"{_format_amount(v)} €" if v is not None else None
    return None


# ── Eligibility text generation ───────────────────────────────────────────────

def _build_result_texts(personne: dict, contrats: list, dormants: list, dormant_total: float):
    prenom = personne.get('prenom') or ''
    nom = personne.get('nom') or ''
    salutation = f"Cher·ère {prenom} {nom}".strip()

    if dormants and dormant_total >= 10000:
        nb = len(dormants)
        total_str = _format_amount(dormant_total)
        assureurs = ', '.join(dict.fromkeys(
            c['assureur'] for c in dormants if c.get('assureur')
        ))
        return (
            True,
            "Oui, un transfert vers la Branche 23 est possible",
            (f"{salutation}, vous disposez d'une réserve totale de {total_str} € "
             f"répartie sur {nb} contrat(s) dormant(s) chez {assureurs or 'votre assureur'}. "
             f"Ces réserves sont éligibles à un transfert vers la Branche 23."),
            [
                f"{nb} contrat(s) dormant(s) — réserve totale : {total_str} €.",
                f"Organisme(s) gestionnaire(s) : {assureurs}.",
                "Statut salarié confirmé — contrats éligibles au transfert Branche 23.",
            ],
            [],
        )

    if dormants:
        total_str = _format_amount(dormant_total)
        return (
            False,
            "Non, un transfert vers la Branche 23 n'est pas possible",
            (f"{salutation}, vos réserves dormantes s'élèvent à {total_str} €, "
             f"ce qui est inférieur au seuil minimum de 10 000 € requis."),
            [f"Réserve totale dormante : {total_str} € — seuil minimum 10 000 € requis."],
            [f"Réserve insuffisante ({total_str} €) — minimum 10 000 € requis pour un transfert."],
        )

    if contrats:
        return (
            False,
            "Non, un transfert vers la Branche 23 n'est pas possible",
            (f"{salutation}, tous vos contrats de pension salarié sont actuellement actifs. "
             f"Un transfert n'est possible que pour les contrats dormants."),
            ["Aucun contrat dormant détecté — tous les plans sont actifs."],
            ["Aucun contrat dormant — le transfert n'est possible que pour les plans non actifs."],
        )

    return (
        "a_verifier",
        "Un transfert est probablement possible — une vérification est nécessaire",
        f"{salutation}, votre dossier nécessite une vérification manuelle.",
        ["Aucun contrat salarié détecté — vérification manuelle requise."],
        [],
    )


# ── Public entry point ────────────────────────────────────────────────────────

def parse_mypension_pdf(file_obj) -> str:
    """
    Parse a mypension.be PDF extract.
    file_obj: file path (str) or file-like object.
    Returns a JSON string matching the schema analyse_avec_claude() used to return.
    """
    pages_text: list[str] = []
    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            pages_text.append(
                page.extract_text(x_tolerance=2, y_tolerance=2) or ''
            )

    full_text = '\n'.join(pages_text)
    date_valeur = _extract_date_situation(full_text)
    personne = _extract_personne(pages_text)
    contrats = _extract_salarie_contrats(pages_text, date_valeur)

    # Couverture décès salarié is on page 3 (index 2)
    page3 = pages_text[2] if len(pages_text) > 2 else ''
    m_cd = _RE_COUVERTURE_SAL_P3.search(page3)
    couverture_deces_num = _parse_float(m_cd.group(1)) if m_cd else None
    couverture_deces = f"{_format_amount(couverture_deces_num)} €" if couverture_deces_num is not None else None

    # Dormant subset and totals
    dormants = [c for c in contrats if c['statut'] == 'dormant']
    dormant_total = sum(
        v for c in dormants
        if (v := _parse_float(c.get('reserve') or '')) is not None
    )
    total_all = sum(
        v for c in contrats
        if (v := _parse_float(c.get('reserve') or '')) is not None
    )

    eligible, titre, resume, details, raisons = _build_result_texts(
        personne, contrats, dormants, dormant_total
    )

    # Death coverage surplus: amount no longer insured after transfer
    couverture_deces_surplus = None
    if couverture_deces_num is not None and dormant_total > 0 and couverture_deces_num > dormant_total:
        couverture_deces_surplus = f"{_format_amount(couverture_deces_num - dormant_total)} €"

    result = {
        'eligible': eligible,
        'titre': titre,
        'resume': resume,
        'details': details,
        'raisons_refus': raisons,
        'montant_total': f"{_format_amount(dormant_total)} €" if dormant_total else None,
        'couverture_deces': couverture_deces,
        'couverture_deces_surplus': couverture_deces_surplus,
        'nb_contrats': len(contrats),
        'contrats': contrats,
        'personne': personne,
    }
    return json.dumps(result, ensure_ascii=False)
