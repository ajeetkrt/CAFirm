#!/bin/bash
# CAFirm container entrypoint: migrate -> seed -> serve.
# Runs on every boot so a fresh database is fully ready (schema, usp_*
# procedures, roles/accounts/categories) before traffic arrives.
set -e

echo ">> Applying schema migrations and installing usp_* procedures..."
python -m flask --app run.py db upgrade

echo ">> Seeding baseline data (roles, accounts, categories)..."
python -m flask --app run.py seed ${SEED_RESET_PASSWORDS:+--force}

echo ">> Starting gunicorn on port ${PORT:-5000}..."
exec gunicorn -w 2 -k gthread --threads 4 --timeout 120 \
     -b "0.0.0.0:${PORT:-5000}" run:app
