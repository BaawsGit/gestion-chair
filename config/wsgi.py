import os
import traceback

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    from django.core.wsgi import get_wsgi_application
    application = get_wsgi_application()
    app = application
except Exception as e:
    tb = traceback.format_exc()
    print("FATAL DJANGO STARTUP ERROR:\n", tb)
    def app(environ, start_response):
        start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8")])
        return [f"Diagnostic - Erreur au demarrage Django sur Vercel:\n\n{tb}".encode("utf-8")]
    application = app

