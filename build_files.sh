#!/bin/bash
pip install -r requirements.txt --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt
python3 manage.py collectstatic --noinput --clear

if [ -n "$DATABASE_URL" ]; then
  echo "==> DATABASE_URL detectee : execution des migrations..."
  python3 manage.py migrate --noinput
else
  echo "==> Aucune DATABASE_URL detectee lors du build (passant la migration)."
fi
