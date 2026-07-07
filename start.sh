#!/bin/bash
# start.sh
# Render start script — downloads latest database from R2 then launches Dash app.

set -e

echo "=== IGRA Radiosonde Monitor ==="
echo "Downloading latest database from R2..."

mkdir -p data

AWS_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID \
AWS_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY \
AWS_DEFAULT_REGION=auto \
aws s3 cp s3://igra-database/igra.duckdb data/igra.duckdb \
    --endpoint-url $R2_ENDPOINT_URL

echo "Database downloaded successfully."
echo "Starting Dash app..."

gunicorn app:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120
