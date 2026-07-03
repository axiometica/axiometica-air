#!/usr/bin/env python
"""
Direct migration script that adds incident enumeration columns and sequence.
Uses the backend's database configuration.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sqlalchemy import text
from agentic_os.db.database import engine, SessionLocal

def run_migration():
    """Execute the incident enumeration migration."""
    try:
        print("Connecting to database...")

        # Create sequence
        print("Creating incident sequence...")
        with engine.connect() as conn:
            conn.execute(text("CREATE SEQUENCE IF NOT EXISTS incident_seq START 1"))
            conn.commit()
        print("✓ Sequence created")

        # Add columns
        print("Adding incident_number and incident_number_str columns...")
        with engine.connect() as conn:
            conn.execute(text("""
                ALTER TABLE workflow_states
                ADD COLUMN IF NOT EXISTS incident_number INTEGER UNIQUE NULL,
                ADD COLUMN IF NOT EXISTS incident_number_str VARCHAR(20) UNIQUE NULL
            """))
            conn.commit()
        print("✓ Columns added")

        # Create indexes
        print("Creating indexes...")
        with engine.connect() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_incident_number ON workflow_states(incident_number)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_incident_number_str ON workflow_states(incident_number_str)"))
            conn.commit()
        print("✓ Indexes created")

        # Verify
        print("\nVerifying migration...")
        with engine.connect() as conn:
            result = conn.execute(text("SELECT nextval('incident_seq')"))
            next_val = result.scalar()
            print(f"✓ Sequence working! Next value: {next_val}")

            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name='workflow_states'
                AND column_name IN ('incident_number', 'incident_number_str')
            """))
            columns = [row[0] for row in result]
            if len(columns) == 2:
                print(f"✓ Columns verified: {', '.join(columns)}")
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
