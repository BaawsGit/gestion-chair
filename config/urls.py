import io
import sys
import traceback

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, HttpResponseServerError
from django.urls import include, path


@user_passes_test(lambda u: u.is_superuser)
def run_migrations_view(request):
    from django.core.management import call_command
    from poulailler_core.finance import sync_all_feed_expenses

    buf = io.StringIO()
    try:
        call_command("migrate", interactive=False, stdout=buf)
        sync_all_feed_expenses()
        output = buf.getvalue()
        return HttpResponse(
            f"<html><body style='font-family:monospace;background:#111;color:#4ade80;padding:2rem;'>"
            f"<h2>✅ Migrations et synchronisation réussies</h2><pre>{output}</pre>"
            f"<a href='/' style='color:#fff;background:#16a34a;padding:8px 16px;text-decoration:none;border-radius:4px;'>Retour au Tableau de bord</a></body></html>"
        )
    except Exception as e:
        return HttpResponse(
            f"<html><body style='font-family:monospace;background:#111;color:#f87171;padding:2rem;'>"
            f"<h2>❌ Erreur de migration</h2><pre>{traceback.format_exc()}</pre></body></html>",
            status=500,
        )


def custom_500_view(request):
    exc_type, exc_value, exc_tb = sys.exc_info()
    formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)) if exc_value else "Aucun traceback disponible."
    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Erreur 500 · Diagnostic</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; }}
    .card {{ max-width: 900px; margin: 2rem auto; background: #1e293b; border-radius: 12px; padding: 2rem; border: 1px solid #334155; }}
    h1 {{ color: #ef4444; font-size: 1.5rem; margin-top: 0; }}
    pre {{ background: #0b0f19; color: #fca5a5; padding: 1.25rem; border-radius: 8px; overflow-x: auto; font-size: 0.85rem; line-height: 1.5; }}
    .btn {{ display: inline-block; background: #22c55e; color: white; padding: 0.6rem 1.2rem; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 1rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>⚠️ Erreur interne du serveur (500)</h1>
    <p>Une exception s'est produite lors de l'exécution de la requête :</p>
    <pre>{formatted}</pre>
    <a href="/" class="btn">Réessayer le tableau de bord</a>
    <a href="/admin/run-migrations/" class="btn" style="background:#3b82f6;margin-left:10px;">Lancer les migrations</a>
  </div>
</body>
</html>"""
    return HttpResponseServerError(html)


handler500 = custom_500_view

urlpatterns = [
    path("admin/run-migrations/", run_migrations_view, name="run-migrations"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("poulailler_core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

