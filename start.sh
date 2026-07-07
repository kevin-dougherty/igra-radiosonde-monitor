#!/bin/bash
# start.sh
# Render start script — downloads latest database from R2 then launches Dash app.
# Set as the start command in Render: bash start.sh

set -e  # exit on any error

echo "=== IGRA Radiosonde Monitor ==="
echo "Downloading latest database from R2..."

mkdir -p data

aws s3 cp s3://igra-database/igra.duckdb data/igra.duckdb \
    --endpoint-url $R2_ENDPOINT_URL

echo "Database downloaded successfully."
echo "Starting Dash app..."

gunicorn app:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120

