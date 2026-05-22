"""
DB migration script — safe to run multiple times.
Adds missing columns to existing tables; creates new tables if absent.
If the schema is too far out of sync, use --reset to drop and recreate everything.

Usage:
    python migrate_db.py          # incremental migration
    python migrate_db.py --reset  # DROP ALL + recreate (loses all data)
"""
import sys
from app import app, db
from sqlalchemy import text, inspect
from models import seed_assureurs, TransfertSignature, SignatureEvent, CabinetCourtage, AssureurDestinataire  # noqa: F401 — ensures tables are registered

COLUMN_MIGRATIONS = [
    # (table, column, sql_type)
    ("client",  "date_naissance",       "VARCHAR(10)"),
    ("client",  "sexe",                 "VARCHAR(1)"),
    ("contrat", "statut",               "VARCHAR(20) DEFAULT 'inconnu'"),
    ("client",  "pays",                 "VARCHAR(100) DEFAULT 'Belgique'"),
    ("client",  "kyc_verifie",          "BOOLEAN DEFAULT 0"),
    ("client",  "kyc_document",         "VARCHAR(200)"),
    ("client",  "frais_acceptes",       "BOOLEAN DEFAULT 0"),
    ("client",  "questionnaire_score",  "INTEGER"),
    ("analyse", "date_extrait",         "VARCHAR(10)"),
    ("contrat", "analyse_id",           "INTEGER"),
    ("contrat", "organisateur",         "VARCHAR(200)"),
    ("contrat", "organisateur_bce",     "VARCHAR(30)"),
    ("contrat", "date_terme",           "VARCHAR(10)"),
    ("client",  "beneficiaire_vie",     "VARCHAR(200)"),
    ("contrat", "date_transfert",       "VARCHAR(10)"),
    ("client",  "courtier_id",          "INTEGER"),
    ("client",  "telephone_gsm",        "VARCHAR(20)"),
    ("user",    "email_confirmed",       "BOOLEAN DEFAULT 0"),
    ("user",    "email_token",           "VARCHAR(64)"),
    ("user",    "cabinet_id",            "INTEGER REFERENCES cabinet_courtage(id)"),
    ("client",  "beneficiaires_json",    "TEXT"),
    ("client",  "cabinet_id",           "INTEGER REFERENCES cabinet_courtage(id)"),
    ("transfert_signature", "nouveau_contrat_id", "INTEGER REFERENCES contrat(id)"),
    ("contrat", "assureur_dest_id", "INTEGER REFERENCES assureur_destinataire(id)"),
    ("transfert_signature", "signing_url", "VARCHAR(500)"),
    ("client", "est_ppe",     "BOOLEAN"),
    ("client", "ppe_details", "VARCHAR(500)"),
]

reset = "--reset" in sys.argv

with app.app_context():
    if reset:
        print("⚠ DROP ALL tables and recreate…")
        db.drop_all()
        db.create_all()
        print("  Tables recreated.")
    else:
        insp = inspect(db.engine)
        existing_tables = insp.get_table_names()

        with db.engine.connect() as conn:
            for table, column, col_type in COLUMN_MIGRATIONS:
                if table not in existing_tables:
                    print(f"  ? table '{table}' does not exist — run with --reset")
                    continue
                existing_cols = [c["name"] for c in insp.get_columns(table)]
                if column not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    print(f"  + {table}.{column}")
                else:
                    print(f"  = {table}.{column} (already exists)")

            db.create_all()   # creates any brand-new tables

            # Link orphaned contracts (analyse_id IS NULL) to the client's
            # most recent analyse. Excludes Branche 23 UpTwoU contracts
            # (they intentionally have analyse_id=NULL to distinguish them).
            result = conn.execute(text("""
                UPDATE contrat
                SET analyse_id = (
                    SELECT id FROM analyse
                    WHERE analyse.client_id = contrat.client_id
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                WHERE analyse_id IS NULL
                  AND (type_branche IS NULL OR type_branche != 'Branche 23')
                  AND client_id IN (SELECT DISTINCT client_id FROM analyse)
            """))
            if result.rowcount:
                print(f"  ~ linked {result.rowcount} orphaned contrat(s) to their latest analyse")

            # Backfill date_extrait on analyses that have linked contracts
            rows = conn.execute(text("""
                SELECT a.id, c.date_valeur
                FROM analyse a
                JOIN contrat c ON c.analyse_id = a.id
                WHERE a.date_extrait IS NULL AND c.date_valeur IS NOT NULL
                GROUP BY a.id
            """)).fetchall()
            for row in rows:
                conn.execute(text(
                    "UPDATE analyse SET date_extrait = :dv WHERE id = :id"
                ), {"dv": row[1], "id": row[0]})
            if rows:
                print(f"  ~ backfilled date_extrait on {len(rows)} analyse(s)")

            # Confirm all pre-existing users so they can still log in after this migration.
            result = conn.execute(text(
                'UPDATE "user" SET email_confirmed = 1 WHERE email_confirmed IS NULL OR email_confirmed = 0'
            ))
            if result.rowcount:
                print(f"  ~ confirmed {result.rowcount} pre-existing user(s)")

            conn.commit()

    seed_assureurs(app)
    print("\nMigration complete.")
