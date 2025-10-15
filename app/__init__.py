from flask import Flask, session
from flask_sqlalchemy import SQLAlchemy
from .config import load_config
from datetime import timedelta
import os
import secrets

db = SQLAlchemy()


def create_app():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    templates_dir = os.path.join(base_dir, 'templates')
    static_dir = os.path.join(base_dir, 'static')

    app = Flask(
        __name__,
        template_folder=templates_dir,
        static_folder=static_dir,
    )

    # Paths
    data_dir = os.path.join(base_dir, 'data')
    uploads_dir = os.path.join(base_dir, 'uploads')
    media_dir = os.path.join(base_dir, 'media')
    pdfs_dir = os.path.join(base_dir, 'pdfs')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(pdfs_dir, exist_ok=True)

    # SQLite DB file at an absolute path to avoid driver path issues
    db_path = os.path.join(base_dir, 'data', 'app.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Generate a strong SECRET_KEY if not provided via env
    secret = os.environ.get('SECRET_KEY')
    if not secret:
        import secrets as _secrets
        secret = _secrets.token_hex(32)
    app.config['SECRET_KEY'] = secret
    # Explicitly disable CSRF (forms are simple and app runs on home network)
    app.config['WTF_CSRF_ENABLED'] = False
    # Session configuration
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)  # 24 hour session
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Load config.yml
    app.config['HOMEHUB_CONFIG'] = load_config()

    db.init_app(app)

    # Ensure models are imported before creating tables
    with app.app_context():
        from . import models  # noqa: F401 ensures model metadata is registered
        from .models import User
        db.create_all()
        # Perform tiny auto-migrations for SQLite to add missing columns if upgrading
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            # Helper to check column existence
            def has_column(table, column):
                cur.execute(f"PRAGMA table_info({table})")
                return any(row[1] == column for row in cur.fetchall())
            # Add 'done' to chore
            if not has_column('chore', 'done'):
                cur.execute("ALTER TABLE chore ADD COLUMN done INTEGER DEFAULT 0")
            # Add 'status' to media
            if not has_column('media', 'status'):
                cur.execute("ALTER TABLE media ADD COLUMN status TEXT DEFAULT 'done'")
            # Add 'progress' to media
            if not has_column('media', 'progress'):
                cur.execute("ALTER TABLE media ADD COLUMN progress TEXT")
            # Reminder new columns (category, color, updated_at)
            if not has_column('reminder', 'category'):
                cur.execute("ALTER TABLE reminder ADD COLUMN category TEXT")
            if not has_column('reminder', 'color'):
                cur.execute("ALTER TABLE reminder ADD COLUMN color TEXT")
            if not has_column('reminder', 'updated_at'):
                cur.execute("ALTER TABLE reminder ADD COLUMN updated_at TIMESTAMP")
            if not has_column('reminder', 'time'):
                cur.execute("ALTER TABLE reminder ADD COLUMN time TEXT")
            if not has_column('reminder', 'duration'):
                cur.execute("ALTER TABLE reminder ADD COLUMN duration INTEGER")
            # Ensure memberstatus table exists
            cur.execute("CREATE TABLE IF NOT EXISTS member_status (id INTEGER PRIMARY KEY, name TEXT, text TEXT, updated_at TIMESTAMP)")
            # Ensure new tables for groceries and expenses exist
            cur.execute("CREATE TABLE IF NOT EXISTS grocery_history (id INTEGER PRIMARY KEY, item TEXT, creator TEXT, timestamp TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS recurring_expense (id INTEGER PRIMARY KEY, title TEXT, unit_price REAL, default_quantity REAL, frequency TEXT, start_date DATE, end_date DATE, last_generated_date DATE, creator TEXT, timestamp TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS expense_entry (id INTEGER PRIMARY KEY, date DATE, title TEXT, category TEXT, unit_price REAL, quantity REAL, amount REAL, payer TEXT, recurring_id INTEGER, timestamp TIMESTAMP)")
            # Add monthly_mode to recurring_expense if missing
            def ensure_column(table, col, type_spec, default=None):
                cur.execute(f"PRAGMA table_info({table})")
                cols = [row[1] for row in cur.fetchall()]
                if col not in cols:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {type_spec}")
                    if default is not None:
                        cur.execute(f"UPDATE {table} SET {col}=? WHERE {col} IS NULL", (default,))
            ensure_column('recurring_expense', 'monthly_mode', 'TEXT', 'day_of_month')
            ensure_column('recurring_expense', 'category', 'TEXT', None)
            # Basic settings table (key/value) for currency and categories
            cur.execute("CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT)")
            # Bitwarden vault mapping table
            cur.execute("CREATE TABLE IF NOT EXISTS bitwarden_vault (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, bitwarden_email TEXT NOT NULL, setup_completed INTEGER DEFAULT 0, timestamp TIMESTAMP)")
            # User authentication table
            cur.execute("CREATE TABLE IF NOT EXISTS user (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, password_set INTEGER DEFAULT 0, created_at TIMESTAMP, last_login TIMESTAMP)")
            # Add caldav_password_hash column for CalDAV authentication
            ensure_column('user', 'caldav_password_hash', 'TEXT', None)
            # Add calendar_write_enabled column for calendar permissions
            ensure_column('user', 'calendar_write_enabled', 'INTEGER', 0)

            # New feature tables (added 2025-10-14)
            cur.execute("CREATE TABLE IF NOT EXISTS photo (id INTEGER PRIMARY KEY, filename VARCHAR(256) NOT NULL, album VARCHAR(128), caption VARCHAR(512), uploader VARCHAR(64), upload_time TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS meal_plan (id INTEGER PRIMARY KEY, day VARCHAR(16) NOT NULL, meal_type VARCHAR(16) NOT NULL, meal_name VARCHAR(256))")
            cur.execute("CREATE TABLE IF NOT EXISTS favorite_meal (id INTEGER PRIMARY KEY, name VARCHAR(256) NOT NULL, ingredients TEXT, creator VARCHAR(64), timestamp TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS maintenance_task (id INTEGER PRIMARY KEY, task_name VARCHAR(256) NOT NULL, description TEXT, icon VARCHAR(32), frequency_days INTEGER, next_due DATE, last_completed DATE, status VARCHAR(16), creator VARCHAR(64), timestamp TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS pet (id INTEGER PRIMARY KEY, name VARCHAR(128) NOT NULL, species VARCHAR(64), breed VARCHAR(128), icon VARCHAR(32), birth_date DATE, creator VARCHAR(64), timestamp TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS pet_care_event (id INTEGER PRIMARY KEY, pet_id INTEGER NOT NULL, event_type VARCHAR(64) NOT NULL, description TEXT, event_date DATE NOT NULL, next_due DATE, creator VARCHAR(64), timestamp TIMESTAMP, FOREIGN KEY(pet_id) REFERENCES pet(id))")
            cur.execute("CREATE TABLE IF NOT EXISTS countdown (id INTEGER PRIMARY KEY, event_name VARCHAR(256) NOT NULL, event_date DATE NOT NULL, icon VARCHAR(32), description TEXT, creator VARCHAR(64), timestamp TIMESTAMP)")

            conn.commit()
            conn.close()
        except Exception:
            # Best-effort; ignore if anything goes wrong
            pass

        # Seed initial users from config (admin + family members)
        try:
            cfg = app.config['HOMEHUB_CONFIG']
            admin_name = cfg.get('admin_name', 'Administrator')
            family_members = cfg.get('family_members', [])

            # Create admin user if doesn't exist
            admin_user = User.query.filter_by(username=admin_name).first()
            if not admin_user:
                admin_user = User(
                    username=admin_name,
                    is_admin=True,
                    password_set=False
                )
                admin_user.set_password('temp')  # Temporary password, will be changed on first login
                admin_user.password_set = False  # Override the flag set by set_password()
                db.session.add(admin_user)

            # Create family member users if they don't exist
            for member in family_members:
                if not User.query.filter_by(username=member).first():
                    user = User(
                        username=member,
                        is_admin=False,
                        password_set=False
                    )
                    user.set_password('temp')  # Temporary password
                    user.password_set = False  # Override the flag set by set_password()
                    db.session.add(user)

            db.session.commit()
        except Exception:
            # Best-effort; ignore if anything goes wrong
            pass

    from .routes import main_bp
    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_auth_state():
        return {
            'is_authed': bool(session.get('authed'))
        }

    return app
