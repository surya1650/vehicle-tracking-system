# Gate Monitor remaining work and pilot plan

## Current position

The project has the MP4 and RTSP sources, vehicle tracking, event-triggered ANPR, PostgreSQL visit records, operator review, and the React dashboard. It also has plate search, camera-area history, operator-confirmed parking, multi-day visits, and vehicle and document records with source and check time. The cloud validation run passed 45 automated tests and desktop and mobile browser checks with demo data. Check Project checks for CI on the revision selected for the office pilot. [Project setup](../README.md) · [GitHub checks](https://github.com/surya1650/vehicle-tracking-system/actions)

The current evidence comes from public footage, staged replays, and cloud tests. The public overhead clip matched two of four labelled crossings. In the controlled Indian-plate test, glare, motion blur, and partial occlusion sent all four cases in each condition to review. The office camera setup and the target Windows 11 CPU have yet to pass field acceptance. [Benchmark results](README.md#measured-results) · [Controlled Indian and BH-series OCR](README.md#controlled-indian-and-bh-series-ocr)

## Core pilot in order

Run the pinned public scorecard from the repository root with the project's worker and OCR environments. Choose a new output directory each time and keep `scorecard.json`, the per-clip diagnostics, and the candidate revision together.

```powershell
& $Python -m validation.pilot_scorecard --output data/reports/pilot-public
```

On Linux, use `.venv/bin/python` in place of `& $Python`. The scorecard includes the video, label, detector-weight, and tracker hashes, plus the replay arguments. It reports correct automatic reads, wrong automatic reads, false automatic movements, and unscored automatic reads separately from the review rate.

The latest public scorecard matched two of four overhead, two of two mixed traffic, and two of two portrait crossings. All six detected crossings entered review. The isolated recovery rehearsal withheld delivery during a simulated API outage and retried it after recovery. The route retained three camera events and one visit. A PostgreSQL and evidence restore on a separate instance preserved both registry sources and all six original evidence images with authenticated access.

| Step | Work | Evidence required to finish |
| --- | --- | --- |
| 1. Prepare a candidate and scorecard | Review the workspace changes and pin the build, schema revision, model weights, OCR model, and tracker settings for the pilot. Run Linux/PostgreSQL, Windows headless, dashboard build, and replay checks on that revision. Agree the thresholds for missed crossings, incorrect automatic records, review workload, and latency. | A traceable candidate, repeatable replay commands, and a scorecard with denominators and agreed targets for each camera. This can start with the existing footage. |
| 2. Collect and label office footage | Capture authorised 1080p MP4 exports from Gate IN and Gate OUT. Add the yard camera if it is in the pilot. Use the actual timestamps with offsets and record camera placement, frame rate, lane, lighting, and tripwire direction. Label vehicles, plates, crossings, and periods with no crossings. | A private, labelled set with separate calibration and held-out recordings. A controlled test drive at the site can supply the first clips if archive footage is unavailable. [Ground-truth example](public-sample.json) |
| 3. Improve accuracy on the target footage | Score the baseline first. Adjust camera position or angle, shutter, exposure, lighting, tripwire placement, and sampling as the failures warrant. Tune detector and tracker settings on calibration clips. Add separate reporting for wrong automatic accepts and plate proposals sent to review. Re-score the held-out set. | Camera-by-camera results against the agreed targets, plus examples of failures and the operator workload. Promote settings only after comparing held-out accuracy and CPU cost. |
| 4. Run the Windows 11 and Hikvision pilot | Install on the actual CPU-only machine and check native playback at the original resolution and frame rate. Connect the real Hikvision streams, measure capture-to-dashboard latency, and check the site clock and event timestamps. Add one stream at a time, then run the planned camera count through representative peak traffic. | Measured CPU, memory, FPS, frames dropped, stage timings, and latency. Recover from a camera disconnect and an API or DB outage with correct visit counts and no duplicate delivery. A failed camera leaves other streams running. |
| 5. Rehearse operations and release | Have operators complete a real entry, recognition review, yard sighting, bay update, exit, and re-entry. Check access controls, timestamps, retention, and review ownership. Schedule database and evidence backups, then restore both in a separate instance. Prepare the GitHub release candidate with migrations, configuration, and a rollback procedure. | A signed-off operator walkthrough, a restore that opens the original evidence from the dashboard, and CI on the exact candidate revision. Use the agreed publication approval for the GitHub release. |

### What to capture and score

- Start with Gate IN and Gate OUT and use the yard when available. A suggested first collection target is roughly 100 labelled crossings across the two gates, with counts by camera, direction, and lighting condition. Treat this as a planning estimate. Use more footage when the initial set misses critical operating conditions.
- Include standard Indian plates and BH-series when present, fast and slow vehicles, motorcycles, cars, buses and trucks, reversing and stopping, two vehicles together, occlusion, glare, and low light. Mark visibly unreadable plates explicitly. Record entry, exit, and any known parking or sighting sequence for test vehicles.
- Report vehicle detection recall, tracking continuity and ID changes, tripwire precision and recall, extra IN and OUT events, plate detection recall, plate-to-vehicle association, exact and character-level reads, automatic-accept coverage, incorrect automatic accepts, and manual review rate. Label all crossings in any clip used to measure false events. Record counts alongside percentages.
- Measure input and processed FPS, CPU, peak memory, frame drops, OCR calls per vehicle, queue delays, and median and 95th-percentile event latency. Add missing tracker-stage timing and track creation/loss diagnostics where the current logs cannot explain a failure. Check recording and RTSP timestamps against a reference clock. Validate presentation timestamps if the site's exports use variable frame rates.
- Rehearse interrupted OCR, worker restart, temporary DB or API failure, event retries, late arrivals, and manually corrected registrations. Check that uncertain events go to review and that a verified sighting updates the last confirmed camera while leaving the gate visit intact.

### Site inputs

The site coordinator needs to arrange camera access, the Windows 11 test machine, sample export files or a controlled test drive, and an operator for the walkthrough. The gate team should supply site working hours, expected peak traffic, and the acceptable review workload. The site owner should confirm how long recordings, evidence, and audit records are retained and who can access each. Keep recordings and camera credentials in the authorised site environment.

## Parallel add-ons

| Add-on | Dependency | Work and acceptance |
| --- | --- | --- |
| Live vehicle and document lookup | A provider-approved endpoint, credentials, permitted fields, and storage terms. | Check VAHAN, Parivahan PUC, and API Setu for the organisation's access. Once a provider is available, validate sample responses and map allowed fields to vehicle, PUC, insurance, and fitness records. Record source and fetch time, preserve administrator access for owner data, and test missing fields, expired documents, outages, and rate limits. Keep site-entered records usable throughout. [VAHAN](https://vahan.parivahan.gov.in/nrservices/faces/user/login.xhtml) · [PUC](https://puc.parivahan.gov.in/puc/views/PucCertificate.xhtml) · [API Setu](https://apisetu.gov.in/) |
| Automatic exact-bay location | A site decision to automate bays and reliable bay-level observations from cameras or sensors. | Map instrumented bays to observations and test arrival, vacancy, neighbouring bays, vehicles changing bays, and competing observations. Preserve operator confirmation and show the latest camera area when the bay is uncertain. Use operator-confirmed bays for the core pilot. |

Begin with the release-candidate checks and the per-camera scorecard while the site organises the first recording. The first field run should use an entry stream, an exit stream, and one operator so detection failures and workflow problems are easy to reproduce.
