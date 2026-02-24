#!/bin/bash
set -e

echo "Running seed script..."
python seed.py

echo "Starting gunicorn..."
exec gunicorn -b 0.0.0.0:5000 --workers 2 "app:create_app()"
