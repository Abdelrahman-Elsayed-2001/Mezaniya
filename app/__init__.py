from flask import Flask, render_template, request

from . import auth, db, i18n
from .auth import current_user, login_required
from .config import Config

# Alpine.js evaluates its x-* expressions with Function(), hence 'unsafe-eval';
# :style bindings write style attributes, hence 'unsafe-inline' for styles only.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
)


def create_app(config: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config)
    db.init_app(app)
    auth.init_app(app)
    i18n.init_app(app)
    from .api import bp as api_bp
    app.register_blueprint(api_bp)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"     # personal financial data
        return response

    @app.get("/")
    @login_required
    def index():
        return render_template("app.html", username=current_user()["username"])

    @app.get("/settings")
    @login_required
    def settings_page():
        return render_template("settings.html", username=current_user()["username"])

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
