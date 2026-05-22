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
from models import seed_assureurs, seed_pension_insurers, TransferSignature, SignatureEvent, BrokerCabinet, PensionInsurer, TransferRequest, ReceivedReserve  # noqa: F401

# French table name -> English table name
TABLE_RENAMES = [
    ("assureur_destinataire", "destination_insurer"),
    ("cabinet_courtage",      "broker_cabinet"),
    ("contrat",               "contract"),
    ("transfert_reserve",     "transfer_request"),
    ("reserve_recue",         "received_reserve"),
    ("analyse",               "analysis"),
    ("assureur_ref",          "insurer_ref"),
    ("profil_change_log",     "profile_change_log"),
    ("vni_historique",        "nav_history"),
    ("transfert_signature",   "transfer_signature"),
    ("signature_event",       "signature_event"),  # no rename needed, already correct
]

COLUMN_MIGRATIONS = [
    # (table, column, sql_type)
    ("client",            "date_naissance",       "VARCHAR(10)"),
    ("client",            "sexe",                 "VARCHAR(1)"),
    ("contract",          "statut",               "VARCHAR(20) DEFAULT 'inconnu'"),
    ("client",            "pays",                 "VARCHAR(100) DEFAULT 'Belgique'"),
    ("client",            "kyc_verifie",          "BOOLEAN DEFAULT 0"),
    ("client",            "kyc_document",         "VARCHAR(200)"),
    ("client",            "frais_acceptes",       "BOOLEAN DEFAULT 0"),
    ("client",            "questionnaire_score",  "INTEGER"),
    ("analysis",          "date_extrait",         "VARCHAR(10)"),
    ("client",            "beneficiaire_vie",     "VARCHAR(200)"),
    ("client",            "courtier_id",          "INTEGER"),
    ("client",            "telephone_gsm",        "VARCHAR(20)"),
    ("user",              "email_confirmed",      "BOOLEAN DEFAULT 0"),
    ("user",              "email_token",          "VARCHAR(64)"),
    ("user",              "cabinet_id",           "INTEGER REFERENCES broker_cabinet(id)"),
    ("client",            "beneficiaires_json",   "TEXT"),
    ("client",            "cabinet_id",           "INTEGER REFERENCES broker_cabinet(id)"),
    ("transfer_signature","nouveau_contrat_id",   "INTEGER REFERENCES contract(id)"),
    ("transfer_signature","signing_url",          "VARCHAR(500)"),
    ("client",            "est_ppe",              "BOOLEAN"),
    ("client",            "ppe_details",          "VARCHAR(500)"),
    ("client",            "ci_date_validite",     "VARCHAR(10)"),
    ("transfer_signature","transfert_id",         "INTEGER REFERENCES transfer_request(id)"),
    ("pension_insurer",   "numero_bce",           "VARCHAR(12)"),
    ("contract",          "pension_insurer_id",   "INTEGER REFERENCES pension_insurer(id)"),
    ("contact_message",   "client_id",            "INTEGER REFERENCES client(id)"),
    ("contact_message",   "reponse",              "TEXT"),
    ("contract",          "cabinet_id",           "INTEGER REFERENCES broker_cabinet(id)"),
    ("contract",          "beneficiaires_json",   "TEXT"),
    ("user",              "failed_login_attempts","INTEGER DEFAULT 0"),
    ("user",              "locked_until",         "DATETIME"),
    ("received_reserve",  "currency",             "VARCHAR(3) NOT NULL DEFAULT 'EUR'"),
    ("received_reserve",  "created_at",           "DATETIME"),
]

reset = "--reset" in sys.argv

with app.app_context():
    if reset:
        print("WARNING: DROP ALL tables and recreate...")
        db.drop_all()
        db.create_all()
        print("  Tables recreated.")
    else:
        insp = inspect(db.engine)
        existing_tables = insp.get_table_names()

        with db.engine.connect() as conn:
            # -- Rename French tables to English (idempotent) ---------------------
            conn.execute(text("PRAGMA foreign_keys = OFF"))
            for old_name, new_name in TABLE_RENAMES:
                if old_name == new_name:
                    continue
                old_exists = old_name in existing_tables
                new_exists = new_name in existing_tables
                if old_exists and not new_exists:
                    conn.execute(text(f"ALTER TABLE {old_name} RENAME TO {new_name}"))
                    conn.commit()
                    print(f"  renamed: {old_name} -> {new_name}")
                elif old_exists and new_exists:
                    # Both exist: copy any rows from old -> new if new is empty, then drop old
                    old_count = conn.execute(text(f"SELECT COUNT(*) FROM {old_name}")).scalar()
                    new_count = conn.execute(text(f"SELECT COUNT(*) FROM {new_name}")).scalar()
                    if old_count > 0 and new_count == 0:
                        old_cols = {c["name"] for c in insp.get_columns(old_name)}
                        new_cols = {c["name"] for c in insp.get_columns(new_name)}
                        common = ", ".join(old_cols & new_cols)
                        conn.execute(text(
                            f"INSERT INTO {new_name} ({common}) SELECT {common} FROM {old_name}"
                        ))
                        print(f"  ~ copied {old_count} row(s) from {old_name} -> {new_name}")
                    elif old_count > 0 and new_count > 0:
                        print(f"  ! {old_name} has {old_count} row(s) but {new_name} already has {new_count} — skipping copy, dropping old")
                    conn.execute(text(f"DROP TABLE {old_name}"))
                    conn.commit()
                    print(f"  dropped: {old_name}")
                elif not old_exists and new_exists:
                    print(f"  = {new_name} (already renamed)")
                else:
                    print(f"  ? neither {old_name} nor {new_name} found")
            conn.execute(text("PRAGMA foreign_keys = ON"))

            # Re-inspect after renames
            insp = inspect(db.engine)
            existing_tables = insp.get_table_names()

            # -- Standard column migrations ---------------------------------------
            for table, column, col_type in COLUMN_MIGRATIONS:
                if table not in existing_tables:
                    print(f"  ? table '{table}' does not exist -- run with --reset")
                    continue
                existing_cols = [c["name"] for c in insp.get_columns(table)]
                if column not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    print(f"  + {table}.{column}")
                else:
                    print(f"  = {table}.{column} (already exists)")

            db.create_all()   # creates new tables: transfer_request, received_reserve, etc.

            # Re-inspect after create_all
            insp = inspect(db.engine)
            existing_tables = insp.get_table_names()

            # -- Migrate dormant contracts -> transfer_request --------------------
            # Only applicable when the old 'contract' table still had an analyse_id column
            # (i.e. before the schema split). Skip if that column no longer exists.
            contract_cols = [c["name"] for c in insp.get_columns('contract')] if 'contract' in existing_tables else []
            if 'contract' in existing_tables and 'transfer_request' in existing_tables and 'analyse_id' in contract_cols:
                count = conn.execute(text(
                    "SELECT COUNT(*) FROM contract WHERE analyse_id IS NOT NULL"
                )).scalar()
                if count:
                    tr_cols = [c["name"] for c in insp.get_columns('transfer_request')]
                    if 'old_contrat_id' not in tr_cols:
                        conn.execute(text(
                            "ALTER TABLE transfer_request ADD COLUMN old_contrat_id INTEGER"
                        ))
                        print("  + transfer_request.old_contrat_id (migration helper)")
                        insp = inspect(db.engine)

                    already = set(conn.execute(text(
                        "SELECT old_contrat_id FROM transfer_request WHERE old_contrat_id IS NOT NULL"
                    )).scalars().all())

                    rows = conn.execute(text(
                        "SELECT id, client_id, analyse_id, assureur, numero, type_branche, "
                        "reserve, date_valeur, organisateur, organisateur_bce, date_terme, "
                        "statut, created_at FROM contract WHERE analyse_id IS NOT NULL"
                    )).fetchall()

                    inserted = 0
                    for row in rows:
                        if row[0] in already:
                            continue
                        st = row[11] if row[11] in ('dormant', 'en_cours', 'recu') else 'dormant'
                        conn.execute(text("""
                            INSERT INTO transfer_request
                                (old_contrat_id, client_id, analyse_id, assureur, numero,
                                 type_branche, reserve, date_valeur, organisateur,
                                 organisateur_bce, date_terme, statut, created_at)
                            VALUES
                                (:oc,:cli,:an,:ass,:num,:tb,:res,:dv,:org,:obce,:dt,:st,:ca)
                        """), dict(oc=row[0], cli=row[1], an=row[2], ass=row[3], num=row[4],
                                   tb=row[5], res=row[6], dv=row[7], org=row[8], obce=row[9],
                                   dt=row[10], st=st, ca=row[12]))
                        inserted += 1
                    if inserted:
                        print(f"  ~ migrated {inserted} dormant contract(s) -> transfer_request")

            # -- Link transfer_signature.transfert_id via old_contrat_id mapping --
            tr_cols_now = [c["name"] for c in insp.get_columns('transfer_request')] if 'transfer_request' in existing_tables else []
            if 'transfer_request' in existing_tables and 'old_contrat_id' in tr_cols_now:
                updated = conn.execute(text("""
                    UPDATE transfer_signature
                    SET transfert_id = (
                        SELECT tr.id FROM transfer_request tr
                        WHERE tr.old_contrat_id = transfer_signature.contrat_id
                    )
                    WHERE transfert_id IS NULL
                      AND contrat_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1 FROM transfer_request tr2
                          WHERE tr2.old_contrat_id = transfer_signature.contrat_id
                      )
                """))
                if updated.rowcount:
                    print(f"  ~ linked {updated.rowcount} transfer_signature(s) -> transfer_request")

            # -- Backfill date_extrait on analyses --------------------------------
            if 'transfer_request' in existing_tables:
                rows = conn.execute(text("""
                    SELECT a.id, tr.date_valeur
                    FROM analysis a
                    JOIN transfer_request tr ON tr.analyse_id = a.id
                    WHERE a.date_extrait IS NULL AND tr.date_valeur IS NOT NULL
                    GROUP BY a.id
                """)).fetchall()
                for row in rows:
                    conn.execute(text(
                        "UPDATE analysis SET date_extrait = :dv WHERE id = :id"
                    ), {"dv": row[1], "id": row[0]})
                if rows:
                    print(f"  ~ backfilled date_extrait on {len(rows)} analysis row(s)")

            # -- Backfill contract.pension_insurer_id -> OneLife SA (id=1) ----------
            if 'contract' in existing_tables and 'pension_insurer' in existing_tables:
                result = conn.execute(text("""
                    UPDATE contract SET pension_insurer_id = 1
                    WHERE pension_insurer_id IS NULL
                """))
                if result.rowcount:
                    print(f"  ~ linked {result.rowcount} contract(s) -> pension_insurer id=1 (OneLife SA)")

            # -- Fix analysis.created_at stored as DD/MM/YYYY -> ISO datetime ------
            if 'analysis' in existing_tables:
                result = conn.execute(text("""
                    UPDATE analysis
                    SET created_at = substr(created_at,7,4)||'-'||substr(created_at,4,2)||'-'||substr(created_at,1,2)||' 00:00:00'
                    WHERE created_at LIKE '__/__/____'
                """))
                if result.rowcount:
                    print(f"  ~ fixed created_at format on {result.rowcount} analysis row(s)")

            # -- Recreate received_reserve with montant as REAL (was VARCHAR) ------
            if 'received_reserve' in existing_tables:
                schema_row = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='received_reserve'"
                )).fetchone()
                schema_sql = schema_row[0] if schema_row else ''
                montant_is_numeric = any(
                    kw in schema_sql for kw in ('montant REAL', 'montant FLOAT', 'montant NUMERIC', 'montant DOUBLE')
                )
                if not montant_is_numeric:
                    conn.execute(text("PRAGMA foreign_keys = OFF"))
                    conn.execute(text("""
                        CREATE TABLE received_reserve_new (
                            id             INTEGER PRIMARY KEY,
                            contract_id    INTEGER NOT NULL REFERENCES contract(id),
                            transfer_id    INTEGER REFERENCES transfer_request(id),
                            montant        REAL,
                            currency       VARCHAR(3) NOT NULL DEFAULT 'EUR',
                            date_reception VARCHAR(10),
                            note           VARCHAR(500),
                            created_at     DATETIME
                        )
                    """))
                    conn.execute(text("""
                        INSERT INTO received_reserve_new
                            (id, contract_id, transfer_id, montant, currency, date_reception, note, created_at)
                        SELECT
                            id, contract_id, transfer_id,
                            CAST(montant AS REAL),
                            currency, date_reception, note, created_at
                        FROM received_reserve
                    """))
                    conn.execute(text("DROP TABLE received_reserve"))
                    conn.execute(text("ALTER TABLE received_reserve_new RENAME TO received_reserve"))
                    conn.execute(text("PRAGMA foreign_keys = ON"))
                    conn.commit()
                    print("  ~ received_reserve.montant migrated from VARCHAR to REAL")
                else:
                    print("  = received_reserve.montant (already numeric)")

            # -- Convert received_reserve.montant from French strings to float ----
            if 'received_reserve' in existing_tables:
                rows = conn.execute(text(
                    "SELECT id, montant FROM received_reserve WHERE montant IS NOT NULL"
                )).fetchall()
                converted = 0
                for row in rows:
                    raw = str(row[1])
                    try:
                        float(raw)   # already a plain float string, no conversion needed
                    except ValueError:
                        s = raw.strip().replace('€', '').replace('\xa0', '').replace(' ', '')
                        if ',' in s and '.' in s:
                            if s.rfind(',') > s.rfind('.'):
                                s = s.replace('.', '').replace(',', '.')
                            else:
                                s = s.replace(',', '')
                        elif ',' in s:
                            s = s.replace(',', '.')
                        try:
                            val = float(s)
                            conn.execute(text(
                                "UPDATE received_reserve SET montant = :v WHERE id = :id"
                            ), {"v": val, "id": row[0]})
                            print(f"  ~ received_reserve id={row[0]}: {repr(row[1])} → {val}")
                            converted += 1
                        except ValueError:
                            print(f"  ! could not parse received_reserve.montant id={row[0]}: {repr(row[1])}")
                if converted:
                    print(f"  ~ converted {converted} received_reserve.montant value(s) to float")

            # -- Confirm all pre-existing users -----------------------------------
            result = conn.execute(text(
                'UPDATE "user" SET email_confirmed = 1 '
                'WHERE email_confirmed IS NULL OR email_confirmed = 0'
            ))
            if result.rowcount:
                print(f"  ~ confirmed {result.rowcount} pre-existing user(s)")

            conn.commit()

    seed_assureurs(app)
    seed_pension_insurers(app)
    print("\nMigration complete.")
