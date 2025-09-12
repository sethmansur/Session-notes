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
import stripe

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
app = Flask(__name__)

# Set secret key - require secure key in production
if os.environ.get("REPLIT_DEPLOYMENT") == "1":
    # Production environment - require a secure secret key
    session_secret = os.environ.get("SESSION_SECRET")
    if not session_secret:
        logging.error("SESSION_SECRET environment variable is required in production but not set")
        raise SystemExit("SESSION_SECRET environment variable must be configured for production deployment")
    app.secret_key = session_secret
else:
    # Development environment
    app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-for-speech-therapy-app-12345")

# Configure server name only when absolutely needed for OAuth
# This allows localhost testing while fixing OAuth redirect URI issues
if os.environ.get('REPLIT_DEPLOYMENT') == '1':
    # In production, use the proper domain
    if os.environ.get('REPLIT_DOMAINS'):
        app.config['SERVER_NAME'] = os.environ['REPLIT_DOMAINS'].split(',')[0]

# Configure file upload limits (10MB max)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# Configure for HTTPS on Replit
app.config['PREFERRED_URL_SCHEME'] = 'https'

# Configure OAuth transport security based on environment
import os
if os.environ.get("REPLIT_DEPLOYMENT") != "1":
    # Only enable insecure transport in development environment
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Stripe configuration with error handling
stripe_secret_key = os.environ.get('STRIPE_SECRET_KEY')
if not stripe_secret_key:
    logging.warning("STRIPE_SECRET_KEY not set - payment functionality will be disabled")
    # Set a placeholder to prevent None errors, but payments will fail gracefully
    stripe.api_key = None
else:
    stripe.api_key = stripe_secret_key

# Domain configuration with fallback handling
YOUR_DOMAIN = os.environ.get('REPLIT_DEV_DOMAIN') if os.environ.get('REPLIT_DEPLOYMENT') != '1' else os.environ.get('REPLIT_DOMAINS', '').split(',')[0] if os.environ.get('REPLIT_DOMAINS') else 'localhost:5000'

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)

# Handle file upload size limit errors
@app.errorhandler(413)
def file_too_large(error):
    return jsonify({'error': 'File too large. Please upload a file smaller than 10MB.'}), 413

# Import database and auth after app creation
from models import db, User, Organization, Membership, Student, Objective, Session, Event, ObjectiveItem, EventSelection
from replit_auth import login_manager, make_replit_blueprint
from flask_login import login_required, current_user
from functools import wraps

# Initialize login manager
login_manager.init_app(app)

# Register secure OAuth blueprint
app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")

# Use secure Flask-Dance OAuth implementation from replit_auth.py

# Custom authentication decorators (removed duplicate - keeping the more complete version below)

# Make session permanent
@app.before_request
def make_session_permanent():
    session.permanent = True

# Database configuration with error handling
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    logging.error("DATABASE_URL environment variable is required but not set")
    raise SystemExit("DATABASE_URL environment variable must be configured for the application to start")

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
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

def require_subscription(f):
    """Decorator to require active subscription (trial or paid)"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # First require login
        if not current_user.is_authenticated:
            return redirect('/auth/replit_auth')
        
        # Check if user has active subscription
        if not current_user.has_active_subscription():
            # Trial expired and no active subscription
            return redirect(url_for('upgrade'))
        
        return f(*args, **kwargs)
    return decorated_function

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
    """Public marketing page for SessionNotes SaaS."""
    return render_template('marketing.html')

@app.route('/app')
@require_subscription
def app_dashboard():
    """Authenticated app dashboard - redirects to collect page."""
    return redirect(url_for('collect'))

# Subscription management routes
@app.route('/upgrade')
@require_subscription
def upgrade():
    """Upgrade page with subscription plans"""
    days_left = 0
    if current_user.subscription_status == 'trial':
        days_left = current_user.days_left_in_trial()
    
    return render_template('upgrade.html', days_left=days_left)

@app.route('/create-checkout-session', methods=['POST'])
@require_subscription  
def create_checkout_session():
    """Create Stripe checkout session"""
    if not stripe.api_key:
        return jsonify({'error': 'Payment processing not configured'}), 500
    
    data = request.get_json()
    plan_type = data.get('plan_type', 'starter')
    
    # Define price IDs for each plan (you'll need to create these in Stripe)
    price_ids = {
        'starter': os.environ.get('STRIPE_STARTER_PRICE_ID', 'price_starter'),
        'pro': os.environ.get('STRIPE_PRO_PRICE_ID', 'price_pro'), 
        'team': os.environ.get('STRIPE_TEAM_PRICE_ID', 'price_team')
    }
    
    try:
        checkout_params = {
            'line_items': [{
                'price': price_ids.get(plan_type, price_ids['starter']),
                'quantity': 1,
            }],
            'mode': 'subscription',
            'success_url': f'https://{YOUR_DOMAIN}/app?success=true',
            'cancel_url': f'https://{YOUR_DOMAIN}/upgrade?canceled=true',
            'automatic_tax': {'enabled': False},
            'metadata': {
                'user_id': current_user.id,
                'plan_type': plan_type
            }
        }
        
        # Only include customer_email if user has email
        if current_user.email:
            checkout_params['customer_email'] = current_user.email
        
        checkout_session = stripe.checkout.Session.create(**checkout_params)
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/webhook/stripe', methods=['POST'])
def handle_stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    if not stripe.api_key:
        logging.error("Stripe webhook called but STRIPE_SECRET_KEY not configured")
        return jsonify({'error': 'Payment processing not configured'}), 400
    
    endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    if not endpoint_secret:
        return '', 400
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError:
        return '', 400
    except stripe.SignatureVerificationError:
        return '', 400
    
    # Handle the event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_successful_payment(session)
    elif event['type'] == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        handle_recurring_payment_success(invoice)
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        handle_failed_payment(invoice)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_canceled(subscription)
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_updated(subscription)
    
    return '', 200

def handle_successful_payment(session):
    """Handle successful checkout session completion"""
    user_id = session.get('metadata', {}).get('user_id')
    plan_type = session.get('metadata', {}).get('plan_type', 'starter')
    
    if not user_id:
        return
    
    user = User.query.get(user_id)
    if user:
        user.subscription_status = 'active'
        user.stripe_customer_id = session.get('customer')
        user.stripe_subscription_id = session.get('subscription')
        user.plan_type = plan_type
        db.session.commit()

def handle_recurring_payment_success(invoice):
    """Handle successful recurring payment"""
    customer_id = invoice.get('customer')
    if not customer_id:
        return
    
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user and user.subscription_status != 'active':
        user.subscription_status = 'active'
        db.session.commit()

def handle_subscription_canceled(subscription):
    """Handle subscription cancellation"""
    customer_id = subscription.get('customer')
    if not customer_id:
        return
    
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user:
        user.subscription_status = 'canceled'
        db.session.commit()

def handle_subscription_updated(subscription):
    """Handle subscription status updates"""
    customer_id = subscription.get('customer')
    status = subscription.get('status')
    
    if not customer_id or not status:
        return
    
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user:
        if status in ['canceled', 'unpaid']:
            user.subscription_status = 'canceled'
        elif status == 'past_due':
            user.subscription_status = 'past_due'
        elif status == 'active':
            user.subscription_status = 'active'
        db.session.commit()

def handle_failed_payment(invoice):
    """Handle failed payment from Stripe"""
    customer_id = invoice.get('customer')
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    if user:
        user.subscription_status = 'past_due'
        db.session.commit()

# Marketing site placeholder pages
@app.route('/privacy')
def privacy():
    """Privacy & Data Practices page."""
    return "<h1>Privacy & Data Practices</h1><p>Your client data is protected with industry-standard security practices.</p><p><a href='/'>&larr; Back to Home</a></p>"

@app.route('/terms')
def terms():
    """Terms of Service page."""
    return "<h1>Terms of Service</h1><p>Terms of service coming soon.</p><p><a href='/'>&larr; Back to Home</a></p>"

@app.route('/support')
def support():
    """Support page."""
    return "<h1>Support</h1><p>For support inquiries, please contact us.</p><p><a href='/'>&larr; Back to Home</a></p>"

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

@app.route('/app/students')
@require_subscription
def students():
    """Show students and objectives management page. Login required to protect client data."""
    # Get default organization
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
    # Get all students with their objectives using proper ORM queries
    students_data = Student.query.filter_by(organization_id=organization.id)\
        .options(db.joinedload(Student.objectives))\
        .order_by(Student.first_name).all()
    
    # Format data for template
    students = []
    for student in students_data:
        student_dict = {
            'id': student.id,
            'first_name': student.first_name,
            'objectives': []
        }
        
        for objective in student.objectives:
            student_dict['objectives'].append({
                'id': objective.id,
                'text': objective.objective_text
            })
        
        students.append(student_dict)
    
    return render_template('students.html', students=students)

@app.route('/app/students/add', methods=['POST'])
@require_subscription
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

@app.route('/app/students/delete', methods=['POST'])
@require_subscription
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

@app.route('/app/objectives/save', methods=['POST'])
@require_subscription
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

@app.route('/app/objectives/delete', methods=['POST'])
@require_subscription
def delete_objective():
    """Delete a specific objective."""
    objective_id = request.form.get('objective_id')
    
    if not objective_id:
        return jsonify({'error': 'Objective ID is required'}), 400
    
    try:
        objective = Objective.query.get(objective_id)
        if objective:
            db.session.delete(objective)
            db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/objective_items/save', methods=['POST'])
@require_subscription
def save_objective_items():
    """Save items for a specific objective."""
    objective_id = request.form.get('objective_id')
    items_text = request.form.get('items_text', '').strip()
    
    if not objective_id:
        return jsonify({'error': 'Objective ID is required'}), 400
    
    try:
        # Delete existing items for this objective
        ObjectiveItem.query.filter_by(objective_id=objective_id).delete()
        
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
                objective_item = ObjectiveItem(objective_id=objective_id, item_text=item_text, display_order=i)
                db.session.add(objective_item)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/objective_items/get/<int:objective_id>')
@require_subscription
def get_objective_items(objective_id):
    """Get items for a specific objective."""
    items = ObjectiveItem.query.filter_by(objective_id=objective_id)\
        .order_by(ObjectiveItem.display_order).all()
    
    items_list = [{'id': item.id, 'text': item.item_text, 'order': item.display_order} for item in items]
    
    return jsonify({'items': items_list})

@app.route('/app/objective_items/delete', methods=['POST'])
@require_subscription
def delete_objective_item():
    """Delete a specific objective item."""
    item_id = request.form.get('item_id')
    
    if not item_id:
        return jsonify({'error': 'Item ID is required'}), 400
    
    try:
        objective_item = ObjectiveItem.query.get(item_id)
        if objective_item:
            db.session.delete(objective_item)
            db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/collect')
@require_subscription
def collect():
    """Show data collection page. Login required to protect client data."""
    # Get default organization
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
    # Get all students with their objectives using proper ORM queries
    students_data = Student.query.filter_by(organization_id=organization.id)\
        .options(db.joinedload(Student.objectives))\
        .order_by(Student.first_name).all()
    
    # Format data for template
    students = []
    for student in students_data:
        student_dict = {
            'id': student.id,
            'first_name': student.first_name,
            'objectives': []
        }
        
        for objective in student.objectives:
            student_dict['objectives'].append({
                'id': objective.id,
                'text': objective.objective_text
            })
        
        students.append(student_dict)
    
    today = date.today().isoformat()
    return render_template('collect.html', students=students, today=today)

@app.route('/app/event/increment', methods=['POST'])
@require_subscription
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
    
    try:
        # Check if event exists
        event = Event.query.filter_by(
            session_id=session_id, 
            student_id=student_id, 
            objective_id=objective_id
        ).first()
        
        if event:
            new_count = event.count + 1
            event.count = new_count
            event.activity = activity if activity else event.activity
            event.prompt_level = prompt_level if prompt_level else event.prompt_level
        else:
            new_count = 1
            event = Event(
                session_id=session_id,
                student_id=student_id,
                objective_id=objective_id,
                count=new_count,
                activity=activity if activity else None,
                prompt_level=prompt_level if prompt_level else None
            )
            db.session.add(event)
        
        db.session.commit()
        return jsonify({'count': new_count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/event/decrement', methods=['POST'])
@require_subscription
def decrement_event():
    """Decrement count for an objective on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    
    if not all([date_str, student_id, objective_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    try:
        # Check if event exists
        event = Event.query.filter_by(
            session_id=session_id, 
            student_id=student_id, 
            objective_id=objective_id
        ).first()
        
        if event and event.count > 0:
            new_count = event.count - 1
            if new_count == 0:
                # Remove the event if count reaches 0
                db.session.delete(event)
            else:
                event.count = new_count
            db.session.commit()
            return jsonify({'count': new_count})
        else:
            return jsonify({'count': 0})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/event/save_notes', methods=['POST'])
@require_subscription
def save_notes():
    """Save notes for a student on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    notes = data.get('notes', '').strip()
    
    if not all([date_str, student_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    session_id = get_or_create_session(date_str)
    
    try:
        # Update notes for all events for this student on this date
        events = Event.query.filter_by(session_id=session_id, student_id=student_id).all()
        for event in events:
            event.notes = notes if notes else None
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/event/counts', methods=['GET'])
@require_subscription
def get_counts():
    """Get existing counts for a date and students."""
    date_str = request.args.get('date')
    student_ids = request.args.getlist('student_id')
    
    if not date_str or not student_ids:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    # Get session for this date
    session = Session.query.filter_by(date=date_str).first()
    
    if not session:
        return jsonify({'counts': {}})
    
    # Get all counts for this session and students
    events = Event.query.filter(
        Event.session_id == session.id,
        Event.student_id.in_(student_ids)
    ).all()
    
    # Format response
    counts = {}
    notes = {}
    for event in events:
        key = f"{event.student_id}-{event.objective_id}"
        counts[key] = event.count
        if event.notes:
            notes[str(event.student_id)] = event.notes
    
    return jsonify({'counts': counts, 'notes': notes})

@app.route('/app/event/toggle_item_selection', methods=['POST'])
@require_subscription
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
    
    try:
        # Get or create event for this session/student/objective
        event = Event.query.filter_by(
            session_id=session_id, 
            student_id=student_id, 
            objective_id=objective_id
        ).first()
        
        if event:
            # Update existing event with activity and prompt_level if provided
            if activity:
                event.activity = activity
            if prompt_level:
                event.prompt_level = prompt_level
        else:
            # Create new event
            event = Event(
                session_id=session_id,
                student_id=student_id,
                objective_id=objective_id,
                count=0,
                activity=activity if activity else None,
                prompt_level=prompt_level if prompt_level else None
            )
            db.session.add(event)
            db.session.flush()  # Ensure event gets an ID
        
        # Handle item selection
        if selected:
            # Check if selection already exists
            selection = EventSelection.query.filter_by(
                event_id=event.id, 
                objective_item_id=item_id
            ).first()
            
            if not selection:
                selection = EventSelection(
                    event_id=event.id,
                    objective_item_id=item_id,
                    selected=True
                )
                db.session.add(selection)
            else:
                selection.selected = True
        else:
            # Remove selection
            EventSelection.query.filter_by(
                event_id=event.id, 
                objective_item_id=item_id
            ).delete()
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/app/event/selections', methods=['GET'])
@require_subscription
def get_selections():
    """Get existing item selections for a date and students."""
    date_str = request.args.get('date')
    student_ids = request.args.getlist('student_id')
    
    if not date_str or not student_ids:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    # Get session for this date
    session = Session.query.filter_by(date=date_str).first()
    
    if not session:
        return jsonify({'selections': {}, 'activities': {}, 'prompt_levels': {}, 'notes': {}})
    
    # Get all events and their selections for this session and students
    events = Event.query.filter(
        Event.session_id == session.id,
        Event.student_id.in_(student_ids)
    ).options(
        db.joinedload(Event.event_selections).joinedload(EventSelection.objective_item)
    ).all()
    
    # Format response
    selections = {}
    activities = {}
    prompt_levels = {}
    notes = {}
    
    for event in events:
        key = f"{event.student_id}-{event.objective_id}"
        
        # Collect selected items
        selected_items = [es.objective_item_id for es in event.event_selections if es.selected]
        if selected_items:
            selections[key] = selected_items
        
        # Collect activities and prompt levels
        if event.activity:
            activities[key] = event.activity
        if event.prompt_level:
            prompt_levels[key] = event.prompt_level
        if event.notes:
            notes[str(event.student_id)] = event.notes
    
    return jsonify({
        'selections': selections,
        'activities': activities, 
        'prompt_levels': prompt_levels,
        'notes': notes
    })

@app.route('/app/report')
@require_subscription
def report():
    """Show reports page with filtering."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Get default organization
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
    # Get all students for the filter dropdown
    students = Student.query.filter_by(organization_id=organization.id)\
        .order_by(Student.first_name).all()
    
    # Default date range (last 30 days)
    if not start_date:
        start_date = (date.today().replace(day=1)).isoformat()
    if not end_date:
        end_date = date.today().isoformat()
    
    # Build base query with joins
    query = db.session.query(Event)\
        .join(Session, Event.session_id == Session.id)\
        .join(Student, Event.student_id == Student.id)\
        .join(Objective, Event.objective_id == Objective.id)
    
    # Apply filters
    if start_date:
        query = query.filter(Session.date >= start_date)
    if end_date:
        query = query.filter(Session.date <= end_date)
    if student_id:
        query = query.filter(Student.id == student_id)
    
    if report_type == 'summary':
        # Summary report: student, objective, total count, date range
        from sqlalchemy import func
        report_data = query.with_entities(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.sum(Event.count).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).group_by(Student.id, Objective.id)\
         .order_by(Student.first_name, Objective.objective_text).all()
    else:
        # By objective report: date, student, objective, count, notes
        report_data = query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
    
    return render_template('report.html', 
                         report_data=report_data,
                         students=students,
                         start_date=start_date,
                         end_date=end_date,
                         student_id=student_id,
                         report_type=report_type)

@app.route('/app/report.csv')
@require_subscription
def report_csv():
    """Export report as CSV."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Build base query with joins
    query = db.session.query(Event)\
        .join(Session, Event.session_id == Session.id)\
        .join(Student, Event.student_id == Student.id)\
        .join(Objective, Event.objective_id == Objective.id)
    
    # Apply filters
    if start_date:
        query = query.filter(Session.date >= start_date)
    if end_date:
        query = query.filter(Session.date <= end_date)
    if student_id:
        query = query.filter(Student.id == student_id)
    
    if report_type == 'summary':
        from sqlalchemy import func
        report_data = query.with_entities(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.sum(Event.count).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).group_by(Student.id, Objective.id)\
         .order_by(Student.first_name, Objective.objective_text).all()
        headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
    else:
        report_data = query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
        headers = ['Date', 'Student', 'Objective', 'Count', 'Notes']
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    
    for row in report_data:
        writer.writerow([getattr(row, attr.lower()) if getattr(row, attr.lower()) is not None else '' 
                        for attr in headers])
    
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

@app.route('/app/report.tsv')
@require_subscription
def report_tsv():
    """Export report as TSV for clipboard."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Build base query with joins
    query = db.session.query(Event)\
        .join(Session, Event.session_id == Session.id)\
        .join(Student, Event.student_id == Student.id)\
        .join(Objective, Event.objective_id == Objective.id)
    
    # Apply filters
    if start_date:
        query = query.filter(Session.date >= start_date)
    if end_date:
        query = query.filter(Session.date <= end_date)
    if student_id:
        query = query.filter(Student.id == student_id)
    
    if report_type == 'summary':
        from sqlalchemy import func
        report_data = query.with_entities(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.sum(Event.count).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).group_by(Student.id, Objective.id)\
         .order_by(Student.first_name, Objective.objective_text).all()
        headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
    else:
        report_data = query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
        headers = ['Date', 'Student', 'Objective', 'Count', 'Notes']
    
    # Create TSV
    lines = []
    lines.append('\t'.join(headers))
    
    for row in report_data:
        line = '\t'.join([str(getattr(row, attr.lower())) if getattr(row, attr.lower()) is not None else '' 
                         for attr in headers])
        lines.append(line)
    
    tsv_content = '\n'.join(lines)
    
    return tsv_content, 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/download/bulk-upload-template')
@require_subscription
def download_bulk_upload_template():
    """Download the Excel template for bulk student and objective uploads"""
    try:
        template_path = os.path.join('attached_assets', 'Data Collection Bulk Upload Template File_1757622957306.xlsx')
        
        if not os.path.exists(template_path):
            return jsonify({'error': 'Template file not found'}), 404
            
        return send_file(
            template_path,
            as_attachment=True,
            download_name='Speech_Therapy_Bulk_Upload_Template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logging.error(f"Error downloading template: {e}")
        return jsonify({'error': 'Failed to download template'}), 500

@app.route('/app/print/sheet')
@require_subscription
def print_data_collection_sheet():
    """Generate printable data collection sheet for offline therapy sessions"""
    try:
        # Get parameters
        student_ids = request.args.get('student_ids', '')
        session_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        boxes_per_objective = int(request.args.get('boxes', 10))
        box_size = request.args.get('box_size', '1')  # inches
        compress = request.args.get('compress', 'false').lower() == 'true'
        show_items = request.args.get('show_items', 'false').lower() == 'true'
        
        # Validate parameters
        if not student_ids:
            return "No students selected for print sheet", 400
            
        try:
            student_id_list = [int(id.strip()) for id in student_ids.split(',') if id.strip()]
        except ValueError:
            return "Invalid student IDs provided", 400
            
        if not student_id_list:
            return "No valid student IDs provided", 400
            
        # Get organization for current user
        organization = Organization.query.first()
        if not organization:
            return "Organization not found", 404
            
        # Fetch students with their objectives, scoped to organization
        students_query = Student.query.filter(
            Student.id.in_(student_id_list),
            Student.organization_id == organization.id
        ).options(db.joinedload(Student.objectives))
        
        if show_items:
            students_query = students_query.options(
                db.joinedload(Student.objectives).joinedload(Objective.items)
            )
            
        students = students_query.order_by(Student.first_name).all()
        
        if not students:
            return "No students found or access denied", 404
            
        # Group students into pages (4 per page, or 6 if compressed)
        students_per_page = 6 if compress else 4
        pages = []
        for i in range(0, len(students), students_per_page):
            page_students = students[i:i + students_per_page]
            pages.append(page_students)
            
        # Prepare template data
        template_data = {
            'pages': pages,
            'session_date': session_date,
            'boxes_per_objective': boxes_per_objective,
            'box_size': box_size,
            'compress': compress,
            'show_items': show_items,
            'therapist_name': '',  # Could be filled from user profile later
            'organization_name': organization.name
        }
        
        return render_template('print_sheet.html', **template_data)
        
    except Exception as e:
        logging.error(f"Error generating print sheet: {e}")
        return f"Error generating print sheet: {str(e)}", 500

@app.route('/app/admin/import_spreadsheet', methods=['GET', 'POST'])
@require_subscription
def import_spreadsheet():
    """Import data from uploaded Excel spreadsheet."""
    if request.method == 'GET':
        return render_template('import.html')
    
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file type - only .xlsx supported
        allowed_extensions = {'.xlsx'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            return jsonify({'error': 'Invalid file type. Please upload an Excel file in .xlsx format only.'}), 400
        
        # Validate file size (10MB limit)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        if file_size > 10 * 1024 * 1024:  # 10MB
            return jsonify({'error': 'File too large. Please upload a file smaller than 10MB.'}), 400
        
        # Create temp directory if it doesn't exist
        temp_dir = 'temp_uploads'
        os.makedirs(temp_dir, exist_ok=True)
        
        # Save uploaded file temporarily with secure filename
        import time
        import uuid
        from werkzeug.utils import secure_filename
        
        # Create secure filename
        original_name = secure_filename(file.filename)
        safe_filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{original_name}"
        temp_filepath = os.path.join(temp_dir, safe_filename)
        
        try:
            file.save(temp_filepath)
            logging.info(f"File uploaded successfully: {safe_filename}")
            
            # Try loading the workbook with different parameters
            workbook = None
            try:
                workbook = openpyxl.load_workbook(temp_filepath, data_only=True)
                logging.info("Workbook loaded with data_only=True")
            except Exception as e1:
                logging.warning(f"Failed with data_only=True: {e1}")
                try:
                    workbook = openpyxl.load_workbook(temp_filepath)
                    logging.info("Workbook loaded with default parameters")
                except Exception as e2:
                    logging.error(f"Failed with default params: {e2}")
                    raise Exception(f"Could not load Excel file. Please ensure it's a valid Excel file (.xlsx or .xls). Error details: {e2}")
            
            if not workbook:
                raise Exception("Failed to load workbook")
            
            # Analyze all sheets first
            analysis = analyze_workbook(workbook)
            logging.info(f"Workbook analysis completed. Found sheets: {list(analysis.keys())}")
            
            # Import data from Database sheet if it exists
            import_results = {}
            database_sheets = [sheet for sheet in workbook.sheetnames if sheet.lower() == 'database']
            
            if database_sheets:
                database_sheet_name = database_sheets[0]
                logging.info(f"Processing Database sheet: {database_sheet_name}")
                import_results['students'] = import_database_sheet(workbook[database_sheet_name])
            else:
                # Look for any sheet that might contain student data
                potential_sheets = [sheet for sheet in workbook.sheetnames 
                                  if any(keyword in sheet.lower() for keyword in ['student', 'data', 'main', 'sheet1'])]
                if potential_sheets:
                    logging.info(f"No 'Database' sheet found, trying sheet: {potential_sheets[0]}")
                    import_results['students'] = import_database_sheet(workbook[potential_sheets[0]])
                else:
                    logging.warning("No suitable sheet found for student data import")
                    return jsonify({'error': 'No "Database" sheet found. Please ensure your Excel file has a sheet named "Database" containing student data.'}), 400
            
            # Analyze data collection sheet structure
            data_collection_sheets = [sheet for sheet in workbook.sheetnames 
                                    if 'data' in sheet.lower() and 'collection' in sheet.lower()]
            if data_collection_sheets:
                sheet_name = data_collection_sheets[0]
                logging.info(f"Analyzing data collection sheet: {sheet_name}")
                import_results['data_collection_structure'] = analyze_data_collection_sheet(workbook[sheet_name])
            
            workbook.close()
            logging.info("Import process completed successfully")
            
            return jsonify({
                'success': True,
                'analysis': analysis,
                'import_results': import_results,
                'file_processed': file.filename
            })
            
        finally:
            # Clean up temporary file
            try:
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
                    logging.info(f"Temporary file cleaned up: {safe_filename}")
            except Exception as cleanup_error:
                logging.warning(f"Failed to cleanup temporary file: {cleanup_error}")
        
    except Exception as e:
        logging.error(f"Import failed: {str(e)}")
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
    
    # Get default organization
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
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
                # Check if student already exists
                existing_student = Student.query.filter_by(
                    organization_id=organization.id, 
                    first_name=student_name
                ).first()
                
                if existing_student:
                    student = existing_student
                else:
                    # Add new student
                    student = Student(
                        organization_id=organization.id,
                        first_name=student_name
                    )
                    db.session.add(student)
                    db.session.flush()  # Get the ID
                    results['students_added'] += 1
                
                # Clear existing objectives for this student
                Objective.query.filter_by(student_id=student.id).delete()
                
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
                    objective = Objective(
                        student_id=student.id,
                        objective_text=objective_text
                    )
                    db.session.add(objective)
                    results['objectives_added'] += 1
                
                results['students'].append({
                    'name': student_name,
                    'objectives_count': len(objectives),
                    'objectives': objectives[:5]  # First 5 for preview
                })
                
            except Exception as e:
                print(f"Error importing student {student_name}: {e}")
                continue
        
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        raise e
    
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