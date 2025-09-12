import jwt
import os
import uuid
import requests
from functools import wraps
from urllib.parse import urlencode
from cryptography.hazmat.primitives import serialization

from flask import g, session, redirect, request, render_template, url_for
from flask_dance.consumer import (
    OAuth2ConsumerBlueprint,
    oauth_authorized,
    oauth_error,
)
from flask_dance.consumer.storage import BaseStorage
from flask_login import LoginManager, login_user, logout_user, current_user
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError
from sqlalchemy.exc import NoResultFound
from werkzeug.local import LocalProxy

from models import OAuth, User, db

login_manager = LoginManager()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

class UserSessionStorage(BaseStorage):

    def get(self, blueprint):
        try:
            oauth_record = db.session.query(OAuth).filter_by(
                user_id=current_user.get_id(),
                browser_session_key=g.browser_session_key,
                provider=blueprint.name,
            ).one()
            token = oauth_record.token
        except NoResultFound:
            token = None
        return token

    def set(self, blueprint, token):
        db.session.query(OAuth).filter_by(
            user_id=current_user.get_id(),
            browser_session_key=g.browser_session_key,
            provider=blueprint.name,
        ).delete()
        new_model = OAuth()
        new_model.user_id = current_user.get_id()
        new_model.browser_session_key = g.browser_session_key
        new_model.provider = blueprint.name
        new_model.token = token
        db.session.add(new_model)
        db.session.commit()

    def delete(self, blueprint):
        db.session.query(OAuth).filter_by(
            user_id=current_user.get_id(),
            browser_session_key=g.browser_session_key,
            provider=blueprint.name).delete()
        db.session.commit()

def make_replit_blueprint():
    try:
        repl_id = os.environ['REPL_ID']
    except KeyError:
        raise SystemExit("the REPL_ID environment variable must be set")

    issuer_url = os.environ.get('ISSUER_URL', "https://replit.com/oidc")

    replit_bp = OAuth2ConsumerBlueprint(
        "replit_auth",
        __name__,
        client_id=repl_id,
        client_secret=None,
        base_url=issuer_url,
        authorization_url_params={
            "prompt": "login consent",
        },
        token_url=issuer_url + "/token",
        token_url_params={
            "auth": (),
            "include_client_id": True,
        },
        auto_refresh_url=issuer_url + "/token",
        auto_refresh_kwargs={
            "client_id": repl_id,
        },
        authorization_url=issuer_url + "/auth",
        use_pkce=True,
        code_challenge_method="S256",
        scope=["openid", "profile", "email", "offline_access"],
        storage=UserSessionStorage(),
    )

    @replit_bp.before_app_request
    def set_applocal_session():
        if '_browser_session_key' not in session:
            session['_browser_session_key'] = uuid.uuid4().hex
        session.modified = True
        g.browser_session_key = session['_browser_session_key']
        g.flask_dance_replit = replit_bp.session

    @replit_bp.route("/logout")
    def logout():
        del replit_bp.token
        logout_user()

        end_session_endpoint = issuer_url + "/session/end"
        encoded_params = urlencode({
            "client_id": repl_id,
            "post_logout_redirect_uri": request.url_root,
        })
        logout_url = f"{end_session_endpoint}?{encoded_params}"

        return redirect(logout_url)

    @replit_bp.route("/error")
    def error():
        return render_template("403.html"), 403

    return replit_bp

def get_jwks_keys():
    """Fetch and cache JWKS keys from Replit's OIDC endpoint."""
    jwks_url = "https://replit.com/oidc/jwks"
    try:
        response = requests.get(jwks_url, timeout=10)
        response.raise_for_status()
        jwks = response.json()
        return jwks['keys']
    except Exception as e:
        raise ValueError(f"Failed to fetch JWKS keys: {str(e)}")

def verify_jwt_token(token, audience=None):
    """Verify JWT token signature against Replit's JWKS."""
    try:
        # Get JWKS keys
        keys = get_jwks_keys()
        
        # Get the key ID from token header
        header = jwt.get_unverified_header(token)
        kid = header.get('kid')
        
        if not kid:
            raise ValueError("Token missing key ID")
        
        # Find the matching key
        jwk_key = None
        for key in keys:
            if key.get('kid') == kid:
                jwk_key = key
                break
        
        if not jwk_key:
            raise ValueError(f"Key ID {kid} not found in JWKS")
        
        # Convert JWK to PEM format for verification
        from jwt.algorithms import RSAAlgorithm
        public_key = RSAAlgorithm.from_jwk(jwk_key)
        
        # Verify the token with proper signature validation
        claims = jwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            audience=audience or os.environ.get('REPL_ID'),
            issuer='https://replit.com/oidc'
        )
        
        return claims
        
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidAudienceError:
        raise ValueError("Invalid token audience")
    except jwt.InvalidIssuerError:
        raise ValueError("Invalid token issuer")
    except jwt.InvalidSignatureError:
        raise ValueError("Invalid token signature")
    except Exception as e:
        raise ValueError(f"Token validation failed: {str(e)}")

def save_user(user_claims):
    user = User()
    user.id = user_claims['sub']
    user.email = user_claims.get('email')
    user.first_name = user_claims.get('first_name')
    user.last_name = user_claims.get('last_name')
    user.profile_image_url = user_claims.get('profile_image_url')
    merged_user = db.session.merge(user)
    db.session.commit()
    return merged_user

@oauth_authorized.connect
def logged_in(blueprint, token):
    try:
        # Properly verify JWT signature against Replit's JWKS
        user_claims = verify_jwt_token(token['id_token'])
        user = save_user(user_claims)
        login_user(user)
        blueprint.token = token
        next_url = session.pop("next_url", None)
        if next_url is not None:
            return redirect(next_url)
    except ValueError as e:
        # Log the error and redirect to error page
        print(f"JWT verification failed: {str(e)}")
        return redirect(url_for('replit_auth.error'))

@oauth_error.connect
def handle_error(blueprint, error, error_description=None, error_uri=None):
    return redirect(url_for('replit_auth.error'))

def require_login(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            session["next_url"] = get_next_navigation_url(request)
            return redirect(url_for('replit_auth.login'))

        expires_in = replit.token.get('expires_in', 0)
        if expires_in < 0:
            issuer_url = os.environ.get('ISSUER_URL', "https://replit.com/oidc")
            refresh_token_url = issuer_url + "/token"
            try:
                token = replit.refresh_token(token_url=refresh_token_url,
                                             client_id=os.environ['REPL_ID'])
            except InvalidGrantError:
                # If the refresh token is invalid, the users needs to re-login.
                session["next_url"] = get_next_navigation_url(request)
                return redirect(url_for('replit_auth.login'))
            replit.token_updater(token)

        return f(*args, **kwargs)

    return decorated_function

def get_next_navigation_url(request):
    is_navigation_url = request.headers.get(
        'Sec-Fetch-Mode') == 'navigate' and request.headers.get(
            'Sec-Fetch-Dest') == 'document'
    if is_navigation_url:
        return request.url
    return request.referrer or request.url

replit = LocalProxy(lambda: g.flask_dance_replit)

# Legacy compatibility functions for existing code
require_subscription = require_login  # Alias for backward compatibility