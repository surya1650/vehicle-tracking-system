# Public-footage benchmark

This benchmark measures crossing events on two fixed-camera recordings. It also tests plate recognition separately on a readable plate crop from the existing demo clip.

## Footage and attribution

Intel IoT Developer Kit publishes `car-detection.mp4` and `person-bicycle-car-detection.mp4` under CC BY 4.0. The downloader uses the source files from a pinned revision and verifies SHA-256. The annotations add vehicle boxes and crossing labels in the original frames. The generated replay videos add tracking and tripwire overlays. [Source repository](https://github.com/intel-iot-devkit/sample-videos/tree/57978890822836f2b4743852f04f62fc511757e4) · [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/)

The readable-plate check uses the existing public Ultralytics demo clip. The stress variants transform its frame-60 vehicle crop during a local run. [Ultralytics test clip](https://github.com/ultralytics/assets/releases/download/v0.0.0/anpr-demo-video.mp4)

## Reproduce on Windows

Install the tracking and OCR environments using the [Windows setup](../README.md#1-install-the-windows-worker). Run these commands from the repository root. Each run writes to a fresh directory.

```powershell
$Python = ".\.venv\Scripts\python.exe"
$Run = "data/reports/traffic-" + [guid]::NewGuid().ToString("N")
New-Item -ItemType Directory -Path $Run | Out-Null
& $Python validation/fetch_assets.py --traffic
if ($LASTEXITCODE -ne 0) { throw "Fixture download failed" }

$Clips = @(
  @{ Video = "intel-car-detection"; Labels = "intel-car-detection"; Line = 216 },
  @{ Video = "intel-person-bicycle-car-detection"; Labels = "intel-mixed-traffic"; Line = 260 }
)
foreach ($Clip in $Clips) {
  $Output = "$Run/$($Clip.Labels)"
  & $Python -m vision.run_video "data/videos/$($Clip.Video).mp4" --track --device cpu --inference-interval 2 --tripwire "0,$($Clip.Line),767,$($Clip.Line)" --tripwire-mode AUTO --entry-side positive --anpr --tracks-jsonl "$Output/tracks.jsonl" --crossings-jsonl "$Output/crossings.jsonl" --anpr-outbox "$Output/outbox" --anpr-evidence "$Output/evidence" --report "$Output/replay.json"
  if ($LASTEXITCODE -ne 0) { throw "Traffic replay failed" }
  & $Python -m vision.evaluate "validation/$($Clip.Labels).json" "$Output/tracks.jsonl" "$Output/outbox"
  if ($LASTEXITCODE -ne 0) { throw "Scoring failed" }
}
& $Python -m validation.ocr_stress
```

The fixtures label the visible car crossings in both complete clips and selected vehicle boxes around those events. Downward crossings are IN. Scoring matches events by direction and within six source frames. When boxes are labelled for a vehicle, the reported track ID must also have matched that vehicle in the replay. The distant Intel plates have no reference texts. Their plate-accuracy denominators are zero.

## Measured results

| Clip | Source frames | Inference sampling | Crossings found | Extra events |
| --- | ---: | --- | ---: | ---: |
| Intel overhead cars | 377 | Every second frame | 1 of 4 | 0 |
| Intel mixed traffic | 647 | Every second frame | 2 of 2 | 0 |
| Intel overhead cars | 377 | Every frame | 2 of 4 | 0 |

All three replays decoded every source frame. The overhead clip still missed two cars at full-frame inference. On its eight labelled vehicle views, the default and full-frame runs matched two boxes of the correct class. The detector sometimes classified a sedan as a bus. Increasing the input size, rotating the input, and spot-checking YOLOv8s at 640 and 960 did not consistently recover the missed views. The shipped detector settings are still the baseline.

I ran a second comparison with YOLO11n on both clips. Each clip used the same line and sampling for both detectors.

| Clip and inference sampling | YOLOv8n crossings | YOLO11n crossings | YOLOv8n source FPS | YOLO11n source FPS |
| --- | ---: | ---: | ---: | ---: |
| Overhead cars, every frame | 2 of 4 | 0 of 4 | 17.16 | 21.84 |
| Mixed traffic, every second frame | 2 of 2 | 1 of 2 | 39.95 | 44.16 |

These are CPU replay measurements on two short clips. The next detector trial should target overhead views and score a separate labelled recording after tuning on these clips. To reproduce the comparison, use the published YOLO11n weights via `--weights` and set the overhead inference interval to 1 in the replay command. [YOLO11n weights](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt)

### Tracking and duplicate suppression trial

On eight labelled views from the Intel overhead clip, YOLOv8n at input size 256 matched five boxes with the correct class. At size 640 it matched two. A horizontal lane crop at sizes 416 and 640 matched three and one, respectively. The YOLO11n oriented-box checkpoint located zero of those labelled vehicles across the tested input sizes and lane crops. [YOLO11n OBB weights](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-obb.pt)

In full replay at size 256, the standard tracker found only one of four overhead crossings. Lowering the tracker activation thresholds recovered a second crossing and produced one duplicate. The combined trial with `--agnostic-nms` removed that duplicate. Use `--image-size 256 --agnostic-nms` and pass a copy of `config/bytetrack.yaml` with `track_high_thresh=0.15`, `new_track_thresh=0.15`, and `fuse_score=false` through `--tracker-config` to reproduce the combined trial.

| Complete replay | Inference sampling | Labelled crossings matched | Extra events | Source FPS |
| --- | --- | ---: | ---: | ---: |
| Intel overhead, combined trial | Every frame | 2 of 4 | 0 | 53.17 |
| Intel mixed traffic, combined trial | Every second frame | 2 of 2 | 0 | 109.07 |

The portrait clip now has a complete crossing inventory. The taxi starts past the line. The silver car and red truck cross once each. Frame labels use visual estimates where tire contact is in shadow. [Complete portrait labels](public-portrait-complete.json)

| Portrait configuration | Frame tolerance | Labelled crossings matched | Unmatched events |
| --- | ---: | ---: | ---: |
| YOLOv8n baseline, size 640 | 6 | 2 of 2 | 0 |
| Tuned settings, size 256 | 6 | 1 of 2 | 2 |
| Tuned settings, size 256 | 8 | 2 of 2 | 1 |

The tuned run placed the silver car event seven frames before its visual reference, which explains the difference between the two tolerances. It also emitted a separate truck track at the silver car crossing, even though the red truck was still behind the line. Keep this portrait clip as a check when tuning for overhead footage.

### Motion cue on fixed cameras

An adaptive foreground mask with ByteTrack found three of four labelled overhead crossings with one unmatched event. It found both mixed-traffic crossings with zero unmatched events. The prototype uses vehicle-sized motion regions as geometry cues for these clips. It provides an independent way to locate possible missed vehicles for a detector to inspect.

A spot check ran YOLOv8n on those foreground regions with 35 pixels of padding. At input size 256 it correctly located three of eight labelled car views. The earlier size-256 full-frame check located five. The proposed detector crops therefore need a stronger overhead model before a full replay.

```powershell
& $Python -m validation.motion_cue data/videos/intel-car-detection.mp4 validation/intel-car-detection.json
& $Python -m validation.motion_cue data/videos/intel-person-bicycle-car-detection.mp4 validation/intel-mixed-traffic.json
```

### Camera-specific fine-tuning trial

The training set uses 20 manually labelled frames of the Intel overhead clip. Five additional frames form the internal validation split. Positive frames show the first and last cars. The two middle vehicles, the scored box frames, and all frames from the mixed-traffic and portrait recordings were excluded from fitting. The overhead replay is an in-camera calibration check because it includes the first and last cars. [Training labels](overhead-training-labels.json)

```powershell
& $Python validation/fetch_assets.py --traffic
$Trial = "data/reports/overhead-" + [guid]::NewGuid().ToString("N")
& $Python -m validation.train_overhead --output $Trial
if ($LASTEXITCODE -ne 0) { throw "Training failed" }
$Replay = "$Trial/overhead-replay"
& $Python -m vision.run_video data/videos/intel-car-detection.mp4 --track --weights "$Trial/fit/weights/last.pt" --image-size 256 --confidence 0.4 --inference-interval 1 --tripwire "0,216,767,216" --anpr --tracks-jsonl "$Replay/tracks.jsonl" --anpr-outbox "$Replay/outbox" --anpr-evidence "$Replay/evidence" --report "$Replay/replay.json"
if ($LASTEXITCODE -ne 0) { throw "Replay failed" }
& $Python -m vision.evaluate validation/intel-car-detection.json "$Replay/tracks.jsonl" "$Replay/outbox"
```

The CPU fit used YOLOv8n for 12 epochs. Using a nominal batch size of four let the optimizer update after each batch. The first run's default accumulation left the early selected checkpoint's weights identical to the pretrained weights.

| Replay with the corrected final checkpoint | Image size | Confidence threshold | Labelled crossings matched | Unmatched events |
| --- | ---: | ---: | ---: | ---: |
| Overhead, every frame | 256 | 0.10 | 2 of 4 | 3 |
| Overhead, every frame | 256 | 0.40 | 2 of 4 | 2 |
| Overhead, every frame | 320 | 0.40 | 2 of 4 | 3 |
| Held-out mixed traffic, every second frame | 256 | 0.40 | 2 of 2 | 22 |
| Held-out portrait, every third frame | 256 | 0.40 | 0 of 2 | 6 |

At size 256 and threshold 0.40, all four overhead events were within the labelled frame and direction windows. Two event tracks failed the box-matching criterion. At threshold 0.10, an additional event occurred on an empty part of the road. The held-out results rule out promoting this checkpoint. The next training data should include more than one overhead camera and label further vehicles, empty intervals, and overlapping traffic.

For the separate plate test, the raw crop, perspective-warped crop, and crop at 33% brightness produced exact matches. The 15-pixel motion blur and the partially covered plate produced wrong reads. The partially covered plate still scored 0.78 OCR confidence. The original demo clip's full pipeline sent both crossings to review for ambiguous attribution. Direct crop recognition and per-vehicle event recognition are separate checks.

A second plate replay used a tripwire at y = 1400, where the plate on the silver car was clear of neighbouring vehicle boxes, and the clip's foreign registration format. The silver car was accepted after six matching frame readings. An overlapping vehicle still required review. Both events reached FastAPI and PostgreSQL. The accepted entry opened a visit, the pending entry stayed in review, and an authenticated user retrieved the accepted event's plate image.

```powershell
$PlateRun = "$Run/plate-at-1400"
& $Python -m vision.run_video data/videos/anpr-demo-video.mp4 --track --device cpu --inference-interval 3 --tripwire "0,1400,1079,1400" --tripwire-mode AUTO --entry-side positive --anpr --plate-format '[0-9]{4}[A-Z]{2}[0-9]' --start-time "2026-11-18T10:30:00+05:30" --anpr-outbox "$PlateRun/outbox" --anpr-evidence "$PlateRun/evidence" --report "$PlateRun/replay.json"
```

The replay uses an illustrative recording reference time and a format override for the foreign plates in this particular demo.

## Controlled Indian and BH-series OCR

The Indian plate benchmark renders two standard-format and two BH-series labels onto the same vehicle crop. Each label is tested clean, dimmed, with glare, motion blur, and partial occlusion. Each case contributes three exposure variants to the production format and voting rules.

```powershell
& $Python -m validation.indian_plate_replay
```

| Condition | Correct automatic records | Manual review | Wrong automatic records |
| --- | ---: | ---: | ---: |
| Clean | 3 of 4 | 1 | 0 |
| Dim | 3 of 4 | 1 | 0 |
| Glare | 0 of 4 | 4 | 0 |
| Motion blur | 0 of 4 | 4 | 0 |
| Partial occlusion | 0 of 4 | 4 | 0 |

Both rendered BH labels were recognized in the clean and dim cases. One standard plate's leading letter was repeatedly misread as a digit and caught by format validation. These cases exercise OCR and the review decision under controlled changes to one background. Indian gate-footage accuracy still needs a separately labelled dataset.

## Database restore

A PostgreSQL backup and restore drill also completed. The restored database matched the test database's event, visit, plate observation, alert, and audit row counts. Image files are stored separately and need a separate backup where required.

## Full brief and remaining acceptance

| Area | Evidence available | Remaining work |
| --- | --- | --- |
| MP4, tracking, crossing logic | Automated tests and three scored public replays | Improve overhead vehicle recall. Expand labels to motorcycles, buses, reversing vehicles, stops, and occlusions. |
| FastALPR and review | CPU OCR tests, five controlled crop conditions, and review outcomes from the original demo | Measure Indian and BH-series plate recognition and plate-to-vehicle attribution across more labelled vehicles, camera angles, and lighting. |
| API, PostgreSQL and dashboard | Integration tests, browser flow checks, retention test, and database restore drill | Validate operator workflows and shared-media backup under site policy. |
| RTSP and CPU performance | Local reconnect test, Linux CPU measurements, and headless Windows CI coverage | Run the Windows 11 hardware and Hikvision pilot. Verify camera timestamps and measure end-to-end latency on that setup. |
| Diagnostics | Structured request, crossing, recognition, delivery, and failure logs | Validate per-stage tracking timing and track lifecycle visibility during the pilot. |

The public recordings establish a repeatable starting point for camera qualification. A useful next addition is a per-camera benchmark with agreed thresholds for missed crossings, incorrect plate attribution, and operator review load. Use its results to decide whether to adjust the camera angle, change detector settings, or use a model trained on overhead traffic.

## Three-camera replay

The replay uses three crops of the same silver car from the public Ultralytics demo. Each clip displays its staged camera label. The route, camera positions, and recording times are simulated. Use the Intel CC BY 4.0 recordings for separate tests with real traffic. [Ultralytics source MP4](https://github.com/ultralytics/assets/releases/download/v0.0.0/anpr-demo-video.mp4) · [Intel source videos](https://github.com/intel-iot-devkit/sample-videos/tree/57978890822836f2b4743852f04f62fc511757e4)

| Clip | Camera mode | Expected event | Staged time (IST) |
| --- | --- | --- | --- |
| `demo-gate-in.mp4` | Entry only | Entry | 10:00 |
| `demo-yard.mp4` | Tracking | Sighting | 10:04 |
| `demo-gate-out.mp4` | Exit only | Exit | 10:11 |

Run from the repository root using the worker environment from the [installation instructions](../README.md#1-install-the-windows-worker). These commands create the three MP4s and a manifest under `data/replays/multicamera`. The second command also runs YOLOv8n, ByteTrack, and FastALPR and requires exactly one automatic read of `3066MP4` in each clip. A format specific to the source plate applies to this replay.

```powershell
& $Python -m validation.multicamera_demo
& $Python -m validation.multicamera_demo --process
```

To load the replay into a local demo API, start the API and dashboard with the current schema and use a dashboard administrator. The loader registers the three replay cameras and submits the exit before the entry and yard sighting. It uses the standard outbox delivery code. Generated evidence and previews are copied into the API's shared `data/snapshots` and `data/live` directories by default.

```powershell
$DemoCredential = Get-Credential -UserName gate-admin -Message "Demo dashboard sign-in"
$env:ANPR_DEMO_USERNAME = $DemoCredential.UserName
$env:ANPR_DEMO_PASSWORD = $DemoCredential.GetNetworkCredential().Password
& $Python -m validation.multicamera_demo --process --api-url http://127.0.0.1:8010
Remove-Item Env:ANPR_DEMO_PASSWORD
```

On Linux, substitute `.venv/bin/python` for `& $Python` and set the credentials in the shell environment. For a fresh timeline, pass `--day YYYY-MM-DD`. The default recording day is 19 November 2026.

Open **Vehicle tracking** and find `3066MP4`. Expect **Gate IN → Yard Camera → Gate OUT** in time order and **Gate OUT** as the last confirmed camera. Open the yard event's evidence to inspect the plate crop and OCR observations. **Open camera** shows the chosen camera's latest published frame. A replay camera shows its last recorded preview.

For an on-site camera inside the yard, choose **Tracking / sightings** under **Camera administration** and set its tripwire. Confirmed sightings update the vehicle's camera history. Gate events determine the visit status, counts, and time on site.

### Replay an operator review

The review variant applies blur to the plate area in the yard clip and leaves one sharp frame. OCR proposes `3066MP4` but has too few matching frames for automatic confirmation. The Gate IN and Gate OUT clips retain clear plates. This replay uses 11:00, 11:04, and 11:11 IST and writes its files to `data/replays/multicamera-review`.

With the local demo administrator credentials set as above, load Gate IN and the pending yard sighting.

```powershell
& $Python -m validation.multicamera_demo --review --process --api-url http://127.0.0.1:8010
```

1. In **Vehicle tracking**, search for `3066MP4` and check that it is **Inside** and last confirmed at **Gate IN (Review Replay)**.
2. In **Recognition review**, open the `demo-review-yard` sighting, inspect the plate crop and OCR observations, then verify the registration.
3. Search again in **Vehicle tracking**. The last confirmed camera should now be **Yard Camera (Review Replay)**. The yard event should show **Verified** and the vehicle should remain **Inside**.
4. Release Gate OUT to complete the visit. Search again for the full route and **Exited** status.

```powershell
& $Python -m validation.multicamera_demo --review --release-exit --process --api-url http://127.0.0.1:8010
Remove-Item Env:ANPR_DEMO_PASSWORD
```

Use a later `--day YYYY-MM-DD` in both commands to repeat the exercise with fresh events on the same demo API.
