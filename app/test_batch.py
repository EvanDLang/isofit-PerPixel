"""
Test an inversion on real pixels without the database or DynamoDB.

Reads a raw ingestion bundle from S3 (spectra.csv, wavelengths.csv,
granule_metadata.csv), takes the first N_PIXELS rows of one granule, and runs
the same tasks.run_inversion the worker uses. Writes the reflectance, timing
and peak memory to RESULTS_URI/<LABEL>.json. Nothing is written to postgres.

Run it as a Batch job with a command override, e.g.
  aws s3 sync s3://vswir-plants-config/isofit-build/test-app/ /root/testapp/ &&
  cd /root/testapp && python3 test_batch.py

Env:
  BUNDLE       s3://…/ingestion/<batch>/raw/   (required)
  LABEL        name for the result file          (required)
  N_PIXELS     default 20
  GRANULE      granule_id to use; default the first one in spectra.csv
  RESULTS_URI  default s3://vswir-plants-config/isofit-build/test-results
  NCPU, SRTMNET_FILE — as for the worker
"""
import os
import json
import time
import logging
import threading
from datetime import datetime, time as dtime
from pathlib import Path

import boto3
import numpy as np
import pandas as pd

from tasks import run_inversion, SENSOR_KEY_MAP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_batch")

s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))

GEOMETRY_COLS = [
    "lon", "lat", "elevation", "path_length", "to_sensor_azimuth", "to_sensor_zenith",
    "to_sun_azimuth", "to_sun_zenith", "solar_phase", "slope", "aspect", "cosine_i", "utc_time",
]


def split_s3(uri: str):
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def download(uri: str, dest: Path) -> Path:
    bucket, key = split_s3(uri)
    s3.download_file(bucket, key, str(dest))
    return dest


def parse_time(val) -> dtime:
    # bundles have both "20:48:03" and "183823"
    s = str(val).strip()
    if ":" in s:
        return datetime.strptime(s, "%H:%M:%S").time()
    return datetime.strptime(s.split(".")[0].zfill(6), "%H%M%S").time()


class MemoryWatcher:
    """Peak memory of the container's cgroup (includes the Ray workers)."""

    def __init__(self):
        root = Path("/sys/fs/cgroup")
        self.current = next((p for p in [root / "memory.current", root / "memory/memory.usage_in_bytes"] if p.exists()), None)
        self.peak_file = next((p for p in [root / "memory.peak", root / "memory/memory.max_usage_in_bytes"] if p.exists()), None)
        limit = next((p for p in [root / "memory.max", root / "memory/memory.limit_in_bytes"] if p.exists()), None)
        self.limit = limit.read_text().strip() if limit else None
        self.sampled_peak = 0
        self._stop = threading.Event()

    def _run(self):
        while not self._stop.wait(2):
            if self.current:
                self.sampled_peak = max(self.sampled_peak, int(self.current.read_text()))

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()

    def peak(self) -> int:
        if self.peak_file:
            return int(self.peak_file.read_text())
        return self.sampled_peak


def load_pixels(bundle: str, n_pixels: int, granule: str, work: Path):
    granules = pd.read_csv(download(bundle + "granule_metadata.csv", work / "granule_metadata.csv"), dtype=str)
    wavelengths = pd.read_csv(download(bundle + "wavelengths.csv", work / "wavelengths.csv"))
    spectra_path = download(bundle + "spectra.csv", work / "spectra.csv")

    # first n_pixels rows of the granule, read in chunks — spectra.csv is large
    rows = []
    for chunk in pd.read_csv(spectra_path, chunksize=5000):
        granule = granule or chunk["granule_id"].iloc[0]
        rows.append(chunk[chunk["granule_id"] == granule])
        if sum(len(r) for r in rows) >= n_pixels:
            break
    df = pd.concat(rows).head(n_pixels)
    if df.empty:
        raise ValueError(f"no rows for granule {granule}")

    campaign, sensor = df["campaign_name"].iloc[0], df["sensor_name"].iloc[0]
    g = granules[granules["granule_id"] == granule].iloc[0]
    acq_date = pd.to_datetime(g["acquisition_date"]).date()
    acq_time = parse_time(g["acquisition_start_time"])

    wl = wavelengths[(wavelengths["campaign_name"] == campaign) & (wavelengths["sensor_name"] == sensor)]
    if "sensor_date" in wl.columns and "sensor_date" in g.index:
        wl = wl[wl["sensor_date"] == g["sensor_date"]]
    wl = wl.sort_values("band")

    band_cols = sorted((c for c in df.columns if c.isdigit()), key=int)
    if len(band_cols) != len(wl):
        raise ValueError(f"{len(band_cols)} radiance bands but {len(wl)} wavelengths")

    pixels = []
    for i, (_, r) in enumerate(df.iterrows()):
        p = {c: float(r[c]) for c in GEOMETRY_COLS}
        p.update(
            pixel_id=i,
            granule_id=granule,
            radiance=[float(r[c]) for c in band_cols],
            acquisition_date=acq_date,
            acquisition_start_time=acq_time,
            plot_name=r.get("plot_name"),
            glt_row=int(r["glt_row"]),
            glt_column=int(r["glt_column"]),
        )
        pixels.append(p)

    return pixels, campaign, sensor, wl["wavelength"].tolist(), wl["fwhm"].tolist()


def main():
    bundle = os.environ["BUNDLE"].rstrip("/") + "/"
    label = os.environ["LABEL"]
    n_pixels = int(os.environ.get("N_PIXELS", "20"))
    results_uri = os.environ.get("RESULTS_URI", "s3://vswir-plants-config/isofit-build/test-results").rstrip("/")
    n_cores = int(os.environ.get("NCPU", str(os.cpu_count())))
    work = Path("/tmp/test_batch")
    work.mkdir(parents=True, exist_ok=True)

    pixels, campaign, sensor, wl, fwhm = load_pixels(bundle, n_pixels, os.environ.get("GRANULE"), work)
    isofit_sensor = SENSOR_KEY_MAP[sensor]
    logger.info(f"{len(pixels)} pixels from {pixels[0]['granule_id']} [{campaign}|{sensor}], {len(wl)} bands, n_cores={n_cores}")

    start = time.time()
    with MemoryWatcher() as mem:
        result = run_inversion(
            pixels, {isofit_sensor: wl}, {isofit_sensor: fwhm}, sensor,
            "/tmp/isofit", label,
            {"surface": "", "channelized_noise": "", "rdn_factors": ""},
            n_cores=n_cores,
        )
    elapsed = round(time.time() - start, 1)

    out = {
        "label": label,
        "status": result["status"],
        "elapsed_seconds": elapsed,
        "peak_memory_gib": round(mem.peak() / 2**30, 2),
        "memory_limit": mem.limit,
        "n_cores": n_cores,
        "cpu_count": os.cpu_count(),
        "srtmnet_file": os.environ.get("SRTMNET_FILE"),
        "bundle": bundle,
        "granule_id": pixels[0]["granule_id"],
        "pixels": [{"plot_name": p["plot_name"], "glt_row": p["glt_row"], "glt_column": p["glt_column"]} for p in pixels],
    }
    if result["status"] == "success":
        inner = result["result"]
        out["statevec"] = inner["statevec"][0]
        out["solution"] = inner["solution"]
        out["rfl"] = [
            [v for n, v in zip(inner["statevec"][i], inner["solution"][i]) if n.startswith("RFL_")]
            for i in range(len(pixels))
        ]
    else:
        out["error"] = result.get("message")
        out["traceback"] = result.get("traceback")

    bucket, prefix = split_s3(results_uri)
    s3.put_object(Bucket=bucket, Key=f"{prefix}/{label}.json", Body=json.dumps(out, default=str).encode())
    logger.info(f"{label}: {result['status']} in {elapsed}s, peak memory {out['peak_memory_gib']} GiB "
                f"→ {results_uri}/{label}.json")

    if result["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
