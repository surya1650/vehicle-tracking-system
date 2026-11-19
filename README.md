# Gate Monitor

A CCTV vehicle tracking and number plate recognition proof of concept. It reads MP4 recordings or RTSP streams, tracks vehicles with YOLOv8n and ByteTrack, detects directional tripwire crossings, and runs FastALPR on selected crops. A durable outbox delivers results to FastAPI and PostgreSQL. The React dashboard provides live previews, visit history, and manual review.

## 1. Install the Windows worker

Use 64-bit Python 3.11 and PowerShell from the repository root. Docker Desktop runs the API and database. Node.js 24 is needed locally only for dashboard development.

```powershell
py -3.11 -m venv .venv
py -3.11 -m venv .venv-ocr
$Python = ".\.venv\Scripts\python.exe"
$OcrPython = ".\.venv-ocr\Scripts\python.exe"
& $Python -m pip install -r requirements-tracking.txt -r requirements-api.txt -r requirements-dev.txt
& $OcrPython -m pip install -r requirements-ocr.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force data\videos, data\models, data\snapshots, data\live | Out-Null
& $Python validation/fetch_assets.py
```

The fixture downloader uses the public Ultralytics releases and checks the video and model against pinned SHA-256 digests. On Linux, use `python3.11 -m venv`, `.venv/bin/python`, and `.venv-ocr/bin/python`. `requirements.txt` is sufficient for MP4 diagnostics and native playback. The tracking environment uses CPU-only PyTorch. A future GPU worker can install compatible PyTorch wheels and set `YOLO_DEVICE`. The separate OCR environment keeps FastALPR's headless OpenCV dependency from replacing the native player.

## 2. Start the API, database, and dashboard

Generate different values for `POSTGRES_PASSWORD` and `API_SIGNING_KEY`, then set them in your local `.env`. Use a hex database password so it can appear safely in a connection URL. Keep these values for subsequent starts of the same database.

```powershell
& $Python -c "import secrets; print(secrets.token_hex(24))"
& $Python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
docker compose up -d --build --wait
docker compose exec api python -m backend.admin gate-admin ADMIN
docker compose exec api python -m backend.admin vision-ingest INGEST
```

Open `http://127.0.0.1:8010`, sign in as `gate-admin`, then open **Camera administration** and add a camera. The API docs are at `/docs` and the health check is at `/health`. The API applies the Alembic migrations on startup. The database volume persists between regular Compose restarts.

For the public replay, use camera ID `sample`, gate ID `demo_gate`, source variable `CAMERA_SOURCE_SAMPLE`, resolution `1080 × 1920`, tripwire `0,1050,1080,1050`, and **Automatic** direction with the positive entry side. Configure the worker environment and run the recording:

```powershell
$WorkerCredential = Get-Credential -UserName vision-ingest -Message "Camera worker API sign-in"
$env:ANPR_INGEST_USERNAME = $WorkerCredential.UserName
$env:ANPR_INGEST_PASSWORD = $WorkerCredential.GetNetworkCredential().Password
$env:CAMERA_SOURCE_SAMPLE = "data/videos/anpr-demo-video.mp4"
& $Python -m vision.camera --camera sample --video-start-time "2026-11-18T10:30:00+05:30" --interval 3
```

The reference time in this demo exercises historical playback. For your own MP4, supply its actual recording start with a UTC offset. Without a start time, the standalone player can report frame-relative time, and the uploader holds timestamp-less events for correction. Confirm that submitted crossings appear in **Recognition review**, inspect the evidence, and enter a verified registration if the plate is readable. The public clip contains foreign plates; the default rules require review for unrecognized formats and ambiguous vehicle associations.

To follow one vehicle across **Gate IN**, **Yard Camera**, and **Gate OUT**, run the [three-camera replay](validation/README.md#three-camera-replay). The dashboard's **Vehicle tracking** page shows the latest confirmed camera, recent camera history, and the latest visit status.

### Vehicle details and parking search

Search a registration in **Vehicle tracking** to see the last confirmed camera, its configured area, entry and exit times, elapsed time on site, and past visits. An administrator can set a camera area under **Camera administration**.

An operator can confirm a zone and optional bay for an open visit. A later confirmed camera event marks the parking record for reconfirmation. An exit makes the parking record historical. A new visit starts with its own parking record.

Administrators can add or edit site records by registration. Available fields include the owner or department, manufacturer, model, color, fuel type, PUC certificate number, and expiry dates for registration, PUC, insurance, and fitness. Owner or department is visible only to administrators. Expiry labels use the configured site date and the dates saved in the site record.

| Official service | How to use it |
| --- | --- |
| [VAHAN vehicle services](https://vahan.parivahan.gov.in/nrservices/faces/user/login.xhtml) | Use the official sign-in and lookup for the vehicle details available to your account. |
| [Parivahan PUC certificate lookup](https://puc.parivahan.gov.in/puc/views/PucCertificate.xhtml) | Check a Pollution Under Control certificate through the official form. |
| [API Setu](https://apisetu.gov.in/) | Check catalogue access and ask the provider which vehicle or document fields your organization can access and store. |

An administrator can use **Vehicle tracking → Record official check** after checking the registration on the official site. Select **VAHAN registration (RC)** or **Parivahan pollution certificate (PUCC)**, copy the registration shown, and enter the fields you checked. The dashboard records the service and check time for each supplied field. Use **Edit details** for site-entered information, including the owner or department.

An approved integration can submit the same normalized fields to `POST /api/v1/vehicles/{plate}/registry-check` with an administrator account. The service limits the accepted fields, requires the registration to match, and rejects an older check that would overwrite a newer edit. Omit fields missing from the provider response. For exact retries, include the original `checked_at` timestamp with its UTC offset. The endpoint accepts already obtained details. Live retrieval requires an approved external service. [API Setu request and example](validation/registry-access.md)

If the build runs behind an HTTPS proxy, use the [optional Compose override](docker-compose.ca.yml) with the site's trusted PEM bundle. The bundle is mounted during the build.

```powershell
$env:ANPR_BUILD_CA = "C:\path\to\trusted-ca-bundle.pem"
docker compose -f docker-compose.yml -f docker-compose.ca.yml up -d --build --wait
```

On Linux, if private evidence is readable only by the worker account, set `ANPR_CONTAINER_USER` to that account's numeric `uid:gid` in `.env`. The API mounts evidence and previews read-only.

## 3. Standalone MP4 playback and calibration

```powershell
$Video = "data\videos\anpr-demo-video.mp4"
& $Python -m vision.run_video $Video --show --report data/reports/playback.json
& $Python -m vision.run_video $Video --track --tripwire "0,1050,1080,1050" --inference-interval 3 --show --output data/reports/traffic-tracked.mp4 --tracks-jsonl data/reports/tracks-sampled.jsonl --crossings-jsonl data/reports/crossings.jsonl
& $Python -m vision.run_video $Video --track --tripwire "0,1050,1080,1050" --inference-interval 3 --anpr --start-time "2026-11-18T10:30:00+05:30" --anpr-outbox data/outbox/standalone --report data/reports/anpr.json
```

Use **Q**, **Esc**, or close the native window to stop. Set `--speed 0.5` for half-speed playback. Omit `--show` on headless machines. Frames start at index zero. MP4 timing uses index divided by nominal FPS, so variable-frame-rate files need a presentation-timestamp implementation for frame-accurate timing. Exported MP4s contain video only. When sampling, the export uses original resolution and FPS divided by the inference interval.

Tripwire coordinates are in source pixels and line direction runs from the first point to the second. **Automatic** uses the signed side of the vehicle's bottom-centre point. Flip `--entry-side negative` to invert the direction mapping. A finite intersection, deadband, minimum movement, confirmation count, maximum observation gap, and cooldown govern crossings. Adjust `TRIPWIRE_*` and the [ByteTrack settings](config/bytetrack.yaml) after checking footage at each gate.

The tracker filters for car, motorcycle, bus, and truck. A track ID is local to the stream and may change after occlusion or reconnect. A larger sampling interval reduces inference work and increases the time between observations. Exported telemetry retains original source-frame indices.

Use `--agnostic-nms` or set `YOLO_AGNOSTIC_NMS=true` to suppress overlapping boxes across vehicle categories. Re-score the camera after enabling it because close vehicles can overlap in the image.

## 4. Recognition, API, and session behavior

- Each crossing saves a recovery checkpoint, then collects its track's recent vehicle crops and subsequent observations. A bounded worker chooses candidates by crop quality. Interrupted jobs and saturation become explicit review events. Candidate association uses plate positions relative to the target and other visible vehicles.
- OCR voting uses detector and OCR confidence and counts distinct frames. Conflicting readings, ambiguous associations, unrecognized formats, and low agreement remain in review. It preserves original observations. Common Indian registrations and BH-series rules are included. Repeat `--plate-format` or set `PLATE_FORMATS` to a JSON array of accepted regular expressions for additional formats.
- The uploader retains undelivered files and moves acknowledged events into `.sent`. API retries with the same UUID and canonical payload return the existing event. A different payload under the same UUID is a conflict. Use `--recording-id` to pin a standalone replay identity across path changes.
- Verified events project into PostgreSQL visits. Per-plate advisory locking and the partial unique index protect the open visit. Duplicate entries and unmatched exits raise alerts. Corrections and out-of-order events rebuild the affected visit history while retaining the original event, raw OCR proposal, and correction audit trail.
- Dashboard roles are **ADMIN**, **OPERATOR**, and **VIEWER**. The **INGEST** account is for workers. Event evidence requires an authenticated viewer role. A low-confidence read does not authorize or deny access.

FastALPR 0.4.0 was checked against its official API using OpenCV BGR input and ONNX Runtime's CPU provider. The defaults are `yolo-v9-t-384-license-plate-end2end` and `cct-xs-v2-global-model`, cached under `.cache/ocr`. Package repositories for [FastALPR](https://github.com/ankandrew/fast-alpr), [open-image-models](https://github.com/ankandrew/open-image-models), and [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr) publish MIT licenses. Confirm terms for the individual weights and Ultralytics before an organizational deployment.

## 5. Hikvision RTSP and operations

Register another camera in the dashboard, configure its wire and direction, and set a `CAMERA_SOURCE_*` variable on the worker to the RTSP URL. A typical Hikvision path is `/Streaming/Channels/101`. Store the complete URL with its credentials in the worker's environment or a private `.env`, and enter only the variable name in the dashboard.

```powershell
& $Python -m vision.camera --camera main_gate --interval 3
```

Run one process per camera. Restart that process after changing camera settings. Acquisition uses FFmpeg timeouts, a bounded queue, and exponential reconnect backoff. The worker publishes its heartbeat and an authenticated, bounded-rate JPEG preview. On reconnect or stream timestamp discontinuity, it resets track and wire state. The report includes RTSP presentation timing when available, while event timestamps use UTC receipt time. Synchronize workers and cameras to the site clock. A worker falling behind may drop buffered frames, so benchmark the sampling interval against your site's traffic.

Base deployment exposes the API and database to localhost. Use HTTPS through your reverse proxy for remote dashboard access. Keep the private snapshot and outbox directories accessible only to the worker and API service accounts. Schedule retention for images, live previews, and outbox receipts according to the site's policy.

Run the retention job from the worker host with `DATABASE_URL` pointing to the database and `ANPR_EVIDENCE` pointing to the shared snapshots directory. Its default run reports the number of expired images. Add `--apply` to delete expired images and their evidence pointers while preserving the movement and audit ledgers:

```powershell
& $Python -m backend.retention
& $Python -m backend.retention --apply
docker compose exec db pg_dump -U anpr -d anpr -Fc -f /tmp/gate-monitor.dump
docker compose cp db:/tmp/gate-monitor.dump .\data\gate-monitor.dump
```

Back up the shared evidence separately if permitted by the retention policy. Restore backups in a separate database to verify them. The database holds file references, while disk storage holds the images.

## 6. Development and tests

On a host development setup, set `DATABASE_URL` and `API_SIGNING_KEY` first. Point the test variable to a migrated disposable database. The integration tests create users, cameras, and events.

```powershell
& $Python -m alembic upgrade head
& $Python -m uvicorn backend.main:create_app --factory --port 8010 --no-access-log
```

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run dev
```

```powershell
& $Python -m pytest -q
$env:ANPR_TEST_VIDEO = "data/videos/anpr-demo-video.mp4"
$env:ANPR_TEST_OCR_PYTHON = ".venv-ocr/Scripts/python.exe"
$env:ANPR_TEST_DATABASE_URL = $env:DATABASE_URL
& $Python -m pytest -q
```

The first test run covers the offline components. GitHub Actions runs vision and PostgreSQL checks on Linux, headless CPU vision checks on Windows Server, and the dashboard production build. The variables in the second run enable real YOLOv8n inference, FastALPR, and PostgreSQL cases. For labeled ground truth, use the structure in [validation/public-sample.json](validation/public-sample.json):

```powershell
& $Python -m vision.evaluate validation/public-sample.json data/reports/tracks-sampled.jsonl data/outbox/standalone
```

On the target machine, keep the annotated replay, OCR outbox, and ground truth for each camera and recording together. Verify the first intersection in each direction and confirm the resulting IN and OUT events in Movement history before scoring the full recording. Use a separate outbox per evaluation recording. Label every crossing before setting `crossings_complete` to true. The public fixture contains six vehicle boxes, one plate box, and one crossing. It exercises the scoring path without making a site-accuracy claim.

See the [public-footage benchmark](validation/README.md) for licensed traffic clips, reproducible scoring, and remaining acceptance work.

## Milestone status

| Phase | Verified in the cloud |
| --- | --- |
| 1. Foundation | Python 3.11, isolated dependency sets, configuration, structured logs |
| 2. MP4 | 168 frames displayed at 1× and 2×. Metadata matched FFprobe at 1080 × 1920, 29.97 FPS. |
| 3. YOLOv8n and ByteTrack | Cars and trucks identified. One truck held its ID across 168 frames. All-frame CPU replay averaged 58.33 ms model inference and 14.06 source FPS. |
| 4. Tripwire | Geometry, direction, jitter, reverse movement and simultaneous-track tests passed. Two crossings emitted on the public clip. |
| 5. FastALPR | Real CPU BGR recognition and event-triggered OCR verified. Both public replay crossings required manual review. |
| 6. API and PostgreSQL | Alembic migrations, concurrent writes, retries, sessions, corrections, and protected evidence verified. |
| 7. Dashboard | TypeScript production build and Chromium checks of sign-in, all six screens, evidence review, correction and camera administration passed. |
| 8. RTSP and deployment | Local RTSP disconnect/reconnect and Docker Compose health passed. The 60-second repeated clip processed 1,801 frames at 36.33 source FPS with inference every fifth frame. That benchmark used tracking and the tripwire. |

A separate landscape smoke test replayed 377 frames of the public [Intel vehicle clip](https://github.com/intel-iot-devkit/sample-videos/blob/master/car-detection.mp4) scaled to 1920 × 1080. FFprobe and OpenCV agreed on 12.5 FPS. The CPU tracker processed every third frame at 9.87 source frames/s and used 411 MiB peak vision-process memory in that cloud test.

### Field acceptance checklist

- [ ] Verify native playback and measure processing speed on the target Windows 11 machine.
- [ ] Connect the office Hikvision camera and calibrate its wire and direction.
- [ ] Label representative gate recordings with fast traffic, occlusion, motorcycles, buses, changing lighting, and local plate formats. Measure accuracy and review rates with those labels.
