# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
python app.py          # starts Flask dev server on http://localhost:5000
```

## Database migrations

The project uses a hand-rolled migration script instead of Alembic. When models change, add the new column to `COLUMN_MIGRATIONS` in `migrate_db.py`, then run:

```bash
python migrate_db.py            # incremental — adds missing columns, safe to re-run
python migrate_db.py --reset    # drops all tables and recreates (data loss)
```

`db.create_all()` is also called inside `app.py` at startup, so new tables are always created automatically. The `seed_assureurs()` call populates the `assureur_ref` table if empty.

## Environment variables

Copy `.env` and set:

| Variable | Purpose |
|---|---|
| `FLASK_SECRET` | Flask session secret |
| `ANTHROPIC_API_KEY` | Claude API key (required for PDF analysis and KYC) |
| `COURTIER_SECRET` | Secret code required to register a courtier account at `/admin/register` |
| `NOUVEL_ASSUREUR_NOM/BCE/IBAN/REF` | UpTwoU's B23 insurer details used in Annexe 1 PDFs |
| `ITSME_CLIENT_ID`, `ITSME_PRIVATE_KEY` | Optional — enables itsme OIDC login |
| `ITSME_ENV` | `sandbox` (default) or `production` |

## Architecture

### Single-file Flask app

All routes, business logic, and AI calls live in `app.py`. There is no blueprint layer.

**User roles**: `client` and `courtier`. Role guards are plain decorators (`@client_required`, `@courtier_required`) applied after `@login_required`. Both roles share the same `User` + `Client` model pair — courtiers also get a `Client` profile row (used to store their name, city, phone for the broker-search API).

### Data model relationships

```
User (role: client|courtier)
 └── Client (profile, kyc, profil_risque, onboarding_step)
      ├── Contrat[] (one per pension plan found in the PDF)
      └── Analyse[] (one per mypension.be PDF uploaded)
           └── Contrat[] via analyse_id (introduced later; orphaned rows use NULL)
```

`Client.contrats_dormants` / `contrats_actifs` always scope to the latest `Analyse` to avoid showing stale contracts from older uploads.

`AssureurRef` is a static reference table (seeded from `ASSUREURS_SEED`) used to pre-fill Annexe 1 transfer forms with insurer BCE numbers and addresses.

### Client onboarding flow

Enforced via `Client.onboarding_step` property and redirects in each route:

1. **`/onboarding/kyc`** — upload ID card → Claude vision extracts personal data → user confirms
2. **`/onboarding/profil`** or **`/onboarding/questionnaire`** — risk profile (direct pick or 6-question MiFID-style quiz scoring 0–18)
3. **`/onboarding/frais`** — fee disclosure acceptance
4. **`/onboarding/synthese`** — summary of dormant contracts

### Anonymous analysis flow

`/analyser` (POST) → `pdfplumber` extracts text + tables → `analyse_avec_claude()` sends to `claude-opus-4-7` with `thinking: {type: adaptive}` → returns JSON → post-processed in Python (exclude Vitis/Onelife, compute pension age eligibility from NISS) → stored in Flask session → user is invited to register.

### AI integration points

- `analyse_avec_claude()` — mypension.be PDF → structured JSON (eligible, contrats[], personne{})
- `analyse_piece_identite()` — ID card image or PDF → personal data JSON for KYC pre-fill
- Both use `claude-opus-4-7`. The KYC call uses vision (`image` or `document` content blocks).

### PDF generation

`pdf_utils.py` uses `fpdf2` to generate the official Annexe 1 transfer request form (one per dormant contract). Downloaded from `/client/transfert/<id>/pdf`.

### itsme OIDC

`itsme_auth.py` implements Authorization Code + PKCE + `private_key_jwt` client auth. Only activated when `ITSME_CLIENT_ID` env var is present. The sandbox discovery URL is `https://idp.int.itsme.services/v2/.well-known/openid-configuration`.

### Templates

Organized by role under `templates/`:
- `admin/` — courtier views (dashboard, client detail)
- `client/` — authenticated client views (dashboard, contrats, données, profil, gestion, transfert)
- `onboarding/` — KYC, profil, questionnaire, frais, synthèse
- Root level — public pages (index, analyser, resultat, register, login, contact, faq, etc.)

Each section has a `_base.html` with its own nav/layout.

## Key business rules

- **Excluded insurers**: Vitis Life / Onelife / Lombard International contracts are stripped from analysis results (already managed as B23).
- **Pension age**: Computed from NISS (Belgian national ID). Age 66 if reached before 2030-01-01, else 67. Transfer refused if < 2 years remaining.
- **Transfer eligibility**: requires salarié status + dormant contract + total reserves > €10 000.
- **Risk profiles**: `prudent` (0–4) / `equilibre` (5–9) / `dynamique` (10–14) / `conviction` (15–18) from questionnaire score.
