#!/usr/bin/env python3
"""
Migration: Add calendar_write_enabled field to User model

This migration adds the calendar_write_enabled boolean field to the User table.
Admins automatically get write permission, non-admins default to False.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import User

def migrate():
    """Add calendar_write_enabled field to User table"""
    app = create_app()

    with app.app_context():
        # Check if column already exists
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('user')]

        if 'calendar_write_enabled' in columns:
            print("✓ Column 'calendar_write_enabled' already exists, skipping migration")
            return

        print("Adding 'calendar_write_enabled' column to User table...")

        # Add the column with default value False
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text('ALTER TABLE user ADD COLUMN calendar_write_enabled BOOLEAN DEFAULT 0'))
                conn.commit()
            print("✓ Column added successfully")

            # Set calendar_write_enabled=True for admin users
            admin_users = User.query.filter_by(is_admin=True).all()
            for admin in admin_users:
                admin.calendar_write_enabled = True
            db.session.commit()
            print(f"✓ Granted calendar write permission to {len(admin_users)} admin user(s)")

        except Exception as e:
            print(f"✗ Migration failed: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    migrate()
