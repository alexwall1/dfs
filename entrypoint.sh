#!/bin/bash
set -e

echo "Running database migrations..."
flask db upgrade

echo "Running seed script..."
python seed.py

echo "Starting gunicorn..."
exec gunicorn -b 0.0.0.0:10001 --workers 2 "app:create_app()"
