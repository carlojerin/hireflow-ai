"""The proctoring AI models and their runtimes are served from our own server (frontend/vendor), the internet is only a fallback."""

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "frontend", "vendor")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _head(path, n=4):
    with open(path, "rb") as f:
        return f.read(n)


# (relative path, minimum size in bytes, expected first bytes or None)
FILES = [
    ("mediapipe/vision_bundle.js", 100_000, None),
    ("mediapipe/wasm/vision_wasm_internal.js", 100_000, None),
    ("mediapipe/wasm/vision_wasm_internal.wasm", 9_000_000, b"\x00asm"),
    ("mediapipe/wasm/vision_wasm_nosimd_internal.js", 100_000, None),
    ("mediapipe/wasm/vision_wasm_nosimd_internal.wasm", 9_000_000, b"\x00asm"),
    ("mediapipe/face_landmarker.task", 3_000_000, None),
    ("onnx/ort.all.min.js", 1_000_000, None),
    ("onnx/ort-wasm-simd.wasm", 10_000_000, b"\x00asm"),
    ("onnx/ort-wasm-simd.jsep.wasm", 18_000_000, b"\x00asm"),
    ("onnx/ort-wasm.wasm", 9_000_000, b"\x00asm"),
    ("models/yolov8n.onnx", 12_000_000, None),
]


@pytest.mark.parametrize("name,minimum,magic", FILES)
def test_every_bundled_file_is_present_and_complete(name, minimum, magic):
    path = os.path.join(VENDOR, *name.split("/"))

    assert os.path.isfile(path), f"{name} is missing: proctoring would silently fall back to the internet"
    assert os.path.getsize(path) >= minimum, f"{name} looks truncated"

    if magic:
        assert _head(path, len(magic)) == magic, f"{name} is not a WebAssembly file"


def test_the_face_model_is_a_task_archive_and_the_bundle_is_a_real_module():
    assert b"PK\x03\x04" in _head(os.path.join(VENDOR, "mediapipe", "face_landmarker.task"), 16)         # a MediaPipe .task is a zip

    bundle = _read("frontend", "vendor", "mediapipe", "vision_bundle.js")
    assert "FaceLandmarker" in bundle and "FilesetResolver" in bundle


def test_the_phone_model_has_the_input_and_output_the_detector_decodes():
    ort = pytest.importorskip("onnxruntime")

    session = ort.InferenceSession(os.path.join(VENDOR, "models", "yolov8n.onnx"), providers=["CPUExecutionProvider"])
    (inp,), (out,) = session.get_inputs(), session.get_outputs()

    assert inp.name == "images" and list(inp.shape) == [1, 3, 640, 640]
    assert out.name == "output0" and list(out.shape) == [1, 84, 8400]                                       # 4 box values + 80 class scores per anchor


def test_the_face_checker_uses_its_own_files_first_and_the_internet_only_as_a_fallback():
    js = _read("frontend", "js", "face-proctor.js")

    assert 'new URL("vendor/mediapipe/", document.baseURI)' in js
    assert js.index('name: "local"') < js.index('name: "cdn"')
    assert "fromSource(SOURCES[0]).catch(" in js and "fromSource(SOURCES[1])" in js                           # local first, then the whole CDN set
    assert js.count("cdn.jsdelivr.net") == 2 and "storage.googleapis.com" in js                              # only inside the fallback source


def test_the_phone_detector_picks_one_complete_set_and_defaults_to_its_own_files():
    js = _read("frontend", "js", "phone-detector.js")

    assert "let ORT_SCRIPT_URL = SOURCES.local.script;" in js and "let YOLO_MODEL_URL = SOURCES.local.model;" in js
    assert "Number(response.headers.get(\"content-length\")) > 1000000" in js                                  # a missing file or an error page does not count
    assert "chooseSources()" in js[js.index("function loadModel()"):]                                         # chosen before the script is loaded
    assert js.index("local: {") < js.index("cdn: {")


def test_the_test_pages_load_the_updated_detectors_and_the_licences_are_recorded():
    for page in ("aptitude-test", "sjt-test", "tda-test", "programming-test", "programming-v2", "programming-v3", "ai-interview-v2", "assessment"):
        html = _read("frontend", f"{page}.html")
        assert "face-proctor.js?v=4" in html and "phone-detector.js?v=6" in html, page

    readme = _read("frontend", "vendor", "README.md")
    assert "AGPL-3.0" in readme and "Apache-2.0" in readme and "MIT" in readme and "falls back to the original internet addresses" in readme
