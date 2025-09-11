# auth_demo.py - Demonstration Flask app with authentication and multi-tenancy
# This shows how the speech therapy app would work as a SaaS product

import os
import logging
from flask import Flask, render_template_string, session, redirect, url_for, request, jsonify, g
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.orm import DeclarativeBase
from models import db, User, Organization, Membership, Student, Objective, Session

# Configure logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "demo-secret-key")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    'pool_pre_ping': True,
    "pool_recycle": 300,
}

# Initialize database
db.init_app(app)

# Import authentication after app initialization
from replit_auth import make_replit_blueprint, require_login, login_manager
from flask_login import current_user

# Initialize login manager
login_manager.init_app(app)

# Register authentication blueprint
app.register_blueprint(make_replit_blueprint(), url_prefix="/auth")

@app.before_request
def make_session_permanent():
    session.permanent = True

@app.before_request
def load_current_organization():
    """Load current organization for authenticated users"""
    g.current_organization = None
    if current_user.is_authenticated:
        # For demo, we'll use the first organization the user belongs to
        membership = db.session.query(Membership).filter_by(user_id=current_user.id).first()
        if membership:
            g.current_organization = membership.organization

# Landing page
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>SpeechTrack Pro - Speech Therapy Data Management</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }
            .header { text-align: center; margin-bottom: 40px; }
            .header h1 { color: #007bff; margin-bottom: 10px; }
            .features { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 30px 0; }
            .feature { padding: 20px; background: #f8f9fa; border-radius: 8px; }
            .feature h3 { color: #333; margin-top: 0; }
            .cta { text-align: center; margin-top: 40px; }
            .btn-primary { background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-weight: bold; }
            .btn-primary:hover { background: #0056b3; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🗣️ SpeechTrack Pro</h1>
                <p>Professional Speech Therapy Data Collection & Management</p>
                <p><em>Demo: Multi-Tenant SaaS Version</em></p>
            </div>
            
            <div class="features">
                <div class="feature">
                    <h3>👥 Team Collaboration</h3>
                    <p>Multiple therapists can work together, share client data securely, and collaborate on treatment plans.</p>
                </div>
                <div class="feature">
                    <h3>🔒 Secure & Compliant</h3>
                    <p>Each organization's data is completely isolated. HIPAA-ready security and access controls.</p>
                </div>
                <div class="feature">
                    <h3>📱 iPad Optimized</h3>
                    <p>Touch-friendly interface designed specifically for tablet use during therapy sessions.</p>
                </div>
                <div class="feature">
                    <h3>🎨 White Label Ready</h3>
                    <p>Customize branding, colors, and subdomain to match your clinic's professional appearance.</p>
                </div>
            </div>
            
            <div class="cta">
                <a href="{{ url_for('replit_auth.login') }}" class="btn-primary">
                    🚀 Try the Demo - Sign In
                </a>
                <p style="margin-top: 15px; color: #666; font-size: 14px;">
                    Sign in with Google, GitHub, or email to see the multi-tenant features
                </p>
            </div>
        </div>
    </body>
    </html>
    ''')

# Dashboard for authenticated users
@app.route('/dashboard')
@require_login
def dashboard():
    if not g.current_organization:
        return redirect(url_for('setup_organization'))
    
    # Get organization statistics
    student_count = db.session.query(Student).filter_by(organization_id=g.current_organization.id).count()
    session_count = db.session.query(Session).filter_by(organization_id=g.current_organization.id).count()
    
    # Get team members
    team_members = db.session.query(Membership).filter_by(
        organization_id=g.current_organization.id
    ).all()
    
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ org.name }} - SpeechTrack Pro Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .header { background: {{ org.primary_color or '#007bff' }}; color: white; padding: 20px; margin: -20px -20px 20px -20px; }
            .header h1 { margin: 0; }
            .user-info { float: right; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
            .stat-card { background: white; padding: 20px; border-radius: 8px; text-align: center; }
            .stat-number { font-size: 2em; font-weight: bold; color: {{ org.primary_color or '#007bff' }}; }
            .team-section { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
            .team-member { padding: 10px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; }
            .role-badge { background: #e9ecef; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
            .actions { margin-top: 30px; }
            .btn { padding: 10px 20px; margin: 5px; text-decoration: none; border-radius: 5px; display: inline-block; }
            .btn-primary { background: {{ org.primary_color or '#007bff' }}; color: white; }
            .btn-secondary { background: #6c757d; color: white; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{{ org.name }}</h1>
            <div class="user-info">
                Welcome, {{ current_user.first_name or current_user.email }}!
                <a href="{{ url_for('replit_auth.logout') }}" style="color: white; margin-left: 15px;">Logout</a>
            </div>
            <div style="clear: both;"></div>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{{ student_count }}</div>
                <div>Students</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ session_count }}</div>
                <div>Sessions</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{{ team_members|length }}</div>
                <div>Team Members</div>
            </div>
        </div>
        
        <div class="team-section">
            <h3>Team Members</h3>
            {% for member in team_members %}
            <div class="team-member">
                <div>
                    <strong>{{ member.user.first_name or member.user.email }}</strong>
                </div>
                <div>
                    <span class="role-badge">{{ member.role.title() }}</span>
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div class="actions">
            <a href="{{ url_for('collect_demo') }}" class="btn btn-primary">📊 Start Data Collection</a>
            <a href="{{ url_for('students_demo') }}" class="btn btn-secondary">👥 Manage Students</a>
            <a href="{{ url_for('organization_settings') }}" class="btn btn-secondary">⚙️ Organization Settings</a>
        </div>
        
        <div style="margin-top: 40px; padding: 20px; background: #fff3cd; border-radius: 8px;">
            <h4>🎉 Demo Features Shown:</h4>
            <ul>
                <li>✅ User authentication with multiple login options</li>
                <li>✅ Organization-specific branding and data isolation</li>
                <li>✅ Team member management with roles</li>
                <li>✅ Secure multi-tenant architecture</li>
                <li>✅ White-label customization (colors, organization name)</li>
            </ul>
            <p><strong>Next:</strong> Full billing integration, advanced role permissions, and subdomain routing!</p>
        </div>
    </body>
    </html>
    ''', org=g.current_organization, current_user=current_user, 
         student_count=student_count, session_count=session_count, team_members=team_members)

@app.route('/setup-organization')
@require_login
def setup_organization():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Set Up Your Organization - SpeechTrack Pro</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background: white; padding: 40px; border-radius: 10px; }
            .form-group { margin-bottom: 20px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; }
            input[type="text"], input[type="color"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; }
            .btn { padding: 12px 24px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
            .btn:hover { background: #0056b3; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏢 Set Up Your Organization</h1>
            <p>Create your speech therapy clinic's workspace.</p>
            
            <form method="POST" action="{{ url_for('create_organization') }}">
                <div class="form-group">
                    <label for="name">Organization Name:</label>
                    <input type="text" id="name" name="name" placeholder="e.g., Sunshine Speech Therapy" required>
                </div>
                
                <div class="form-group">
                    <label for="subdomain">Subdomain:</label>
                    <input type="text" id="subdomain" name="subdomain" placeholder="e.g., sunshine (for sunshine.speechtrack.com)" required>
                </div>
                
                <div class="form-group">
                    <label for="primary_color">Primary Color:</label>
                    <input type="color" id="primary_color" name="primary_color" value="#007bff">
                </div>
                
                <button type="submit" class="btn">Create Organization</button>
            </form>
        </div>
    </body>
    </html>
    ''')

@app.route('/create-organization', methods=['POST'])
@require_login
def create_organization():
    name = request.form.get('name')
    subdomain = request.form.get('subdomain')
    primary_color = request.form.get('primary_color', '#007bff')
    
    # Create organization
    org = Organization(
        name=name,
        subdomain=subdomain,
        primary_color=primary_color
    )
    db.session.add(org)
    db.session.flush()  # Get the ID
    
    # Add user as owner
    membership = Membership(
        user_id=current_user.id,
        organization_id=org.id,
        role='owner'
    )
    db.session.add(membership)
    db.session.commit()
    
    return redirect(url_for('dashboard'))

# Demo routes that show organization scoping
@app.route('/collect-demo')
@require_login
def collect_demo():
    if not g.current_organization:
        return redirect(url_for('setup_organization'))
    
    # Show only students from current organization
    students = db.session.query(Student).filter_by(
        organization_id=g.current_organization.id
    ).all()
    
    return render_template_string('''
    <h2>Data Collection - {{ org.name }}</h2>
    <p>This would show data collection interface with only YOUR organization's students:</p>
    <ul>
    {% for student in students %}
        <li>{{ student.first_name }}</li>
    {% else %}
        <li>No students yet - <a href="{{ url_for('students_demo') }}">Add some students</a></li>
    {% endfor %}
    </ul>
    <p><a href="{{ url_for('dashboard') }}">Back to Dashboard</a></p>
    ''', org=g.current_organization, students=students)

@app.route('/students-demo')
@require_login  
def students_demo():
    if not g.current_organization:
        return redirect(url_for('setup_organization'))
    
    return render_template_string('''
    <h2>Student Management - {{ org.name }}</h2>
    <p>This would show student management interface scoped to your organization.</p>
    <p>Each organization sees only their own students - complete data isolation!</p>
    <p><a href="{{ url_for('dashboard') }}">Back to Dashboard</a></p>
    ''', org=g.current_organization)

@app.route('/organization-settings')
@require_login
def organization_settings():
    if not g.current_organization:
        return redirect(url_for('setup_organization'))
    
    return render_template_string('''
    <h2>Organization Settings - {{ org.name }}</h2>
    <p>Here organizations could customize:</p>
    <ul>
        <li>Branding (logo, colors)</li>
        <li>Team member invitations</li>
        <li>Billing and subscription</li>
        <li>Custom domain setup</li>
    </ul>
    <p><a href="{{ url_for('dashboard') }}">Back to Dashboard</a></p>
    ''', org=g.current_organization)

# Create tables
with app.app_context():
    db.create_all()
    logging.info("Database tables created")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)