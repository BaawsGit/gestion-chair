import os
import traceback

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

_migrations_done = False


def apply_pending_migrations():
    global _migrations_done
    if _migrations_done:
        return
    _migrations_done = True
    if os.environ.get("VERCEL") or os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL"):
        try:
            from django.core.management import call_command
            print("Auto-migration Vercel : vérification et application...")
            call_command("migrate", interactive=False)
            print("Auto-migration Vercel : terminée avec succès.")
        except Exception as e:
            print("Avertissement auto-migration Vercel:", e)


try:
    from django.core.wsgi import get_wsgi_application
    application = get_wsgi_application()
    apply_pending_migrations()
    app = application
except Exception as e:
    tb = traceback.format_exc()
    print("FATAL DJANGO STARTUP ERROR:\n", tb)
    def app(environ, start_response):
        start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8")])
        return [f"Diagnostic - Erreur au demarrage Django sur Vercel:\n\n{tb}".encode("utf-8")]
    application = app


