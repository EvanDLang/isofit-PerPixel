#!/usr/bin/env bash
set -euo pipefail

echo "Starting container..."

# Debug (optional)
echo "JOB_ID=$JOB_ID"
echo "PIXEL_IDS=$PIXEL_IDS"

# Sync your code/data
aws s3 sync s3://vswir-plants-config/isofit-app/ /root/app/ --region us-west-2

# Run your app
exec python3 /root/app/entrypoint.py