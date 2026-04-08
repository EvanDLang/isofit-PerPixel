import traceback
import logging
import numpy as np

from run_inversion import main as isofit_main
from models import InversionData

logger = logging.getLogger(__name__)

# Map database sensor names to isofit short keys expected by FID() and sensor_name_to_dt()
SENSOR_KEY_MAP = {
    'NEON AIS 1':      'neon',
    'NEON AIS 2':      'neon',
    'NEON AIS 3':      'neon',
    'AVIRIS-Classic':  'avcl',
    'AVIRIS-NG':       'ang',
    'AVIRIS-3':        'av3',
    'AVIRIS-5':        'av5',
}

def run_inversion(
    pixels: list,
    wl,
    fwhm,
    sensor_name: str,
    rundir: str,
    job_id: str,
) -> dict:
    try:
        isofit_sensor = SENSOR_KEY_MAP.get(sensor_name)
        if not isofit_sensor:
            raise ValueError(f"Unknown sensor name '{sensor_name}'. Add it to SENSOR_KEY_MAP in inversion.py")

        logger.info(f"Running inversion on {len(pixels)} pixels — sensor: {sensor_name} → {isofit_sensor}")

        # ── Diagnostic logging ────────────────────────────────────────────
        wl_arr = wl[isofit_sensor]
        fwhm_arr = fwhm[isofit_sensor]
        logger.info(f"wl type={type(wl_arr).__name__} len={len(wl_arr)} dtype={np.array(wl_arr).dtype} range={wl_arr[0]:.1f}–{wl_arr[-1]:.1f} nm")
        logger.info(f"fwhm type={type(fwhm_arr).__name__} len={len(fwhm_arr)} dtype={np.array(fwhm_arr).dtype} range={fwhm_arr[0]:.3f}–{fwhm_arr[-1]:.3f} nm")
        for i, p in enumerate(pixels):
            radiance = p["radiance"]
            rdn_arr = np.array(radiance)
            logger.info(
                f"pixel[{i}] id={p.get('pixel_id')} "
                f"radiance type={type(radiance).__name__} len={len(radiance)} dtype={rdn_arr.dtype} "
                f"range=[{rdn_arr.min():.4f}, {rdn_arr.max():.4f}] "
                f"lon={p['lon']:.2f} lat={p['lat']:.2f} elev={p['elevation']:.1f} "
                f"cosine_i={p['cosine_i']:.4f} utc={p['utc_time']:.4f} "
                f"slope={p['slope']:.2f} aspect={p['aspect']:.2f} "
                f"granule={p['granule_id']}"
            )
        # ─────────────────────────────────────────────────────────────────

        result = isofit_main(
            rdn_data=       InversionData([np.array(p["radiance"])          for p in pixels]),
            longitude=      InversionData([np.array(p["lon"])               for p in pixels]),
            latitude=       InversionData([np.array(p["lat"])               for p in pixels]),
            elevation=      InversionData([np.array(p["elevation"])         for p in pixels]),
            path_length=    InversionData([np.array(p["path_length"])       for p in pixels]),
            sensor_azimuth= InversionData([np.array(p["to_sensor_azimuth"]) for p in pixels]),
            sensor_zenith=  InversionData([np.array(p["to_sensor_zenith"])  for p in pixels]),
            sun_azimuth=    InversionData([np.array(p["to_sun_azimuth"])    for p in pixels]),
            sun_zenith=     InversionData([np.array(p["to_sun_zenith"])     for p in pixels]),
            solar_phase=    InversionData([np.array(p["solar_phase"])       for p in pixels]),
            slope=          InversionData([np.array(p["slope"])             for p in pixels]),
            aspect=         InversionData([np.array(p["aspect"])            for p in pixels]),
            cos_i=          InversionData([np.array(p["cosine_i"])          for p in pixels]),
            utc_time=       InversionData([np.array(p["utc_time"])          for p in pixels]),
            wl=wl,
            fwhm=fwhm,
            sensor=         InversionData([isofit_sensor for _ in pixels]),
            gid=            InversionData([p["granule_id"] for p in pixels]),
            rundir=rundir,
            task_id=job_id,
            delete_all_files=True,
        )

        return {"status": "success", "result": result}

    except Exception as e:
        logger.error(f"Inversion failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "traceback": str(traceback.format_exc()),
            "error_type": type(e).__name__
        }