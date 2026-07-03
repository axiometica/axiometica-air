#!/usr/bin/env python
"""
Run the incident enumeration migration programmatically.
This script executes the migration SQL using SQLAlchemy.
"""

import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection parameters
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "agenticplatform_v2")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# Connection string
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def run_migration():
    """Execute the incident enumeration migration."""
    try:
        # Create engine
        engine = create_engine(DATABASE_URL)

        # Read migration SQL
        migration_file = os.path.join(
            os.path.dirname(__file__),
            "migrations/versions/add_incident_enumeration.sql"
        )

        with open(migration_file, 'r') as f:
            migration_sql = f.read()

        print("Connecting to database...")
        with engine.connect() as conn:
            print(f"Connected to {DB_NAME} at {DB_HOST}:{DB_PORT}")

            # Execute migration statements
            print("\nExecuting migration...")
            for statement in migration_sql.split(';'):
                statement = statement.strip()
                if statement and not statement.startswith('--'):
                    print(f"  Executing: {statement[:80]}...")
                    conn.execute(text(statement))

            conn.commit()

        # Verify sequence was created
        print("\nVerifying sequence creation...")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT nextval('incident_seq')"))
            next_val = result.scalar()
            print(f"✅ Sequence created successfully! Next value: {next_val}")

        # Check columns were added
        print("\nVerifying columns added...")
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='workflow_states'
                AND column_name IN ('incident_number', 'incident_number_str')
            """))
            columns = [row[0] for row in result]
            if len(columns) == 2:
                print(f"✅ Columns added: {', '.join(columns)}")
            else:
                print(f"⚠️  Found {len(columns)} columns: {columns}")

        print("\n✅ Migration completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
