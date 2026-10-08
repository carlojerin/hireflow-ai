# Third-party files served from our own server

The proctoring features (camera face checks, phone detection) load their AI models and runtimes from here instead of the internet, so a
candidate whose network blocks a public CDN is still proctored. The code (`js/face-proctor.js`, `js/phone-detector.js`) uses these files first and
falls back to the original internet addresses only if a file here is missing.

| Folder / file | What | Version | Source it was downloaded from | Licence |
|---|---|---|---|---|
| `mediapipe/vision_bundle.js` (+ `wasm/`) | MediaPipe Tasks Vision runtime (face landmarks) | 0.10.14 | `cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14` | Apache-2.0 |
| `mediapipe/face_landmarker.task` | MediaPipe Face Landmarker model (float16) | 1 | `storage.googleapis.com/mediapipe-models/face_landmarker/...` | Apache-2.0 |
| `onnx/` | ONNX Runtime Web (`ort.all.min.js` + the `.wasm` files) | 1.18.0 | `cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist` | MIT |
| `models/yolov8n.onnx` | YOLOv8n object detector (the "cell phone" class) | Ultralytics export | `raw.githubusercontent.com/Hyuto/yolov8-onnxruntime-web` | **AGPL-3.0** (Ultralytics) |

| `bootstrap/bootstrap.min.css`, `bootstrap/bootstrap.bundle.min.js` | Bootstrap (layout, components) | 5.3.3 | `cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist` | MIT |
| `bootstrap-icons/bootstrap-icons.min.css` + `fonts/` | Bootstrap Icons (the icon font) | 1.11.3 | `cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font` | MIT |
| `livekit/livekit-client.umd.min.js` | LiveKit browser client (the role-play call) | 2.22.3 | `cdn.jsdelivr.net/npm/livekit-client@2.22.3/dist` | Apache-2.0 |

Every page uses the Bootstrap and icon copies above. The AI interview page (`ai-interview-v2.html`) is deliberately left exactly as it was and still loads Bootstrap and LiveKit
from the CDN. Still loaded from the internet on purpose: the code editor (Monaco) on the coding page and the Google web fonts (the pages fall back to system fonts without them).

**Licence note for the phone model:** Ultralytics YOLOv8 weights are AGPL-3.0. Using them in a product you sell or host for others can require
either publishing the product's source under the AGPL or buying an Ultralytics enterprise licence. Check this before commercial use; a
permissively licensed detector can replace it behind the same file path.

`vision_bundle.js` is the package's `vision_bundle.mjs`, renamed so every static file server sends it with a JavaScript content type.

To update a file, download the new version from the same place and replace it here; keep the names. Not bundled here: the `.wasm` threaded
ONNX variants (the pages are not cross-origin isolated, so they are never requested).
