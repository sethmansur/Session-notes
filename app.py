#!/usr/bin/env python3
import os
import re
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file, session, make_response
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

# Cookie configuration moved below for consistency

# Remove SERVER_NAME configuration to let Flask use the actual request host
# This ensures OAuth redirect_uri matches the domain the user is actually on

# Configure file upload limits (10MB max)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# Configure for HTTPS on Replit with proper proxy handling
app.config['PREFERRED_URL_SCHEME'] = 'https'

# Essential: Configure secure session cookies for OAuth iframe support  
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE='None',
    REMEMBER_COOKIE_SECURE=True,
    REMEMBER_COOKIE_SAMESITE='None'
)

# Configure Flask-WTF for CSRF protection
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = None  # No time limit for CSRF tokens

# Stripe configuration with error handling
stripe_secret_key = os.environ.get('STRIPE_SECRET_KEY')
if not stripe_secret_key:
    logging.warning("STRIPE_SECRET_KEY not set - payment functionality will be disabled")
    # Set a placeholder to prevent None errors, but payments will fail gracefully
    stripe.api_key = None
else:
    stripe.api_key = stripe_secret_key

# Domain configuration - use request host for dynamic domain support
# This will be set dynamically based on actual request

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)

# Handle file upload size limit errors
@app.errorhandler(413)
def file_too_large(error):
    return jsonify({'error': 'File too large. Please upload a file smaller than 10MB.'}), 413

# Import database and auth after app creation
from models import db, User, Organization, Membership, Student, Objective, Session, Event, ObjectiveItem, EventSelection
from auth import init_auth, require_subscription, admin_required
from flask_login import login_required, current_user
from functools import wraps

# Initialize custom authentication system
login_manager = init_auth(app)

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
# Authentication decorators are now imported from auth.py module

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
    """Landing page for SessionNotes SaaS - marketing website"""
    return render_template('landing.html')

@app.route('/app')
@require_subscription
def app_dashboard():
    """Authenticated app dashboard - redirects to collect page."""
    return redirect(url_for('collect'))

@app.route('/app/profile', methods=['GET', 'POST'])
@require_subscription
def user_profile():
    """User profile management page with HIPAA compliance reminder."""
    from forms import ProfileForm, ChangePasswordForm
    
    profile_form = ProfileForm(current_user.id)
    password_form = ChangePasswordForm()
    
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'profile' and profile_form.validate_on_submit():
            # Update profile information
            current_user.first_name = profile_form.first_name.data
            current_user.last_name = profile_form.last_name.data
            current_user.email = profile_form.email.data.lower().strip()
            current_user.updated_at = datetime.now()
            
            try:
                db.session.commit()
                flash('Profile updated successfully!', 'success')
                return redirect(url_for('user_profile'))
            except Exception as e:
                db.session.rollback()
                flash('Failed to update profile. Please try again.', 'error')
                current_app.logger.error(f"Profile update error: {str(e)}")
        
        elif form_type == 'password' and password_form.validate_on_submit():
            # Verify current password
            if not current_user.check_password(password_form.current_password.data):
                flash('Current password is incorrect.', 'error')
            else:
                # Update password
                current_user.set_password(password_form.new_password.data)
                current_user.updated_at = datetime.now()
                
                try:
                    db.session.commit()
                    flash('Password updated successfully!', 'success')
                    return redirect(url_for('user_profile'))
                except Exception as e:
                    db.session.rollback()
                    flash('Failed to update password. Please try again.', 'error')
                    current_app.logger.error(f"Password update error: {str(e)}")
    
    # Pre-populate the profile form with current user data
    if request.method == 'GET':
        profile_form.first_name.data = current_user.first_name
        profile_form.last_name.data = current_user.last_name
        profile_form.email.data = current_user.email
    
    return render_template('profile.html', 
                         profile_form=profile_form, 
                         password_form=password_form,
                         user=current_user)

# Pricing calculation helper
def get_pricing_info(plan_type, billing_period='monthly', user_count=1):
    """Calculate pricing with discounts for different plan types"""
    base_prices = {
        'individual_monthly': 2.99,
        'individual_yearly': 24.99,  # ~17% savings vs monthly (2.99 * 12 = 35.88)
        'team_monthly': 2.99,  # Base price before team discount
        'team_yearly': 24.99   # Base price before team discount
    }
    
    base_price = base_prices.get(plan_type, base_prices['individual_monthly'])
    
    # Calculate team discount (10% off for 3+ users)
    if 'team' in plan_type and user_count >= 3:
        team_discount = 0.10
        discounted_price = base_price * (1 - team_discount)
        total_price = discounted_price * user_count
    else:
        team_discount = 0.0
        discounted_price = base_price
        total_price = base_price * user_count
    
    # Calculate yearly savings percentage
    yearly_savings_pct = 0
    if 'yearly' in plan_type:
        monthly_equivalent = base_prices.get(plan_type.replace('yearly', 'monthly'), 2.99) * 12
        yearly_price = base_prices.get(plan_type, 24.99)
        yearly_savings_pct = round(((monthly_equivalent - yearly_price) / monthly_equivalent) * 100)
    
    return {
        'plan_type': plan_type,
        'billing_period': billing_period,
        'user_count': user_count,
        'base_price': base_price,
        'discounted_price': discounted_price,
        'total_price': total_price,
        'team_discount_pct': int(team_discount * 100),
        'yearly_savings_pct': yearly_savings_pct,
        'is_team_plan': 'team' in plan_type,
        'is_yearly': 'yearly' in plan_type
    }

# Subscription management routes
@app.route('/upgrade')
@app.route('/app/upgrade')
def upgrade():
    """Upgrade page with subscription plans"""
    days_left = 0
    current_plan = 'freemium'  # Default for non-authenticated users
    
    if current_user.is_authenticated:
        if current_user.subscription_status == 'trial':
            days_left = current_user.days_left_in_trial()
            current_plan = 'trial'
        else:
            current_plan = current_user.plan_type
    
    # Calculate pricing info for display
    pricing = {
        'individual_monthly': get_pricing_info('individual_monthly'),
        'individual_yearly': get_pricing_info('individual_yearly'),
        'team_monthly': get_pricing_info('team_monthly', user_count=3),  # Show 3-user example
        'team_yearly': get_pricing_info('team_yearly', user_count=3)     # Show 3-user example
    }
    
    return render_template('upgrade.html', 
                         days_left=days_left, 
                         current_plan=current_plan,
                         pricing=pricing)

# Admin Dashboard Routes (PROTECTED)
@app.route('/admin')
@admin_required
def admin_dashboard():
    """Internal user management dashboard for backend subscription management"""
    from sqlalchemy import func
    
    # Get user statistics
    total_users = User.query.count()
    trial_users = User.query.filter_by(subscription_status='trial').count()
    active_subscribers = User.query.filter_by(subscription_status='active').count()
    freemium_users = User.query.filter_by(plan_type='freemium').count()
    
    # Recent users (last 30 days)
    thirty_days_ago = datetime.now() - timedelta(days=30)
    recent_users = User.query.filter(User.created_at >= thirty_days_ago).count()
    
    # Revenue metrics (simulated for now)
    monthly_revenue = active_subscribers * 9  # Assuming $9 avg plan
    
    stats = {
        'total_users': total_users,
        'trial_users': trial_users,
        'active_subscribers': active_subscribers,
        'freemium_users': freemium_users,
        'recent_users': recent_users,
        'monthly_revenue': monthly_revenue
    }
    
    return render_template('admin_dashboard.html', stats=stats)

@app.route('/admin/users')
@admin_required
def admin_users():
    """User list with search and filtering"""
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    plan_filter = request.args.get('plan', '')
    page = int(request.args.get('page', 1))
    per_page = 50
    
    query = User.query
    
    # Apply filters
    if search:
        from sqlalchemy import or_
        query = query.filter(or_(
            User.email.contains(search),
            User.first_name.contains(search),
            User.last_name.contains(search)
        ))
    
    if status_filter:
        query = query.filter(User.subscription_status == status_filter)
        
    if plan_filter:
        query = query.filter(User.plan_type == plan_filter)
    
    # Paginate results
    users = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    
    return render_template('admin_users.html', 
                         users=users, 
                         search=search, 
                         status_filter=status_filter,
                         plan_filter=plan_filter)

@app.route('/admin/users/<user_id>')
@admin_required
def admin_user_detail(user_id):
    """Individual user management page"""
    user = User.query.get_or_404(user_id)
    
    # Get user activity stats
    user_orgs = [m.organization_id for m in user.memberships]
    total_students = 0
    total_sessions = 0
    
    if user_orgs:
        total_students = Student.query.filter(Student.organization_id.in_(user_orgs)).count()
        total_sessions = Session.query.filter(Session.organization_id.in_(user_orgs)).count()
    
    activity_stats = {
        'total_students': total_students,
        'total_sessions': total_sessions,
        'last_login': user.updated_at,  # Approximate
        'trial_days_left': user.days_left_in_trial() if user.subscription_status == 'trial' else 0
    }
    
    return render_template('admin_user_detail.html', user=user, activity_stats=activity_stats)

@app.route('/admin/users/<user_id>/update', methods=['POST'])
@admin_required
def admin_update_user(user_id):
    """Update user subscription status and plan"""
    user = User.query.get_or_404(user_id)
    
    new_status = request.form.get('subscription_status')
    new_plan = request.form.get('plan_type')
    extend_trial = request.form.get('extend_trial')
    
    if new_status and new_status != user.subscription_status:
        user.subscription_status = new_status
        
    if new_plan and new_plan != user.plan_type:
        user.plan_type = new_plan
        
    if extend_trial:
        # Extend trial by 7 days
        user.trial_start_date = datetime.now()
        user.subscription_status = 'trial'
    
    user.updated_at = datetime.now()
    db.session.commit()
    
    return redirect(url_for('admin_user_detail', user_id=user_id))

# GHL Webhook endpoint for lead integration
@app.route('/webhook/ghl-leads', methods=['POST'])
def ghl_webhook():
    """Webhook endpoint for Go High Level lead data"""
    try:
        data = request.get_json()
        
        # Log the webhook data (for debugging)
        logging.info(f"GHL Webhook received: {data}")
        
        # Extract lead information
        email = data.get('email', '')
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')
        phone = data.get('phone', '')
        
        # TODO: Process lead data (create user, send welcome email, etc.)
        # For now, just return success
        
        return jsonify({
            'success': True,
            'message': 'Lead data received successfully',
            'lead_email': email
        }), 200
        
    except Exception as e:
        logging.error(f"GHL Webhook error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to process webhook'
        }), 400

@app.route('/create-checkout-session', methods=['POST'])
@require_subscription  
def create_checkout_session():
    """Create Stripe checkout session"""
    if not current_user.is_authenticated:
        return jsonify({'error': 'Payments disabled - authentication required'}), 403
    
    if not stripe.api_key:
        return jsonify({'error': 'Payment processing not configured'}), 500
    
    data = request.get_json()
    plan_type = data.get('plan_type', 'individual_monthly')
    billing_period = data.get('billing_period', 'monthly')  # 'monthly' or 'yearly'
    user_count = data.get('user_count', 1)  # For team plans
    
    # Define price IDs for each plan (you'll need to create these in Stripe)
    price_ids = {
        'individual_monthly': os.environ.get('STRIPE_INDIVIDUAL_MONTHLY_ID', 'price_individual_monthly'),
        'individual_yearly': os.environ.get('STRIPE_INDIVIDUAL_YEARLY_ID', 'price_individual_yearly'),
        'team_monthly': os.environ.get('STRIPE_TEAM_MONTHLY_ID', 'price_team_monthly'),
        'team_yearly': os.environ.get('STRIPE_TEAM_YEARLY_ID', 'price_team_yearly')
    }
    
    # Calculate pricing and discounts
    pricing_info = get_pricing_info(plan_type, billing_period, user_count)
    
    try:
        checkout_params = {
            'line_items': [{
                'price': price_ids.get(plan_type, price_ids['starter']),
                'quantity': 1,
            }],
            'mode': 'subscription',
            'success_url': f'https://{request.host}/app?success=true',
            'cancel_url': f'https://{request.host}/upgrade?canceled=true',
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

# ===== DIRECT ACCESS ROUTES (No authentication required) =====
# These routes provide bypass access for trusted users like Sam

@app.route('/direct')
def direct_dashboard():
    """Direct access dashboard - redirects to collect page."""
    return redirect('/direct/collect')

@app.route('/direct/report')
def direct_report():
    """Show reports page via direct access. No authentication required."""
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    student_id = request.args.get('student_id', '')
    report_type = request.args.get('type', 'by_objective')
    
    # Get default organization (same logic as auth version but without login check)
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name='Default Clinic', subdomain='default')
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
    
    # Use the same report logic as the authenticated version
    from sqlalchemy import func, or_, and_
    
    report_data = None
    chart_data = None
    
    if report_type == 'summary':
        # Summary report: show all objectives with counts (including zero)
        report_data = db.session.query(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.coalesce(func.sum(Event.count), 0).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).select_from(Student)\
         .join(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        # Apply date filters safely using SQLAlchemy ORM filters
        if start_date and end_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        elif start_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), Session.date >= start_date)
            )
        elif end_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), Session.date <= end_date)
            )
        
        # Apply student filter
        if student_id:
            report_data = report_data.filter(Student.id == student_id)
        
        report_data = report_data.group_by(Student.first_name, Objective.objective_text)\
            .order_by(Student.first_name, Objective.objective_text).all()
    
    return render_template('report.html', 
                         report_data=report_data,
                         students=students,
                         start_date=start_date,
                         end_date=end_date,
                         student_id=student_id,
                         report_type=report_type,
                         chart_data=chart_data,
                         is_direct_access=True)

@app.route('/direct/students')
def direct_students():
    """Show students and objectives management page. No authentication required."""
    # Get default organization (same logic as auth version but without login check)
    organization = Organization.query.first()
    if not organization:
        # Create default organization if none exists
        organization = Organization(name='Default Clinic', subdomain='default')
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
    
    return render_template('students.html', students=students, is_direct_access=True)

@app.route('/direct/students/add', methods=['POST'])
def direct_add_student():
    """Add a new student via direct access."""
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

@app.route('/direct/collect')
def direct_collect():
    """Show data collection page via direct access."""
    # Get default organization
    organization = Organization.query.first()
    if not organization:
        organization = Organization(name="Default Clinic", subdomain="default")
        db.session.add(organization)
        db.session.commit()
    
    # Get students with their objectives
    students_data = Student.query.filter_by(organization_id=organization.id)\
                           .options(db.joinedload(Student.objectives))\
                           .order_by(Student.first_name).all()
    
    # Format students data for JSON serialization
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
                'objective_text': objective.objective_text
            })
        
        students.append(student_dict)
    
    # Get today's date for the session
    today = datetime.now().strftime('%Y-%m-%d')
    
    return render_template('collect.html', 
                         students=students, 
                         session_date=today,
                         is_direct_access=True)

@app.route('/direct/objectives/save', methods=['POST'])
def direct_save_objectives():
    """Save objectives for a student via direct access."""
    student_id = request.form.get('student_id')
    objectives_text = request.form.get('objectives', '').strip()
    
    if not student_id:
        return jsonify({'error': 'Student ID is required'}), 400
        
    student = Student.query.get(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    
    try:
        # Delete existing objectives
        Objective.query.filter_by(student_id=student_id).delete()
        
        # Parse and add new objectives
        if objectives_text:
            objective_lines = [line.strip() for line in objectives_text.split('\n') if line.strip()]
            for line in objective_lines:
                # Remove leading numbers/bullets if present
                clean_text = re.sub(r'^\d+[\.\)]\s*', '', line.strip())
                clean_text = re.sub(r'^\*\s*', '', clean_text.strip())
                
                if clean_text:
                    objective = Objective(student_id=student_id, objective_text=clean_text)
                    db.session.add(objective)
        
        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/direct/event/increment', methods=['POST'])
def direct_increment_event():
    """Increment count for an objective via direct access."""
    data = request.get_json()
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    date_str = data.get('date')
    
    if not all([student_id, objective_id, date_str]):
        return jsonify({'error': 'Missing required data'}), 400
    
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get or create session
        session = Session.query.filter_by(date=date_obj).first()
        if not session:
            session = Session(date=date_obj)
            db.session.add(session)
            db.session.commit()
        
        # Get or create event
        event = Event.query.filter_by(
            session_id=session.id,
            student_id=student_id,
            objective_id=objective_id
        ).first()
        
        if event:
            event.count += 1
        else:
            event = Event(
                session_id=session.id,
                student_id=student_id,
                objective_id=objective_id,
                count=1,
                prompt_level='Independent'
            )
            db.session.add(event)
        
        db.session.commit()
        
        return jsonify({'success': True, 'new_count': event.count})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/direct/event/decrement', methods=['POST'])
def direct_decrement_event():
    """Decrement count for an objective via direct access."""
    data = request.get_json()
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    date_str = data.get('date')
    
    if not all([student_id, objective_id, date_str]):
        return jsonify({'error': 'Missing required data'}), 400
    
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get session
        session = Session.query.filter_by(date=date_obj).first()
        if not session:
            return jsonify({'success': True, 'new_count': 0})
        
        # Get event
        event = Event.query.filter_by(
            session_id=session.id,
            student_id=student_id,
            objective_id=objective_id
        ).first()
        
        if event and event.count > 0:
            event.count -= 1
            if event.count == 0:
                db.session.delete(event)
            new_count = event.count if event.count > 0 else 0
        else:
            new_count = 0
        
        db.session.commit()
        return jsonify({'success': True, 'new_count': new_count})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/direct/save_session', methods=['POST'])
def direct_save_session():
    """Mark session as saved/completed with timestamp (direct access)."""
    try:
        data = request.get_json()
        session_date = data.get('date')
        
        if not session_date:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        from datetime import datetime
        date_obj = datetime.strptime(session_date, '%Y-%m-%d').date()
        
        # Get default organization
        organization = Organization.query.first()
        if not organization:
            organization = Organization(name="Default Clinic", subdomain="default")
            db.session.add(organization)
            db.session.commit()
        
        # Get or create session
        session = Session.query.filter_by(organization_id=organization.id, date=date_obj).first()
        if not session:
            session = Session(organization_id=organization.id, date=date_obj, created_at=datetime.now())
            db.session.add(session)
        
        # Update session with save timestamp  
        session.updated_at = datetime.now()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'saved_at': session.updated_at.isoformat(),
            'message': f'Session for {session_date} saved successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/direct/event/counts', methods=['GET'])
def direct_get_counts():
    """Get existing counts for a date and students via direct access."""
    date_str = request.args.get('date')
    student_ids = request.args.getlist('student_id')
    
    if not date_str:
        return jsonify({'error': 'Date is required'}), 400
    
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get session for this date
        session = Session.query.filter_by(date=date_obj).first()
        if not session:
            return jsonify({'counts': {}})
        
        # Build query for events
        query = Event.query.filter_by(session_id=session.id)
        if student_ids:
            query = query.filter(Event.student_id.in_(student_ids))
        
        events = query.all()
        
        # Format results
        counts = {}
        for event in events:
            key = f"{event.student_id}_{event.objective_id}"
            counts[key] = {
                'count': event.count,
                'prompt_level': event.prompt_level or 'Independent'
            }
        
        return jsonify({'counts': counts})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/direct/event/update', methods=['POST'])
def direct_update_event():
    """Update count and prompt level for an objective on a date (direct access)."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    count = int(data.get('count', 0))
    prompt_level = data.get('prompt_level', '')
    activity = data.get('activity', '')
    
    if not all([date_str, student_id, objective_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    if count < 0:
        return jsonify({'error': 'Count cannot be negative'}), 400
    
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        # Get default organization
        organization = Organization.query.first()
        if not organization:
            organization = Organization(name="Default Clinic", subdomain="default")
            db.session.add(organization)
            db.session.commit()
        
        # Get or create session
        session = Session.query.filter_by(organization_id=organization.id, date=date_obj).first()
        if not session:
            session = Session(organization_id=organization.id, date=date_obj)
            db.session.add(session)
            db.session.commit()
        
        # Check if event exists
        event = Event.query.filter_by(
            session_id=session.id, 
            student_id=student_id, 
            objective_id=objective_id
        ).first()
        
        if count == 0:
            # Delete event if count is 0
            if event:
                db.session.delete(event)
        else:
            # Create or update event
            if event:
                event.count = count
                event.prompt_level = prompt_level if prompt_level else event.prompt_level
                event.activity = activity if activity else event.activity
            else:
                event = Event(
                    session_id=session.id,
                    student_id=student_id,
                    objective_id=objective_id,
                    count=count,
                    prompt_level=prompt_level if prompt_level else None,
                    activity=activity if activity else None
                )
                db.session.add(event)
        
        db.session.commit()
        return jsonify({'success': True, 'count': count})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/direct/reset_session', methods=['POST'])
def direct_reset_session():
    """Reset all counts for the current session date (direct access)."""
    try:
        data = request.get_json()
        session_date = data.get('date')
        
        if not session_date:
            return jsonify({'error': 'Date is required'}), 400
        
        date_obj = datetime.strptime(session_date, '%Y-%m-%d').date()
        
        # Get default organization
        organization = Organization.query.first()
        if not organization:
            organization = Organization(name="Default Clinic", subdomain="default")
            db.session.add(organization)
            db.session.commit()
        
        # Get session for the date
        session = Session.query.filter_by(organization_id=organization.id, date=date_obj).first()
        if session:
            # Delete all events for this session
            Event.query.filter_by(session_id=session.id).delete()
            db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/direct/print/sheet')
def direct_print_data_collection_sheet():
    """Generate printable data collection sheet via direct access."""
    try:
        # Get parameters
        student_ids_param = request.args.get('student_ids', '')
        session_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        boxes_per_objective = int(request.args.get('boxes', 10))
        
        # Parse student IDs
        if not student_ids_param:
            return "No students selected", 400
        
        try:
            student_id_list = [int(id.strip()) for id in student_ids_param.split(',') if id.strip()]
        except ValueError:
            return "Invalid student IDs", 400
        
        # Get default organization
        organization = Organization.query.first()
        if not organization:
            organization = Organization(name="Default Clinic", subdomain="default")
            db.session.add(organization)
            db.session.commit()
            
        # Fetch students with their objectives
        students = Student.query.filter(
            Student.id.in_(student_id_list),
            Student.organization_id == organization.id
        ).options(db.joinedload(Student.objectives)).order_by(Student.first_name).all()
        
        if not students:
            return "No students found", 404
            
        # Single page layout for 4 or fewer students, multiple pages for 5+
        pages = []
        total_students = len(students)
        
        if total_students <= 4:
            # Single page for 1-4 students with dynamic sizing
            pages = [students]
        else:
            # Multiple pages for 5+ students - max 4 per page
            max_per_page = 4
            num_pages = (total_students + max_per_page - 1) // max_per_page
            
            start_idx = 0
            for page_idx in range(num_pages):
                end_idx = min(start_idx + max_per_page, total_students)
                pages.append(students[start_idx:end_idx])
                start_idx = end_idx
            
        # Prepare template data
        template_data = {
            'pages': pages,
            'session_date': session_date,
            'boxes_per_objective': boxes_per_objective,
            'therapist_name': '',
            'organization_name': organization.name,
            'therapy_type': request.args.get('therapy_type', 'Speech/OT/PT Data Collection'),
            'page_subtitle': request.args.get('page_subtitle', f'{total_students} Students')
        }
        
        response = make_response(render_template('print_sheet.html', **template_data))
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        return response
        
    except Exception as e:
        logging.error(f"Error generating direct print sheet: {e}")
        return f"Error generating print sheet: {str(e)}", 500

# ===== APP ROUTES (Protected by subscription) =====

@app.route('/app/students')
def students():
    """Show students and objectives management page. No login required."""
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
        
        # Check freemium limits for authenticated users
        if current_user.is_authenticated and not current_user.can_add_student(organization.id):
            student_limit = current_user.get_student_limit()
            return jsonify({
                'error': f'Freemium plan limited to {student_limit} students. Upgrade to add more students.',
                'upgrade_required': True,
                'current_plan': 'freemium',
                'limit_type': 'students'
            }), 403
        
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
        # Parse objectives to check freemium limits first
        objectives = []
        if objectives_text:
            lines = [line.strip() for line in objectives_text.split('\n') if line.strip()]
            
            # Handle numbered lists (remove numbers)
            for line in lines:
                # Remove leading numbers like "1.", "2.", etc.
                import re
                cleaned = re.sub(r'^\d+\.\s*', '', line).strip()
                if cleaned:
                    objectives.append(cleaned)
        
        # Check freemium limits for authenticated users
        if current_user.is_authenticated and current_user.is_freemium_user():
            objectives_limit = current_user.get_objectives_per_student_limit()
            if len(objectives) > objectives_limit:
                return jsonify({
                    'error': f'Freemium plan limited to {objectives_limit} objectives per student. You tried to add {len(objectives)} objectives.',
                    'upgrade_required': True,
                    'current_plan': 'freemium',
                    'limit_type': 'objectives'
                }), 403
        
        # Delete existing objectives for this student
        Objective.query.filter_by(student_id=student_id).delete()
        
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
def get_objective_items(objective_id):
    """Get items for a specific objective."""
    items = ObjectiveItem.query.filter_by(objective_id=objective_id)\
        .order_by(ObjectiveItem.display_order).all()
    
    items_list = [{'id': item.id, 'text': item.item_text, 'order': item.display_order} for item in items]
    
    return jsonify({'items': items_list})

@app.route('/app/objective_items/delete', methods=['POST'])
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
    """Show data collection page. No login required."""
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

@app.route('/app/event/update', methods=['POST'])
@require_subscription
def update_event():
    """Update count and prompt level for an objective on a date."""
    data = request.get_json()
    date_str = data.get('date')
    student_id = data.get('student_id')
    objective_id = data.get('objective_id')
    count = float(data.get('count', 0))
    count2 = float(data.get('count2', 0)) if data.get('count2') else 0
    count3 = float(data.get('count3', 0)) if data.get('count3') else 0
    count_field = data.get('count_field', 'count')  # Which count field to update
    prompt_level = data.get('prompt_level', '')
    activity = data.get('activity', '')
    
    if not all([date_str, student_id, objective_id]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    if count < 0 or count2 < 0 or count3 < 0:
        return jsonify({'error': 'Count cannot be negative'}), 400
    
    session_id = get_or_create_session(date_str)
    
    try:
        # Check if event exists
        event = Event.query.filter_by(
            session_id=session_id, 
            student_id=student_id, 
            objective_id=objective_id
        ).first()
        
        # Handle different count field updates
        if count_field == 'count2':
            count_value = count2
        elif count_field == 'count3': 
            count_value = count3
        else:
            count_value = count

        # Create or update event
        if event:
            if count_field == 'count':
                event.count = count_value
            elif count_field == 'count2':
                event.count2 = count_value
            elif count_field == 'count3':
                event.count3 = count_value
            event.prompt_level = prompt_level if prompt_level else event.prompt_level
            event.activity = activity if activity else event.activity
        else:
            # Create new event with appropriate field set
            new_event_data = {
                'session_id': session_id,
                'student_id': student_id,
                'objective_id': objective_id,
                'count': count if count_field == 'count' else 0,
                'count2': count2 if count_field == 'count2' else 0,
                'count3': count3 if count_field == 'count3' else 0,
                'prompt_level': prompt_level if prompt_level else None,
                'activity': activity if activity else None
            }
            event = Event(**new_event_data)
            db.session.add(event)
        
        # Delete event if all counts are 0
        if event and event.count == 0 and event.count2 == 0 and event.count3 == 0:
            db.session.delete(event)
            count_value = 0
        
        db.session.commit()
        return jsonify({'success': True, 'count': count_value})
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
    counts2 = {}
    counts3 = {}
    prompt_levels = {}
    notes = {}
    for event in events:
        key = f"{event.student_id}-{event.objective_id}"
        counts[key] = event.count
        counts2[key] = event.count2
        counts3[key] = event.count3
        if event.prompt_level:
            prompt_levels[key] = event.prompt_level
        if event.notes:
            notes[str(event.student_id)] = event.notes
    
    return jsonify({
        'counts': counts, 
        'counts2': counts2,
        'counts3': counts3,
        'prompt_levels': prompt_levels, 
        'notes': notes
    })

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

@app.route('/app/reset_session', methods=['POST'])
def reset_session():
    """Reset all counts for the current session date."""
    try:
        data = request.get_json()
        session_date = data.get('date')
        
        if not session_date:
            return jsonify({'error': 'Date is required'}), 400
        
        # Get or create session for the date
        session = Session.query.filter_by(date=session_date).first()
        if session:
            # Delete all events for this session
            Event.query.filter_by(session_id=session.id).delete()
            db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/app/save_session', methods=['POST'])
def save_session():
    """Mark session as saved/completed with timestamp."""
    try:
        data = request.get_json()
        session_date = data.get('date')
        
        if not session_date:
            return jsonify({'error': 'Date is required'}), 400
        
        # Get or create session for the date
        session_id = get_or_create_session(session_date)
        session = Session.query.get(session_id)
        
        # Update session with save timestamp
        session.updated_at = datetime.now()
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'saved_at': session.updated_at.isoformat(),
            'message': f'Session for {session_date} saved successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/app/report')
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
    
    # Build base query - get all students and objectives, then LEFT JOIN events
    from sqlalchemy import func, or_, and_
    
    # Start with all student-objective combinations
    base_query = db.session.query(Student, Objective)\
        .filter(Student.organization_id == organization.id)\
        .filter(Objective.student_id == Student.id)
    
    # Apply student filter
    if student_id:
        base_query = base_query.filter(Student.id == student_id)
    
    if report_type == 'summary':
        # Summary report: show all objectives with counts (including zero)
        report_data = db.session.query(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.coalesce(func.sum(Event.count), 0).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).select_from(Student)\
         .join(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        # Apply date filters safely using SQLAlchemy ORM filters
        if start_date and end_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        elif start_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), Session.date >= start_date)
            )
        elif end_date:
            report_data = report_data.filter(
                or_(Session.date.is_(None), Session.date <= end_date)
            )
        
        if student_id:
            report_data = report_data.filter(Student.id == student_id)
            
        report_data = report_data.group_by(Student.id, Objective.id)\
                                 .order_by(Student.first_name, Objective.objective_text).all()
        
        # Debug logging for summary report
        logging.info(f"Summary report found {len(report_data)} records")
        if len(report_data) > 0:
            logging.info(f"Sample summary record: {report_data[0]}")
    else:
        # By objective report: show detailed events, but include zero-count objectives
        event_query = db.session.query(Event)\
            .join(Session, Event.session_id == Session.id)\
            .join(Student, Event.student_id == Student.id)\
            .join(Objective, Event.objective_id == Objective.id)\
            .filter(Student.organization_id == organization.id)
        
        # Apply filters
        if start_date:
            event_query = event_query.filter(Session.date >= start_date)
        if end_date:
            event_query = event_query.filter(Session.date <= end_date)
        if student_id:
            event_query = event_query.filter(Student.id == student_id)
        
        # Get events data including prompt level
        event_data = event_query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.prompt_level.label('prompt_level'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
        
        # Get all objectives that don't have events in this period (apply same filters)
        objectives_query = base_query
        if student_id:
            objectives_query = objectives_query.filter(Student.id == student_id)
        all_objectives = objectives_query.all()
        event_objective_ids = {(row.student, row.objective) for row in event_data}
        
        # Add zero-count entries for objectives without events
        zero_entries = []
        for student, objective in all_objectives:
            key = (student.first_name, objective.objective_text)
            if key not in event_objective_ids:
                # Create a mock row for zero count including prompt level
                from collections import namedtuple
                Row = namedtuple('Row', ['date', 'student', 'objective', 'count', 'prompt_level', 'notes'])
                zero_entries.append(Row(
                    date=None,
                    student=student.first_name,
                    objective=objective.objective_text,
                    count=0,
                    prompt_level='Not Attempted',
                    notes=''
                ))
        
        # Combine and sort
        report_data = list(event_data) + zero_entries
        report_data.sort(key=lambda x: (x.student, x.objective, x.date or date.min))
        
        # Debug logging to understand data retrieval
        logging.info(f"Report query found {len(event_data)} events and {len(zero_entries)} zero entries")
        if len(event_data) > 0:
            logging.info(f"Sample event: {event_data[0]}")
        if len(report_data) == 0:
            logging.info(f"No data found. Organization: {organization.id}, Students: {len(students)}, Start: {start_date}, End: {end_date}")
    
    # For chart types, prepare chart data
    chart_data = None
    if report_type in ['chart', 'pie']:
        # Get summary data for charts
        summary_query = db.session.query(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.coalesce(func.sum(Event.count), 0).label('total_count')
        ).select_from(Student)\
         .join(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        # Apply date filters safely using SQLAlchemy ORM filters
        if start_date and end_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        elif start_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), Session.date >= start_date)
            )
        elif end_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), Session.date <= end_date)
            )
        
        if student_id:
            summary_query = summary_query.filter(Student.id == student_id)
            
        summary_data = summary_query.group_by(Student.id, Objective.id)\
                                   .order_by(Student.first_name, Objective.objective_text).all()
        
        if report_type == 'chart':
            # Prepare data for line chart (progress over time)
            # Get session dates and student progress over time
            sessions_query = db.session.query(
                Session.date,
                Student.first_name.label('student'),
                func.sum(Event.count).label('daily_total')
            ).select_from(Session)\
             .join(Event, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)
            
            # Apply date and student filters
            if start_date and end_date:
                sessions_query = sessions_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            if student_id:
                sessions_query = sessions_query.filter(Student.id == student_id)
            
            session_data = sessions_query.group_by(Session.date, Student.first_name)\
                                       .order_by(Session.date).all()
            
            # Organize data for line chart
            chart_data = {
                'type': 'line',
                'dates': [],
                'datasets': {}
            }
            
            # Collect all unique dates and students
            all_dates = sorted(set(row.date.isoformat() for row in session_data))
            all_students = sorted(set(row.student for row in session_data))
            
            chart_data['dates'] = all_dates
            
            # Create datasets for each student
            for student in all_students:
                student_data = []
                for date_str in all_dates:
                    # Find data for this student on this date
                    daily_total = 0
                    for row in session_data:
                        if row.student == student and row.date.isoformat() == date_str:
                            daily_total = row.daily_total
                            break
                    student_data.append(daily_total)
                chart_data['datasets'][student] = student_data
            
        elif report_type == 'pie':
            # Prepare data for pie chart (objective distribution)
            objective_totals = {}
            for row in summary_data:
                # Truncate long objective names for display
                obj_name = row.objective[:30] + '...' if len(row.objective) > 30 else row.objective
                objective_totals[obj_name] = objective_totals.get(obj_name, 0) + row.total_count
            
            # Only show objectives with counts > 0 for pie chart
            objective_totals = {k: v for k, v in objective_totals.items() if v > 0}
            
            chart_data = {
                'type': 'pie',
                'labels': list(objective_totals.keys()),
                'data': list(objective_totals.values())
            }
        
        elif report_type == 'student_progress':
            # Student progress cards showing individual achievements
            chart_data = {'type': 'cards', 'students': []}
            for student in students:
                student_data = [row for row in summary_data if row.student == student.first_name]
                total_attempts = sum(row.total_count for row in student_data)
                active_goals = len([row for row in student_data if row.total_count > 0])
                chart_data['students'].append({
                    'name': student.first_name,
                    'total_attempts': total_attempts,
                    'active_goals': active_goals,
                    'total_goals': len(student_data)
                })
                
        elif report_type == 'daily_summary':
            # Daily breakdown showing session productivity
            daily_query = db.session.query(
                Session.date,
                func.sum(Event.count).label('total_attempts'),
                func.count(Event.id.distinct()).label('goal_instances'),
                func.count(Student.id.distinct()).label('students_seen')
            ).select_from(Session)\
             .join(Event, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                daily_query = daily_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            daily_data = daily_query.group_by(Session.date).order_by(Session.date.desc()).all()
            chart_data = {
                'type': 'daily',
                'sessions': [{
                    'date': row.date.isoformat(),
                    'total_attempts': row.total_attempts,
                    'goal_instances': row.goal_instances,
                    'students_seen': row.students_seen
                } for row in daily_data]
            }
            
        elif report_type == 'goal_tracking':
            # Goal achievement tracker with progress indicators
            goal_data = []
            for row in summary_data:
                # Calculate progress based on count thresholds
                progress = 'Not Started' if row.total_count == 0 else \
                          'Beginning' if row.total_count < 5 else \
                          'Developing' if row.total_count < 15 else \
                          'Proficient' if row.total_count < 30 else 'Mastered'
                goal_data.append({
                    'student': row.student,
                    'objective': row.objective,
                    'count': row.total_count,
                    'progress': progress
                })
            chart_data = {'type': 'goals', 'goals': goal_data}
            
        elif report_type == 'therapist_notes':
            # Notes view for documentation and observations
            notes_query = db.session.query(
                Session.date,
                Student.first_name.label('student'),
                Event.notes,
                func.count(Event.id).label('session_activities')
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)\
             .filter(Event.notes.isnot(None))\
             .filter(Event.notes != '')
            
            if start_date and end_date:
                notes_query = notes_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            notes_data = notes_query.group_by(Session.date, Student.first_name, Event.notes)\
                                   .order_by(Session.date.desc()).all()
            chart_data = {
                'type': 'notes',
                'entries': [{
                    'date': row.date.isoformat(),
                    'student': row.student,
                    'notes': row.notes,
                    'activities': row.session_activities
                } for row in notes_data]
            }
            
        elif report_type == 'data_grid':
            # Quick data grid showing all students and objectives
            grid_data = {}
            for row in summary_data:
                if row.student not in grid_data:
                    grid_data[row.student] = {}
                # Truncate objective for grid display
                short_obj = row.objective[:20] + '...' if len(row.objective) > 20 else row.objective
                grid_data[row.student][short_obj] = row.total_count
            
            chart_data = {
                'type': 'grid',
                'students': list(grid_data.keys()),
                'objectives': list(set(obj for student_data in grid_data.values() for obj in student_data.keys())),
                'data': grid_data
            }
            
        elif report_type == 'student_dashboard':
            # Comprehensive student dashboard with individual session details and totals
            dashboard_query = db.session.query(
                Session.date,
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                Event.count,
                Event.prompt_level,
                Event.notes
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .join(Objective, Event.objective_id == Objective.id)\
             .filter(Student.organization_id == organization.id)
            
            # Apply date filters
            if start_date and end_date:
                dashboard_query = dashboard_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            elif start_date:
                dashboard_query = dashboard_query.filter(Session.date >= start_date)
            elif end_date:
                dashboard_query = dashboard_query.filter(Session.date <= end_date)
            
            # Apply student filter
            if student_id:
                dashboard_query = dashboard_query.filter(Student.id == student_id)
            
            # Execute query and get data
            dashboard_data = dashboard_query.order_by(Session.date.desc()).all()
            
            # Create session snapshots and summary data
            sessions_summary = {}
            student_totals = {}
            
            for event in dashboard_data:
                # Group by date for session snapshots
                date_key = event.date.isoformat()
                if date_key not in sessions_summary:
                    sessions_summary[date_key] = {
                        'date': event.date,
                        'events': [],
                        'total_attempts': 0,
                        'students': set()
                    }
                
                sessions_summary[date_key]['events'].append({
                    'student': event.student,
                    'objective': event.objective,
                    'count': event.count,
                    'prompt_level': event.prompt_level,
                    'notes': event.notes
                })
                sessions_summary[date_key]['total_attempts'] += event.count
                sessions_summary[date_key]['students'].add(event.student)
                
                # Calculate student totals
                if event.student not in student_totals:
                    student_totals[event.student] = {
                        'total_attempts': 0,
                        'sessions': set(),
                        'objectives': set(),
                        'latest_session': None
                    }
                
                student_totals[event.student]['total_attempts'] += event.count
                student_totals[event.student]['sessions'].add(event.date)
                student_totals[event.student]['objectives'].add(event.objective)
                if not student_totals[event.student]['latest_session'] or event.date > student_totals[event.student]['latest_session']:
                    student_totals[event.student]['latest_session'] = event.date
            
            # Convert sets to counts and format for template
            for student in student_totals:
                student_totals[student]['session_count'] = len(student_totals[student]['sessions'])
                student_totals[student]['objective_count'] = len(student_totals[student]['objectives'])
                del student_totals[student]['sessions']
                del student_totals[student]['objectives']
                if student_totals[student]['latest_session']:
                    student_totals[student]['latest_session'] = student_totals[student]['latest_session'].isoformat()
            
            # Convert sessions set to list for template
            for session in sessions_summary.values():
                session['student_count'] = len(session['students'])
                session['students'] = list(session['students'])
            
            # Calculate sessions count per student for template compatibility
            student_session_counts = {}
            for student in student_totals:
                student_session_counts[student] = student_totals[student]['session_count']
            
            # Format sessions_by_date for template (convert from sessions_summary)
            sessions_by_date = {}
            for date_key, session_data in sessions_summary.items():
                sessions_by_date[session_data['date'].strftime('%Y-%m-%d')] = session_data['events']
            
            chart_data = {
                'type': 'student_dashboard',
                'student_totals': {student: data['total_attempts'] for student, data in student_totals.items()},
                'total_sessions': student_session_counts,
                'sessions_by_date': dict(sorted(sessions_by_date.items(), reverse=True)),  # Most recent first
                'filtered_student': Student.query.get(student_id).first_name if student_id and Student.query.get(student_id) else None,
                'date_range': {
                    'start': start_date,
                    'end': end_date
                }
            }
            
        elif report_type == 'session_analytics':
            # Session Analytics: Track +/+pt/additional counts with percentages and prompt levels
            analytics_query = db.session.query(
                Session.date,
                Student.first_name.label('student_name'),
                Student.id.label('student_id'),
                func.sum(Event.count).label('main_count'),  # + button interactions
                func.sum(Event.count2).label('count2_total'),  # Additional count box 1
                func.sum(Event.count3).label('count3_total'),  # Additional count box 2
                func.count(Event.id).label('event_instances'),  # Total objective instances
                Event.prompt_level.label('session_prompt')  # Session-wide prompt level
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)
            
            # Apply date filters
            if start_date and end_date:
                analytics_query = analytics_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            elif start_date:
                analytics_query = analytics_query.filter(Session.date >= start_date)
            elif end_date:
                analytics_query = analytics_query.filter(Session.date <= end_date)
            
            # Apply student filter
            if student_id:
                analytics_query = analytics_query.filter(Student.id == student_id)
            
            # Group by session, student, and prompt level
            analytics_data = analytics_query.group_by(
                Session.date, Student.id, Student.first_name, Event.prompt_level
            ).order_by(Session.date.desc(), Student.first_name).all()
            
            # Process the data for display
            session_analytics = []
            for row in analytics_data:
                # Handle null values
                main_count = float(row.main_count or 0)
                count2_total = float(row.count2_total or 0) 
                count3_total = float(row.count3_total or 0)
                event_instances = int(row.event_instances or 0)
                
                # Calculate combined total (+ and +pt interpretation)
                combined_total = main_count + count2_total
                
                # Calculate grand total of all counts
                grand_total = main_count + count2_total + count3_total
                
                # Calculate fractions and percentages (avoid division by zero)
                if grand_total > 0:
                    main_fraction = main_count / grand_total
                    count2_fraction = count2_total / grand_total
                    count3_fraction = count3_total / grand_total
                    combined_fraction = combined_total / grand_total
                    
                    main_percentage = round(main_fraction * 100, 1)
                    count2_percentage = round(count2_fraction * 100, 1)
                    count3_percentage = round(count3_fraction * 100, 1)
                    combined_percentage = round(combined_fraction * 100, 1)
                else:
                    main_fraction = count2_fraction = count3_fraction = combined_fraction = 0
                    main_percentage = count2_percentage = count3_percentage = combined_percentage = 0
                
                session_analytics.append({
                    'date': row.date.isoformat(),
                    'student_name': row.student_name,
                    'session_prompt': row.session_prompt or 'Not Set',
                    'main_count': main_count,
                    'count2_total': count2_total,
                    'count3_total': count3_total,
                    'combined_total': combined_total,
                    'grand_total': grand_total,
                    'event_instances': event_instances,
                    'main_fraction': f"{main_count:.1f}/{grand_total:.1f}",
                    'count2_fraction': f"{count2_total:.1f}/{grand_total:.1f}",
                    'count3_fraction': f"{count3_total:.1f}/{grand_total:.1f}",
                    'combined_fraction': f"{combined_total:.1f}/{grand_total:.1f}",
                    'main_percentage': main_percentage,
                    'count2_percentage': count2_percentage,
                    'count3_percentage': count3_percentage,
                    'combined_percentage': combined_percentage
                })
            
            chart_data = {
                'type': 'session_analytics',
                'analytics': session_analytics,
                'total_sessions': len(set((row['date'], row['student_name']) for row in session_analytics))
            }
            
        elif report_type == 'comprehensive_dashboard':
            # Zoho-style comprehensive dashboard with advanced analytics
            dashboard_data = {}
            
            # If student is selected, create detailed individual dashboard
            if student_id:
                selected_student = Student.query.get(student_id)
                if selected_student:
                    # Get all data for this student
                    student_events = db.session.query(
                        Session.date,
                        Objective.objective_text,
                        Event.count,
                        Event.prompt_level,
                        Event.notes
                    ).select_from(Event)\
                     .join(Session, Event.session_id == Session.id)\
                     .join(Objective, Event.objective_id == Objective.id)\
                     .filter(Event.student_id == student_id)
                    
                    # Apply date filters
                    if start_date and end_date:
                        student_events = student_events.filter(
                            and_(Session.date >= start_date, Session.date <= end_date)
                        )
                    
                    events_data = student_events.order_by(Session.date.desc()).all()
                    
                    # Calculate metrics
                    total_attempts = sum(e.count for e in events_data)
                    total_sessions = len(set(e.date for e in events_data))
                    active_objectives = len(set(e.objective_text for e in events_data))
                    
                    # Prompt level distribution
                    prompt_levels = {}
                    for event in events_data:
                        if event.prompt_level:
                            prompt_levels[event.prompt_level] = prompt_levels.get(event.prompt_level, 0) + event.count
                    
                    # Progress by objective
                    objectives_progress = {}
                    for event in events_data:
                        obj = event.objective_text[:50] + '...' if len(event.objective_text) > 50 else event.objective_text
                        if obj not in objectives_progress:
                            objectives_progress[obj] = {'total': 0, 'sessions': set(), 'latest_prompt': 'Unknown'}
                        objectives_progress[obj]['total'] += event.count
                        objectives_progress[obj]['sessions'].add(event.date)
                        if event.prompt_level:
                            objectives_progress[obj]['latest_prompt'] = event.prompt_level
                    
                    # Convert sessions sets to counts
                    for obj in objectives_progress:
                        objectives_progress[obj]['session_count'] = len(objectives_progress[obj]['sessions'])
                        del objectives_progress[obj]['sessions']
                    
                    dashboard_data = {
                        'type': 'individual',
                        'student_name': selected_student.first_name,
                        'student_id': student_id,
                        'total_attempts': total_attempts,
                        'total_sessions': total_sessions,
                        'active_objectives': active_objectives,
                        'prompt_levels': prompt_levels,
                        'objectives_progress': objectives_progress,
                        'recent_events': events_data[:10]  # Last 10 events
                    }
            else:
                # Organization overview dashboard
                all_events = db.session.query(
                    Student.first_name,
                    func.sum(Event.count).label('total_attempts'),
                    func.count(Session.date.distinct()).label('sessions'),
                    func.count(Event.objective_id.distinct()).label('objectives')
                ).select_from(Event)\
                 .join(Student, Event.student_id == Student.id)\
                 .join(Session, Event.session_id == Session.id)\
                 .filter(Student.organization_id == organization.id)
                
                # Apply date filters
                if start_date and end_date:
                    all_events = all_events.filter(
                        and_(Session.date >= start_date, Session.date <= end_date)
                    )
                
                student_summaries = all_events.group_by(Student.id).all()
                
                dashboard_data = {
                    'type': 'overview',
                    'student_summaries': [{
                        'name': s.first_name,
                        'total_attempts': s.total_attempts,
                        'sessions': s.sessions,
                        'objectives': s.objectives
                    } for s in student_summaries],
                    'total_students': len(student_summaries),
                    'total_attempts': sum(s.total_attempts for s in student_summaries),
                    'total_sessions': sum(s.sessions for s in student_summaries)
                }
            
            chart_data = dashboard_data
            
    
    # Final debug logging
    logging.info(f"Rendering report: type={report_type}, data_count={len(report_data) if report_data else 0}")
    
    return render_template('report.html', 
                         report_data=report_data,
                         students=students,
                         start_date=start_date,
                         end_date=end_date,
                         student_id=student_id,
                         report_type=report_type,
                         chart_data=chart_data)

@app.route('/app/report.pdf')
@require_subscription
def report_pdf():
    """Export report as PDF for ALL report types."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.units import inch
        from reportlab.lib.colors import black, white, lightgrey, green, red, yellow
        from reportlab.lib import colors
        import io
        from datetime import datetime
        
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        student_id = request.args.get('student_id', '')
        report_type = request.args.get('type', 'by_objective')
        
        # Get the same data as the regular report
        organization = Organization.query.first()
        if not organization:
            return "No organization found", 404
        
        # Get students for context
        students = Student.query.filter_by(organization_id=organization.id).all()
        selected_student = None
        if student_id:
            selected_student = next((s for s in students if str(s.id) == str(student_id)), None)
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
        
        # Create styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, spaceAfter=30, textColor=colors.darkblue)
        heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=12, spaceAfter=12, textColor=colors.black)
        
        # Build story
        story = []
        
        # Title
        story.append(Paragraph(f"Speech Therapy Report - {report_type.replace('_', ' ').title()}", title_style))
        story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", styles['Normal']))
        story.append(Spacer(1, 12))
        
        # Report details
        details = [
            f"Date Range: {start_date} to {end_date}" if start_date and end_date else "All dates",
            f"Student: {selected_student.first_name}" if selected_student else "All students",
            f"Organization: {organization.name}"
        ]
        for detail in details:
            story.append(Paragraph(detail, styles['Normal']))
        story.append(Spacer(1, 20))
        
        from sqlalchemy import func, or_, and_
        
        # Generate data based on report type - reuse logic from CSV export
        if report_type == 'summary':
            # Summary report
            query = db.session.query(Event)\
                .join(Session, Event.session_id == Session.id)\
                .join(Student, Event.student_id == Student.id)\
                .join(Objective, Event.objective_id == Objective.id)
            
            if start_date:
                query = query.filter(Session.date >= start_date)
            if end_date:
                query = query.filter(Session.date <= end_date)
            if student_id:
                query = query.filter(Student.id == student_id)
            
            report_data = query.with_entities(
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                func.sum(Event.count).label('total_count'),
                func.min(Session.date).label('start_date'),
                func.max(Session.date).label('end_date')
            ).group_by(Student.id, Objective.id)\
             .order_by(Student.first_name, Objective.objective_text).all()
            
            headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
            data = [headers]
            for row in report_data:
                obj_text = row.objective[:40] + '...' if len(row.objective) > 40 else row.objective
                data.append([row.student, obj_text, str(row.total_count), str(row.start_date), str(row.end_date)])
                
        elif report_type == 'by_objective':
            # Detailed by objective report
            query = db.session.query(Event)\
                .join(Session, Event.session_id == Session.id)\
                .join(Student, Event.student_id == Student.id)\
                .join(Objective, Event.objective_id == Objective.id)
            
            if start_date:
                query = query.filter(Session.date >= start_date)
            if end_date:
                query = query.filter(Session.date <= end_date)
            if student_id:
                query = query.filter(Student.id == student_id)
            
            report_data = query.with_entities(
                Session.date.label('date'),
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                Event.count.label('count'),
                Event.prompt_level.label('prompt_level'),
                Event.notes.label('notes')
            ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
            
            headers = ['Date', 'Student', 'Objective', 'Count', 'Prompt Level']
            data = [headers]
            for row in report_data:
                obj_text = row.objective[:30] + '...' if len(row.objective) > 30 else row.objective
                data.append([str(row.date), row.student, obj_text, str(row.count), row.prompt_level or 'Independent'])
                
        elif report_type == 'student_progress':
            # Student progress cards
            summary_query = db.session.query(
                Student.first_name.label('student'),
                func.coalesce(func.sum(Event.count), 0).label('total_count')
            ).select_from(Student)\
             .outerjoin(Objective, Objective.student_id == Student.id)\
             .outerjoin(Event, Event.objective_id == Objective.id)\
             .outerjoin(Session, Event.session_id == Session.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                summary_query = summary_query.filter(
                    or_(Session.date.is_(None), 
                        and_(Session.date >= start_date, Session.date <= end_date))
                )
            if student_id:
                summary_query = summary_query.filter(Student.id == student_id)
            
            summary_data = summary_query.group_by(Student.id).all()
            
            headers = ['Student', 'Total Attempts', 'Progress Level']
            data = [headers]
            for row in summary_data:
                progress_level = 'Not Started' if row.total_count == 0 else \
                               'Beginning' if row.total_count < 5 else \
                               'Developing' if row.total_count < 15 else \
                               'Proficient' if row.total_count < 30 else 'Mastered'
                data.append([row.student, str(row.total_count), progress_level])
                
        elif report_type == 'daily_summary':
            # Daily session breakdown
            daily_query = db.session.query(
                Session.date,
                func.sum(Event.count).label('total_attempts'),
                func.count(Event.id.distinct()).label('goal_instances'),
                func.count(Student.id.distinct()).label('students_seen')
            ).select_from(Session)\
             .join(Event, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                daily_query = daily_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            daily_data = daily_query.group_by(Session.date).order_by(Session.date.desc()).all()
            headers = ['Date', 'Total Attempts', 'Goal Instances', 'Students Seen']
            data = [headers]
            for row in daily_data:
                data.append([str(row.date), str(row.total_attempts), str(row.goal_instances), str(row.students_seen)])
                
        elif report_type == 'goal_tracking':
            # Goal achievement tracker
            summary_query = db.session.query(
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                func.coalesce(func.sum(Event.count), 0).label('total_count')
            ).select_from(Student)\
             .join(Objective, Objective.student_id == Student.id)\
             .outerjoin(Event, Event.objective_id == Objective.id)\
             .outerjoin(Session, Event.session_id == Session.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                summary_query = summary_query.filter(
                    or_(Session.date.is_(None), 
                        and_(Session.date >= start_date, Session.date <= end_date))
                )
            if student_id:
                summary_query = summary_query.filter(Student.id == student_id)
            
            summary_data = summary_query.group_by(Student.id, Objective.id).all()
            
            headers = ['Student', 'Objective', 'Count', 'Progress']
            data = [headers]
            for row in summary_data:
                progress = 'Not Started' if row.total_count == 0 else \
                          'Beginning' if row.total_count < 5 else \
                          'Developing' if row.total_count < 15 else \
                          'Proficient' if row.total_count < 30 else 'Mastered'
                obj_text = row.objective[:35] + '...' if len(row.objective) > 35 else row.objective
                data.append([row.student, obj_text, str(row.total_count), progress])
                
        elif report_type == 'therapist_notes':
            # Notes view
            notes_query = db.session.query(
                Session.date,
                Student.first_name.label('student'),
                Event.notes,
                func.count(Event.id).label('session_activities')
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)\
             .filter(Event.notes.isnot(None))\
             .filter(Event.notes != '')
            
            if start_date and end_date:
                notes_query = notes_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            notes_data = notes_query.group_by(Session.date, Student.first_name, Event.notes)\
                                   .order_by(Session.date.desc()).all()
            headers = ['Date', 'Student', 'Notes', 'Activities']
            data = [headers]
            for row in notes_data:
                notes_text = row.notes[:60] + '...' if len(row.notes) > 60 else row.notes
                data.append([str(row.date), row.student, notes_text, str(row.session_activities)])
                
        elif report_type == 'data_grid':
            # Grid format - students x objectives
            summary_query = db.session.query(
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                func.coalesce(func.sum(Event.count), 0).label('total_count')
            ).select_from(Student)\
             .join(Objective, Objective.student_id == Student.id)\
             .outerjoin(Event, Event.objective_id == Objective.id)\
             .outerjoin(Session, Event.session_id == Session.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                summary_query = summary_query.filter(
                    or_(Session.date.is_(None), 
                        and_(Session.date >= start_date, Session.date <= end_date))
                )
            if student_id:
                summary_query = summary_query.filter(Student.id == student_id)
            
            summary_data = summary_query.group_by(Student.id, Objective.id).all()
            headers = ['Student', 'Objective', 'Count']
            data = [headers]
            for row in summary_data:
                obj_text = row.objective[:40] + '...' if len(row.objective) > 40 else row.objective
                data.append([row.student, obj_text, str(row.total_count)])
                
        elif report_type == 'student_dashboard':
            # Session details by date
            dashboard_query = db.session.query(
                Session.date,
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                Event.count,
                Event.prompt_level,
                Event.notes
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .join(Objective, Event.objective_id == Objective.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                dashboard_query = dashboard_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            if student_id:
                dashboard_query = dashboard_query.filter(Student.id == student_id)
            
            session_details = dashboard_query.order_by(Session.date.desc(), Student.first_name).all()
            headers = ['Date', 'Student', 'Objective', 'Count', 'Prompt Level']
            data = [headers]
            for row in session_details:
                obj_text = row.objective[:30] + '...' if len(row.objective) > 30 else row.objective
                data.append([str(row.date), row.student, obj_text, str(row.count), 
                            row.prompt_level or 'Independent'])
                
        elif report_type == 'comprehensive_dashboard':
            if student_id:
                # Individual student detailed dashboard
                student_events = db.session.query(
                    Session.date,
                    Objective.objective_text,
                    Event.count,
                    Event.prompt_level,
                    Event.notes
                ).select_from(Event)\
                 .join(Session, Event.session_id == Session.id)\
                 .join(Objective, Event.objective_id == Objective.id)\
                 .filter(Event.student_id == student_id)
                
                if start_date and end_date:
                    student_events = student_events.filter(
                        and_(Session.date >= start_date, Session.date <= end_date)
                    )
                
                events_data = student_events.order_by(Session.date.desc()).all()
                headers = ['Date', 'Objective', 'Count', 'Prompt Level']
                data = [headers]
                for row in events_data:
                    obj_text = row.objective_text[:40] + '...' if len(row.objective_text) > 40 else row.objective_text
                    data.append([str(row.date), obj_text, str(row.count), 
                                row.prompt_level or 'Independent'])
            else:
                # Organization overview
                all_events = db.session.query(
                    Student.first_name,
                    func.sum(Event.count).label('total_attempts'),
                    func.count(Session.date.distinct()).label('sessions'),
                    func.count(Event.objective_id.distinct()).label('objectives')
                ).select_from(Event)\
                 .join(Student, Event.student_id == Student.id)\
                 .join(Session, Event.session_id == Session.id)\
                 .filter(Student.organization_id == organization.id)
                
                if start_date and end_date:
                    all_events = all_events.filter(
                        and_(Session.date >= start_date, Session.date <= end_date)
                    )
                
                student_summaries = all_events.group_by(Student.id).all()
                headers = ['Student', 'Total Attempts', 'Sessions', 'Objectives']
                data = [headers]
                for s in student_summaries:
                    data.append([s.first_name, str(s.total_attempts), str(s.sessions), str(s.objectives)])
        
        elif report_type == 'session_analytics':
            # Session Analytics: Track +/+pt/additional counts with percentages and prompt levels
            analytics_query = db.session.query(
                Session.date,
                Student.first_name.label('student_name'),
                Student.id.label('student_id'),
                func.sum(Event.count).label('main_count'),  # + button interactions
                func.sum(Event.count2).label('count2_total'),  # Additional count box 1
                func.sum(Event.count3).label('count3_total'),  # Additional count box 2
                func.count(Event.id).label('event_instances'),  # Total objective instances
                Event.prompt_level.label('session_prompt')  # Session-wide prompt level
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Student, Event.student_id == Student.id)\
             .filter(Student.organization_id == organization.id)
            
            # Apply date filters
            if start_date and end_date:
                analytics_query = analytics_query.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            elif start_date:
                analytics_query = analytics_query.filter(Session.date >= start_date)
            elif end_date:
                analytics_query = analytics_query.filter(Session.date <= end_date)
            
            # Apply student filter
            if student_id:
                analytics_query = analytics_query.filter(Student.id == student_id)
            
            # Group by session, student, and prompt level
            analytics_data = analytics_query.group_by(
                Session.date, Student.id, Student.first_name, Event.prompt_level
            ).order_by(Session.date.desc(), Student.first_name).all()
            
            # Process the data for PDF export
            headers = ['Date', 'Student', 'Prompt', 'Main', 'Count2', 'Count3', 'Combined', 'Grand', 'Main %', 'Comb %']
            data = [headers]
            
            for row in analytics_data:
                # Handle null values
                main_count = float(row.main_count or 0)
                count2_total = float(row.count2_total or 0) 
                count3_total = float(row.count3_total or 0)
                
                # Calculate combined total (+ and +pt interpretation)
                combined_total = main_count + count2_total
                
                # Calculate grand total of all counts
                grand_total = main_count + count2_total + count3_total
                
                # Calculate percentages (avoid division by zero)
                if grand_total > 0:
                    main_percentage = round((main_count / grand_total) * 100, 1)
                    combined_percentage = round((combined_total / grand_total) * 100, 1)
                else:
                    main_percentage = combined_percentage = 0
                
                data.append([
                    str(row.date),
                    row.student_name,
                    (row.session_prompt or 'Not Set')[:8],  # Truncate for PDF width
                    str(int(main_count)),
                    str(int(count2_total)),
                    str(int(count3_total)),
                    str(int(combined_total)),
                    str(int(grand_total)),
                    f"{main_percentage}%",
                    f"{combined_percentage}%"
                ])
        
        else:
            # Fallback to by_objective for unknown types
            query = db.session.query(Event)\
                .join(Session, Event.session_id == Session.id)\
                .join(Student, Event.student_id == Student.id)\
                .join(Objective, Event.objective_id == Objective.id)
            
            if start_date:
                query = query.filter(Session.date >= start_date)
            if end_date:
                query = query.filter(Session.date <= end_date)
            if student_id:
                query = query.filter(Student.id == student_id)
            
            report_data = query.with_entities(
                Session.date.label('date'),
                Student.first_name.label('student'),
                Objective.objective_text.label('objective'),
                Event.count.label('count'),
                Event.prompt_level.label('prompt_level')
            ).order_by(Session.date.desc(), Student.first_name).all()
            
            headers = ['Date', 'Student', 'Objective', 'Count', 'Prompt Level']
            data = [headers]
            for row in report_data:
                obj_text = row.objective[:40] + '...' if len(row.objective) > 40 else row.objective
                data.append([str(row.date), row.student, obj_text, str(row.count), row.prompt_level or 'Independent'])
        
        # Create and add table if we have data
        if len(data) > 1:  # More than just headers
            # Create table
            table = Table(data)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey])
            ]))
            
            story.append(Paragraph("Report Data", heading_style))
            story.append(table)
        else:
            story.append(Paragraph("No data found for the specified criteria.", styles['Normal']))
        
        # Build PDF
        doc.build(story)
        
        # Create response
        buffer.seek(0)
        response = make_response(buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename="therapy_report_{report_type}_{start_date or "all"}.pdf"'
        
        return response
        
    except Exception as e:
        logging.error(f"PDF generation error: {str(e)}")
        return f"Error generating PDF: {str(e)}", 500

@app.route('/app/report.csv')
def report_csv():
    """Export report as CSV for ALL report types."""
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
    
    # Get all students for filtering
    students = Student.query.filter_by(organization_id=organization.id).order_by(Student.first_name).all()
    
    from sqlalchemy import func, or_, and_
    
    # Generate data based on report type
    if report_type == 'summary':
        # Existing summary logic
        query = db.session.query(Event)\
            .join(Session, Event.session_id == Session.id)\
            .join(Student, Event.student_id == Student.id)\
            .join(Objective, Event.objective_id == Objective.id)
        
        if start_date:
            query = query.filter(Session.date >= start_date)
        if end_date:
            query = query.filter(Session.date <= end_date)
        if student_id:
            query = query.filter(Student.id == student_id)
        
        report_data = query.with_entities(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.sum(Event.count).label('total_count'),
            func.min(Session.date).label('start_date'),
            func.max(Session.date).label('end_date')
        ).group_by(Student.id, Objective.id)\
         .order_by(Student.first_name, Objective.objective_text).all()
        headers = ['Student', 'Objective', 'Total Count', 'Start Date', 'End Date']
        rows = [[row.student, row.objective, row.total_count, row.start_date, row.end_date] for row in report_data]
        
    elif report_type == 'by_objective':
        # Existing by_objective logic
        query = db.session.query(Event)\
            .join(Session, Event.session_id == Session.id)\
            .join(Student, Event.student_id == Student.id)\
            .join(Objective, Event.objective_id == Objective.id)
        
        if start_date:
            query = query.filter(Session.date >= start_date)
        if end_date:
            query = query.filter(Session.date <= end_date)
        if student_id:
            query = query.filter(Student.id == student_id)
        
        report_data = query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.prompt_level.label('prompt_level'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
        headers = ['Date', 'Student', 'Objective', 'Count', 'Prompt Level', 'Notes']
        rows = [[row.date, row.student, row.objective, row.count, row.prompt_level or '', row.notes or ''] for row in report_data]
        
    elif report_type == 'student_progress':
        # Student progress cards data
        summary_query = db.session.query(
            Student.first_name.label('student'),
            func.coalesce(func.sum(Event.count), 0).label('total_count')
        ).select_from(Student)\
         .outerjoin(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        if start_date and end_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        if student_id:
            summary_query = summary_query.filter(Student.id == student_id)
        
        summary_data = summary_query.group_by(Student.id).all()
        
        headers = ['Student', 'Total Attempts', 'Progress Level']
        rows = []
        for row in summary_data:
            progress_level = 'Not Started' if row.total_count == 0 else \
                           'Beginning' if row.total_count < 5 else \
                           'Developing' if row.total_count < 15 else \
                           'Proficient' if row.total_count < 30 else 'Mastered'
            rows.append([row.student, row.total_count, progress_level])
        
    elif report_type == 'daily_summary':
        # Daily session breakdown
        daily_query = db.session.query(
            Session.date,
            func.sum(Event.count).label('total_attempts'),
            func.count(Event.id.distinct()).label('goal_instances'),
            func.count(Student.id.distinct()).label('students_seen')
        ).select_from(Session)\
         .join(Event, Event.session_id == Session.id)\
         .join(Student, Event.student_id == Student.id)\
         .filter(Student.organization_id == organization.id)
        
        if start_date and end_date:
            daily_query = daily_query.filter(
                and_(Session.date >= start_date, Session.date <= end_date)
            )
        
        daily_data = daily_query.group_by(Session.date).order_by(Session.date.desc()).all()
        headers = ['Date', 'Total Attempts', 'Goal Instances', 'Students Seen']
        rows = [[row.date, row.total_attempts, row.goal_instances, row.students_seen] for row in daily_data]
        
    elif report_type == 'goal_tracking':
        # Goal achievement tracker
        summary_query = db.session.query(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.coalesce(func.sum(Event.count), 0).label('total_count')
        ).select_from(Student)\
         .join(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        if start_date and end_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        if student_id:
            summary_query = summary_query.filter(Student.id == student_id)
        
        summary_data = summary_query.group_by(Student.id, Objective.id).all()
        
        headers = ['Student', 'Objective', 'Total Count', 'Progress Status']
        rows = []
        for row in summary_data:
            progress = 'Not Started' if row.total_count == 0 else \
                      'Beginning' if row.total_count < 5 else \
                      'Developing' if row.total_count < 15 else \
                      'Proficient' if row.total_count < 30 else 'Mastered'
            rows.append([row.student, row.objective, row.total_count, progress])
        
    elif report_type == 'therapist_notes':
        # Notes view
        notes_query = db.session.query(
            Session.date,
            Student.first_name.label('student'),
            Event.notes,
            func.count(Event.id).label('session_activities')
        ).select_from(Event)\
         .join(Session, Event.session_id == Session.id)\
         .join(Student, Event.student_id == Student.id)\
         .filter(Student.organization_id == organization.id)\
         .filter(Event.notes.isnot(None))\
         .filter(Event.notes != '')
        
        if start_date and end_date:
            notes_query = notes_query.filter(
                and_(Session.date >= start_date, Session.date <= end_date)
            )
        
        notes_data = notes_query.group_by(Session.date, Student.first_name, Event.notes)\
                               .order_by(Session.date.desc()).all()
        headers = ['Date', 'Student', 'Notes', 'Session Activities']
        rows = [[row.date, row.student, row.notes, row.session_activities] for row in notes_data]
        
    elif report_type == 'data_grid':
        # Grid format - students x objectives
        summary_query = db.session.query(
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            func.coalesce(func.sum(Event.count), 0).label('total_count')
        ).select_from(Student)\
         .join(Objective, Objective.student_id == Student.id)\
         .outerjoin(Event, Event.objective_id == Objective.id)\
         .outerjoin(Session, Event.session_id == Session.id)\
         .filter(Student.organization_id == organization.id)
        
        if start_date and end_date:
            summary_query = summary_query.filter(
                or_(Session.date.is_(None), 
                    and_(Session.date >= start_date, Session.date <= end_date))
            )
        if student_id:
            summary_query = summary_query.filter(Student.id == student_id)
        
        summary_data = summary_query.group_by(Student.id, Objective.id).all()
        headers = ['Student', 'Objective', 'Count']
        rows = [[row.student, row.objective, row.total_count] for row in summary_data]
        
    elif report_type == 'student_dashboard':
        # Session details by date
        dashboard_query = db.session.query(
            Session.date,
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count,
            Event.prompt_level,
            Event.notes
        ).select_from(Event)\
         .join(Session, Event.session_id == Session.id)\
         .join(Student, Event.student_id == Student.id)\
         .join(Objective, Event.objective_id == Objective.id)\
         .filter(Student.organization_id == organization.id)
        
        if start_date and end_date:
            dashboard_query = dashboard_query.filter(
                and_(Session.date >= start_date, Session.date <= end_date)
            )
        if student_id:
            dashboard_query = dashboard_query.filter(Student.id == student_id)
        
        session_details = dashboard_query.order_by(Session.date.desc(), Student.first_name).all()
        headers = ['Date', 'Student', 'Objective', 'Count', 'Prompt Level', 'Notes']
        rows = [[row.date, row.student, row.objective, row.count, 
                row.prompt_level or 'Independent', row.notes or ''] for row in session_details]
        
    elif report_type == 'comprehensive_dashboard':
        if student_id:
            # Individual student detailed dashboard
            student_events = db.session.query(
                Session.date,
                Objective.objective_text,
                Event.count,
                Event.prompt_level,
                Event.notes
            ).select_from(Event)\
             .join(Session, Event.session_id == Session.id)\
             .join(Objective, Event.objective_id == Objective.id)\
             .filter(Event.student_id == student_id)
            
            if start_date and end_date:
                student_events = student_events.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            events_data = student_events.order_by(Session.date.desc()).all()
            headers = ['Date', 'Objective', 'Count', 'Prompt Level', 'Notes']
            rows = [[row.date, row.objective_text, row.count, 
                    row.prompt_level or 'Independent', row.notes or ''] for row in events_data]
        else:
            # Organization overview
            all_events = db.session.query(
                Student.first_name,
                func.sum(Event.count).label('total_attempts'),
                func.count(Session.date.distinct()).label('sessions'),
                func.count(Event.objective_id.distinct()).label('objectives')
            ).select_from(Event)\
             .join(Student, Event.student_id == Student.id)\
             .join(Session, Event.session_id == Session.id)\
             .filter(Student.organization_id == organization.id)
            
            if start_date and end_date:
                all_events = all_events.filter(
                    and_(Session.date >= start_date, Session.date <= end_date)
                )
            
            student_summaries = all_events.group_by(Student.id).all()
            headers = ['Student', 'Total Attempts', 'Sessions', 'Objectives']
            rows = [[s.first_name, s.total_attempts, s.sessions, s.objectives] for s in student_summaries]
    
    elif report_type == 'session_analytics':
        # Session Analytics: Track +/+pt/additional counts with percentages and prompt levels
        analytics_query = db.session.query(
            Session.date,
            Student.first_name.label('student_name'),
            Student.id.label('student_id'),
            func.sum(Event.count).label('main_count'),  # + button interactions
            func.sum(Event.count2).label('count2_total'),  # Additional count box 1
            func.sum(Event.count3).label('count3_total'),  # Additional count box 2
            func.count(Event.id).label('event_instances'),  # Total objective instances
            Event.prompt_level.label('session_prompt')  # Session-wide prompt level
        ).select_from(Event)\
         .join(Session, Event.session_id == Session.id)\
         .join(Student, Event.student_id == Student.id)\
         .filter(Student.organization_id == organization.id)
        
        # Apply date filters
        if start_date and end_date:
            analytics_query = analytics_query.filter(
                and_(Session.date >= start_date, Session.date <= end_date)
            )
        elif start_date:
            analytics_query = analytics_query.filter(Session.date >= start_date)
        elif end_date:
            analytics_query = analytics_query.filter(Session.date <= end_date)
        
        # Apply student filter
        if student_id:
            analytics_query = analytics_query.filter(Student.id == student_id)
        
        # Group by session, student, and prompt level
        analytics_data = analytics_query.group_by(
            Session.date, Student.id, Student.first_name, Event.prompt_level
        ).order_by(Session.date.desc(), Student.first_name).all()
        
        # Process the data for CSV export
        headers = ['Date', 'Student', 'Session Prompt', 'Main Count', 'Count2 Total', 'Count3 Total', 
                  'Combined Total', 'Grand Total', 'Event Instances', 'Main Fraction', 'Count2 Fraction', 
                  'Count3 Fraction', 'Combined Fraction', 'Main %', 'Count2 %', 'Count3 %', 'Combined %']
        rows = []
        
        for row in analytics_data:
            # Handle null values
            main_count = float(row.main_count or 0)
            count2_total = float(row.count2_total or 0) 
            count3_total = float(row.count3_total or 0)
            event_instances = int(row.event_instances or 0)
            
            # Calculate combined total (+ and +pt interpretation)
            combined_total = main_count + count2_total
            
            # Calculate grand total of all counts
            grand_total = main_count + count2_total + count3_total
            
            # Calculate fractions and percentages (avoid division by zero)
            if grand_total > 0:
                main_fraction = main_count / grand_total
                count2_fraction = count2_total / grand_total
                count3_fraction = count3_total / grand_total
                combined_fraction = combined_total / grand_total
                
                main_percentage = round(main_fraction * 100, 1)
                count2_percentage = round(count2_fraction * 100, 1)
                count3_percentage = round(count3_fraction * 100, 1)
                combined_percentage = round(combined_fraction * 100, 1)
            else:
                main_fraction = count2_fraction = count3_fraction = combined_fraction = 0
                main_percentage = count2_percentage = count3_percentage = combined_percentage = 0
            
            rows.append([
                row.date.isoformat(),
                row.student_name,
                row.session_prompt or 'Not Set',
                main_count,
                count2_total,
                count3_total,
                combined_total,
                grand_total,
                event_instances,
                f"{main_count:.1f}/{grand_total:.1f}",
                f"{count2_total:.1f}/{grand_total:.1f}",
                f"{count3_total:.1f}/{grand_total:.1f}",
                f"{combined_total:.1f}/{grand_total:.1f}",
                main_percentage,
                count2_percentage,
                count3_percentage,
                combined_percentage
            ])
    
    else:
        # Fallback to by_objective for unknown types
        query = db.session.query(Event)\
            .join(Session, Event.session_id == Session.id)\
            .join(Student, Event.student_id == Student.id)\
            .join(Objective, Event.objective_id == Objective.id)
        
        if start_date:
            query = query.filter(Session.date >= start_date)
        if end_date:
            query = query.filter(Session.date <= end_date)
        if student_id:
            query = query.filter(Student.id == student_id)
        
        report_data = query.with_entities(
            Session.date.label('date'),
            Student.first_name.label('student'),
            Objective.objective_text.label('objective'),
            Event.count.label('count'),
            Event.notes.label('notes')
        ).order_by(Session.date.desc(), Student.first_name, Objective.objective_text).all()
        headers = ['Date', 'Student', 'Objective', 'Count', 'Notes']
        rows = [[row.date, row.student, row.objective, row.count, row.notes or ''] for row in report_data]
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    
    for row in rows:
        writer.writerow(row)
    
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
    
    # Create mapping from headers to column names  
    if report_type == 'summary':
        column_mapping = {'Student': 'student', 'Objective': 'objective', 'Total Count': 'total_count', 'Start Date': 'start_date', 'End Date': 'end_date'}
    else:
        column_mapping = {'Date': 'date', 'Student': 'student', 'Objective': 'objective', 'Count': 'count', 'Notes': 'notes'}
    
    for row in report_data:
        line = '\t'.join([str(getattr(row, column_mapping[attr], '')) if getattr(row, column_mapping[attr], None) is not None else '' 
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
            
        # Single page layout for 4 or fewer students, multiple pages for 5+
        pages = []
        total_students = len(students)
        
        if total_students <= 4:
            # Single page for 1-4 students with dynamic sizing
            pages = [students]
        else:
            # Multiple pages for 5+ students - max 4 per page
            max_per_page = 4
            num_pages = (total_students + max_per_page - 1) // max_per_page
            
            start_idx = 0
            for page_idx in range(num_pages):
                end_idx = min(start_idx + max_per_page, total_students)
                pages.append(students[start_idx:end_idx])
                start_idx = end_idx
            
        # Prepare template data
        template_data = {
            'pages': pages,
            'session_date': session_date,
            'boxes_per_objective': boxes_per_objective,
            'box_size': box_size,
            'compress': compress,
            'show_items': show_items,
            'therapist_name': '',  # Could be filled from user profile later
            'organization_name': organization.name,
            'therapy_type': request.args.get('therapy_type', 'Speech/OT/PT Data Collection'),
            'page_subtitle': request.args.get('page_subtitle', f'{total_students} Students')
        }
        
        response = make_response(render_template('print_sheet.html', **template_data))
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        return response
        
    except Exception as e:
        logging.error(f"Error generating print sheet: {e}")
        return f"Error generating print sheet: {str(e)}", 500

@app.route('/app/admin/import_spreadsheet', methods=['GET', 'POST'])
@admin_required
def import_spreadsheet():
    """Import data from uploaded Excel spreadsheet."""
    if request.method == 'GET':
        return render_template('import.html')
    
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if not file.filename or file.filename == '':
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
        original_name = secure_filename(file.filename or 'uploaded_file.xlsx')
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