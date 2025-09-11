#!/usr/bin/env python3
import os
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file, session
from flask_login import current_user
import csv
import io
import openpyxl
from werkzeug.middleware.proxy_fix import ProxyFix
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
app = Flask(__name__)

# Set secret key - use different keys for production vs development
if os.environ.get("REPLIT_DEPLOYMENT") == "1":
    # Production environment - use a secure secret key
    app.secret_key = os.environ.get("SESSION_SECRET", "production-speech-therapy-secret-2024")
else:
    # Development environment
    app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-for-speech-therapy-app-12345")

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Import database and auth after app creation
from models import db, User, Organization, Membership, Student, Objective, Session, Event, ObjectiveItem, EventSelection
from replit_auth import login_manager, make_replit_blueprint, require_login

# Initialize login manager
login_manager.init_app(app)

# Register auth blueprint
app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")

# Make session permanent
@app.before_request
def make_session_permanent():
    session.permanent = True

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    'pool_pre_ping': True,
    "pool_recycle": 300,
}

# Initialize database
db.init_app(app)

# Create tables
with app.app_context():
    db.create_all()
    logging.info("Database tables created")

# Database tables are managed by SQLAlchemy models in models.py

def get_db():
    """Get database session using SQLAlchemy."""
    return db.session

def get_or_create_session(date_str):
    """Get or create a session for the given date."""
    from datetime import datetime
    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # For now, we'll use a default organization until proper auth is implemented
    # TODO: Get organization from current user context
    organization = Organization.query.first()
    if not organization:
        # Create a default organization if none exists
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
    # Check if session exists
    session = Session.query.filter_by(organization_id=organization.id, date=date_obj).first()
    
    if session:
        return session.id
    else:
        # Create new session
        new_session = Session(organization_id=organization.id, date=date_obj)
        db.session.add(new_session)
        db.session.commit()
        return new_session.id

@app.route('/')
def index():
    """Landing page - shows login for logged out users, redirects to collect for logged in users."""
    if current_user.is_authenticated:
        return redirect(url_for('collect'))
    else:
        return render_template('landing.html')

@app.route('/health')
def health_check():
    """Dedicated health check endpoint for deployment monitoring."""
    try:
        # Quick database connectivity check
        db.session.execute(db.text('SELECT 1'))
        return jsonify({'status': 'healthy', 'service': 'speech-therapy-app'}), 200
    except Exception as e:
        app.logger.error(f"Health check failed: {str(e)}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 503

@app.route('/students')
@require_login
def students():
    """Show students and objectives management page. Login required to protect client data."""
    conn = get_db()
    
    # Get all students with their objectives
    students_data = conn.execute('''
        SELECT s.id, s.first_name,
               GROUP_CONCAT(o.id || '|' || o.objective_text, '|||') as objectives
        FROM students s
        LEFT JOIN objectives o ON s.id = o.student_id
        GROUP BY s.id, s.first_name
        ORDER BY s.first_name
    ''').fetchall()
    
    conn.close()
    
    # Parse objectives for each student
    students = []
    for row in students_data:
        student = {
            'id': row['id'],
            'first_name': row['first_name'],
            'objectives': []
        }
        
        if row['objectives']:
            for obj_str in row['objectives'].split('|||'):
                if obj_str:
                    obj_id, obj_text = obj_str.split('|', 1)
                    student['objectives'].append({
                        'id': int(obj_id),
                        'text': obj_text
                    })
        
        students.append(student)
    
    return render_template('students.html', students=students)

@app.route('/students/add', methods=['POST'])
@require_login
def add_student():
    """Add a new student."""
    first_name = request.form.get('first_name', '').strip()
    
    if not first_name:
        return jsonify({'error': 'First name is required'}), 400
    
    try:
        # Get default organization
        organization = Organization.query.first()
        if not organization:
            organization = Organization(name="Default Clinic", subdomain="default")
            db.session.add(organization)
            db.session.commit()
        
        # Check if student already exists in this organization
        existing_student = Student.query.filter_by(organization_id=organization.id, first_name=first_name).first()
        if existing_student:
            return jsonify({'error': 'Student name already exists'}), 400
        
        # Create new student
        student = Student(organization_id=organization.id, first_name=first_name)
        db.session.add(student)
        db.session.commit()
        return jsonify({'success': True, 'student_id': student.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/students/delete', methods=['POST'])
@require_login
def delete_student():
    """Delete a student and all their objectives/events."""
    student_id = request.form.get('student_id')
    
    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400
    
    try:
        student = Student.query.get(student_id)
        if student:
            db.session.delete(student)
            db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/objectives/save', methods=['POST'])
@require_login
def save_objectives():
    """Save objectives for a student."""
    student_id = request.form.get('student_id')
    objectives_text = request.form.get('objectives_text', '').strip()
    
    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400
    
    try:
        # Delete existing objectives for this student
        Objective.query.filter_by(student_id=student_id).delete()
        
        # Parse and save new objectives
        if objectives_text:
            lines = [line.strip() for line in objectives_text.split('\n') if line.strip()]
            
            # Handle numbered lists (remove numbers)
            objectives = []
            for line in lines:
                # Remove leading numbers like "1.", "2.", etc.
                import re
                cleaned = re.sub(r'^\d+\.\s*', '', line).strip()
                if cleaned:
                    objectives.append(cleaned)
            
            # Insert new objectives
            for obj_text in objectives:
                objective = Objective(student_id=student_id, objective_text=obj_text)
                db.session.add(objective)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/objectives/delete', methods=['POST'])
@require_login
def delete_objective():
    """Delete a specific objective."""
    objective_id = request.form.get('objective_id')
    
    if not objective_id:
        return jsonify({'error': 'Objective ID is required'}), 400
    
    conn = get_db()
    conn.execute('DELETE FROM objectives WHERE id = ?', (objective_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/objective_items/save', methods=['POST'])
@require_login
def save_objective_items():
    """Save items for a specific objective."""
    objective_id = request.form.get('objective_id')
    items_text = request.form.get('items_text', '').strip()
    
    if not objective_id:
        return jsonify({'error': 'Objective ID is required'}), 400
    
    conn = get_db()
    
    # Delete existing items for this objective
    conn.execute('DELETE FROM objective_items WHERE objective_id = ?', (objective_id,))
    
    # Parse and save new items
    if items_text:
        lines = [line.strip() for line in items_text.split('\n') if line.strip()]
        
        # Handle numbered lists (remove numbers)
        items = []
        for line in lines:
            # Remove leading numbers like "1.", "2.", etc.
            import re
            cleaned = re.sub(r'^\d+\.\s*', '', line).strip()
            # Remove leading bullets like "•", "-", "*"
            cleaned = re.sub(r'^[•\-\*]\s*', '', cleaned).strip()
            if cleaned:
                items.append(cleaned)
        
        # Insert new items with display order
        for i, item_text in enumerate(items):
            conn.execute('INSERT INTO objective_items (objective_id, item_text, display_order) VALUES (?, ?, ?)',
                        (objective_id, item_text, i))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/objective_items/get/<int:objective_id>')
@require_login
def get_objective_items(objective_id):
    """Get items for a specific objective."""
    conn = get_db()
    
    items = conn.execute('''
        SELECT id, item_text, display_order
        FROM objective_items
        WHERE objective_id = ?
        ORDER BY display_order
    ''', (objective_id,)).fetchall()
    
    conn.close()
    
    items_list = [{'id': item['id'], 'text': item['item_text'], 'order': item['display_order']} for item in items]
    
    return jsonify({'items': items_list})

@app.route('/objective_items/delete', methods=['POST'])
@require_login
def delete_objective_item():
    """Delete a specific objective item."""
    item_id = request.form.get('item_id')
    
    if not item_id:
        return jsonify({'error': 'Item ID is required'}), 400
    
    conn = get_db()
    conn.execute('DELETE FROM objective_items WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/collect')
@require_login
def collect():
    """Show data collection page. Login required to protect client data."""
    conn = get_db()
    
    # Get all students with their objectives
    students_data = conn.execute('''
        SELECT s.id, s.first_name,
               GROUP_CONCAT(o.id || '|' || o.objective_text, '|||') as objectives
        FROM students s
        LEFT JOIN objectives o ON s.id = o.student_id
        GROUP BY s.id, s.first_name
        ORDER BY s.first_name
    ''').fetchall()
    
    conn.close()
    
    # Parse objectives for each student
    students = []
    for row in students_data:
        student = {
            'id': row['id'],
            'first_name': row['first_name'],
            'objectives': []
        }
        
        if row['objectives']:
            for obj_str in row['objectives'].split('|||'):
                if obj_str:
                    obj_id, obj_text = obj_str.split('|', 1)
                    student['objectives'].append({
                        'id': int(obj_id),
                        'text': obj_text
                    })
        
        students.append(student)
    
    today = date.today().isoformat()
    return render_template('collect.html', students=students, today=today)

@app.route('/event/increment', methods=['POST'])
@require_login
def increment_event():
    """Increment count for an objective on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    activity = data.get('activity', '')
    prompt_level = data.get('prompt_level', '')
    
    if not all([date_str, student_id, objective_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    conn = get_db()
    
    # Check if event exists
    cursor = conn.execute('''
        SELECT count FROM events 
        WHERE session_id = ? AND student_id = ? AND objective_id = ?
    ''', (session_id, student_id, objective_id))
    
    event = cursor.fetchone()
    
    if event:
        new_count = event['count'] + 1
        conn.execute('''
            UPDATE events SET count = ?, activity = ?, prompt_level = ? 
            WHERE session_id = ? AND student_id = ? AND objective_id = ?
        ''', (new_count, activity, prompt_level, session_id, student_id, objective_id))
    else:
        new_count = 1
        conn.execute('''
            INSERT INTO events (session_id, student_id, objective_id, count, activity, prompt_level)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, student_id, objective_id, new_count, activity, prompt_level))
    
    conn.commit()
    conn.close()
    
    return jsonify({'count': new_count})

@app.route('/event/decrement', methods=['POST'])
@require_login
def decrement_event():
    """Decrement count for an objective on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    
    if not all([date_str, student_id, objective_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    conn = get_db()
    
    # Check if event exists
    cursor = conn.execute('''
        SELECT count FROM events 
        WHERE session_id = ? AND student_id = ? AND objective_id = ?
    ''', (session_id, student_id, objective_id))
    
    event = cursor.fetchone()
    
    if event and event['count'] > 0:
        new_count = event['count'] - 1
        if new_count == 0:
            # Remove the event if count reaches 0
            conn.execute('''
                DELETE FROM events 
                WHERE session_id = ? AND student_id = ? AND objective_id = ?
            ''', (session_id, student_id, objective_id))
        else:
            conn.execute('''
                UPDATE events SET count = ? 
                WHERE session_id = ? AND student_id = ? AND objective_id = ?
            ''', (new_count, session_id, student_id, objective_id))
        conn.commit()
        conn.close()
        return jsonify({'count': new_count})
    else:
        conn.close()
        return jsonify({'count': 0})

@app.route('/event/save_notes', methods=['POST'])
@require_login
def save_notes():
    """Save notes for a student on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    notes = data.get('notes', '').strip()
    
    if not all([date_str, student_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    conn = get_db()
    
    # Update notes for all events for this student on this date
    conn.execute('''
        UPDATE events SET notes = ? 
        WHERE session_id = ? AND student_id = ?
    ''', (notes if notes else None, session_id, student_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/event/counts', methods=['GET'])
@require_login
def get_counts():
    """Get existing counts for a date and students."""
    date_str = request.args.get('date')
    student_ids = request.args.getlist('student_id')
    
    if not date_str or not student_ids:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    conn = get_db()
    
    # Get session for this date
    cursor = conn.execute('SELECT id FROM sessions WHERE date = ?', (date_str,))
    session = cursor.fetchone()
    
    if not session:
        conn.close()
        return jsonify({'counts': {}})
    
    session_id = session['id']
    
    # Get all counts for this session and students
    placeholders = ','.join(['?' for _ in student_ids])
    query = f'''
        SELECT student_id, objective_id, count, notes
        FROM events 
        WHERE session_id = ? AND student_id IN ({placeholders})
    '''
    
    params = [session_id] + student_ids
    events = conn.execute(query, params).fetchall()
    conn.close()
    
    # Format response
    counts = {}
    notes = {}
    for event in events:
        key = f"{event['student_id']}-{event['objective_id']}"
        counts[key] = event['count']
        if event['notes']:
            notes[str(event['student_id'])] = event['notes']
    
    return jsonify({'counts': counts, 'notes': notes})

@app.route('/event/toggle_item_selection', methods=['POST'])
def toggle_item_selection():
    """Toggle selection of a specific objective item for an event."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    item_id = data.get('item_id')
    selected = data.get('selected', False)
    activity = data.get('activity', '').strip()
    prompt_level = data.get('prompt_level', '').strip()
    
    if not all([date_str, student_id, objective_id, item_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    conn = get_db()
    
    # Get or create event for this session/student/objective
    cursor = conn.execute('''
        SELECT id FROM events 
        WHERE session_id = ? AND student_id = ? AND objective_id = ?
    ''', (session_id, student_id, objective_id))
    event = cursor.fetchone()
    
    if event:
        event_id = event['id']
        # Update existing event with activity and prompt_level if provided
        conn.execute('''
            UPDATE events SET activity = ?, prompt_level = ? 
            WHERE id = ?
        ''', (activity if activity else None, prompt_level if prompt_level else None, event_id))
    else:
        # Create new event
        cursor = conn.execute('''
            INSERT INTO events (session_id, student_id, objective_id, count, activity, prompt_level) 
            VALUES (?, ?, ?, 0, ?, ?)
        ''', (session_id, student_id, objective_id, activity if activity else None, prompt_level if prompt_level else None))
        event_id = cursor.lastrowid
    
    # Handle item selection
    if selected:
        # Add or update selection
        conn.execute('''
            INSERT OR REPLACE INTO event_selections (event_id, objective_item_id, selected) 
            VALUES (?, ?, 1)
        ''', (event_id, item_id))
    else:
        # Remove selection
        conn.execute('''
            DELETE FROM event_selections 
            WHERE event_id = ? AND objective_item_id = ?
        ''', (event_id, item_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/event/selections', methods=['GET'])
@require_login
def get_selections():
    """Get existing item selections for a date and students."""
    date_str = request.args.get('date')
    student_ids = request.args.getlist('student_id')
    
    if not date_str or not student_ids:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    conn = get_db()
    
    # Get session for this date
    cursor = conn.execute('SELECT id FROM sessions WHERE date = ?', (date_str,))
    session = cursor.fetchone()
    
    if not session:
        conn.close()
        return jsonify({'selections': {}, 'activities': {}, 'prompt_levels': {}, 'notes': {}})
    
    session_id = session['id']
    
    # Get all events and their selections for this session and students
    placeholders = ','.join(['?' for _ in student_ids])
    query = f'''
        SELECT e.student_id, e.objective_id, e.activity, e.prompt_level, e.notes,
               es.objective_item_id
        FROM events e
        LEFT JOIN event_selections es ON e.id = es.event_id AND es.selected = 1
        WHERE e.session_id = ? AND e.student_id IN ({placeholders})
    '''
    
    params = [session_id] + student_ids
    results = conn.execute(query, params).fetchall()
    conn.close()
    
    # Format response
    selections = {}
    activities = {}
    prompt_levels = {}
    notes = {}
    
    for row in results:
        key = f"{row['student_id']}-{row['objective_id']}"
        
        # Collect selected items
        if row['objective_item_id'] is not None:
            if key not in selections:
                selections[key] = []
            selections[key].append(row['objective_item_id'])
        
        # Collect activities and prompt levels
        if row['activity']:
            activities[key] = row['activity']
        if row['prompt_level']:
            prompt_levels[key] = row['prompt_level']
        if row['notes']:
            notes[str(row['student_id'])] = row['notes']
    
    return jsonify({
        'selections': selections,
        'activities': activities, 
        'prompt_levels': prompt_levels,
        'notes': notes
    })

@app.route('/report')
@require_login
def report():
    """Show reports page with filtering."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Get all students for the filter dropdown
    conn = get_db()
    students = conn.execute('SELECT id, first_name FROM students ORDER BY first_name').fetchall()
    
    # Default date range (last 30 days)
    if not start_date:
        start_date = (date.today().replace(day=1)).isoformat()
    if not end_date:
        end_date = date.today().isoformat()
    
    # Build query based on filters
    params = []
    where_clauses = []
    
    if start_date:
        where_clauses.append('s.date >= ?')
        params.append(start_date)
    
    if end_date:
        where_clauses.append('s.date <= ?')
        params.append(end_date)
    
    if student_id:
        where_clauses.append('st.id = ?')
        params.append(student_id)
    
    where_sql = 'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''
    
    if report_type == 'summary':
        # Summary report: student, objective, total count, date range
        query = f'''
            SELECT st.first_name as student, o.objective_text as objective,
                   SUM(e.count) as total_count,
                   MIN(s.date) as start_date,
                   MAX(s.date) as end_date
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            GROUP BY st.id, o.id
            ORDER BY st.first_name, o.objective_text
        '''
    else:
        # By objective report: date, student, objective, count, notes
        query = f'''
            SELECT s.date, st.first_name as student, o.objective_text as objective,
                   e.count, e.notes
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            ORDER BY s.date DESC, st.first_name, o.objective_text
        '''
    
    report_data = conn.execute(query, params).fetchall()
    conn.close()
    
    return render_template('report.html', 
                         report_data=report_data,
                         students=students,
                         start_date=start_date,
                         end_date=end_date,
                         student_id=student_id,
                         report_type=report_type)

@app.route('/report.csv')
@require_login
def report_csv():
    """Export report as CSV."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Same query logic as report()
    conn = get_db()
    params = []
    where_clauses = []
    
    if start_date:
        where_clauses.append('s.date >= ?')
        params.append(start_date)
    
    if end_date:
        where_clauses.append('s.date <= ?')
        params.append(end_date)
    
    if student_id:
        where_clauses.append('st.id = ?')
        params.append(student_id)
    
    where_sql = 'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''
    
    if report_type == 'summary':
        query = f'''
            SELECT st.first_name as student, o.objective_text as objective,
                   SUM(e.count) as total_count,
                   MIN(s.date) as start_date,
                   MAX(s.date) as end_date
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            GROUP BY st.id, o.id
            ORDER BY st.first_name, o.objective_text
        '''
        headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
    else:
        query = f'''
            SELECT s.date, st.first_name as student, o.objective_text as objective,
                   e.count, e.notes
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            ORDER BY s.date DESC, st.first_name, o.objective_text
        '''
        headers = ['Date', 'Student', 'Objective', 'Count', 'Notes']
    
    report_data = conn.execute(query, params).fetchall()
    conn.close()
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    
    for row in report_data:
        writer.writerow([row[i] if row[i] is not None else '' for i in range(len(headers))])
    
    output.seek(0)
    
    # Create a file-like object for sending
    csv_output = io.BytesIO()
    csv_output.write(output.getvalue().encode('utf-8'))
    csv_output.seek(0)
    
    filename = f"speech_therapy_report_{report_type}_{datetime.now().strftime('%Y%m%d')}.csv"
    
    return send_file(csv_output, 
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name=filename)

@app.route('/report.tsv')
@require_login
def report_tsv():
    """Export report as TSV for clipboard."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Same query logic as report()
    conn = get_db()
    params = []
    where_clauses = []
    
    if start_date:
        where_clauses.append('s.date >= ?')
        params.append(start_date)
    
    if end_date:
        where_clauses.append('s.date <= ?')
        params.append(end_date)
    
    if student_id:
        where_clauses.append('st.id = ?')
        params.append(student_id)
    
    where_sql = 'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''
    
    if report_type == 'summary':
        query = f'''
            SELECT st.first_name as student, o.objective_text as objective,
                   SUM(e.count) as total_count,
                   MIN(s.date) as start_date,
                   MAX(s.date) as end_date
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            GROUP BY st.id, o.id
            ORDER BY st.first_name, o.objective_text
        '''
        headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
    else:
        query = f'''
            SELECT s.date, st.first_name as student, o.objective_text as objective,
                   e.count, e.notes
            FROM events e
            JOIN sessions s ON e.session_id = s.id
            JOIN students st ON e.student_id = st.id
            JOIN objectives o ON e.objective_id = o.id
            {where_sql}
            ORDER BY s.date DESC, st.first_name, o.objective_text
        '''
        headers = ['Date', 'Student', 'Objective', 'Count', 'Notes']
    
    report_data = conn.execute(query, params).fetchall()
    conn.close()
    
    # Create TSV
    lines = []
    lines.append('\t'.join(headers))
    
    for row in report_data:
        line = '\t'.join([str(row[i]) if row[i] is not None else '' for i in range(len(headers))])
        lines.append(line)
    
    tsv_content = '\n'.join(lines)
    
    return tsv_content, 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/admin/import_spreadsheet', methods=['GET', 'POST'])
@require_login
def import_spreadsheet():
    """Import data from the Excel spreadsheet."""
    if request.method == 'GET':
        return render_template('import.html')
    
    try:
        # For now, use the attached file directly
        filename = 'attached_assets/Sam_Student_Dat_25-26_1757599841065.xlsx'
        
        if not os.path.exists(filename):
            return jsonify({'error': 'Spreadsheet file not found'}), 400
        
        # Try loading the workbook with different parameters
        try:
            workbook = openpyxl.load_workbook(filename, data_only=True)
        except Exception as e1:
            print(f"Failed with data_only=True: {e1}")
            try:
                workbook = openpyxl.load_workbook(filename)
            except Exception as e2:
                print(f"Failed with default params: {e2}")
                raise Exception(f"Could not load workbook: {e1}, {e2}")
        
        # Analyze all sheets first
        analysis = analyze_workbook(workbook)
        
        # Import data from Database sheet if it exists
        import_results = {}
        if 'database' in [sheet.lower() for sheet in workbook.sheetnames]:
            database_sheet_name = next(sheet for sheet in workbook.sheetnames if sheet.lower() == 'database')
            import_results['students'] = import_database_sheet(workbook[database_sheet_name])
        
        # Analyze data collection sheet structure
        data_collection_sheets = [sheet for sheet in workbook.sheetnames if 'data' in sheet.lower() and 'collection' in sheet.lower()]
        if data_collection_sheets:
            sheet_name = data_collection_sheets[0]
            import_results['data_collection_structure'] = analyze_data_collection_sheet(workbook[sheet_name])
        
        workbook.close()
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'import_results': import_results
        })
        
    except Exception as e:
        return jsonify({'error': f'Import failed: {str(e)}'}), 500

def analyze_workbook(workbook):
    """Analyze the entire workbook structure."""
    analysis = {}
    
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        
        # Get basic sheet info with null checks
        max_row = sheet.max_row or 0
        max_col = sheet.max_column or 0
        
        sheet_info = {
            'dimensions': f"{max_row} rows x {max_col} columns",
            'sheet_name': sheet_name
        }
        
        # Sample headers (first row)
        headers = []
        if max_row > 0 and max_col > 0:
            for col in range(1, min(max_col + 1, 11)):  # First 10 columns
                cell_value = sheet.cell(row=1, column=col).value
                if cell_value:
                    headers.append(str(cell_value).strip())
                else:
                    headers.append("")
        
        sheet_info['headers'] = headers
        analysis[sheet_name] = sheet_info
    
    return analysis

def import_database_sheet(sheet):
    """Import student data from the Database sheet."""
    results = {'students_added': 0, 'objectives_added': 0, 'students': []}
    
    conn = get_db()
    max_col = sheet.max_column or 0
    max_row = sheet.max_row or 0
    
    try:
        # Assume first row contains student names as headers
        student_columns = []
        for col in range(1, max_col + 1):
            header = sheet.cell(row=1, column=col).value
            if header and str(header).strip():
                student_name = str(header).strip()
                student_columns.append((col, student_name))
        
        for col, student_name in student_columns:
            try:
                # Add student (ignore duplicates)
                cursor = conn.execute('INSERT OR IGNORE INTO students (first_name) VALUES (?)', (student_name,))
                if cursor.rowcount > 0:
                    results['students_added'] += 1
                
                # Get student ID
                student_row = conn.execute('SELECT id FROM students WHERE first_name = ?', (student_name,)).fetchone()
                student_id = student_row['id']
                
                # Clear existing objectives for this student
                conn.execute('DELETE FROM objectives WHERE student_id = ?', (student_id,))
                
                # Extract objectives from column
                objectives = []
                for row in range(2, max_row + 1):
                    cell_value = sheet.cell(row=row, column=col).value
                    if cell_value and str(cell_value).strip():
                        objective_text = str(cell_value).strip()
                        # Remove numbering if present (1., 2., etc.)
                        import re
                        objective_text = re.sub(r'^\d+\.\s*', '', objective_text)
                        if objective_text:
                            objectives.append(objective_text)
                
                # Insert objectives
                for objective_text in objectives:
                    conn.execute('INSERT INTO objectives (student_id, objective_text) VALUES (?, ?)',
                               (student_id, objective_text))
                    results['objectives_added'] += 1
                
                results['students'].append({
                    'name': student_name,
                    'objectives_count': len(objectives),
                    'objectives': objectives[:5]  # First 5 for preview
                })
                
            except Exception as e:
                print(f"Error importing student {student_name}: {e}")
                continue
        
        conn.commit()
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
    
    return results

def analyze_data_collection_sheet(sheet):
    """Analyze the data collection sheet to understand its structure."""
    structure = {
        'headers': [],
        'sample_data': [],
        'column_analysis': {}
    }
    
    max_col = sheet.max_column or 0
    max_row = sheet.max_row or 0
    
    # Get headers
    for col in range(1, max_col + 1):
        header = sheet.cell(row=1, column=col).value
        if header:
            structure['headers'].append(str(header).strip())
        else:
            structure['headers'].append(f"Column_{col}")
    
    # Get sample data (first 5 rows after header)
    for row in range(2, min(7, max_row + 1)):
        row_data = []
        for col in range(1, max_col + 1):
            cell_value = sheet.cell(row=row, column=col).value
            if cell_value is not None:
                row_data.append(str(cell_value))
            else:
                row_data.append("")
        structure['sample_data'].append(row_data)
    
    # Analyze column types and purposes
    for i, header in enumerate(structure['headers']):
        header_lower = header.lower()
        analysis = {'suggested_type': 'text', 'likely_purpose': 'data'}
        
        if any(term in header_lower for term in ['date', 'time']):
            analysis['suggested_type'] = 'date'
            analysis['likely_purpose'] = 'date'
        elif any(term in header_lower for term in ['activity', 'task']):
            analysis['likely_purpose'] = 'activity'
        elif any(term in header_lower for term in ['data', 'score', 'count', 'result']):
            analysis['likely_purpose'] = 'data'
            analysis['suggested_type'] = 'number'
        elif any(term in header_lower for term in ['prompt', 'cue', 'support']):
            analysis['likely_purpose'] = 'prompt_level'
        elif any(term in header_lower for term in ['note', 'comment']):
            analysis['likely_purpose'] = 'notes'
        
        structure['column_analysis'][header] = analysis
    
    return structure

if __name__ == '__main__':
    # Get port from environment variable (for production) or default to 5000 (for development)
    port = int(os.environ.get('PORT', 5000))
    
    # Set debug mode based on environment
    debug_mode = os.environ.get("REPLIT_DEPLOYMENT") != "1"
    
    # Run the app
    app.run(host='0.0.0.0', port=port, debug=debug_mode)