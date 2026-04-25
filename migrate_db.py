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
from models import seed_assureurs

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
            conn.commit()

    seed_assureurs(app)
    print("\nMigration complete.")
