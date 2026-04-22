"""
One-shot migration script — run once after pulling the new models.
Safe to run multiple times: skips columns/tables that already exist.
"""
from app import app, db
from sqlalchemy import text, inspect

MIGRATIONS = [
    ("client",  "date_naissance", "VARCHAR(10)"),
    ("client",  "sexe",           "VARCHAR(1)"),
    ("contrat", "statut",         "VARCHAR(20) DEFAULT 'inconnu'"),
]

with app.app_context():
    insp = inspect(db.engine)

    with db.engine.connect() as conn:
        for table, column, col_type in MIGRATIONS:
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                print(f"  + {table}.{column}")
            else:
                print(f"  = {table}.{column} (already exists)")

        # Create new tables (assureur_ref) if missing
        db.create_all()
        conn.commit()

    # Seed reference insurers if empty
    from models import seed_assureurs
    seed_assureurs(app)

    print("\nMigration complete.")
