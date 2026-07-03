#!/usr/bin/env python3
"""
Apply SQL migrations to the database manually.
Usage: python apply_migrations.py
"""

import os
import sys
from sqlalchemy import create_engine, text
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from agentic_os.db.database import DATABASE_URL

def apply_migrations():
    """Apply all SQL migrations in the migrations/versions directory."""

    # Get database connection
    db_url = DATABASE_URL
    print(f"Connecting to database: {db_url}")

    engine = create_engine(db_url)

    # Directory with migration files
    migrations_dir = Path(__file__).parent / "migrations" / "versions"

    if not migrations_dir.exists():
        print(f"Migrations directory not found: {migrations_dir}")
        return False

    # Get all SQL files sorted by name (assumption: they're numbered or named in order)
    migration_files = sorted([f for f in migrations_dir.glob("*.sql")])

    if not migration_files:
        print(f"No migration files found in {migrations_dir}")
        return False

    print(f"Found {len(migration_files)} migration files")

    # Apply each migration
    with engine.connect() as conn:
        for migration_file in migration_files:
            print(f"\nApplying migration: {migration_file.name}")

            try:
                with open(migration_file, 'r') as f:
                    migration_sql = f.read()

                # Execute the migration (split by ; to handle multiple statements)
                # Also filter out comments and empty lines
                statements = []
                for s in migration_sql.split(';'):
                    s = s.strip()
                    # Remove comment lines
                    lines = [line.strip() for line in s.split('\n') if line.strip() and not line.strip().startswith('--')]
                    statement = '\n'.join(lines).strip()
                    if statement:
                        statements.append(statement)

                for statement in statements:
                    print(f"  Executing: {statement[:60]}...")
                    conn.execute(text(statement))

                conn.commit()
                print(f"  [OK] Migration applied successfully")

            except Exception as e:
                print(f"  ✗ Error applying migration: {e}")
                conn.rollback()
                return False

    print("\n[SUCCESS] All migrations applied successfully!")
    return True

if __name__ == "__main__":
    success = apply_migrations()
    sys.exit(0 if success else 1)
