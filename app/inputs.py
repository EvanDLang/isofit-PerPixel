import os
import logging
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-west-2")

# Optional ISOFIT input files live in two places, split by what they depend on:
#
#   <ISOFIT_INPUTS_URI>/sensors/<sensor_name>/<sensor_date>/   instrument calibration
#       rdn_factors.txt, channelized_noise.txt, noise.txt
#   <ISOFIT_INPUTS_URI>/campaigns/<campaign_name>/             scene / surface prior
#       surface.json (+ any files it references by relative path)
#
# Every file is optional — anything missing falls back to the defaults baked
# into the image (isofit env data) or to no file at all. Each whole prefix is
# downloaded so files a surface config references by relative path come along.
# See README.md "Per-calibration input files".
INPUTS_URI = os.environ.get("ISOFIT_INPUTS_URI", "s3://vswir-plants-config/isofit-inputs")

# file name in the sensor prefix → key used by run_inversion
SENSOR_FILES = {
    "rdn_factors.txt":        "rdn_factors",
    "channelized_noise.txt":  "channelized_noise",
    "noise.txt":              "noise",
}
# file name in the campaign prefix → key used by run_inversion
CAMPAIGN_FILES = {
    "surface.json": "surface",
}

s3 = boto3.client("s3", region_name=REGION)


def _download_prefix(bucket: str, prefix: str, dest: Path) -> list:
    """Download every object under prefix into dest, keeping relative paths. Returns the relative paths."""
    downloaded = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            local = dest / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(local))
            downloaded.append(rel)
    logger.info(f"inputs from s3://{bucket}/{prefix}: {downloaded or 'none — using defaults'}")
    return downloaded


def fetch_calibration_inputs(campaign_name: str, sensor_name: str, sensor_date: str, dest: Path) -> dict:
    """
    Download the sensor-calibration and campaign input prefixes into dest/sensor and dest/campaign.
    Returns { "surface", "channelized_noise", "rdn_factors", "noise" } → local path, or "" if absent.
    """
    bucket, _, root = INPUTS_URI.removeprefix("s3://").partition("/")
    root = root.strip("/")

    def prefix(*parts):
        return "/".join(p for p in (root, *parts) if p) + "/"

    out = {}
    for sub, path_parts, files in (
        ("sensor",   ("sensors", sensor_name, sensor_date), SENSOR_FILES),
        ("campaign", ("campaigns", campaign_name),          CAMPAIGN_FILES),
    ):
        local_dir = dest / sub
        downloaded = _download_prefix(bucket, prefix(*path_parts), local_dir)
        for name, key in files.items():
            out[key] = str(local_dir / name) if name in downloaded else ""
    return out
