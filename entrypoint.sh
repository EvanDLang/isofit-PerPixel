#!/usr/bin/env bash
set -euo pipefail

echo "Starting container..."

# If the job passes a command (Batch job definition / container override),
# run it — e.g. a test script. Otherwise run the worker as before.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Debug (optional)
echo "JOB_ID=${JOB_ID:-}"
echo "PIXEL_IDS=${PIXEL_IDS:-}"

# Sync your code/data
aws s3 sync s3://vswir-plants-config/isofit-app/ /root/app/ --region us-west-2

# Run your app
exec python3 /root/app/entrypoint.py
