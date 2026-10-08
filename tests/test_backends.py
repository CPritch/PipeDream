import base64
import hashlib
import http.server
import io
import json
import os
import platform
import threading
import zipfile

import pytest

from pipedream import runtime
from pipedream.director import Director
from pipedream.imagegen import ImageGenerator, Painter, darkness_png
from pipedream.world import Place


@pytest.fixture
def server():
    """A local HTTP server whose responses are set per (method, path) in `routes`."""
    routes, seen = {}, []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self, method):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            seen.append((method, self.path, body))
            status, payload, headers = routes[(method, self.path)](self.headers)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", routes, seen
    httpd.shutdown()


def json_reply(data, status=200):
    return lambda headers: (status, json.dumps(data).encode(), {"Content-Type": "application/json"})


def chat_reply(scene):
    return json_reply({"choices": [{"message": {"content": json.dumps(scene)}}]})


def test_director_describes_a_new_place_without_directions(server):
    url, routes, seen = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"prompt": "An open field before a white house. Exits: north."})

    prompt = Director(url).describe_place("West of House", "West of House\nYou are in an open field.")

    assert prompt == "An open field before a white house."
    request = json.loads(seen[0][2])
    assert request["response_format"]["type"] == "json_schema"
    assert "West of House" in request["messages"][1]["content"]


@pytest.mark.parametrize("reply, expected", [
    ({"kind": "change", "edit": "The trap door stands open."}, ("change", "The trap door stands open.")),
    ({"kind": "none", "edit": "The lamp is gone."}, ("none", "")),
    ({"kind": "dark", "edit": ""}, ("dark", "")),
    ({"kind": "change", "edit": "Exits: north."}, ("none", "")),  # nothing left to paint once cleaned
])
def test_director_assesses_changes_in_the_current_place(server, reply, expected):
    url, routes, seen = server
    routes[("POST", "/v1/chat/completions")] = chat_reply(reply)
    living_room = Place("1", "Living Room", "Living Room", prompt="A living room with a rug.", edits=["The rug is aside."])

    assert Director(url).assess(living_room, "The door reluctantly opens.", "open trap door") == expected
    content = json.loads(seen[0][2])["messages"][1]["content"]
    assert "A living room with a rug. The rug is aside." in content and "open trap door" in content


@pytest.mark.parametrize("location, expected", [("Field", "Field"), ("none", None), ("", None)])
def test_director_locates_places_for_games_without_titles(server, location, expected):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"location": location})

    assert Director(url).locate("You are in an open field.") == expected


def test_director_keeps_the_complete_sentences_of_a_cut_off_reply(server):
    url, routes, _ = server
    cut_off = '{"prompt": "A dark and damp cellar. A steep metal ramp rises from the floor. Water drips from the'
    routes[("POST", "/v1/chat/completions")] = json_reply({"choices": [{"message": {"content": cut_off}}]})

    assert Director(url).describe_place("Cellar", "Cellar") == "A dark and damp cellar. A steep metal ramp rises from the floor."


def test_director_survives_a_failing_server(server):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = json_reply({"error": "boom"}, status=500)

    assert Director(url).describe_place("Field", "You are in an open field.") is None
    assert Director(url).locate("You are in an open field.") is None


def test_image_generator_polls_the_job_and_sends_references(server, tmp_path):
    url, routes, seen = server
    reference = tmp_path / "previous.png"
    reference.write_bytes(b"previous image")
    statuses = iter([{"status": "generating"},
                     {"status": "completed", "result": {"images": [{"b64_json": base64.b64encode(b"PNG").decode()}]}}])
    routes[("POST", "/sdcpp/v1/img_gen")] = json_reply({"id": "job_1"}, status=202)
    routes[("GET", "/sdcpp/v1/jobs/job_1")] = lambda headers: json_reply(next(statuses))(headers)

    image = ImageGenerator(url, runtime.FOUR_STEP).generate("A dark forest.", [str(reference)])

    assert image == b"PNG"
    request = json.loads(seen[0][2])
    assert request["sample_params"] == runtime.FOUR_STEP
    assert request["ref_images"] == [base64.b64encode(b"previous image").decode()]


def test_image_generator_raises_when_the_job_fails(server):
    url, routes, _ = server
    routes[("POST", "/sdcpp/v1/img_gen")] = json_reply({"id": "job_1"}, status=202)
    routes[("GET", "/sdcpp/v1/jobs/job_1")] = json_reply({"status": "failed", "error": {"message": "out of memory"}})

    with pytest.raises(RuntimeError, match="out of memory"):
        ImageGenerator(url, runtime.FOUR_STEP).generate("A dark forest.")


def test_painter_edits_keep_the_style_and_send_the_picture(tmp_path):
    calls = []

    class Generator:
        width, height = 64, 48

        def generate(self, prompt, reference_images=(), seed=-1):
            calls.append((prompt, list(reference_images), seed))
            return b"PNG"

    painter = Painter(Generator(), str(tmp_path), "Ink", seed=7)
    base = painter.paint("A cellar.")
    edited = painter.edit(base, "The trap door is open.")

    assert calls == [("Ink. A cellar.", [], 7), ("Ink. The trap door is open. Keep everything else exactly the same.", [base], 7)]
    assert open(edited, "rb").read() == b"PNG" and edited != base


def test_darkness_is_a_valid_near_black_png():
    from PySide6.QtGui import QImage

    image = QImage.fromData(darkness_png(64, 48))
    assert (image.width(), image.height()) == (64, 48)
    assert max(image.pixelColor(x, y).value() for x in range(0, 64, 8) for y in range(0, 48, 8)) < 20


def serve_file(routes, path, data, fail_ranges=()):
    def handler(headers):
        if not headers.get("Range"):
            return 200, data, {}
        start, end = map(int, headers["Range"].split("=")[1].split("-"))
        if start in fail_ranges:
            return 503, b"", {}
        return 206, data[start:end + 1], {}
    routes[("GET", path)] = handler


def item_for(url, name, data, sha256=None):
    return runtime.Download(name, f"{url}/{name}", len(data), sha256 or hashlib.sha256(data).hexdigest())


def test_download_resumes_only_the_missing_chunks(server, tmp_path, monkeypatch):
    url, routes, seen = server
    monkeypatch.setattr(runtime, "CHUNK_SIZE", 1_000_000)
    data = os.urandom(3_500_000)
    dest = tmp_path / "model.gguf"
    item = item_for(url, "model.gguf", data)

    serve_file(routes, "/model.gguf", data, fail_ranges={2_000_000})
    with pytest.raises(Exception):
        runtime.download(item, str(dest), progress=lambda msg: None)
    requests_before_resume = len(seen)

    serve_file(routes, "/model.gguf", data)
    runtime.download(item, str(dest), progress=lambda msg: None)

    assert dest.read_bytes() == data
    assert [path for _, path, _ in seen[requests_before_resume:]] == ["/model.gguf"]  # just the failed chunk
    assert list(tmp_path.iterdir()) == [dest]


def test_download_rejects_a_checksum_mismatch(server, tmp_path):
    url, routes, _ = server
    serve_file(routes, "/model.gguf", b"tampered")

    with pytest.raises(RuntimeError, match="corrupt"):
        runtime.download(item_for(url, "model.gguf", b"tampered", sha256="0" * 64), str(tmp_path / "model.gguf"),
                         progress=lambda msg: None)
    assert list(tmp_path.iterdir()) == []


# Stands in for sd-cli's convert mode: copies -m to -o with a marker so the test can tell it ran
FAKE_SD_CLI = """#!/bin/sh
while [ $# -gt 0 ]; do case $1 in -m) src=$2;; -o) dst=$2;; esac; shift; done
{ printf 'quantized:'; cat "$src"; } > "$dst"
"""


@pytest.mark.parametrize("quantize", ["", "q8_0"])
def test_install_fetches_everything_once(server, tmp_path, monkeypatch, quantize):
    url, routes, seen = server
    archives = {}
    for kind, binary in runtime.SERVER_BINARIES.items():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr(f"build/bin/{binary}{runtime.EXE}", "#!/bin/sh\n")
            if kind == "sd":
                z.writestr(f"build/bin/sd-cli{runtime.EXE}", FAKE_SD_CLI)
        archives[kind] = buffer.getvalue()
        serve_file(routes, f"/{kind}.zip", archives[kind])
    models = {role: f"{role} weights".encode() for role in ("diffusion", "vae", "llm")}
    for role, data in models.items():
        serve_file(routes, f"/{role}.safetensors", data)

    monkeypatch.setattr(runtime, "RUNTIMES_DIR", str(tmp_path / "runtimes"))
    monkeypatch.setattr(runtime, "MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(runtime, "RUNTIMES", {(platform.system(), platform.machine()): {
        kind: item_for(url, f"{kind}.zip", data) for kind, data in archives.items()}})
    files = {role: item_for(url, f"{role}.safetensors", data) for role, data in models.items()}
    monkeypatch.setattr(runtime, "PROFILES", {"test": runtime.ImageProfile(files, runtime.FOUR_STEP, quantize)})

    paths = runtime.install("test", progress=lambda msg: None)
    requests_after_first_install = len(seen)
    assert runtime.install("test", progress=lambda msg: None) == paths

    assert requests_after_first_install == len(archives) + len(models)
    assert len(seen) == requests_after_first_install
    assert os.path.basename(paths["sd"]) == "sd-server" + runtime.EXE
    assert os.access(paths["llama"], os.X_OK)
    assert open(paths["vae"], "rb").read() == models["vae"]
    if quantize:
        assert paths["diffusion"].endswith("-q8_0.gguf")
        assert open(paths["diffusion"], "rb").read() == b"quantized:" + models["diffusion"]
        assert not (tmp_path / "models" / "diffusion.safetensors").exists()
    else:
        assert open(paths["diffusion"], "rb").read() == models["diffusion"]


@pytest.mark.parametrize("devices, expected", [
    (["CUDA0"], "mage-flow"),
    (["Vulkan0", "Vulkan1"], runtime.FALLBACK_PROFILE),
    ([], runtime.FALLBACK_PROFILE),
])
def test_mage_flow_falls_back_where_it_cannot_render(devices, expected):
    assert runtime.choose_profile("mage-flow", devices, progress=lambda msg: None) == expected


def test_models_without_device_limits_are_never_swapped():
    assert runtime.choose_profile("qwen-image", ["Vulkan0"], progress=lambda msg: None) == "qwen-image"
