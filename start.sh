#!/bin/sh
# Startup script for HomeHub
# Runs both the web app and the Radicale sync service

# Start the sync service in the background
echo "Starting Radicale sync service..."
python3 /app/sync/radicale_sync.py &
SYNC_PID=$!

# Start the web app in the foreground
echo "Starting HomeHub web application..."
exec gunicorn wsgi:app -w 1 -k sync -b 0.0.0.0:5000 --timeout 120 --access-logfile - --error-logfile -
