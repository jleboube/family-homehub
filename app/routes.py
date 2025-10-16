from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, current_app, session
from .config import load_config
from threading import Thread
from .models import db, Note, File, Media, PDF, ShoppingItem, GroceryHistory, HomeStatus, Chore, Recipe, ExpiryItem, ShortURL, QRCode, Notice, Reminder, MemberStatus, RecurringExpense, ExpenseEntry, BitwardenVault, User, Photo, MealPlan, FavoriteMeal, MaintenanceTask, Pet, PetCareEvent, Countdown
from .utils import generate_short_code
import os
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import calendar as _calendar
import bleach
import json
import secrets
import time

main_bp = Blueprint('main', __name__)

ALLOWED_TAGS = ['b', 'i', 'u', 'a']
ALLOWED_ATTRIBUTES = {'a': ['href', 'title']}

@main_bp.before_app_request
def reload_config_and_auth():
    # Always reload config to reflect changes to config.yml without rebuilding
    try:
        current_app.config['HOMEHUB_CONFIG'] = load_config()
    except Exception:
        pass

    endpoint = request.endpoint or ''

    # Skip authentication for static files and login/setup routes
    if endpoint.startswith('static') or endpoint in ('main.login', 'main.setup_password'):
        return

    # Skip authentication for chess game links with game_id (remote multiplayer)
    if endpoint == 'main.chess_game' and request.args.get('game_id'):
        return

    # Skip authentication for chess API endpoints (remote multiplayer)
    if endpoint in ('main.create_remote_chess_game', 'main.get_chess_game', 'main.submit_chess_move'):
        return

    # Check if user is authenticated
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('main.login'))

    # Verify user still exists and load into g
    from flask import g
    user = User.query.get(user_id)
    if not user:
        session.clear()
        return redirect(url_for('main.login'))

    g.current_user = user

    # Check if user needs to set password (first time)
    if not user.password_set and endpoint != 'main.setup_password':
        return redirect(url_for('main.setup_password'))

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
PDF_FOLDER = os.path.join(BASE_DIR, 'pdfs')
GAMES_FOLDER = os.path.join(BASE_DIR, 'games')

@main_bp.route('/')
def index():
    config = current_app.config['HOMEHUB_CONFIG']
    notice = Notice.query.order_by(Notice.updated_at.desc()).first()
    # Calendar: gather reminders grouped by date
    # Use with_entities to avoid passing ORM models around accidentally
    try:
        # Include time and category so initial month cache has full data
        rows = Reminder.query.with_entities(
            Reminder.id,
            Reminder.title,
            Reminder.description,
            Reminder.creator,
            Reminder.date,
            Reminder.time,
            Reminder.category
        ).all()
    except Exception:
        rows = []
    by_date = {}
    for rid, title, description, creator, rdate, rtime, rcat in rows:
        try:
            key = rdate.strftime('%Y-%m-%d')
        except Exception:
            # Fallback if rdate is already a string or None
            key = str(rdate) if rdate else ''
        by_date.setdefault(key, []).append({
            'id': int(rid),
            'title': title or '',
            'description': description or '',
            'creator': creator or '',
            'time': rtime or None,
            'category': rcat or None,
        })
    # Serialize once server-side to avoid Jinja tojson on ORM-related objects
    import json
    try:
        reminders_json = json.dumps(by_date)
    except Exception:
        reminders_json = '{}'
    # Who is Home summary
    family = list(dict.fromkeys(config.get('family_members', [])))
    who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
    member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
    # Get configurable who_is_home status options (with defaults if not configured)
    who_status_options = config.get('who_is_home_statuses', ['Home', 'Away', 'Out', 'Traveling'])
    # Extract reminder categories (config structure: reminders: { categories: [ {key,label,color}, ... ] })
    reminder_categories = []
    try:
        rcfg = (config.get('reminders') or {}).get('categories') or []
        if isinstance(rcfg, list):
            for entry in rcfg:
                if not isinstance(entry, dict):
                    continue
                key = entry.get('key')
                if not key:
                    continue
                reminder_categories.append({
                    'key': key,
                    'label': entry.get('label') or key,
                    'color': entry.get('color') or None
                })
    except Exception:
        reminder_categories = []
    return render_template('index.html', config=config, notice=notice, reminders_json=reminders_json, who_statuses=who_statuses, member_statuses=member_statuses, reminder_categories=reminder_categories, who_status_options=who_status_options)

# ---------------------- API (Phase 2) Reminders ----------------------

def serialize_reminder(r: Reminder):
    return {
        'id': r.id,
        'date': r.date.strftime('%Y-%m-%d') if r.date else None,
        'time': getattr(r, 'time', None) or None,
        'duration': getattr(r, 'duration', None),
        'title': r.title,
        'description': r.description or '',
        'creator': r.creator or '',
        'category': getattr(r, 'category', None),
        'color': getattr(r, 'color', None),
        'timestamp': r.timestamp.isoformat() if r.timestamp else None,
        'updated_at': getattr(r, 'updated_at', None).isoformat() if getattr(r, 'updated_at', None) else None,
    }

def parse_date_param(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return default

@main_bp.route('/api/reminders')
def api_reminders_list():
    """List reminders by scope (day|week|month). Default day of supplied date or today."""
    scope = request.args.get('scope', 'day').lower()
    date_s = request.args.get('date')
    base_date = parse_date_param(date_s, date.today())
    q = Reminder.query
    # For month scope fetch whole month
    if scope == 'month':
        start = base_date.replace(day=1)
        # naive month end
        if start.month == 12:
            next_month = start.replace(year=start.year+1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month+1, day=1)
        end = next_month - timedelta(days=1)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    elif scope == 'week':
        # ISO week start (Monday)
        start = base_date - timedelta(days=base_date.weekday())
        end = start + timedelta(days=6)
        q = q.filter(Reminder.date >= start, Reminder.date <= end)
    else:  # day
        q = q.filter(Reminder.date == base_date)
    # Order by date then time (placing NULL/blank times last) then id
    try:
        from sqlalchemy import case
        rows = q.order_by(
            Reminder.date.asc(),
            case((Reminder.time.is_(None), 1), (Reminder.time == '', 1), else_=0).asc(),  # noqa: E711
            Reminder.time.asc(),
            Reminder.id.asc()
        ).all()
    except Exception:
        rows = q.order_by(Reminder.date.asc(), Reminder.id.asc()).all()
    data = [serialize_reminder(r) for r in rows]
    # Aggregates for month scope (counts per day + per-category counts)
    counts = {}
    categories_counts = {}
    if scope == 'month':
        for r in rows:
            k = r.date.strftime('%Y-%m-%d')
            counts[k] = counts.get(k, 0) + 1
            cat = getattr(r, 'category', None) or '_uncategorized'
            if k not in categories_counts:
                categories_counts[k] = {}
            categories_counts[k][cat] = categories_counts[k].get(cat, 0) + 1
    return jsonify({
        'ok': True,
        'scope': scope,
        'date': base_date.strftime('%Y-%m-%d'),
        'reminders': data,
        'counts': counts,
        'categories_counts': categories_counts
    })

@main_bp.route('/api/reminders', methods=['POST'])
def api_reminders_create():
    payload = request.get_json(silent=True) or {}
    title = bleach.clean(payload.get('title', '')).strip()
    date_s = payload.get('date')
    creator = bleach.clean(payload.get('creator', '')).strip()
    description = bleach.clean(payload.get('description', ''), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)

    # Check calendar write permission
    user = User.query.filter_by(username=creator).first()
    if not user or (not user.is_admin and not user.calendar_write_enabled):
        return jsonify({'ok': False, 'error': 'Calendar write permission required'}), 403

    if not title:
        return jsonify({'ok': False, 'error': 'Title required'}), 400
    d = parse_date_param(date_s, None)
    if not d:
        return jsonify({'ok': False, 'error': 'Invalid date'}), 400
    time_raw = payload.get('time')
    tval = None
    if isinstance(time_raw, str) and len(time_raw) == 5 and time_raw[2] == ':':
        hh, mm = time_raw.split(':', 1)
        if hh.isdigit() and mm.isdigit():
            hhi, mmi = int(hh), int(mm)
            if 0 <= hhi < 24 and 0 <= mmi < 60:
                tval = f"{hhi:02d}:{mmi:02d}"
    # Parse duration (in minutes)
    duration_raw = payload.get('duration')
    duration = None
    if duration_raw is not None:
        try:
            duration = int(duration_raw)
            if duration < 0:
                duration = None
        except (ValueError, TypeError):
            duration = None
    r = Reminder(date=d, title=title, description=description, creator=creator, time=tval)
    # Optional fields (category/color/duration)
    cat = payload.get('category'); col = payload.get('color')
    if hasattr(r, 'category'):
        r.category = bleach.clean(cat) if cat else None
    if hasattr(r, 'color'):
        r.color = bleach.clean(col) if col else None
    if hasattr(r, 'duration'):
        r.duration = duration
    db.session.add(r)
    db.session.commit()
    return jsonify({'ok': True, 'reminder': serialize_reminder(r)})

@main_bp.route('/api/reminders/<int:rid>', methods=['PATCH'])
def api_reminders_update(rid):
    r = Reminder.query.get_or_404(rid)
    payload = request.get_json(silent=True) or {}
    username = bleach.clean(payload.get('creator', ''))

    # Check calendar write permission
    user_obj = User.query.filter_by(username=username).first()
    if not user_obj or (not user_obj.is_admin and not user_obj.calendar_write_enabled):
        return jsonify({'ok': False, 'error': 'Calendar write permission required'}), 403

    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if username not in admin_aliases and username != (r.creator or ''):
        return jsonify({'ok': False, 'error': 'Not allowed'}), 403
    if 'title' in payload:
        title = bleach.clean(payload['title']).strip()
        if title:
            r.title = title
    if 'description' in payload:
        r.description = bleach.clean(payload['description'], tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    if 'date' in payload:
        nd = parse_date_param(payload['date'], None)
        if nd:
            r.date = nd
    if hasattr(r, 'time') and 'time' in payload:
        time_raw = payload.get('time')
        if isinstance(time_raw, str) and len(time_raw) == 5 and time_raw[2] == ':':
            hh, mm = time_raw.split(':', 1)
            if hh.isdigit() and mm.isdigit():
                hhi, mmi = int(hh), int(mm)
                if 0 <= hhi < 24 and 0 <= mmi < 60:
                    r.time = f"{hhi:02d}:{mmi:02d}"
    if hasattr(r, 'duration') and 'duration' in payload:
        duration_raw = payload.get('duration')
        if duration_raw is not None:
            try:
                duration = int(duration_raw)
                r.duration = duration if duration >= 0 else None
            except (ValueError, TypeError):
                r.duration = None
        else:
            r.duration = None
    if hasattr(r, 'category') and 'category' in payload:
        r.category = bleach.clean(payload.get('category')) if payload.get('category') else None
    if hasattr(r, 'color') and 'color' in payload:
        r.color = bleach.clean(payload.get('color')) if payload.get('color') else None
    db.session.commit()
    return jsonify({'ok': True, 'reminder': serialize_reminder(r)})

@main_bp.route('/api/reminders', methods=['DELETE'])
def api_reminders_delete_bulk():
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids') or []
    username = bleach.clean(payload.get('creator', ''))

    # Check calendar write permission
    user_obj = User.query.filter_by(username=username).first()
    if not user_obj or (not user_obj.is_admin and not user_obj.calendar_write_enabled):
        return jsonify({'ok': False, 'error': 'Calendar write permission required'}), 403

    if not isinstance(ids, list) or not ids:
        return jsonify({'ok': False, 'error': 'No ids provided'}), 400
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    deleted = 0
    dates = set()
    for rid in ids:
        if not isinstance(rid, int):
            continue
        r = Reminder.query.get(rid)
        if not r:
            continue
        if username in admin_aliases or username == (r.creator or ''):
            if r.date:
                dates.add(r.date.strftime('%Y-%m-%d'))
            db.session.delete(r)
            deleted += 1
    if deleted:
        db.session.commit()
    return jsonify({'ok': True, 'deleted': deleted, 'dates': list(dates)})

@main_bp.route('/login', methods=['GET', 'POST'])
def login():
    config = current_app.config['HOMEHUB_CONFIG']

    # If already logged in, redirect to home
    if session.get('user_id'):
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        username = bleach.clean(request.form.get('username', ''))
        password = request.form.get('password', '')

        if not username:
            flash('Please select a username', 'error')
            return render_template('login.html', config=config, hide_user_ui=True)

        user = User.query.filter_by(username=username).first()

        if not user:
            flash('Invalid username or password', 'error')
            return render_template('login.html', config=config, hide_user_ui=True)

        # Check if user needs to set password for first time
        if not user.password_set:
            session['setup_user_id'] = user.id
            return redirect(url_for('main.setup_password'))

        # For existing users with passwords, validate password was provided
        if not password:
            flash('Please enter your password', 'error')
            return render_template('login.html', config=config, hide_user_ui=True)

        # Verify password
        if not user.check_password(password):
            flash('Invalid username or password', 'error')
            return render_template('login.html', config=config, hide_user_ui=True)

        # Successful login
        session.permanent = True  # Use permanent session (respects PERMANENT_SESSION_LIFETIME)
        session['authed'] = True
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_admin'] = user.is_admin
        user.last_login = datetime.utcnow()
        db.session.commit()

        flash(f'Welcome back, {user.username}!', 'success')
        return redirect(url_for('main.index'))

    return render_template('login.html', config=config, hide_user_ui=True)

@main_bp.route('/setup-password', methods=['GET', 'POST'])
def setup_password():
    config = current_app.config['HOMEHUB_CONFIG']

    # Check if user is in setup mode
    setup_user_id = session.get('setup_user_id')
    if not setup_user_id:
        # Check if already logged in and needs password update
        user_id = session.get('user_id')
        if user_id:
            user = User.query.get(user_id)
            if user and not user.password_set:
                setup_user_id = user_id
            else:
                return redirect(url_for('main.index'))
        else:
            return redirect(url_for('main.login'))

    user = User.query.get(setup_user_id)
    if not user:
        session.clear()
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not password or len(password) < 8:
            flash('Password must be at least 8 characters long', 'error')
            return render_template('setup_password.html', username=user.username, is_admin=user.is_admin, config=config, hide_user_ui=True)

        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('setup_password.html', username=user.username, is_admin=user.is_admin, config=config, hide_user_ui=True)

        # Set password
        user.set_password(password)
        db.session.commit()

        # Log the user in
        session.pop('setup_user_id', None)
        session['authed'] = True
        session['user_id'] = user.id
        session['username'] = user.username
        session['is_admin'] = user.is_admin
        user.last_login = datetime.utcnow()
        db.session.commit()

        flash(f'Password set successfully! Welcome to {config.get("instance_name", "HomeHub")}, {user.username}!', 'success')

        # All users go to Welcome page after setting password
        return redirect(url_for('main.index'))

    return render_template('setup_password.html', username=user.username, is_admin=user.is_admin, config=config, hide_user_ui=True)

@main_bp.route('/logout')
def logout():
    username = session.get('username', 'User')
    session.clear()
    flash(f'Goodbye, {username}! You have been logged out.', 'info')
    return redirect(url_for('main.login'))

@main_bp.route('/admin/reset-password', methods=['GET', 'POST'])
def admin_reset_password():
    config = current_app.config['HOMEHUB_CONFIG']
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    users = User.query.filter_by(is_admin=False).all()

    if request.method == 'POST':
        target_username = bleach.clean(request.form.get('username', ''))
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not target_username or not new_password:
            flash('Username and password are required', 'error')
            return render_template('admin_reset_password.html', users=users, config=config)

        if len(new_password) < 8:
            flash('Password must be at least 8 characters', 'error')
            return render_template('admin_reset_password.html', users=users, config=config)

        if new_password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('admin_reset_password.html', users=users, config=config)

        target_user = User.query.filter_by(username=target_username).first()
        if not target_user:
            flash('User not found', 'error')
            return render_template('admin_reset_password.html', users=users, config=config)

        if target_user.is_admin and target_user.id != g.current_user.id:
            flash('Cannot reset another administrator\'s password', 'error')
            return render_template('admin_reset_password.html', users=users, config=config)

        # Reset password
        target_user.set_password(new_password)
        db.session.commit()

        flash(f'Password reset successfully for {target_username}', 'success')
        return redirect(url_for('main.admin_reset_password'))

    return render_template('admin_reset_password.html', users=users, config=config)

# Shared Notes
@main_bp.route('/notes', methods=['GET', 'POST'])
def notes():
    if request.method == 'POST':
        note_id = request.form.get('note_id')
        content = bleach.clean(request.form['content'], tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        creator = bleach.clean(request.form['creator'])
        if note_id:
            n = Note.query.get_or_404(int(note_id))
            # allow edit by admin or creator
            admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
            admin_aliases = {admin_name, 'Administrator', 'admin'}
            if creator in admin_aliases or creator == n.creator:
                n.content = content
                db.session.commit()
        else:
            note = Note(content=content, creator=creator)
            db.session.add(note)
            db.session.commit()
        return redirect(url_for('main.notes'))
    notes = Note.query.order_by(Note.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('notes.html', notes=notes, config=config)

@main_bp.route('/notes/delete/<int:note_id>', methods=['POST'])
def delete_note(note_id):
    note = Note.query.get_or_404(note_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == note.creator:
        db.session.delete(note)
        db.session.commit()
    return redirect(url_for('main.notes'))

# File Uploader
@main_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        files = request.files.getlist('files') or ([request.files['file']] if 'file' in request.files else [])
        creator = bleach.clean(request.form['creator'])
        for file in files:
            if not file or not getattr(file, 'filename', ''):
                continue
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_FOLDER, filename))
            db_file = File(filename=filename, creator=creator)
            db.session.add(db_file)
        db.session.commit()
        return redirect(url_for('main.upload'))
    files = File.query.order_by(File.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('upload.html', files=files, config=config)

@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@main_bp.route('/upload/delete/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    db_file = File.query.get_or_404(file_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == db_file.creator:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, db_file.filename))
        except Exception:
            pass
        db.session.delete(db_file)
        db.session.commit()
    return redirect(url_for('main.upload'))

# Shopping List
@main_bp.route('/shopping', methods=['GET', 'POST'])
def shopping():
    if request.method == 'POST':
        item = bleach.clean(request.form['item'])
        creator = bleach.clean(request.form['creator'])
        shopping_item = ShoppingItem(item=item, creator=creator)
        db.session.add(shopping_item)
        # Log to grocery history for suggestions
        db.session.add(GroceryHistory(item=item, creator=creator))
        db.session.commit()
        return redirect(url_for('main.shopping'))
    items = ShoppingItem.query.order_by(ShoppingItem.timestamp.desc()).all()
    # Suggestion logic: top 10 most frequent items in last 90 days not already on list
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=90)
    existing = {i.item.lower() for i in items}
    rows = db.session.execute(db.text("""
        SELECT item, COUNT(*) as cnt
        FROM grocery_history
        WHERE timestamp >= :cutoff
        GROUP BY item
        ORDER BY cnt DESC
        LIMIT 20
    """), {"cutoff": cutoff}).fetchall()
    suggestions = [r[0] for r in rows if r[0].lower() not in existing][:10]
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shopping.html', items=items, suggestions=suggestions, config=config)

# Expense Tracker
@main_bp.route('/expenses', methods=['GET', 'POST'])
def expenses():
    # Generate recurring entries up to today
    today = date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        # Determine next date to generate
        start = r.start_date or today
        # For monthly same-day mode, base day should be from start_date to preserve intent
        base_day = (r.start_date or today).day
        last = r.last_generated_date  # may be None
        # Iterate dates based on frequency
        def next_date(d):
            if r.frequency == 'daily':
                return d + timedelta(days=1)
            if r.frequency == 'weekly':
                return d + timedelta(weeks=1)
            # monthly: honor monthly_mode
            mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
            if mode == 'calendar':
                # jump to first day of next month
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                return date(ny, nm, 1)
            else:
                # same day-of-month next month (clamped to last day)
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                last_dom = _calendar.monthrange(ny, nm)[1]
                day = min(base_day, last_dom)
                return date(ny, nm, day)
        # Seed generation correctly for each frequency
        if last is None or (last and last < start):
            # Treat as fresh generation starting from start date
            if r.frequency == 'daily':
                d = start
            elif r.frequency == 'weekly':
                d = start
            else:  # monthly
                mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
                if mode == 'calendar':
                    # If start is on the 1st, include it; otherwise begin on first day of next month
                    if start.day == 1:
                        d = start
                    else:
                        ny = start.year + (1 if start.month == 12 else 0)
                        nm = 1 if start.month == 12 else start.month + 1
                        d = date(ny, nm, 1)
                else:
                    d = start
        else:
            # Continue from last generated date
            d = next_date(last)
        while d <= today and (not r.end_date or d <= r.end_date):
            # only create if not already present
            exists = ExpenseEntry.query.filter_by(date=d, recurring_id=r.id).first()
            if not exists:
                qty = r.default_quantity or 1.0
                amt = (r.unit_price or 0.0) * qty
                db.session.add(ExpenseEntry(date=d, title=r.title, category=getattr(r, 'category', None), unit_price=r.unit_price, quantity=qty, amount=amt, payer=r.creator, recurring_id=r.id))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()

    # Handle add entry
    if request.method == 'POST':
        # Two forms: new entry or new recurring
        form_type = request.form.get('form_type')
        if form_type == 'recurring':
            title = bleach.clean(request.form.get('title',''))
            unit_price = float(request.form.get('unit_price') or 0)
            default_quantity = float(request.form.get('default_quantity') or 1)
            frequency = bleach.clean(request.form.get('frequency','daily'))
            monthly_mode = bleach.clean(request.form.get('monthly_mode','day_of_month'))
            category = bleach.clean(request.form.get('category',''))
            start_date = request.form.get('start_date')
            end_date = request.form.get('end_date')
            creator = bleach.clean(request.form.get('creator',''))
            sd = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else date.today()
            ed = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
            db.session.add(RecurringExpense(title=title, unit_price=unit_price, default_quantity=default_quantity, frequency=frequency, monthly_mode=monthly_mode, category=category, start_date=sd, end_date=ed, creator=creator))
            db.session.commit()
            flash('Recurring expense added.', 'success')
            # Preserve view state if provided
            y = request.args.get('y') or today.year
            m = request.args.get('m') or today.month
            sel = request.args.get('sel')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))
        else:
            title = bleach.clean(request.form.get('title',''))
            amount = float(request.form.get('amount') or 0)
            category = bleach.clean(request.form.get('category') or '')
            payer = bleach.clean(request.form.get('payer') or '')
            date_s = request.form.get('date')
            d = datetime.strptime(date_s, '%Y-%m-%d').date() if date_s else date.today()
            unit_price = request.form.get('unit_price'); quantity = request.form.get('quantity')
            up = float(unit_price) if unit_price else None
            q = float(quantity) if quantity else None
            db.session.add(ExpenseEntry(date=d, title=title, category=category, unit_price=up, quantity=q, amount=amount, payer=payer))
            db.session.commit()
            flash('Expense added.', 'success')
            # Preserve view state if provided, else default to the added date
            y = request.args.get('y') or d.year
            m = request.args.get('m') or d.month
            sel = request.args.get('sel') or d.strftime('%Y-%m-%d')
            return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

    # Compute month to show (defaults to current month, allow query params)
    try:
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
    except Exception:
        y, m = today.year, today.month
    month_start = date(y, m, 1)
    last_day = _calendar.monthrange(y, m)[1]
    month_end = date(y, m, last_day)

    q_entries = ExpenseEntry.query.filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end).order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc()).all()
    # Prepare data structure for client rendering
    by_date = {}
    total = 0.0
    per_payer = {}
    per_category = {}
    for e in q_entries:
        ds = e.date.strftime('%Y-%m-%d')
        by_date.setdefault(ds, {'total': 0.0, 'entries': []})
        by_date[ds]['total'] += float(e.amount or 0)
        total += float(e.amount or 0)
        per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + float(e.amount or 0)
        if e.category:
            per_category[e.category] = per_category.get(e.category, 0.0) + float(e.amount or 0)
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': float(e.amount or 0),
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring': bool(e.recurring_id),
            'payer': e.payer or ''
        })
    # Determine top category name
    top_category = None
    if per_category:
        top_category = max(per_category.items(), key=lambda kv: kv[1])[0]

    rules = RecurringExpense.query.order_by(RecurringExpense.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    import json
    # Settings
    settings = {'currency': '₹', 'categories': []}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories')"))
        data = {k: v for k, v in rows}
        if data.get('currency'): settings['currency'] = data['currency']
        if data.get('categories'): settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
    except Exception:
        pass
    payload = {
        'by_date': by_date,
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_category': per_category,
            'top_category': top_category
        },
        'year': y,
        'month': m,
        'settings': settings
    }
    return render_template('expenses.html', rules=rules, config=config, expenses_json=json.dumps(payload))

@main_bp.route('/expenses/delete/<int:entry_id>', methods=['POST'])
def delete_expense(entry_id):
    e = ExpenseEntry.query.get_or_404(entry_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == e.payer:
        db.session.delete(e)
        db.session.commit()
    # Preserve view; if args missing, fall back to the deleted entry's date
    y = request.args.get('y') or (e.date.year if e.date else date.today().year)
    m = request.args.get('m') or (e.date.month if e.date else date.today().month)
    sel = request.args.get('sel') or (e.date.strftime('%Y-%m-%d') if e.date else None)
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

@main_bp.route('/expenses/recurring/delete/<int:rec_id>', methods=['POST'])
def delete_recurring(rec_id):
    r = RecurringExpense.query.get_or_404(rec_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == r.creator:
        # If requested, delete all generated entries for this rule
        if request.form.get('delete_entries'):
            try:
                entries = ExpenseEntry.query.filter_by(recurring_id=r.id).all()
                for e in entries:
                    db.session.delete(e)
            except Exception:
                pass
        db.session.delete(r)
        db.session.commit()
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Edit Expense (admin or owner)
@main_bp.route('/expenses/edit/<int:entry_id>', methods=['POST'])
def edit_expense(entry_id):
    e = ExpenseEntry.query.get_or_404(entry_id)
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user not in admin_aliases and user != (e.payer or ''):
        flash('Not allowed to edit this expense.', 'error')
        return redirect(url_for('main.expenses'))
    # Update allowed fields
    date_s = request.form.get('date')
    if date_s:
        try:
            e.date = datetime.strptime(date_s, '%Y-%m-%d').date()
        except Exception:
            pass
    title = request.form.get('title'); category = request.form.get('category')
    unit_price = request.form.get('unit_price'); quantity = request.form.get('quantity'); amount = request.form.get('amount')
    payer = request.form.get('payer')
    if title is not None: e.title = bleach.clean(title)
    if category is not None: e.category = bleach.clean(category)
    if unit_price is not None and unit_price != '': e.unit_price = float(unit_price)
    if quantity is not None and quantity != '': e.quantity = float(quantity)
    if amount is not None and amount != '': e.amount = float(amount)
    if payer is not None: e.payer = bleach.clean(payer)
    db.session.commit()
    flash('Expense updated.', 'success')
    y = request.args.get('y') or e.date.year
    m = request.args.get('m') or e.date.month
    sel = request.args.get('sel') or e.date.strftime('%Y-%m-%d')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# JSON API for monthly expenses
@main_bp.route('/api/expenses/month', methods=['GET'])
def api_expenses_month():
    # Ensure recurring generation has run recently
    today = date.today()
    recs = RecurringExpense.query.all()
    for r in recs:
        start = r.start_date or today
        last = r.last_generated_date
        def next_date(d):
            if r.frequency == 'daily':
                return d + timedelta(days=1)
            if r.frequency == 'weekly':
                return d + timedelta(weeks=1)
            mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
            if mode == 'calendar':
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                return date(ny, nm, 1)
            else:
                base_day = (r.start_date or today).day
                ny = d.year + (1 if d.month == 12 else 0)
                nm = 1 if d.month == 12 else d.month + 1
                last = _calendar.monthrange(ny, nm)[1]
                day = min(base_day, last)
                return date(ny, nm, day)
        # Seed similarly to page route
        if last is None or (last and last < start):
            if r.frequency == 'daily':
                d = start
            elif r.frequency == 'weekly':
                d = start
            else:
                mode = getattr(r, 'monthly_mode', 'day_of_month') or 'day_of_month'
                if mode == 'calendar':
                    if start.day == 1:
                        d = start
                    else:
                        ny = start.year + (1 if start.month == 12 else 0)
                        nm = 1 if start.month == 12 else start.month + 1
                        d = date(ny, nm, 1)
                else:
                    d = start
        else:
            d = next_date(last)
        while d <= today and (not r.end_date or d <= r.end_date):
            exists = ExpenseEntry.query.filter_by(date=d, recurring_id=r.id).first()
            if not exists:
                qty = r.default_quantity or 1.0
                amt = (r.unit_price or 0.0) * qty
                db.session.add(ExpenseEntry(date=d, title=r.title, category=getattr(r, 'category', None), unit_price=r.unit_price, quantity=qty, amount=amt, payer=r.creator, recurring_id=r.id))
            r.last_generated_date = d
            d = next_date(d)
    db.session.commit()

    try:
        y = int(request.args.get('year') or today.year)
        m = int(request.args.get('month') or today.month)
    except Exception:
        y, m = today.year, today.month
    month_start = date(y, m, 1)
    last_day = _calendar.monthrange(y, m)[1]
    month_end = date(y, m, last_day)

    q_entries = ExpenseEntry.query.filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end).order_by(ExpenseEntry.date.asc(), ExpenseEntry.timestamp.asc()).all()
    by_date = {}
    total = 0.0
    per_payer = {}
    per_category = {}
    for e in q_entries:
        ds = e.date.strftime('%Y-%m-%d')
        by_date.setdefault(ds, {'total': 0.0, 'entries': []})
        by_date[ds]['total'] += float(e.amount or 0)
        total += float(e.amount or 0)
        per_payer[e.payer or ''] = per_payer.get(e.payer or '', 0.0) + float(e.amount or 0)
        if e.category:
            per_category[e.category] = per_category.get(e.category, 0.0) + float(e.amount or 0)
        by_date[ds]['entries'].append({
            'id': e.id,
            'title': e.title,
            'category': e.category,
            'unit_price': float(e.unit_price) if e.unit_price is not None else None,
            'amount': float(e.amount or 0),
            'quantity': float(e.quantity or 0) if e.quantity is not None else None,
            'recurring': bool(e.recurring_id),
            'payer': e.payer or ''
        })
    top_category = None
    if per_category:
        top_category = max(per_category.items(), key=lambda kv: kv[1])[0]
    # Settings
    settings = {'currency': '₹', 'categories': []}
    try:
        rows = db.session.execute(db.text("SELECT key, value FROM app_setting WHERE key IN ('currency','categories')"))
        data = {k: v for k, v in rows}
        if data.get('currency'): settings['currency'] = data['currency']
        if data.get('categories'): settings['categories'] = [c.strip() for c in data['categories'].split(',') if c.strip()]
    except Exception:
        pass
    return jsonify({
        'by_date': by_date,
        'summary': {
            'total_this_month': total,
            'per_payer': per_payer,
            'per_category': per_category,
            'top_category': top_category
        },
        'year': y,
        'month': m,
        'settings': settings
    })

# Bulk delete expenses (admin or owners for each)
@main_bp.route('/expenses/bulk-delete', methods=['POST'])
def bulk_delete_expenses():
    ids = request.form.getlist('ids')
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    deleted = 0
    for sid in ids:
        try:
            eid = int(sid)
        except Exception:
            continue
        e = ExpenseEntry.query.get(eid)
        if not e:
            continue
        if user in admin_aliases or user == (e.payer or ''):
            db.session.delete(e)
            deleted += 1
    if deleted:
        db.session.commit()
        flash(f'Deleted {deleted} expense(s).', 'success')
    else:
        flash('No expenses deleted (not allowed or invalid IDs).', 'error')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Edit recurring rule (admin or creator)
@main_bp.route('/expenses/recurring/edit/<int:rec_id>', methods=['POST'])
def edit_recurring(rec_id):
    r = RecurringExpense.query.get_or_404(rec_id)
    user = bleach.clean(request.form.get('user',''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user not in admin_aliases and user != (r.creator or ''):
        flash('Not allowed to edit this rule.', 'error')
        return redirect(url_for('main.expenses'))
    # Update fields
    title = request.form.get('title'); unit_price = request.form.get('unit_price'); default_quantity = request.form.get('default_quantity')
    category = request.form.get('category')
    frequency = request.form.get('frequency'); monthly_mode = request.form.get('monthly_mode')
    start_date = request.form.get('start_date'); end_date = request.form.get('end_date')
    if title is not None: r.title = bleach.clean(title)
    if unit_price not in (None, ''): r.unit_price = float(unit_price)
    if default_quantity not in (None, ''): r.default_quantity = float(default_quantity)
    if category is not None: r.category = bleach.clean(category)
    if frequency is not None: r.frequency = bleach.clean(frequency)
    if monthly_mode is not None: r.monthly_mode = bleach.clean(monthly_mode)
    if start_date: r.start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
    if end_date: r.end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    # Also update existing entries generated by this rule to reflect changed fields
    entries = ExpenseEntry.query.filter_by(recurring_id=r.id).all()
    for e in entries:
        # Keep date and payer/amount integrity, but recompute amount if unit price or quantity changed
        e.title = r.title
        e.category = getattr(r, 'category', None)
        # If the rule defines a unit_price/quantity, propagate and recompute amount; else preserve existing
        if r.unit_price is not None:
            e.unit_price = r.unit_price
        if r.default_quantity is not None:
            e.quantity = r.default_quantity
        if e.unit_price is not None and e.quantity is not None:
            try:
                e.amount = float(e.unit_price) * float(e.quantity)
            except Exception:
                pass
    # Align last_generated_date to latest existing generated entry after edits
    try:
        if entries:
            r.last_generated_date = max(e.date for e in entries if getattr(e, 'date', None))
        else:
            r.last_generated_date = None
    except Exception:
        pass
    db.session.commit()
    flash('Recurring rule updated.', 'success')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

# Settings endpoints (currency, categories)
@main_bp.route('/expenses/settings', methods=['POST'])
def save_expense_settings():
    # Only admin can change app-wide settings
    user = bleach.clean(request.form.get('user',''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name and user not in {'Administrator', 'admin'}:
        flash('Only admin can update settings.', 'error')
        return redirect(url_for('main.expenses'))
    currency = bleach.clean(request.form.get('currency','₹'))
    categories = bleach.clean(request.form.get('categories',''))  # comma-separated
    from sqlalchemy import text as _text
    db.session.execute(_text("REPLACE INTO app_setting(key, value) VALUES('currency', :v)"), { 'v': currency })
    db.session.execute(_text("REPLACE INTO app_setting(key, value) VALUES('categories', :v)"), { 'v': categories })
    db.session.commit()
    flash('Settings saved.', 'success')
    y = request.args.get('y') or date.today().year
    m = request.args.get('m') or date.today().month
    sel = request.args.get('sel')
    return redirect(url_for('main.expenses', y=y, m=m, sel=sel))

@main_bp.route('/shopping/check/<int:item_id>', methods=['POST'])
def check_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    item.checked = not item.checked
    db.session.commit()
    return redirect(url_for('main.shopping'))

@main_bp.route('/shopping/delete/<int:item_id>', methods=['POST'])
def delete_shopping(item_id):
    item = ShoppingItem.query.get_or_404(item_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == item.creator:
        db.session.delete(item)
        db.session.commit()
    return redirect(url_for('main.shopping'))

# Deprecated: Dedicated Who is Home page has been removed in favor of dashboard controls.

# To-Do/Chore List
@main_bp.route('/chores', methods=['GET', 'POST'])
def chores():
    if request.method == 'POST':
        description = bleach.clean(request.form['description'])
        creator = bleach.clean(request.form['creator'])
        chore = Chore(description=description, creator=creator)
        db.session.add(chore)
        db.session.commit()
        return redirect(url_for('main.chores'))
    chores = Chore.query.order_by(Chore.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('chores.html', chores=chores, config=config)

@main_bp.route('/chores/toggle/<int:chore_id>', methods=['POST'])
def toggle_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    chore.done = not getattr(chore, 'done', False)
    db.session.commit()
    return redirect(url_for('main.chores'))

@main_bp.route('/chores/delete/<int:chore_id>', methods=['POST'])
def delete_chore(chore_id):
    chore = Chore.query.get_or_404(chore_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == chore.creator:
        db.session.delete(chore)
        db.session.commit()
    return redirect(url_for('main.chores'))

# Recipe Book
@main_bp.route('/recipes', methods=['GET', 'POST'])
def recipes():
    if request.method == 'POST':
        title = bleach.clean(request.form['title'])
        link = bleach.clean(request.form.get('link'))
        ingredients = bleach.clean(request.form.get('ingredients'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        instructions = bleach.clean(request.form.get('instructions'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
        creator = bleach.clean(request.form['creator'])
        if not (ingredients and ingredients.strip()) and not (instructions and instructions.strip()):
            flash('Please add ingredients or instructions (or both).', 'error')
            # render page without losing title/link fields
            recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes, config=config, form_title=title, form_link=link, form_ingredients=ingredients or '', form_instructions=instructions or '')
        recipe = Recipe(title=title, link=link, ingredients=ingredients, instructions=instructions, creator=creator)
        db.session.add(recipe)
        db.session.commit()
        return redirect(url_for('main.recipes'))
    recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('recipes.html', recipes=recipes, config=config)

@main_bp.route('/recipes/delete/<int:recipe_id>', methods=['POST'])
def delete_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == recipe.creator:
        db.session.delete(recipe)
        db.session.commit()
    return redirect(url_for('main.recipes'))

# Expiry Tracker
@main_bp.route('/expiry', methods=['GET', 'POST'])
def expiry():
    if request.method == 'POST':
        name = bleach.clean(request.form['name'])
        expiry_date = request.form['expiry_date']
        creator = bleach.clean(request.form['creator'])
        expiry_item = ExpiryItem(name=name, expiry_date=datetime.strptime(expiry_date, '%Y-%m-%d').date(), creator=creator)
        db.session.add(expiry_item)
        db.session.commit()
        return redirect(url_for('main.expiry'))
    items = ExpiryItem.query.order_by(ExpiryItem.expiry_date.asc()).all()
    today = date.today()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('expiry.html', items=items, today=today, config=config)

@main_bp.route('/expiry/delete/<int:item_id>', methods=['POST'])
def delete_expiry(item_id):
    it = ExpiryItem.query.get_or_404(item_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == it.creator:
        db.session.delete(it)
        db.session.commit()
    return redirect(url_for('main.expiry'))

# URL Shortener
@main_bp.route('/shorten', methods=['GET', 'POST'])
def shorten():
    if request.method == 'POST':
        original_url = bleach.clean(request.form['original_url'])
        creator = bleach.clean(request.form['creator'])
        short_code = generate_short_code()
        while ShortURL.query.filter_by(short_code=short_code).first():
            short_code = generate_short_code()
        short_url = ShortURL(original_url=original_url, short_code=short_code, creator=creator)
        db.session.add(short_url)
        db.session.commit()
        return redirect(url_for('main.shorten'))
    urls = ShortURL.query.order_by(ShortURL.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('shorten.html', urls=urls, config=config)

@main_bp.route('/s/<short_code>')
def redirect_short(short_code):
    short_url = ShortURL.query.filter_by(short_code=short_code).first_or_404()
    return redirect(short_url.original_url)

@main_bp.route('/shorten/delete/<int:url_id>', methods=['POST'])
def delete_short(url_id):
    su = ShortURL.query.get_or_404(url_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == su.creator:
        db.session.delete(su)
        db.session.commit()
    return redirect(url_for('main.shorten'))


# Media Downloader (yt-dlp integration)
@main_bp.route('/media', methods=['GET', 'POST'])
def media():
    import subprocess, re
    if request.method == 'POST':
        url = bleach.clean(request.form['url'])
        creator = bleach.clean(request.form['creator'])
        fmt = bleach.clean(request.form.get('format', 'mp4'))
        quality = bleach.clean(request.form.get('quality', 'best'))
        # Create a placeholder record marked pending
        base = f"media_{int(datetime.utcnow().timestamp())}"
        # Let yt-dlp append extension automatically
        output_tmpl = os.path.join(MEDIA_FOLDER, base + ".%(ext)s")
        media_obj = Media(title=url, url=url, creator=creator, filepath='', status='pending')
        db.session.add(media_obj)
        db.session.commit()
        flash('Download queued. You can switch tabs; refresh to check status.', 'info')
        # Build yt-dlp command
        cmd = ["yt-dlp", "-o", output_tmpl]
        if fmt == 'mp3':
            cmd += ["-x", "--audio-format", "mp3"]
        else:
            # Prefer bestvideo+bestaudio with mp4 fallback, honoring selected quality
            # Provide a sane default ladder if user selected shorthand like 'best'
            selected = quality or 'best'
            if selected == 'best':
                # best available up to original, prefer mp4 mux else fallback
                fmt_string = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
            else:
                # Use user-provided filter, but still add fallbacks and prefer mp4 container when possible
                fmt_string = f"{selected}/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best"
            cmd += ["-f", fmt_string]
            # Merge into mp4 when possible without re-encoding
            cmd += ["--merge-output-format", "mp4"]
        cmd += [url]

        # Capture the real app object now to use inside the background thread
        app_obj = current_app._get_current_object()

        def worker(app, mid: int, base_prefix: str, command: list):
            # Use the app's context explicitly inside the thread
            with app.app_context():
                m = Media.query.get(mid)
                try:
                    # Stream output to capture progress lines
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    last_percent = -1
                    for line in proc.stdout:
                        # Parse percent like: "[download]  12.3% of ..."
                        try:
                            m = Media.query.get(mid)
                            if not m:
                                continue
                            match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                            if match:
                                p = int(float(match.group(1)))
                                if p != last_percent and p % 5 == 0:
                                    m.progress = f"{p}%"
                                    db.session.commit()
                                    last_percent = p
                        except Exception:
                            pass
                    ret = proc.wait()
                    if ret != 0:
                        raise RuntimeError(f"yt-dlp exited with {ret}")
                    saved = None
                    for fname in os.listdir(MEDIA_FOLDER):
                        if fname.startswith(base_prefix):
                            saved = fname
                            break
                    m.filepath = saved or ''
                    m.status = 'done'
                except Exception:
                    m.status = 'error'
                finally:
                    m.progress = None
                    db.session.commit()

        Thread(target=worker, args=(app_obj, media_obj.id, base, cmd), daemon=True).start()
        return redirect(url_for('main.media'))
    media_list = Media.query.order_by(Media.download_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('media.html', media_list=media_list, config=config)

@main_bp.route('/media/status/<int:media_id>')
def media_status(media_id):
    m = Media.query.get_or_404(media_id)
    return jsonify({
        'status': m.status,
        'progress': m.progress,
        'filepath': m.filepath,
    })

# Calendar/Reminders
@main_bp.route('/calendar/add', methods=['POST'])
def add_reminder():
    date_s = bleach.clean(request.form.get('date'))
    title = bleach.clean(request.form.get('title'))
    description = bleach.clean(request.form.get('description'), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    creator = bleach.clean(request.form.get('creator'))

    # Check calendar write permission
    user = User.query.filter_by(username=creator).first()
    if not user or (not user.is_admin and not user.calendar_write_enabled):
        flash('You do not have permission to add calendar events.', 'error')
        return redirect(url_for('main.index'))

    if not (date_s and title):
        flash('Date and title are required for reminders.', 'error')
        return redirect(url_for('main.index'))
    try:
        d = datetime.strptime(date_s, '%Y-%m-%d').date()
    except Exception:
        flash('Invalid date.', 'error')
        return redirect(url_for('main.index'))
    r = Reminder(date=d, title=title, description=description, creator=creator)
    db.session.add(r)
    db.session.commit()
    flash('Reminder added.', 'success')
    # Preserve the selected date in query so UI can stay on that month
    return redirect(url_for('main.index', date=date_s))

@main_bp.route('/calendar/delete/<int:reminder_id>', methods=['POST'])
def delete_reminder(reminder_id):
    r = Reminder.query.get_or_404(reminder_id)
    username = bleach.clean(request.form.get('user'))

    # Check calendar write permission
    user_obj = User.query.filter_by(username=username).first()
    if not user_obj or (not user_obj.is_admin and not user_obj.calendar_write_enabled):
        flash('You do not have permission to delete calendar events.', 'error')
        return redirect(url_for('main.index'))

    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if username in admin_aliases or username == r.creator:
        db.session.delete(r)
        db.session.commit()
        flash('Reminder deleted.', 'success')
    else:
        flash('Not allowed to delete this reminder.', 'error')
    # After deletion, try to stay on the same date (day of deleted reminder)
    date_s = None
    try:
        if r.date:
            date_s = r.date.strftime('%Y-%m-%d')
    except Exception:
        date_s = None
    return redirect(url_for('main.index', date=date_s) if date_s else url_for('main.index'))

@main_bp.route('/calendar/delete_bulk', methods=['POST'])
def delete_reminders_bulk():
    """Delete multiple reminders in one action.
    Expects form field 'ids' as comma-separated reminder ids and 'user'."""
    ids_raw = bleach.clean(request.form.get('ids', ''))
    username = bleach.clean(request.form.get('user', ''))

    # Check calendar write permission
    user_obj = User.query.filter_by(username=username).first()
    if not user_obj or (not user_obj.is_admin and not user_obj.calendar_write_enabled):
        flash('You do not have permission to delete calendar events.', 'error')
        return redirect(url_for('main.index'))

    if not ids_raw:
        return redirect(url_for('main.index'))
    id_list = []
    for part in ids_raw.split(','):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
    if not id_list:
        return redirect(url_for('main.index'))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    kept_date = None
    deleted = 0
    for rid in id_list:
        r = Reminder.query.get(rid)
        if not r:
            continue
        if kept_date is None and getattr(r, 'date', None):
            try:
                kept_date = r.date.strftime('%Y-%m-%d')
            except Exception:
                kept_date = None
        if username in admin_aliases or username == r.creator:
            db.session.delete(r)
            deleted += 1
    if deleted:
        db.session.commit()
        flash(f'Deleted {deleted} reminder(s).', 'success')
    else:
        flash('No reminders deleted (permission?).', 'error')
    return redirect(url_for('main.index', date=kept_date) if kept_date else url_for('main.index'))

@main_bp.route('/media/<filename>')
def serve_media(filename):
    return send_from_directory(MEDIA_FOLDER, filename)

@main_bp.route('/media/delete/<int:media_id>', methods=['POST'])
def delete_media(media_id):
    m = Media.query.get_or_404(media_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == m.creator:
        # remove files that match base prefix
        try:
            if m.filepath:
                base = m.filepath.rsplit('.', 1)[0]
                for fname in os.listdir(MEDIA_FOLDER):
                    if fname.startswith(base):
                        os.remove(os.path.join(MEDIA_FOLDER, fname))
        except Exception:
            pass
        db.session.delete(m)
        db.session.commit()
    return redirect(url_for('main.media'))

# PDF Compressor
@main_bp.route('/pdfs', methods=['GET', 'POST'])
def pdfs():
    import shutil, subprocess
    if request.method == 'POST':
        pdf_file = request.files['pdf']
        creator = bleach.clean(request.form['creator'])
        mode = bleach.clean(request.form.get('mode', 'fast'))
        filename = secure_filename(pdf_file.filename)
        input_path = os.path.join(PDF_FOLDER, filename)
        pdf_file.save(input_path)
        # Compress PDF using Ghostscript only
        compressed_path = f"compressed_{filename}"
        output_path = os.path.join(PDF_FOLDER, compressed_path)
        try:
            gs_cmd = [
                'gs', '-sDEVICE=pdfwrite', '-dCompatibilityLevel=1.4',
                '-dPDFSETTINGS=/ebook', '-dNOPAUSE', '-dQUIET', '-dBATCH',
                f'-sOutputFile={output_path}', input_path
            ]
            subprocess.run(gs_cmd, check=True)
        except Exception:
            # As a minimal fallback just copy the file
            shutil.copy(input_path, output_path)
        # Save record
        pdf_obj = PDF(filename=filename, creator=creator, compressed_path=compressed_path)
        db.session.add(pdf_obj)
        db.session.commit()
        return redirect(url_for('main.pdfs'))
    pdfs = PDF.query.order_by(PDF.upload_time.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('pdfs.html', pdfs=pdfs, config=config)

@main_bp.route('/pdfs/<filename>')
def serve_pdf(filename):
    return send_from_directory(PDF_FOLDER, filename)

@main_bp.route('/pdfs/delete/<int:pdf_id>', methods=['POST'])
def delete_pdf(pdf_id):
    p = PDF.query.get_or_404(pdf_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == p.creator:
        try:
            if p.compressed_path:
                os.remove(os.path.join(PDF_FOLDER, p.compressed_path))
        except Exception:
            pass
        db.session.delete(p)
        db.session.commit()
    return redirect(url_for('main.pdfs'))

# QR Code Generator
@main_bp.route('/qr', methods=['GET', 'POST'])
def qr():
    import qrcode
    import base64
    import re
    qr_img = None
    if request.method == 'POST':
        qrtext = bleach.clean(request.form['qrtext'])
        creator = bleach.clean(request.form['creator'])
        ssid_match = re.search(r'ssid:([^ ]+)', qrtext, re.IGNORECASE)
        pass_match = re.search(r'pass:([^ ]+)', qrtext, re.IGNORECASE)
        type_match = re.search(r'type:([^ ]+)', qrtext, re.IGNORECASE)
        hidden_match = re.search(r'hidden:([^ ]+)', qrtext, re.IGNORECASE)
        if ssid_match and pass_match:
            ssid = bleach.clean(ssid_match.group(1))
            password = bleach.clean(pass_match.group(1))
            enc_type = (type_match.group(1) if type_match else 'WPA').upper()
            hidden = hidden_match.group(1) if hidden_match else 'false'
            wifi_str = f"WIFI:S:{ssid};T:{enc_type};P:{password};H:{hidden};"
            qrtext_for_qr = wifi_str
        else:
            qrtext_for_qr = qrtext
        qr_code = qrcode.make(qrtext_for_qr)
        from io import BytesIO
        buf = BytesIO()
        qr_code.save(buf, format='PNG')
        qr_img = base64.b64encode(buf.getvalue()).decode('utf-8')
        # Save to disk and record history
        filename = f"qr_{int(datetime.utcnow().timestamp())}.png"
        path = os.path.join(BASE_DIR, 'static', filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(buf.getvalue())
        db.session.add(QRCode(text=qrtext, filename=filename, creator=creator))
        db.session.commit()
    history = QRCode.query.order_by(QRCode.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('qr.html', qr_img=qr_img, history=history, config=config)

@main_bp.route('/qr/delete/<int:qr_id>', methods=['POST'])
def delete_qr(qr_id):
    q = QRCode.query.get_or_404(qr_id)
    user = bleach.clean(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == q.creator:
        try:
            os.remove(os.path.join(BASE_DIR, 'static', q.filename))
        except Exception:
            pass
        db.session.delete(q)
        db.session.commit()
    return redirect(url_for('main.qr'))

# Admin: Manage Family Members
@main_bp.route('/admin/manage-family')
def manage_family():
    config = current_app.config['HOMEHUB_CONFIG']
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    users = User.query.order_by(User.is_admin.desc(), User.username.asc()).all()
    return render_template('manage_family.html', config=config, users=users)

@main_bp.route('/admin/reset-user-password/<int:user_id>', methods=['POST'])
def reset_user_password(user_id):
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    user = User.query.get_or_404(user_id)
    user.password_set = False
    db.session.commit()

    flash(f'{user.username} will be prompted to set a new password on next login.', 'success')
    return redirect(url_for('main.manage_family'))

@main_bp.route('/admin/add-family-member', methods=['POST'])
def add_family_member():
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    username = bleach.clean(request.form.get('username', '')).strip()

    if not username:
        flash('Username is required', 'error')
        return redirect(url_for('main.manage_family'))

    # Check if user already exists
    if User.query.filter_by(username=username).first():
        flash(f'User "{username}" already exists', 'error')
        return redirect(url_for('main.manage_family'))

    # Create new user
    new_user = User(
        username=username,
        is_admin=False,
        password_set=False
    )
    new_user.set_password('temp')  # Temporary password
    new_user.password_set = False  # Override flag to force password setup
    db.session.add(new_user)
    db.session.commit()

    flash(f'Family member "{username}" added successfully!', 'success')
    return redirect(url_for('main.manage_family'))

@main_bp.route('/admin/remove-family-member/<int:user_id>', methods=['POST'])
def remove_family_member(user_id):
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    user = User.query.get_or_404(user_id)

    # Don't allow removing admin users
    if user.is_admin:
        flash('Cannot remove administrator accounts', 'error')
        return redirect(url_for('main.manage_family'))

    username = user.username
    db.session.delete(user)
    db.session.commit()

    flash(f'Family member "{username}" removed successfully', 'success')
    return redirect(url_for('main.manage_family'))

@main_bp.route('/admin/toggle-calendar-permission/<int:user_id>', methods=['POST'])
def toggle_calendar_permission(user_id):
    from flask import g

    # Only admin can access
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. Admin only.', 'error')
        return redirect(url_for('main.index'))

    user = User.query.get_or_404(user_id)

    # Don't allow modifying admin users' permissions
    if user.is_admin:
        flash('Cannot modify administrator permissions', 'error')
        return redirect(url_for('main.manage_family'))

    # Toggle the permission
    user.calendar_write_enabled = not user.calendar_write_enabled
    db.session.commit()

    status = "enabled" if user.calendar_write_enabled else "disabled"
    flash(f'Calendar write permission {status} for {user.username}', 'success')
    return redirect(url_for('main.manage_family'))

# CalDAV Calendar Sync Setup
@main_bp.route('/caldav')
def caldav_setup():
    config = current_app.config['HOMEHUB_CONFIG']
    from flask import g

    # Only admin can access CalDAV setup (shared family calendar)
    if not hasattr(g, 'current_user') or not g.current_user.is_admin:
        flash('Access denied. CalDAV sync is only available to the Administrator.', 'error')
        return redirect(url_for('main.index'))

    username = g.current_user.username

    # Detect current host from request (removes :port if present in request.host)
    detected_host = request.host.split(':')[0] if request.host else 'localhost'

    # Determine protocol (http vs https) from request
    # X-Forwarded-Proto header is set by reverse proxies like Cloudflare
    is_https = request.headers.get('X-Forwarded-Proto', 'http') == 'https' or request.is_secure
    protocol = 'https' if is_https else 'http'

    # CalDAV server configuration
    # For my-house.dev setup, calendar subdomain already maps to port 5232 via Cloudflare Tunnel
    if 'my-house.dev' in detected_host:
        # User has hub.my-house.dev for HomeHub and calendar.my-house.dev for CalDAV
        # Cloudflare Tunnel handles port mapping, so don't include port in URL
        server_host = 'calendar.my-house.dev'
        server_port = ''  # Port is handled by Cloudflare Tunnel
        caldav_url = f"{protocol}://{server_host}/{username}/homehub-calendar/"
    else:
        # Fallback for other domains or localhost (include port)
        server_host = detected_host
        server_port = '5232'
        caldav_url = f"{protocol}://{server_host}:{server_port}/{username}/homehub-calendar/"

    return render_template('caldav.html',
                         config=config,
                         username=username,
                         caldav_url=caldav_url,
                         server_host=server_host,
                         server_port=server_port)

# Family Calendar Setup (for non-admin family members)
@main_bp.route('/caldav/family-setup')
def family_calendar_setup():
    """Setup page for family members to add shared calendar to their devices"""
    config = current_app.config['HOMEHUB_CONFIG']
    from flask import g

    # Skip for admin (they use the main caldav setup page)
    if hasattr(g, 'current_user') and g.current_user.is_admin:
        flash('Administrators should use the Calendar setup page from the sidebar.', 'info')
        return redirect(url_for('main.caldav_setup'))

    username = session.get('username', '')
    user = User.query.filter_by(username=username).first()

    # Detect current host and protocol
    detected_host = request.host.split(':')[0] if request.host else 'localhost'
    is_https = request.headers.get('X-Forwarded-Proto', 'http') == 'https' or request.is_secure
    protocol = 'https' if is_https else 'http'

    # CalDAV server configuration using user's own account
    if 'my-house.dev' in detected_host:
        server_host = 'calendar.my-house.dev'
        server_port = ''
        caldav_url = f"{protocol}://{server_host}/{username}/homehub-calendar/"
    else:
        server_host = detected_host
        server_port = '5232'
        caldav_url = f"{protocol}://{server_host}:{server_port}/{username}/homehub-calendar/"

    # Check if user has calendar write permission
    can_write = user.calendar_write_enabled if user else False

    return render_template('family_calendar_setup.html',
                         config=config,
                         username=username,
                         caldav_url=caldav_url,
                         caldav_username=username,
                         server_host=server_host,
                         server_port=server_port,
                         can_write=can_write)


@main_bp.route('/caldav/family-calendar.mobileconfig')
def download_family_calendar_profile():
    """Generate and download iOS configuration profile for family calendar"""
    username = session.get('username', 'User')

    # Detect current host and protocol
    detected_host = request.host.split(':')[0] if request.host else 'localhost'
    is_https = request.headers.get('X-Forwarded-Proto', 'http') == 'https' or request.is_secure
    protocol = 'https' if is_https else 'http'

    # CalDAV server configuration using user's own account
    if 'my-house.dev' in detected_host:
        server_host = 'calendar.my-house.dev'
        server_port = ''
        server_url = f"{protocol}://{server_host}"
        caldav_path = f"/{username}/homehub-calendar/"
    else:
        server_host = detected_host
        server_port = '5232'
        server_url = f"{protocol}://{server_host}:{server_port}"
        caldav_path = f"/{username}/homehub-calendar/"

    # Generate unique ID for this profile
    import uuid
    profile_uuid = str(uuid.uuid4()).upper()
    caldav_uuid = str(uuid.uuid4()).upper()

    # Create iOS configuration profile (XML format)
    # Note: Password is not included for security - user will be prompted to enter it during installation
    profile_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>CalDAVAccountDescription</key>
            <string>HomeHub Family Calendar</string>
            <key>CalDAVHostName</key>
            <string>{server_host}</string>
            <key>CalDAVPort</key>
            <integer>{server_port if server_port else '443' if is_https else '80'}</integer>
            <key>CalDAVPrincipalURL</key>
            <string>{server_url}{caldav_path}</string>
            <key>CalDAVUseSSL</key>
            {('<true/>' if is_https else '<false/>')}
            <key>CalDAVUsername</key>
            <string>{username}</string>
            <key>PayloadDescription</key>
            <string>Configures CalDAV account for HomeHub Family Calendar</string>
            <key>PayloadDisplayName</key>
            <string>HomeHub Family Calendar</string>
            <key>PayloadIdentifier</key>
            <string>com.homehub.caldav.{caldav_uuid}</string>
            <key>PayloadType</key>
            <string>com.apple.caldav.account</string>
            <key>PayloadUUID</key>
            <string>{caldav_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
        </dict>
    </array>
    <key>PayloadDisplayName</key>
    <string>HomeHub Family Calendar ({username})</string>
    <key>PayloadIdentifier</key>
    <string>com.homehub.profile.{profile_uuid}</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
    <key>PayloadDescription</key>
    <string>Install this profile to add the HomeHub Family Calendar to your device. You will be prompted to enter your HomeHub password during installation.</string>
</dict>
</plist>'''

    # Return as downloadable file
    from flask import Response
    response = Response(profile_content, mimetype='application/x-apple-aspen-config')
    response.headers['Content-Disposition'] = 'attachment; filename=HomeHub-Family-Calendar.mobileconfig'
    return response

# Bitwarden Password Manager
@main_bp.route('/bitwarden', methods=['GET', 'POST'])
def bitwarden():
    config = current_app.config['HOMEHUB_CONFIG']
    # Get current user from session (authenticated user)
    current_user = session.get('username', '')

    # Handle vault setup completion
    if request.method == 'POST':
        username = bleach.clean(request.form.get('username', ''))
        email = bleach.clean(request.form.get('email', ''))

        # Verify the user is setting up their own vault
        if username != current_user:
            flash('You can only set up your own vault.', 'error')
            return redirect(url_for('main.bitwarden'))

        if username and email:
            # Check if vault already exists
            vault = BitwardenVault.query.filter_by(username=username).first()
            if vault:
                vault.bitwarden_email = email
                vault.setup_completed = True
            else:
                vault = BitwardenVault(
                    username=username,
                    bitwarden_email=email,
                    setup_completed=True
                )
                db.session.add(vault)
            db.session.commit()
            flash('Bitwarden vault setup completed! You can now access your vault.', 'success')
            return redirect(url_for('main.bitwarden'))

    # Check if current user has a vault
    vault = None
    if current_user:
        vault = BitwardenVault.query.filter_by(username=current_user).first()

    # Get Vaultwarden URL from environment or use default
    vaultwarden_url = os.environ.get('VAULTWARDEN_URL', 'https://vault.my-house.dev')

    return render_template('bitwarden.html',
                         config=config,
                         vault=vault,
                         current_user=current_user,
                         vaultwarden_url=vaultwarden_url)

# Notice Board APIs
@main_bp.route('/notice', methods=['POST'])
def update_notice():
    content = bleach.clean(request.form.get('content', ''), tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
    user = bleach.clean(request.form.get('user', ''))
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    if user != admin_name:
        flash('Only admin can update the notice.', 'error')
        return redirect(url_for('main.index'))
    n = Notice.query.order_by(Notice.updated_at.desc()).first()
    now = datetime.utcnow()
    if n:
        n.content = content
        n.updated_by = user
        n.updated_at = now
    else:
        db.session.add(Notice(content=content, updated_by=user, updated_at=now))
    db.session.commit()
    flash('Notice updated.', 'success')
    return redirect(url_for('main.index'))

# Lightweight endpoints for updating/clearing current user's home status from the dashboard
@main_bp.route('/whoishome', methods=['POST'])
def who_is_home_action():
    """Unified endpoint for updating or clearing a home status.
    Expects hidden field 'action' = update|clear."""
    action = bleach.clean(request.form.get('action', 'update'))
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status.', 'error')
        # For AJAX we just return a JSON error payload
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    result = None
    if action == 'clear':
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            db.session.delete(hs)
            db.session.commit()
            result = 'cleared'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash('Status cleared.', 'success')
        else:
            result = 'none'
            if request.headers.get('X-Requested-With') != 'fetch':
                flash('No status to clear.', 'info')
    else:  # update
        status = bleach.clean(request.form.get('status', '')) or 'Away'
        hs = HomeStatus.query.filter_by(name=name).first()
        if hs:
            hs.status = status
        else:
            db.session.add(HomeStatus(name=name, status=status))
        db.session.commit()
        result = 'updated'
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Status updated.', 'success')
    # AJAX (fetch) support
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        # Ensure result has a value
        result = result or 'updated'
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': result})
    # Preserve date if present (calendar context)
    date_q = request.args.get('date') or request.form.get('date')
    return redirect(url_for('main.index', date=date_q) if date_q else url_for('main.index'))

# Member personal status (text) updates under notice board
@main_bp.route('/status/update', methods=['POST'])
def member_status_update():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    raw_text = request.form.get('text', '') or ''
    text = bleach.clean(raw_text).strip()
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status.', 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    # Do not allow blank/whitespace-only personal statuses
    if not text:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Empty status'}), 400
        else:
            flash('Status cannot be empty.', 'error')
            return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    now = datetime.utcnow()
    if ms:
        ms.text = text
        ms.updated_at = now
    else:
        db.session.add(MemberStatus(name=name, text=text, updated_at=now))
    db.session.commit()
    if request.headers.get('X-Requested-With') != 'fetch':
        flash('Status saved.', 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'saved'})
    return redirect(url_for('main.index'))

@main_bp.route('/status/delete', methods=['POST'])
def member_status_delete():
    config = current_app.config['HOMEHUB_CONFIG']
    family = set(config.get('family_members', []))
    name = bleach.clean(request.form.get('name', ''))
    if not name or name not in family:
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Invalid user for status removal.', 'error')
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'Invalid user'}), 400
        return redirect(url_for('main.index'))
    ms = MemberStatus.query.filter_by(name=name).first()
    removed = False
    if ms:
        db.session.delete(ms)
        db.session.commit()
        removed = True
        if request.headers.get('X-Requested-With') != 'fetch':
            flash('Status removed.', 'success')
    if request.headers.get('X-Requested-With') == 'fetch':
        who_statuses = {s.name: s.status for s in HomeStatus.query.all() if s.name in family}
        member_statuses = {ms.name: ms.text for ms in MemberStatus.query.all() if ms.name in family and (ms.text or '').strip()}
        return jsonify({'ok': True, 'who_statuses': who_statuses, 'member_statuses': member_statuses, 'result': 'removed' if removed else 'none'})
    return redirect(url_for('main.index'))

# Games
@main_bp.route('/games')
def games():
    config = current_app.config['HOMEHUB_CONFIG']

    # Only browser-based games that work without emulation
    game_categories = {
        'builtin': {
            'name': 'Built-in Games',
            'icon': 'fa-solid fa-chess',
            'description': 'Play directly in your browser',
            'games': [
                {
                    'name': 'Chess',
                    'path': '/games/chess',
                    'category': 'builtin',
                    'icon': 'fa-solid fa-chess-board'
                }
            ]
        },
        'arcade': {
            'name': 'Arcade Games',
            'icon': 'fa-solid fa-gamepad',
            'description': 'Classic HTML5 arcade games',
            'games': []
        }
    }

    # Scan arcade folder for HTML5 games
    arcade_path = os.path.join(GAMES_FOLDER, 'arcade')
    if os.path.exists(arcade_path):
        for item in os.listdir(arcade_path):
            item_path = os.path.join(arcade_path, item)
            # Skip hidden files and README
            if item.startswith('.') or item.lower() == 'readme.md':
                continue
            # Add directories (HTML5 games are usually in folders)
            if os.path.isdir(item_path):
                game_categories['arcade']['games'].append({
                    'name': item,
                    'path': f'/games/arcade/{item}',
                    'category': 'arcade'
                })

    return render_template('games.html',
                         config=config,
                         is_authed=True,
                         game_categories=game_categories)

@main_bp.route('/games/chess')
def chess_game():
    """Chess game with AI and multiplayer"""
    config = current_app.config['HOMEHUB_CONFIG']
    game_id = request.args.get('game_id')

    # If accessed with game_id, it's a remote game link (no auth required, no nav)
    is_remote_link = game_id is not None
    is_authed = session.get('user_id') is not None

    # Render standalone template for remote links, otherwise use full template with nav
    template = 'chess_standalone.html' if is_remote_link else 'chess.html'

    return render_template(template,
                         config=config,
                         is_authed=is_authed,
                         game_id=game_id)

# Remote Chess Game Storage
# Format: {game_id: {'fen': fen_string, 'white_player': session_id, 'black_player': session_id,
#                    'current_turn': 'w'/'b', 'last_activity': timestamp, 'moves': []}}
remote_chess_games = {}

def cleanup_old_games():
    """Remove games older than 24 hours"""
    cutoff_time = time.time() - (24 * 60 * 60)
    games_to_remove = [gid for gid, game in remote_chess_games.items()
                       if game['last_activity'] < cutoff_time]
    for gid in games_to_remove:
        del remote_chess_games[gid]

@main_bp.route('/api/chess/create', methods=['POST'])
def create_remote_chess_game():
    """Create a new remote chess game"""
    cleanup_old_games()

    data = request.json
    player_token = data.get('player_token')  # Unique token from client

    # Generate secure game ID
    game_id = secrets.token_urlsafe(16)

    # Initialize game state - CREATOR IS ALWAYS WHITE
    remote_chess_games[game_id] = {
        'fen': 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',  # Starting position
        'white_player': player_token,  # Creator is always white
        'black_player': None,  # Second player will be black
        'current_turn': 'w',
        'last_activity': time.time(),
        'moves': [],
        'game_over': False,
        'result': None
    }

    print(f"[CHESS] Created game {game_id}, creator (WHITE): {player_token[:12]}..., total games: {len(remote_chess_games)}")

    return jsonify({
        'success': True,
        'game_id': game_id,
        'player_color': 'white'  # Creator is always white
    })

@main_bp.route('/api/chess/game/<game_id>', methods=['GET'])
def get_chess_game(game_id):
    """Get current game state"""
    cleanup_old_games()

    print(f"[CHESS] Get game request for {game_id}, total games: {len(remote_chess_games)}, games: {list(remote_chess_games.keys())}")

    if game_id not in remote_chess_games:
        print(f"[CHESS] Game {game_id} not found!")
        return jsonify({'success': False, 'error': 'Game not found'}), 404

    game = remote_chess_games[game_id]
    player_token = request.args.get('player_token')

    # SIMPLE LOGIC: Player 1 (creator) = White, Player 2 = Black
    if game['white_player'] == player_token:
        # This is Player 1 (creator)
        player_color = 'white'
        print(f"[CHESS] Player 1 (WHITE/creator): {player_token[:12]}")
    else:
        # This is Player 2 (joiner) - assign to black
        if game['black_player'] != player_token:
            game['black_player'] = player_token
            print(f"[CHESS] Player 2 (BLACK/joiner) assigned: {player_token[:12]}")
        player_color = 'black'

    return jsonify({
        'success': True,
        'fen': game['fen'],
        'current_turn': game['current_turn'],
        'moves': game['moves'],
        'player_color': player_color,
        'players_connected': game['white_player'] is not None and game['black_player'] is not None,
        'game_over': game['game_over'],
        'result': game['result']
    })

@main_bp.route('/api/chess/game/<game_id>/move', methods=['POST'])
def submit_chess_move(game_id):
    """Submit a move to a remote game"""
    cleanup_old_games()

    if game_id not in remote_chess_games:
        return jsonify({'success': False, 'error': 'Game not found'}), 404

    game = remote_chess_games[game_id]
    data = request.json
    player_token = data.get('player_token')

    # Verify it's the player's turn
    player_color = None
    if game['white_player'] == player_token:
        player_color = 'w'
    elif game['black_player'] == player_token:
        player_color = 'b'
    else:
        return jsonify({'success': False, 'error': 'You are not a player in this game'}), 403

    if game['current_turn'] != player_color:
        return jsonify({'success': False, 'error': 'Not your turn'}), 400

    # Update game state
    game['fen'] = data['fen']
    game['current_turn'] = data['turn']
    game['moves'].append(data['move'])
    game['last_activity'] = time.time()

    if data.get('game_over'):
        game['game_over'] = True
        game['result'] = data.get('result')

    return jsonify({'success': True})

@main_bp.route('/games/<category>/<path:filename>')
def serve_game(category, filename):
    """Serve game files from the games directory"""
    category_path = os.path.join(GAMES_FOLDER, category)
    return send_from_directory(category_path, filename)

# File Converter (VERT)
@main_bp.route('/converter')
def file_converter():
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('converter.html', config=config, is_authed=True)

# Development Tools
@main_bp.route('/devtools')
def devtools():
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('devtools.html', config=config, is_authed=True)

# Photo Gallery
PHOTOS_FOLDER = os.path.join(BASE_DIR, 'photos')
os.makedirs(PHOTOS_FOLDER, exist_ok=True)
os.makedirs(os.path.join(PHOTOS_FOLDER, 'thumbs'), exist_ok=True)

@main_bp.route('/photos')
def photos():
    from .models import Photo
    config = current_app.config['HOMEHUB_CONFIG']
    photos = Photo.query.order_by(Photo.upload_time.desc()).all()
    albums = db.session.query(Photo.album).distinct().all()
    albums = [a[0] for a in albums if a[0]]
    return render_template('photos.html', config=config, is_authed=True, photos=photos, albums=albums)

@main_bp.route('/photos/upload', methods=['POST'])
def photos_upload():
    from .models import Photo
    from PIL import Image

    files = request.files.getlist('photos')
    album = request.form.get('album', '').strip() or 'General'
    caption = request.form.get('caption', '').strip()
    uploader = request.form.get('uploader', 'Unknown')

    for file in files:
        if file and file.filename:
            filename = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}")
            filepath = os.path.join(PHOTOS_FOLDER, filename)
            file.save(filepath)

            # Create thumbnail
            try:
                img = Image.open(filepath)
                img.thumbnail((400, 400))
                thumb_path = os.path.join(PHOTOS_FOLDER, 'thumbs', filename)
                img.save(thumb_path)
            except:
                pass

            photo = Photo(
                filename=filename,
                album=album,
                caption=caption,
                uploader=uploader,
                upload_time=datetime.now()
            )
            db.session.add(photo)

    db.session.commit()
    flash('Photos uploaded successfully!', 'success')
    return redirect(url_for('main.photos'))

@main_bp.route('/photos/full/<filename>')
def photos_full(filename):
    return send_from_directory(PHOTOS_FOLDER, filename)

@main_bp.route('/photos/thumb/<filename>')
def photos_thumb(filename):
    thumb_path = os.path.join(PHOTOS_FOLDER, 'thumbs', filename)
    if os.path.exists(thumb_path):
        return send_from_directory(os.path.join(PHOTOS_FOLDER, 'thumbs'), filename)
    return send_from_directory(PHOTOS_FOLDER, filename)

@main_bp.route('/photos/get/<int:photo_id>')
def photos_get(photo_id):
    from .models import Photo
    photo = Photo.query.get_or_404(photo_id)
    return jsonify({
        'filename': photo.filename,
        'caption': photo.caption,
        'uploader': photo.uploader,
        'upload_time': photo.upload_time.strftime('%B %d, %Y')
    })

@main_bp.route('/photos/delete/<int:photo_id>', methods=['POST'])
def photos_delete(photo_id):
    from .models import Photo
    photo = Photo.query.get_or_404(photo_id)

    # Delete files
    try:
        os.remove(os.path.join(PHOTOS_FOLDER, photo.filename))
        os.remove(os.path.join(PHOTOS_FOLDER, 'thumbs', photo.filename))
    except:
        pass

    db.session.delete(photo)
    db.session.commit()
    return jsonify({'success': True})

# Meal Planner
@main_bp.route('/meals')
def meals():
    from .models import MealPlan, FavoriteMeal
    config = current_app.config['HOMEHUB_CONFIG']
    meal_plans = MealPlan.query.all()
    favorites = FavoriteMeal.query.order_by(FavoriteMeal.name).all()
    return render_template('meals.html', config=config, is_authed=True, meal_plans=meal_plans, favorites=favorites)

@main_bp.route('/meals/plan/save', methods=['POST'])
def meals_plan_save():
    from .models import MealPlan
    data = request.get_json()
    day = data.get('day')
    meal_type = data.get('meal_type')
    meal_name = data.get('meal_name')

    plan = MealPlan.query.filter_by(day=day, meal_type=meal_type).first()
    if plan:
        plan.meal_name = meal_name
    else:
        plan = MealPlan(day=day, meal_type=meal_type, meal_name=meal_name)
        db.session.add(plan)

    db.session.commit()
    return jsonify({'success': True})

@main_bp.route('/meals/favorite/add', methods=['POST'])
def meals_favorite_add():
    from .models import FavoriteMeal
    name = request.form.get('name')
    ingredients = request.form.get('ingredients', '')
    creator = request.form.get('creator', 'Unknown')

    fav = FavoriteMeal(name=name, ingredients=ingredients, creator=creator)
    db.session.add(fav)
    db.session.commit()
    flash('Favorite meal added!', 'success')
    return redirect(url_for('main.meals'))

@main_bp.route('/meals/favorite/delete/<int:fav_id>', methods=['POST'])
def meals_favorite_delete(fav_id):
    from .models import FavoriteMeal
    fav = FavoriteMeal.query.get_or_404(fav_id)
    db.session.delete(fav)
    db.session.commit()
    return jsonify({'success': True})

# Household Maintenance
@main_bp.route('/maintenance')
def maintenance():
    from .models import MaintenanceTask
    config = current_app.config['HOMEHUB_CONFIG']
    tasks = MaintenanceTask.query.order_by(MaintenanceTask.next_due).all()

    # Update status based on dates
    today = date.today()
    for task in tasks:
        if task.next_due:
            if task.next_due < today:
                task.status = 'overdue'
            elif task.next_due <= today + timedelta(days=7):
                task.status = 'upcoming'
            else:
                task.status = 'ok'

    return render_template('maintenance.html', config=config, is_authed=True, tasks=tasks)

@main_bp.route('/maintenance/add', methods=['POST'])
def maintenance_add():
    from .models import MaintenanceTask
    task = MaintenanceTask(
        task_name=request.form.get('task_name'),
        description=request.form.get('description'),
        icon=request.form.get('icon', 'tools'),
        frequency_days=int(request.form.get('frequency_days', 90)),
        next_due=datetime.strptime(request.form.get('next_due'), '%Y-%m-%d').date(),
        creator=request.form.get('creator', 'Unknown')
    )
    db.session.add(task)
    db.session.commit()
    flash('Maintenance task added!', 'success')
    return redirect(url_for('main.maintenance'))

@main_bp.route('/maintenance/complete/<int:task_id>', methods=['POST'])
def maintenance_complete(task_id):
    from .models import MaintenanceTask
    task = MaintenanceTask.query.get_or_404(task_id)
    task.last_completed = date.today()
    task.next_due = date.today() + timedelta(days=task.frequency_days)
    task.status = 'ok'
    db.session.commit()
    return jsonify({'success': True})

@main_bp.route('/maintenance/delete/<int:task_id>', methods=['POST'])
def maintenance_delete(task_id):
    from .models import MaintenanceTask
    task = MaintenanceTask.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'success': True})

# Pet Care Tracker
@main_bp.route('/petcare')
def petcare():
    from .models import Pet
    config = current_app.config['HOMEHUB_CONFIG']
    pets = Pet.query.all()

    # Calculate age for each pet
    today = date.today()
    for pet in pets:
        if pet.birth_date:
            age = today.year - pet.birth_date.year
            if today.month < pet.birth_date.month or (today.month == pet.birth_date.month and today.day < pet.birth_date.day):
                age -= 1
            pet.age = age

    return render_template('petcare.html', config=config, is_authed=True, pets=pets)

@main_bp.route('/petcare/add', methods=['POST'])
def petcare_add():
    from .models import Pet
    birth_date_str = request.form.get('birth_date')
    birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date() if birth_date_str else None

    pet = Pet(
        name=request.form.get('name'),
        species=request.form.get('species'),
        breed=request.form.get('breed'),
        icon=request.form.get('icon', 'paw'),
        birth_date=birth_date,
        creator=request.form.get('creator', 'Unknown')
    )
    db.session.add(pet)
    db.session.commit()
    flash(f'{pet.name} added to pet tracker!', 'success')
    return redirect(url_for('main.petcare'))

@main_bp.route('/petcare/delete/<int:pet_id>', methods=['POST'])
def petcare_delete(pet_id):
    from .models import Pet, PetCareEvent
    pet = Pet.query.get_or_404(pet_id)
    PetCareEvent.query.filter_by(pet_id=pet_id).delete()
    db.session.delete(pet)
    db.session.commit()
    return jsonify({'success': True})

@main_bp.route('/petcare/events/<int:pet_id>')
def petcare_events(pet_id):
    from .models import PetCareEvent
    events = PetCareEvent.query.filter_by(pet_id=pet_id).order_by(PetCareEvent.event_date.desc()).all()
    return jsonify([{
        'id': e.id,
        'event_type': e.event_type,
        'description': e.description,
        'event_date': e.event_date.strftime('%B %d, %Y'),
        'next_due': e.next_due.strftime('%B %d, %Y') if e.next_due else None
    } for e in events])

@main_bp.route('/petcare/event/add', methods=['POST'])
def petcare_event_add():
    from .models import PetCareEvent
    next_due_str = request.form.get('next_due')
    next_due = datetime.strptime(next_due_str, '%Y-%m-%d').date() if next_due_str else None

    event = PetCareEvent(
        pet_id=int(request.form.get('pet_id')),
        event_type=request.form.get('event_type'),
        description=request.form.get('description'),
        event_date=datetime.strptime(request.form.get('event_date'), '%Y-%m-%d').date(),
        next_due=next_due,
        creator=request.form.get('creator', 'Unknown')
    )
    db.session.add(event)
    db.session.commit()
    flash('Care event added!', 'success')
    return redirect(url_for('main.petcare'))

@main_bp.route('/petcare/event/delete/<int:event_id>', methods=['POST'])
def petcare_event_delete(event_id):
    from .models import PetCareEvent
    event = PetCareEvent.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return jsonify({'success': True})

# Weather Widget
import requests

def get_weather_data(location=None, lat=None, lon=None):
    """Fetch weather data from Open-Meteo API (free, no API key needed)"""
    print(f"[DEBUG] get_weather_data called with location={location}, lat={lat}, lon={lon}")
    try:
        # Default to ZIP 47725 (Evansville, IN area)
        if not location and not (lat and lon):
            location = "47725"
            print(f"[DEBUG] Using default location: {location}")

        # If location is provided, try to geocode it
        if location:
            # Use OpenWeatherMap geocoding (can also work without API key for basic lookups)
            # Or use Open-Meteo's geocoding
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&language=en&format=json"
            print(f"[DEBUG] Geocoding URL: {geo_url}")
            geo_response = requests.get(geo_url, timeout=5)
            print(f"[DEBUG] Geocoding response status: {geo_response.status_code}")
            if geo_response.ok:
                geo_data = geo_response.json()
                print(f"[DEBUG] Geocoding data: {geo_data}")
                if geo_data.get('results'):
                    result = geo_data['results'][0]
                    lat = result['latitude']
                    lon = result['longitude']
                    location_name = result.get('name', location)
                    country = result.get('country', '')
                    admin1 = result.get('admin1', '')
                    location_display = f"{location_name}, {admin1}, {country}" if admin1 else f"{location_name}, {country}"
                    print(f"[DEBUG] Geocoded: {location_display} at {lat}, {lon}")
                else:
                    print("[DEBUG] No geocoding results")
                    return None
            else:
                print(f"[DEBUG] Geocoding failed with status {geo_response.status_code}")
                return None

        if not (lat and lon):
            print("[DEBUG] No lat/lon available")
            return None

        # Fetch comprehensive weather from Open-Meteo (all free data)
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&"
            f"current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
            f"wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,cloud_cover,visibility,uv_index,is_day&"
            f"hourly=temperature_2m,precipitation_probability,weather_code,wind_speed_10m,relative_humidity_2m&"
            f"daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,"
            f"precipitation_probability_max,precipitation_sum,wind_speed_10m_max,wind_gusts_10m_max&"
            f"temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=auto&forecast_days=7"
        )
        print(f"[DEBUG] Weather URL: {weather_url}")

        weather_response = requests.get(weather_url, timeout=10)
        print(f"[DEBUG] Weather response status: {weather_response.status_code}")
        if not weather_response.ok:
            print("[DEBUG] Weather API request failed")
            return None

        data = weather_response.json()
        current = data.get('current', {})
        hourly = data.get('hourly', {})
        daily = data.get('daily', {})
        print(f"[DEBUG] Weather data: {current}")
        print(f"[DEBUG] Daily forecast data keys: {daily.keys() if daily else 'None'}")

        # Map weather codes to descriptions and icons
        weather_code = current.get('weather_code', 0)
        weather_desc, weather_icon = map_weather_code(weather_code)

        # Wind direction to compass
        def wind_direction_to_compass(degrees):
            if degrees is None:
                return "N/A"
            directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
            idx = round(degrees / 22.5) % 16
            return directions[idx]

        # Process hourly forecast (next 24 hours)
        hourly_list = []
        if hourly:
            h_times = hourly.get('time', [])
            h_temps = hourly.get('temperature_2m', [])
            h_precip_prob = hourly.get('precipitation_probability', [])
            h_weather_codes = hourly.get('weather_code', [])
            h_wind_speeds = hourly.get('wind_speed_10m', [])
            h_humidity = hourly.get('relative_humidity_2m', [])

            # Get next 24 hours
            for i in range(min(24, len(h_times))):
                try:
                    time_obj = datetime.fromisoformat(h_times[i].replace('Z', '+00:00'))
                    hour_display = time_obj.strftime('%I %p')  # 12-hour format
                    h_code = h_weather_codes[i] if i < len(h_weather_codes) else 0
                    h_desc, h_icon = map_weather_code(h_code)

                    hourly_list.append({
                        'hour': hour_display,
                        'temperature': round(h_temps[i]) if i < len(h_temps) else 0,
                        'precipitation_prob': h_precip_prob[i] if i < len(h_precip_prob) else 0,
                        'weather_code': h_code,
                        'icon': h_icon,
                        'description': h_desc,
                        'wind_speed': round(h_wind_speeds[i], 1) if i < len(h_wind_speeds) else 0,
                        'humidity': h_humidity[i] if i < len(h_humidity) else 0
                    })
                except (IndexError, ValueError) as e:
                    print(f"[DEBUG] Error processing hourly {i}: {e}")
                    continue

        # Process 5-day forecast (skip today, take next 5 days)
        forecast_list = []
        if daily:
            dates = daily.get('time', [])
            weather_codes = daily.get('weather_code', [])
            temp_max = daily.get('temperature_2m_max', [])
            temp_min = daily.get('temperature_2m_min', [])
            sunrise = daily.get('sunrise', [])
            sunset = daily.get('sunset', [])
            uv_max = daily.get('uv_index_max', [])
            precip_prob = daily.get('precipitation_probability_max', [])
            precip_sum = daily.get('precipitation_sum', [])
            wind_max = daily.get('wind_speed_10m_max', [])
            gust_max = daily.get('wind_gusts_10m_max', [])

            # Skip first day (today), take next 5
            for i in range(1, min(6, len(dates))):
                try:
                    date_obj = datetime.strptime(dates[i], '%Y-%m-%d')
                    day_name = date_obj.strftime('%a')  # Mon, Tue, etc.
                    fc_code = weather_codes[i] if i < len(weather_codes) else 0
                    fc_desc, fc_icon = map_weather_code(fc_code)

                    # Parse sunrise/sunset
                    sunrise_time = datetime.fromisoformat(sunrise[i].replace('Z', '+00:00')).strftime('%I:%M %p') if i < len(sunrise) and sunrise[i] else 'N/A'
                    sunset_time = datetime.fromisoformat(sunset[i].replace('Z', '+00:00')).strftime('%I:%M %p') if i < len(sunset) and sunset[i] else 'N/A'

                    forecast_list.append({
                        'day': day_name,
                        'date': dates[i],
                        'weather_code': fc_code,
                        'icon': fc_icon,
                        'description': fc_desc,
                        'high': round(temp_max[i]),
                        'low': round(temp_min[i]),
                        'sunrise': sunrise_time,
                        'sunset': sunset_time,
                        'uv_max': round(uv_max[i], 1) if i < len(uv_max) and uv_max[i] else 0,
                        'precipitation_prob': precip_prob[i] if i < len(precip_prob) else 0,
                        'precipitation_sum': round(precip_sum[i], 2) if i < len(precip_sum) else 0,
                        'wind_max': round(wind_max[i], 1) if i < len(wind_max) else 0,
                        'wind_gusts_max': round(gust_max[i], 1) if i < len(gust_max) else 0
                    })
                except (IndexError, ValueError) as e:
                    print(f"[DEBUG] Error processing forecast day {i}: {e}")
                    continue

        # Today's detailed data from daily[0]
        today_data = {}
        if daily and len(daily.get('time', [])) > 0:
            try:
                sunrise_today = datetime.fromisoformat(daily['sunrise'][0].replace('Z', '+00:00')).strftime('%I:%M %p') if daily.get('sunrise') else 'N/A'
                sunset_today = datetime.fromisoformat(daily['sunset'][0].replace('Z', '+00:00')).strftime('%I:%M %p') if daily.get('sunset') else 'N/A'
                today_data = {
                    'sunrise': sunrise_today,
                    'sunset': sunset_today,
                    'uv_index': round(daily['uv_index_max'][0], 1) if daily.get('uv_index_max') else 0,
                    'precip_prob': daily['precipitation_probability_max'][0] if daily.get('precipitation_probability_max') else 0,
                    'precip_sum': round(daily['precipitation_sum'][0], 2) if daily.get('precipitation_sum') else 0
                }
            except (IndexError, ValueError, KeyError) as e:
                print(f"[DEBUG] Error processing today's data: {e}")

        # Add high/low from today's daily data
        today_high = round(daily['temperature_2m_max'][0]) if daily and daily.get('temperature_2m_max') and len(daily['temperature_2m_max']) > 0 else 0
        today_low = round(daily['temperature_2m_min'][0]) if daily and daily.get('temperature_2m_min') and len(daily['temperature_2m_min']) > 0 else 0

        result = {
            'location': location_display if location else f"{lat}, {lon}",
            'current': {
                'temperature': round(current.get('temperature_2m', 0)),
                'feels_like': round(current.get('apparent_temperature', 0)),
                'humidity': current.get('relative_humidity_2m', 0),
                'wind_speed': round(current.get('wind_speed_10m', 0), 1),
                'wind_direction': wind_direction_to_compass(current.get('wind_direction_10m')),
                'wind_direction_degrees': current.get('wind_direction_10m', 0),
                'wind_gusts': round(current.get('wind_gusts_10m', 0), 1),
                'pressure': round(current.get('pressure_msl', 0)),
                'cloud_cover': current.get('cloud_cover', 0),
                'visibility': round(current.get('visibility', 0) / 1609.34, 1),  # meters to miles
                'uv_index': round(current.get('uv_index', 0), 1),
                'is_day': current.get('is_day', 1),
                'weather_code': weather_code,
                'description': weather_desc,
                'icon': weather_icon
            },
            'today': {
                'high': today_high,
                'low': today_low,
                'sunrise': today_data.get('sunrise', 'N/A'),
                'sunset': today_data.get('sunset', 'N/A'),
                'uv_index': today_data.get('uv_index', 0),
                'precip_prob': today_data.get('precip_prob', 0),
                'precip_sum': today_data.get('precip_sum', 0)
            },
            'forecast': forecast_list,
            'hourly': hourly_list
        }
        print(f"[DEBUG] Returning weather result with {len(forecast_list)} forecast days and {len(hourly_list)} hourly entries")
        return result
    except Exception as e:
        print(f"[ERROR] Weather fetch error: {e}")
        import traceback
        traceback.print_exc()
        return None

def map_weather_code(code):
    """Map WMO weather codes to descriptions and Font Awesome icons"""
    weather_map = {
        0: ("Clear sky", "fa-sun"),
        1: ("Mainly clear", "fa-sun"),
        2: ("Partly cloudy", "fa-cloud-sun"),
        3: ("Overcast", "fa-cloud"),
        45: ("Foggy", "fa-smog"),
        48: ("Foggy", "fa-smog"),
        51: ("Light drizzle", "fa-cloud-rain"),
        53: ("Moderate drizzle", "fa-cloud-rain"),
        55: ("Dense drizzle", "fa-cloud-rain"),
        61: ("Slight rain", "fa-cloud-rain"),
        63: ("Moderate rain", "fa-cloud-showers-heavy"),
        65: ("Heavy rain", "fa-cloud-showers-heavy"),
        71: ("Slight snow", "fa-snowflake"),
        73: ("Moderate snow", "fa-snowflake"),
        75: ("Heavy snow", "fa-snowflake"),
        77: ("Snow grains", "fa-snowflake"),
        80: ("Slight rain showers", "fa-cloud-rain"),
        81: ("Moderate rain showers", "fa-cloud-showers-heavy"),
        82: ("Violent rain showers", "fa-cloud-showers-heavy"),
        85: ("Slight snow showers", "fa-snowflake"),
        86: ("Heavy snow showers", "fa-snowflake"),
        95: ("Thunderstorm", "fa-cloud-bolt"),
        96: ("Thunderstorm with hail", "fa-cloud-bolt"),
        99: ("Thunderstorm with heavy hail", "fa-cloud-bolt"),
    }
    return weather_map.get(code, ("Unknown", "fa-cloud"))

@main_bp.route('/weather')
def weather():
    print("[DEBUG] /weather route called")
    config = current_app.config['HOMEHUB_CONFIG']
    # Default to ZIP 47725
    weather_data = get_weather_data(location="47725")
    print(f"[DEBUG] Weather page rendering with data: {weather_data}")
    return render_template('weather.html', config=config, is_authed=True, weather=weather_data, location="47725")

@main_bp.route('/weather/update', methods=['POST'])
def weather_update():
    print("[DEBUG] /weather/update route called")
    data = request.get_json()
    print(f"[DEBUG] Request data: {data}")
    location = data.get('location')
    lat = data.get('lat')
    lon = data.get('lon')

    weather_data = get_weather_data(location=location, lat=lat, lon=lon)
    print(f"[DEBUG] Weather update result: {weather_data}")

    if weather_data:
        response = jsonify({'success': True, 'weather': weather_data})
        print(f"[DEBUG] Returning success response")
        return response
    else:
        print("[DEBUG] Returning error response")
        return jsonify({'success': False, 'error': 'Could not fetch weather data'})

@main_bp.route('/api/weather', methods=['GET'])
def api_weather():
    """API endpoint for weather data (used by widget)"""
    location = request.args.get('zip')
    lat = request.args.get('lat')
    lon = request.args.get('lon')

    weather_data = get_weather_data(location=location, lat=lat, lon=lon)

    if weather_data:
        return jsonify(weather_data)
    else:
        return jsonify({'error': 'Could not fetch weather data'}), 400

# Countdown Timers
@main_bp.route('/countdowns')
def countdowns():
    from .models import Countdown
    config = current_app.config['HOMEHUB_CONFIG']
    countdowns = Countdown.query.order_by(Countdown.event_date).all()

    # Mark if events are past
    today = date.today()
    for countdown in countdowns:
        countdown.is_past = countdown.event_date < today

    return render_template('countdowns.html', config=config, is_authed=True, countdowns=countdowns)

@main_bp.route('/countdowns/add', methods=['POST'])
def countdowns_add():
    from .models import Countdown
    countdown = Countdown(
        event_name=request.form.get('event_name'),
        event_date=datetime.strptime(request.form.get('event_date'), '%Y-%m-%d').date(),
        icon=request.form.get('icon', 'calendar-day'),
        description=request.form.get('description'),
        creator=request.form.get('creator', 'Unknown')
    )
    db.session.add(countdown)
    db.session.commit()
    flash('Countdown added!', 'success')
    return redirect(url_for('main.countdowns'))

@main_bp.route('/countdowns/delete/<int:countdown_id>', methods=['POST'])
def countdowns_delete(countdown_id):
    from .models import Countdown
    countdown = Countdown.query.get_or_404(countdown_id)
    db.session.delete(countdown)
    db.session.commit()
    return jsonify({'success': True})