import os
import json
import boto3
import psycopg2
import logging
import psycopg2.extras
from contextlib import contextmanager

logger = logging.getLogger(__name__)

secret_arn = os.environ['DB_SECRET_ARN']
region = os.environ.get("AWS_REGION", "us-west-2")

client = boto3.client("secretsmanager", region_name=region)
secret = json.loads(client.get_secret_value(SecretId=secret_arn)['SecretString'])

DB_HOST = secret["host"]
DB_USER = secret["username"]
DB_PASS = secret["password"]
DB_NAME = "vswirplants"


@contextmanager
def get_connection():
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port='5432',
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            connect_timeout=10,
        )
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {type(e).__name__}: {repr(e)}")
        raise
    finally:
        if conn:
            conn.close()


def fetch_pixels(pixel_ids: list) -> list:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM vswir_plants.extracted_spectra_view WHERE pixel_id = ANY(%s)",
                (pixel_ids,)
            )
            return cur.fetchall()

def fetch_sensor_metadata(campaign_name: str, sensor_name: str, sensor_date: str) -> tuple[list, list]:
    """Wavelengths and fwhm of one sensor calibration — (campaign, sensor, sensor_date) is its key."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT wavelength_center, fwhm
                FROM vswir_plants.extracted_metadata_view
                WHERE campaign_name = %s AND sensor_name = %s AND sensor_date = %s
                """,
                (campaign_name, sensor_name, sensor_date)
            )
            rows = cur.fetchall()

    if len(rows) != 1:
        raise ValueError(
            f"expected 1 calibration for {campaign_name}|{sensor_name}|{sensor_date}, found {len(rows)}"
        )

    return list(rows[0]["wavelength_center"]), list(rows[0]["fwhm"])


def fetch_completed_pixel_ids(pixel_ids: list, job_id: str) -> set:
    """Used on retry to skip pixels already saved in a prior attempt."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pixel_id FROM vswir_plants_staging.output_pixel_rfl WHERE pixel_id = ANY(%s) AND job_id = %s",
                (pixel_ids, job_id)
            )
            return {row[0] for row in cur.fetchall()}


def save_results(job_id: str, pixel_ids: list, result: dict):
    # result is {"status": "success", "result": {"statevec": ..., "solution": ..., "runtime_seconds": ...}}
    # pixel_ids must be in the same order as the pixels passed to the inversion.
    # Keep only the surface reflectance states (RFL_*) — the rest of the state
    # vector (H2OSTR, AOT550, aerosol fractions, …) varies with the LUT setup
    inner = result.get("result", result)
    rows = []
    for i, pixel_id in enumerate(pixel_ids):
        names = inner["statevec"][i]
        rfl = [float(v) for n, v in zip(names, inner["solution"][i]) if n.startswith("RFL_")]
        if not rfl:
            raise ValueError(f"no RFL_ states in the solution for pixel {pixel_id}: {names}")
        rows.append((pixel_id, job_id, rfl))
    with get_connection() as conn:
        with conn.cursor() as cur:
            logger.info(f"Inserting {len(rows)} rows, pixel_ids={[r[0] for r in rows]}, reflectance_len={len(rows[0][1]) if rows else 0}")
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO vswir_plants_staging.output_pixel_rfl (pixel_id, job_id, reflectance)
                VALUES %s
                ON CONFLICT (pixel_id, job_id) DO UPDATE
                    SET reflectance = EXCLUDED.reflectance
                """,
                rows
            )
    logger.info(f"Saved {len(pixel_ids)} results for job {job_id}")