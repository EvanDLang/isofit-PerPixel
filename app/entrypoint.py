import os
import sys
import logging
from pathlib import Path

from job_status import update_job_status, record_attempt_event
from db import fetch_pixels, fetch_sensor_metadata, fetch_completed_pixel_ids, save_results
from tasks import run_inversion, SENSOR_KEY_MAP
from inputs import fetch_calibration_inputs

# all logger.info/error calls end up in CloudWatch automatically via Fargate
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run():
    # JOB_ID is the DynamoDB record ID passed by the pixel selection lambda.
    # AWS_BATCH_JOB_ID is the Batch-assigned ID — different and not in DynamoDB.
    job_id    = os.environ["JOB_ID"]
    pixel_ids = [int(i) for i in os.environ["PIXEL_IDS"].split(",")]
    rundir    = os.environ.get("RUNDIR", "/tmp/isofit")
    attempt   = int(os.environ.get("AWS_BATCH_JOB_ATTEMPT", "0"))
    n_cores   = int(os.environ.get("NCPU", str(os.cpu_count())))

    logger.info(f"job {job_id} attempt {attempt} — processing {len(pixel_ids)} pixels")
    logger.info(f"n_cores={n_cores} (NCPU env var)")

    try:
        # update_job_status writes the current state to DynamoDB so the dashboard
        # can show what's happening in real time.
        # record_attempt_event appends a timestamped entry to the attempt_history
        # list in the same DynamoDB record — gives us a full audit trail per job
        update_job_status(job_id, "running", attempt=attempt)
        record_attempt_event(job_id, "started", attempt, f"{len(pixel_ids)} pixels requested")

        # Retry logic disabled — ON CONFLICT DO UPDATE handles overwrites.
        # Uncomment to skip already-completed pixels on retry:
        # completed = fetch_completed_pixel_ids(pixel_ids)
        # remaining = [pid for pid in pixel_ids if pid not in completed]
        # if not remaining:
        #     update_job_status(job_id, "complete", pixels_processed=len(pixel_ids), attempt=attempt)
        #     record_attempt_event(job_id, "complete", attempt, "all pixels already completed on prior attempt")
        #     return
        remaining = pixel_ids

        pixels = fetch_pixels(remaining)
        if not pixels:
            raise ValueError(f"no pixels found in db for ids: {remaining}")

        # The pixel selection lambda groups jobs by (campaign, sensor, sensor_date,
        # granule) — sensor_date picks the calibration (wavelengths, fwhm, input files)
        campaign_name = os.environ["CAMPAIGN_NAME"]
        sensor_name   = os.environ["SENSOR_NAME"]
        sensor_date   = os.environ["SENSOR_DATE"]

        isofit_sensor = SENSOR_KEY_MAP.get(sensor_name)
        if not isofit_sensor:
            raise ValueError(f"Unknown sensor name '{sensor_name}'. Add it to SENSOR_KEY_MAP in tasks.py")
        wl_raw, fwhm_raw = fetch_sensor_metadata(campaign_name, sensor_name, sensor_date)
        if len(wl_raw) != len(pixels[0]["radiance"]):
            raise ValueError(
                f"calibration {campaign_name}|{sensor_name}|{sensor_date} has {len(wl_raw)} bands "
                f"but pixel {pixels[0]['pixel_id']} has {len(pixels[0]['radiance'])}"
            )
        wl   = {isofit_sensor: wl_raw}
        fwhm = {isofit_sensor: fwhm_raw}
        logger.info(f"DB wavelengths: {len(wl_raw)} bands, {wl_raw[0]:.2f}–{wl_raw[-1]:.2f} nm [{campaign_name}|{sensor_name}|{sensor_date}]")

        # optional input files from S3: sensors/<sensor>/<date>/ (rdn factors, noise)
        # and campaigns/<campaign>/ (surface prior) — see README
        inputs = fetch_calibration_inputs(
            campaign_name, sensor_name, sensor_date, Path(rundir) / job_id / "inputs"
        )

        granule_ids = list({p['granule_id'] for p in pixels})
        logger.info(f"granules in job: {granule_ids}")

        # mark as inverting in dynamo so the dashboard shows progress
        # pixels_processed here reflects any pixels saved on prior attempts
        update_job_status(job_id, "inverting", pixels_processed=0, attempt=attempt)
        record_attempt_event(job_id, "inverting", attempt, f"running inversion on {len(remaining)} pixels")

        # this is where the actual work happens — lut generation + oe inversion.
        # everything above and below is just orchestration
        result = run_inversion(pixels, wl, fwhm, sensor_name, rundir, job_id, inputs, n_cores=n_cores)

        if result.get("status") == "error":
            raise RuntimeError(
                f"Inversion failed: {result.get('message', 'unknown error')}\n"
                f"{result.get('traceback', '')}"
            )

        logger.info(f"Inversion result keys: {list(result.get('result', result).keys())}")

        # write results back to postgres — results are in fetch order, not request order
        save_results(job_id, [p["pixel_id"] for p in pixels], result)

        # final dynamo update — dashboard shows complete with total pixel count
        total = len(pixels)
        update_job_status(job_id, "complete", pixels_processed=total, attempt=attempt)
        record_attempt_event(job_id, "complete", attempt, f"all {total} pixels saved successfully")
        logger.info(f"job {job_id} complete — {total} pixels processed across {attempt + 1} attempt(s)")

    except Exception as e:
        # write failure to dynamo before exiting so the dashboard knows what happened.
        # re-raise so batch sees a non-zero exit code and retries if configured
        logger.error(f"job {job_id} failed on attempt {attempt}: {e}")
        record_attempt_event(job_id, "failed", attempt, str(e))
        update_job_status(job_id, "failed", error=str(e), attempt=attempt)
        raise


if __name__ == "__main__":
    run()
