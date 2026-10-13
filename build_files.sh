#!/bin/bash
pip install -r requirements.txt --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt
python3 manage.py collectstatic --noinput --clear

DB_CONN="${DATABASE_URL:-$POSTGRES_URL}"
if [ -n "$DB_CONN" ]; then
  echo "==> Base de donnees detectee : execution des migrations et creation des comptes..."
  python3 manage.py migrate --noinput
  python3 -c "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); django.setup(); from poulailler_core.auth_setup import ensure_default_accounts; ensure_default_accounts(); print('==> Comptes admin et user synchronises avec succes.')"
else
  echo "==> Aucune URL de base detectee lors du build (passant la migration)."
fi
