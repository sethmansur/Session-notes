# auth.py - Custom email/password authentication system
# Replaces Replit Auth with standalone authentication

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash
from models import User, db

# Initialize extensions
login_manager = LoginManager()
bcrypt = Bcrypt()
mail = Mail()

# Configure login manager
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    """Load user from database"""
    return User.query.get(int(user_id))

# Create auth blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if current_user.is_authenticated:
        return redirect(url_for('app_dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        
        # Basic validation
        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('auth/register.html')
        
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
            return render_template('auth/register.html')
        
        # Check if user already exists
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('An account with this email already exists.', 'error')
            return render_template('auth/register.html')
        
        # Create new user
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name
        )
        user.set_password(password)
        
        # Generate email verification token
        verification_token = user.generate_email_verification_token()
        
        try:
            db.session.add(user)
            db.session.commit()
            
            # Send verification email (optional - skip if no email config)
            try:
                send_verification_email(user.email, verification_token)
                flash('Registration successful! Please check your email to verify your account.', 'success')
            except Exception as e:
                # If email fails, still allow registration but mark as verified
                user.email_verified = True
                db.session.commit()
                flash('Registration successful! You can now log in.', 'success')
            
            return redirect(url_for('auth.login'))
            
        except Exception as e:
            db.session.rollback()
            flash('Registration failed. Please try again.', 'error')
            current_app.logger.error(f"Registration error: {str(e)}")
    
    return render_template('auth/register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('app_dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        password = request.form.get('password', '')
        remember_me = request.form.get('remember_me', False)
        
        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('auth/login.html')
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user, remember=bool(remember_me))
            
            # Redirect to next page or dashboard
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('app_dashboard'))
        elif user:
            # User exists but wrong password
            flash('Invalid email or password.', 'error')
        else:
            # User doesn't exist - suggest creating account
            flash('No account found with this email. Would you like to create an account?', 'info')
            return redirect(url_for('auth.register', email=email))
    
    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    """Verify email address"""
    user = User.query.filter_by(email_verification_token=token).first()
    
    if user and user.verify_email_token(token):
        db.session.commit()
        flash('Email verified successfully! You can now access all features.', 'success')
        if current_user.is_authenticated:
            return redirect(url_for('app_dashboard'))
        else:
            return redirect(url_for('auth.login'))
    else:
        flash('Invalid or expired verification token.', 'error')
        return redirect(url_for('auth.login'))

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request"""
    if current_user.is_authenticated:
        return redirect(url_for('app_dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').lower().strip()
        
        if not email:
            flash('Email is required.', 'error')
            return render_template('auth/forgot_password.html')
        
        user = User.query.filter_by(email=email).first()
        
        if user:
            reset_token = user.generate_password_reset_token()
            db.session.commit()
            
            try:
                send_password_reset_email(user.email, reset_token)
                flash('Password reset instructions have been sent to your email.', 'info')
            except Exception as e:
                flash('Failed to send reset email. Please try again later.', 'error')
                current_app.logger.error(f"Email sending error: {str(e)}")
        else:
            # Don't reveal if email exists for security
            flash('If an account with that email exists, password reset instructions have been sent.', 'info')
        
        return redirect(url_for('auth.login'))
    
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Reset password with token"""
    if current_user.is_authenticated:
        return redirect(url_for('app_dashboard'))
    
    user = User.query.filter_by(password_reset_token=token).first()
    
    if not user or not user.verify_password_reset_token(token):
        flash('Invalid or expired reset token.', 'error')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if not password or len(password) < 8:
            flash('Password must be at least 8 characters long.', 'error')
            return render_template('auth/reset_password.html', token=token)
        
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth/reset_password.html', token=token)
        
        user.reset_password(password)
        db.session.commit()
        
        flash('Password reset successful! You can now log in with your new password.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/reset_password.html', token=token)

def send_verification_email(email, token):
    """Send email verification email"""
    if not current_app.config.get('MAIL_SERVER'):
        raise Exception("Email not configured")
    
    subject = "Verify Your Email - SessionNotes"
    
    # Create verification URL
    verify_url = url_for('auth.verify_email', token=token, _external=True)
    
    html_body = f"""
    <h2>Welcome to SessionNotes!</h2>
    <p>Please click the link below to verify your email address:</p>
    <p><a href="{verify_url}">Verify Email Address</a></p>
    <p>If you didn't create an account, please ignore this email.</p>
    """
    
    text_body = f"""
    Welcome to SessionNotes!
    
    Please visit the following link to verify your email address:
    {verify_url}
    
    If you didn't create an account, please ignore this email.
    """
    
    msg = Message(
        subject=subject,
        recipients=[email],
        html=html_body,
        body=text_body
    )
    
    mail.send(msg)

def send_password_reset_email(email, token):
    """Send password reset email"""
    if not current_app.config.get('MAIL_SERVER'):
        raise Exception("Email not configured")
    
    subject = "Password Reset - SessionNotes"
    
    # Create reset URL
    reset_url = url_for('auth.reset_password', token=token, _external=True)
    
    html_body = f"""
    <h2>Password Reset Request</h2>
    <p>Click the link below to reset your password:</p>
    <p><a href="{reset_url}">Reset Password</a></p>
    <p>This link will expire in 1 hour.</p>
    <p>If you didn't request this, please ignore this email.</p>
    """
    
    text_body = f"""
    Password Reset Request
    
    Visit the following link to reset your password:
    {reset_url}
    
    This link will expire in 1 hour.
    If you didn't request this, please ignore this email.
    """
    
    msg = Message(
        subject=subject,
        recipients=[email],
        html=html_body,
        body=text_body
    )
    
    mail.send(msg)

# Authentication decorators
def require_login(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            session["next_url"] = request.url
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def require_subscription(f):
    """Decorator to require active subscription (trial or paid)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Bypass authentication if AUTH_DISABLED=1
        if os.environ.get('AUTH_DISABLED') == '1':
            return f(*args, **kwargs)
        
        # First require login
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        # Check if user has active subscription
        if not current_user.has_active_subscription():
            # Trial expired and no active subscription
            return redirect(url_for('upgrade'))
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin access - DEPRECATED, use super_admin_required instead"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Bypass authentication if AUTH_DISABLED=1
        if os.environ.get('AUTH_DISABLED') == '1':
            return f(*args, **kwargs)
        
        # First require login
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        # Check if user has active subscription
        if not current_user.has_active_subscription():
            return redirect(url_for('upgrade'))
            
        # Check if user is admin
        if not getattr(current_user, 'is_admin', False):
            from flask import abort
            abort(403)  # Forbidden access
        
        return f(*args, **kwargs)
    return decorated_function

def super_admin_required(f):
    """Decorator to require super admin access (global admin)"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Bypass authentication if AUTH_DISABLED=1
        if os.environ.get('AUTH_DISABLED') == '1':
            return f(*args, **kwargs)
        
        # First require login
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        
        # Check if user has active subscription
        if not current_user.has_active_subscription():
            return redirect(url_for('upgrade'))
            
        # Check if user is super admin
        if not getattr(current_user, 'is_admin', False):
            from flask import abort
            abort(403)  # Forbidden access
        
        return f(*args, **kwargs)
    return decorated_function

def org_admin_required(extract_org_id=None):
    """Decorator factory to require organization admin access"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Bypass authentication if AUTH_DISABLED=1
            if os.environ.get('AUTH_DISABLED') == '1':
                return f(*args, **kwargs)
            
            # First require login
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            
            # Check if user has active subscription
            if not current_user.has_active_subscription():
                return redirect(url_for('upgrade'))
            
            # Super admins have access to everything
            if getattr(current_user, 'is_admin', False):
                return f(*args, **kwargs)
            
            # Extract organization ID
            org_id = None
            if extract_org_id:
                org_id = extract_org_id(**kwargs)
            elif 'org_id' in kwargs:
                org_id = kwargs['org_id']
            elif 'organization_id' in kwargs:
                org_id = kwargs['organization_id']
            
            if not org_id:
                from flask import abort
                abort(400)  # Bad request - no org ID found
            
            # Check if user is admin of this organization
            if not current_user.is_org_admin(org_id):
                from flask import abort
                abort(403)  # Forbidden access
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def init_auth(app):
    """Initialize authentication with Flask app"""
    login_manager.init_app(app)
    bcrypt.init_app(app)
    
    # Configure email (optional)
    app.config.setdefault('MAIL_SERVER', None)
    app.config.setdefault('MAIL_PORT', 587)
    app.config.setdefault('MAIL_USE_TLS', True)
    app.config.setdefault('MAIL_USERNAME', None)
    app.config.setdefault('MAIL_PASSWORD', None)
    app.config.setdefault('MAIL_DEFAULT_SENDER', None)
    
    if app.config.get('MAIL_SERVER'):
        mail.init_app(app)
    
    # Register blueprint
    app.register_blueprint(auth_bp)
    
    return login_manager