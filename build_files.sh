#!/bin/bash
pip install -r requirements.txt --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt
python3 manage.py collectstatic --noinput --clear

DB_CONN="${DATABASE_URL:-$POSTGRES_URL}"
if [ -n "$DB_CONN" ]; then
  echo "==> Base de donnees detectee : execution des migrations..."
  python3 manage.py migrate --noinput
else
  echo "==> Aucune URL de base detectee lors du build (passant la migration)."
fi
