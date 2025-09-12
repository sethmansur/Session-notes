# models.py - Database models for speech therapy SaaS demo
# Using PostgreSQL and SQLAlchemy for multi-tenant architecture

from datetime import datetime, timedelta
import secrets
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from flask_bcrypt import generate_password_hash, check_password_hash
from sqlalchemy import UniqueConstraint

db = SQLAlchemy()

# Authentication models for email/password auth
class User(UserMixin, db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    profile_image_url = db.Column(db.String, nullable=True)
    
    # Email verification
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verification_token = db.Column(db.String(100), unique=True, nullable=True)
    
    # Password reset
    password_reset_token = db.Column(db.String(100), unique=True, nullable=True)
    password_reset_expires = db.Column(db.DateTime, nullable=True)
    
    # Trial and subscription tracking
    trial_start_date = db.Column(db.DateTime, default=datetime.now)
    subscription_status = db.Column(db.String(20), default='trial')  # 'trial', 'active', 'past_due', 'canceled'
    stripe_customer_id = db.Column(db.String)
    stripe_subscription_id = db.Column(db.String)
    plan_type = db.Column(db.String(20), default='freemium')  # 'freemium', 'individual', 'team'
    
    # Admin access control
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationship to memberships
    memberships = db.relationship('Membership', back_populates='user', cascade='all, delete-orphan')
    
    def is_trial_active(self):
        """Check if user's 7-day trial is still active"""
        if self.subscription_status != 'trial' or not self.trial_start_date:
            return False
        trial_end = self.trial_start_date + timedelta(days=7)
        return datetime.now() < trial_end
    
    def days_left_in_trial(self):
        """Calculate days remaining in trial"""
        if self.subscription_status != 'trial' or not self.trial_start_date:
            return 0
        trial_end = self.trial_start_date + timedelta(days=7)
        remaining = trial_end - datetime.now()
        return max(0, remaining.days)
    
    def has_active_subscription(self):
        """Check if user has active access (trial, freemium, or paid)"""
        return self.is_trial_active() or self.subscription_status == 'active' or self.plan_type == 'freemium'
    
    def is_freemium_user(self):
        """Check if user is on freemium plan"""
        return self.plan_type == 'freemium' and self.subscription_status != 'active'
    
    def get_student_limit(self):
        """Get maximum number of students allowed for user's plan"""
        if self.is_freemium_user():
            return 4
        else:
            return None  # No limit for paid plans
    
    def get_objectives_per_student_limit(self):
        """Get maximum number of objectives per student for user's plan"""  
        if self.is_freemium_user():
            return 2
        else:
            return None  # No limit for paid plans
    
    def can_add_student(self, organization_id):
        """Check if user can add another student"""
        if not self.is_freemium_user():
            return True
        
        # Import here to avoid circular import issues
        current_count = Student.query.filter_by(organization_id=organization_id).count()
        return current_count < self.get_student_limit()
    
    def can_add_objective(self, student_id):
        """Check if user can add another objective to a student"""
        if not self.is_freemium_user():
            return True
            
        # Import here to avoid circular import issues
        current_count = Objective.query.filter_by(student_id=student_id).count()
        return current_count < self.get_objectives_per_student_limit()
    
    # Password authentication methods
    def set_password(self, password):
        """Hash and set password"""
        self.password_hash = generate_password_hash(password).decode('utf-8')
    
    def check_password(self, password):
        """Check if provided password matches the hash"""
        return check_password_hash(self.password_hash, password)
    
    def generate_email_verification_token(self):
        """Generate a secure token for email verification"""
        self.email_verification_token = secrets.token_urlsafe(32)
        return self.email_verification_token
    
    def verify_email_token(self, token):
        """Verify email verification token"""
        if self.email_verification_token == token:
            self.email_verified = True
            self.email_verification_token = None
            return True
        return False
    
    def generate_password_reset_token(self):
        """Generate a secure token for password reset"""
        self.password_reset_token = secrets.token_urlsafe(32)
        self.password_reset_expires = datetime.now() + timedelta(hours=1)
        return self.password_reset_token
    
    def verify_password_reset_token(self, token):
        """Verify password reset token and check expiration"""
        if (self.password_reset_token == token and 
            self.password_reset_expires and 
            datetime.now() < self.password_reset_expires):
            return True
        return False
    
    def reset_password(self, password):
        """Reset password and clear reset token"""
        self.set_password(password)
        self.password_reset_token = None
        self.password_reset_expires = None
    
    @property
    def full_name(self):
        """Get user's full name"""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        else:
            return self.email


# Multi-tenant organization models
class Organization(db.Model):
    """Organization/clinic that uses the speech therapy app"""
    __tablename__ = 'organizations'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # e.g., "Sunshine Speech Therapy"
    subdomain = db.Column(db.String(50), unique=True)  # e.g., "sunshine"
    
    # White-label branding settings
    logo_url = db.Column(db.String(255))
    primary_color = db.Column(db.String(7), default='#007bff')  # CSS color
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    memberships = db.relationship('Membership', back_populates='organization', cascade='all, delete-orphan')
    students = db.relationship('Student', back_populates='organization', cascade='all, delete-orphan')
    sessions = db.relationship('Session', back_populates='organization', cascade='all, delete-orphan')

class Membership(db.Model):
    """Links users to organizations with roles"""
    __tablename__ = 'memberships'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String, db.ForeignKey('users.id'), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'owner', 'admin', 'clinician', 'viewer'
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    user = db.relationship('User', back_populates='memberships')
    organization = db.relationship('Organization', back_populates='memberships')
    
    __table_args__ = (
        UniqueConstraint('user_id', 'organization_id', name='uq_user_organization'),
    )

# Speech therapy data models (now organization-scoped)
class Student(db.Model):
    """Student model - now scoped to organization"""
    __tablename__ = 'students'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    organization = db.relationship('Organization', back_populates='students')
    objectives = db.relationship('Objective', back_populates='student', cascade='all, delete-orphan')
    events = db.relationship('Event', back_populates='student', cascade='all, delete-orphan')
    
    # Ensure unique names within organization only
    __table_args__ = (
        UniqueConstraint('organization_id', 'first_name', name='uq_org_student_name'),
    )

class Objective(db.Model):
    """Therapy objectives - scoped to student and organization"""
    __tablename__ = 'objectives'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    objective_text = db.Column(db.Text, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    student = db.relationship('Student', back_populates='objectives')
    objective_items = db.relationship('ObjectiveItem', back_populates='objective', cascade='all, delete-orphan')
    events = db.relationship('Event', back_populates='objective', cascade='all, delete-orphan')

class Session(db.Model):
    """Therapy sessions - scoped to organization"""
    __tablename__ = 'sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Relationships
    organization = db.relationship('Organization', back_populates='sessions')
    events = db.relationship('Event', back_populates='session', cascade='all, delete-orphan')
    
    # Ensure unique session dates within organization
    __table_args__ = (
        UniqueConstraint('organization_id', 'date', name='uq_org_session_date'),
    )

class Event(db.Model):
    """Session events/data collection - inherits organization scope from student"""
    __tablename__ = 'events'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('sessions.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    objective_id = db.Column(db.Integer, db.ForeignKey('objectives.id'), nullable=False)
    count = db.Column(db.Integer, default=0)
    activity = db.Column(db.String(100))
    prompt_level = db.Column(db.String(50))
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    # Relationships
    session = db.relationship('Session', back_populates='events')
    student = db.relationship('Student', back_populates='events')
    objective = db.relationship('Objective', back_populates='events')
    event_selections = db.relationship('EventSelection', back_populates='event', cascade='all, delete-orphan')
    
    __table_args__ = (
        UniqueConstraint('session_id', 'student_id', 'objective_id', name='uq_session_student_objective'),
    )

class ObjectiveItem(db.Model):
    """Individual items within objectives"""
    __tablename__ = 'objective_items'
    
    id = db.Column(db.Integer, primary_key=True)
    objective_id = db.Column(db.Integer, db.ForeignKey('objectives.id'), nullable=False)
    item_text = db.Column(db.Text, nullable=False)
    display_order = db.Column(db.Integer, default=0)
    
    # Relationships
    objective = db.relationship('Objective', back_populates='objective_items')
    event_selections = db.relationship('EventSelection', back_populates='objective_item', cascade='all, delete-orphan')

class EventSelection(db.Model):
    """Tracks selections of objective items in events"""
    __tablename__ = 'event_selections'
    
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('events.id'), nullable=False)
    objective_item_id = db.Column(db.Integer, db.ForeignKey('objective_items.id'), nullable=False)
    selected = db.Column(db.Boolean, default=False)
    
    # Relationships
    event = db.relationship('Event', back_populates='event_selections')
    objective_item = db.relationship('ObjectiveItem', back_populates='event_selections')
    
    __table_args__ = (
        UniqueConstraint('event_id', 'objective_item_id', name='uq_event_objective_item'),
    )