#!/usr/bin/env python3
import sqlite3
import os
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
import csv
import io

app = Flask(__name__)
app.config['DATABASE'] = 'speech_therapy.db'

def init_db():
    """Initialize the database with required tables and indices."""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.execute('PRAGMA foreign_keys = ON')
    
    # Create tables
    conn.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT UNIQUE NOT NULL
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS objectives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            objective_text TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            objective_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (objective_id) REFERENCES objectives (id) ON DELETE CASCADE,
            UNIQUE(session_id, student_id, objective_id)
        )
    ''')
    
    # Create indices for performance
    conn.execute('CREATE INDEX IF NOT EXISTS idx_objectives_student_id ON objectives (student_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_events_student_id ON events (student_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_events_objective_id ON events (objective_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id)')
    
    conn.commit()
    conn.close()

def get_db():
    """Get database connection."""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.execute('PRAGMA foreign_keys = ON')
    conn.row_factory = sqlite3.Row
    return conn

def get_or_create_session(date_str):
    """Get or create a session for the given date."""
    conn = get_db()
    cursor = conn.execute('SELECT id FROM sessions WHERE date = ?', (date_str,))
    session = cursor.fetchone()
    
    if session:
        session_id = session['id']
    else:
        cursor = conn.execute('INSERT INTO sessions (date) VALUES (?)', (date_str,))
        session_id = cursor.lastrowid
        conn.commit()
    
    conn.close()
    return session_id

@app.route('/')
def index():
    """Redirect to collect page."""
    return redirect(url_for('collect'))

@app.route('/students')
def students():
    """Show students and objectives management page."""
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
def add_student():
    """Add a new student."""
    first_name = request.form.get('first_name', '').strip()
    
    if not first_name:
        return jsonify({'error': 'First name is required'}), 400
    
    conn = get_db()
    try:
        cursor = conn.execute('INSERT INTO students (first_name) VALUES (?)', (first_name,))
        student_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'student_id': student_id})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Student name already exists'}), 400

@app.route('/students/delete', methods=['POST'])
def delete_student():
    """Delete a student and all their objectives/events."""
    student_id = request.form.get('student_id')
    
    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400
    
    conn = get_db()
    conn.execute('DELETE FROM students WHERE id = ?', (student_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/objectives/save', methods=['POST'])
def save_objectives():
    """Save objectives for a student."""
    student_id = request.form.get('student_id')
    objectives_text = request.form.get('objectives_text', '').strip()
    
    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400
    
    conn = get_db()
    
    # Delete existing objectives for this student
    conn.execute('DELETE FROM objectives WHERE student_id = ?', (student_id,))
    
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
            conn.execute('INSERT INTO objectives (student_id, objective_text) VALUES (?, ?)',
                        (student_id, obj_text))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/objectives/delete', methods=['POST'])
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

@app.route('/collect')
def collect():
    """Show data collection page."""
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
def increment_event():
    """Increment count for an objective on a date."""
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
    
    if event:
        new_count = event['count'] + 1
        conn.execute('''
            UPDATE events SET count = ? 
            WHERE session_id = ? AND student_id = ? AND objective_id = ?
        ''', (new_count, session_id, student_id, objective_id))
    else:
        new_count = 1
        conn.execute('''
            INSERT INTO events (session_id, student_id, objective_id, count)
            VALUES (?, ?, ?, ?)
        ''', (session_id, student_id, objective_id, new_count))
    
    conn.commit()
    conn.close()
    
    return jsonify({'count': new_count})

@app.route('/event/decrement', methods=['POST'])
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

@app.route('/report')
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

if __name__ == '__main__':
    # Initialize database on startup
    init_db()
    
    # Run the app
    app.run(host='0.0.0.0', port=5000, debug=True)