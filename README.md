# isofit-PerPixel

### Description

This contains the scripts (and prototype api) to run ISOFIT OE inversions on single pixel-row examples. 

On the ISOFIT-side, this contains a new script to directly build the ISOFIT config, perform the LUT generation for both the presolve and main solve, and return a dictionary with the statevector and OE solution.

On the api-side, this contains a Docker set up for a simple api endpoint and task worker. JSON files with the input data are sent to the endpoint, which kicks off the ISOFIT run. These can be returned as string dictionaries or output as .csv.

Some important considerations:
- I disabled Ray in the Docker environment. I was having issues with the internal multiprocessing.
- I need to hook up a direct, local click CLI for the ISOFIT inversion. The only way to currently run the inversion is via the API call.
- I have a list of things ISOFIT inputs that I haven't hooked up yet (e.g. custom surface files, instrument noise files). Much of the configuration is currently coming from `env.paths`


### Per-calibration input files

Optional ISOFIT input files are read from S3 at job start. They are split by what
they depend on: instrument calibration files are keyed by sensor + sensor date,
and the surface prior is keyed by campaign.

```
s3://vswir-plants-config/isofit-inputs/          ← ISOFIT_INPUTS_URI
├── sensors/<sensor_name>/<sensor_date>/
│   ├── rdn_factors.txt          radiance correction, one value per band
│   ├── channelized_noise.txt    channelized uncertainty
│   └── noise.txt                instrument noise model (overrides NOISE_PATH)
└── campaigns/<campaign_name>/
    ├── surface.json             surface prior config
    └── …                        any files surface.json references by relative path
```

Example:

```
isofit-inputs/sensors/AVIRIS-NG/2022-02-24/rdn_factors.txt
isofit-inputs/campaigns/SHIFT/surface.json
```

Rules:
- `campaign_name`, `sensor_name` and `sensor_date` must match the values in
  `vswir_plants.extracted_metadata_view` exactly (case, spaces, date format).
  They reach the job as the `CAMPAIGN_NAME`, `SENSOR_NAME` and `SENSOR_DATE` env vars.
  If a folder name doesn't match, the job silently falls back to defaults.
- Every file is optional. A missing file falls back to the image defaults
  (isofit env data; SNR 500 for noise if `NOISE_PATH` is also unset).
- The whole prefix is downloaded, so put anything `surface.json` points to
  alongside it.
- `rdn_factors.txt` must have exactly as many values as the calibration has bands,
  or the job fails.
- Wavelengths and fwhm are **not** read from S3. They come from the database, keyed by
  (campaign, sensor, sensor_date).

Check CloudWatch for `inputs from s3://…: [...]` to see which files a job picked up.


### How to run

Installing Docker is a pre-requisite. I've only tested locally with the containers running on the same local machine that I send commands from.

1) At the root with `docker-compose.yml`, run: 

    `docker compose up`

    to build the containers.

2) I've provided an example format for the input data in `examples/test_call.json`. Currently, the call contains `RDN`, `LOC`, `OBS`, `wavelength`, `fwhm` lists, and `sensor`, `granule_id`, `rundir` strings, and `delete_all_files`, a bool.

3) To run on the container for the test_call:
```
    curl -X POST http://localhost:8000/process \
    -H "Content-Type: application/json" \
    -d @examples/test_call.json
```

4) Check status of inversion task:
```
curl -X GET http://localhost:8000/result/{task_id}
```

5) Download reslt of inversion task to csv. NOTE: will "wait" for `success` or `failure`
```
curl -o path/to/output.csv http://localhost:8000/download/{task_id}
```
