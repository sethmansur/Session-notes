# forms.py - WTF Forms for authentication
# Forms for registration, login, and password reset

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError
from models import User

class RegistrationForm(FlaskForm):
    """User registration form"""
    email = StringField('Email Address', validators=[
        DataRequired(message='Email is required.'),
        Email(message='Please enter a valid email address.'),
        Length(min=5, max=120, message='Email must be between 5 and 120 characters.')
    ])
    
    first_name = StringField('First Name', validators=[
        Length(max=80, message='First name must be less than 80 characters.')
    ])
    
    last_name = StringField('Last Name', validators=[
        Length(max=80, message='Last name must be less than 80 characters.')
    ])
    
    password = PasswordField('Password', validators=[
        DataRequired(message='Password is required.'),
        Length(min=8, max=128, message='Password must be between 8 and 128 characters.')
    ])
    
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(message='Please confirm your password.'),
        EqualTo('password', message='Passwords do not match.')
    ])
    
    submit = SubmitField('Create Account')
    
    def validate_email(self, email):
        """Check if email is already registered"""
        user = User.query.filter_by(email=email.data.lower().strip()).first()
        if user:
            raise ValidationError('An account with this email address already exists. Please use a different email or log in.')

class LoginForm(FlaskForm):
    """User login form"""
    email = StringField('Email Address', validators=[
        DataRequired(message='Email is required.'),
        Email(message='Please enter a valid email address.')
    ])
    
    password = PasswordField('Password', validators=[
        DataRequired(message='Password is required.')
    ])
    
    remember_me = BooleanField('Remember Me')
    
    submit = SubmitField('Sign In')

class ForgotPasswordForm(FlaskForm):
    """Forgot password form"""
    email = StringField('Email Address', validators=[
        DataRequired(message='Email is required.'),
        Email(message='Please enter a valid email address.')
    ])
    
    submit = SubmitField('Send Reset Instructions')

class ResetPasswordForm(FlaskForm):
    """Reset password form"""
    password = PasswordField('New Password', validators=[
        DataRequired(message='Password is required.'),
        Length(min=8, max=128, message='Password must be between 8 and 128 characters.')
    ])
    
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(message='Please confirm your password.'),
        EqualTo('password', message='Passwords do not match.')
    ])
    
    submit = SubmitField('Reset Password')

class ChangePasswordForm(FlaskForm):
    """Change password form for logged-in users"""
    current_password = PasswordField('Current Password', validators=[
        DataRequired(message='Current password is required.')
    ])
    
    new_password = PasswordField('New Password', validators=[
        DataRequired(message='New password is required.'),
        Length(min=8, max=128, message='Password must be between 8 and 128 characters.')
    ])
    
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(message='Please confirm your new password.'),
        EqualTo('new_password', message='Passwords do not match.')
    ])
    
    submit = SubmitField('Change Password')

class ProfileForm(FlaskForm):
    """User profile form"""
    first_name = StringField('First Name', validators=[
        Length(max=80, message='First name must be less than 80 characters.')
    ])
    
    last_name = StringField('Last Name', validators=[
        Length(max=80, message='Last name must be less than 80 characters.')
    ])
    
    email = StringField('Email Address', validators=[
        DataRequired(message='Email is required.'),
        Email(message='Please enter a valid email address.'),
        Length(min=5, max=120, message='Email must be between 5 and 120 characters.')
    ])
    
    organization_name = StringField('Organization Name', validators=[
        Length(max=100, message='Organization name must be less than 100 characters.')
    ])
    
    submit = SubmitField('Update Profile')
    
    def __init__(self, current_user_id, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user_id = current_user_id
    
    def validate_email(self, email):
        """Check if email is already taken by another user"""
        user = User.query.filter_by(email=email.data.lower().strip()).first()
        if user and user.id != self.current_user_id:
            raise ValidationError('This email address is already in use by another account.')