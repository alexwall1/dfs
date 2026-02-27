from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_smorest import Api as SmorestApi

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
smorest_api = SmorestApi()
login_manager.login_view = "auth.login"
login_manager.login_message = "Du måste logga in för att komma åt denna sida."


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    proxy_count = app.config.get("PROXY_COUNT", 0)
    if proxy_count > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    smorest_api.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.routes.arenden import arenden_bp
    from app.routes.handlingar import handlingar_bp
    from app.routes.sok import sok_bp
    from app.routes.admin import admin_bp
    from app.routes.arkiv import arkiv_bp
    from app.routes.api import blp as api_blp

    app.register_blueprint(auth_bp)
    app.register_blueprint(arenden_bp)
    app.register_blueprint(handlingar_bp)
    app.register_blueprint(sok_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(arkiv_bp)

    csrf.exempt(api_blp)
    smorest_api.register_blueprint(api_blp)

    @app.after_request
    def lägg_till_säkerhetsheaders(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'"
        )
        return response

    with app.app_context():
        _registrera_fragetimeout(app)

    return app


def _registrera_fragetimeout(app):
    """Sätter statement_timeout på PostgreSQL-anslutningar för att skydda mot DoS."""
    from sqlalchemy import event

    timeout_ms = int(app.config.get("DB_QUERY_TIMEOUT_MS", 5000))
    if timeout_ms <= 0:
        return
    if db.engine.dialect.name != "postgresql":
        return

    @event.listens_for(db.engine, "connect")
    def _set_timeout(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET statement_timeout = {timeout_ms}")
        cursor.close()
