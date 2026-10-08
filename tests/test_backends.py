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
from pipedream.imagegen import ImageGenerator, TURBO_SAMPLING


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


def test_director_returns_styled_prompt_for_a_new_scene(server):
    url, routes, seen = server
    routes[("POST", "/v1/chat/completions")] = chat_reply(
        {"category": "scene_changed", "location": "Field", "prompt": "An open field before a white house."})

    prompt = Director(None, url, style_prompt="Pencil sketch").describe_scene("You are in an open field.")

    assert prompt == "Pencil sketch. An open field before a white house."
    request = json.loads(seen[0][2])
    assert request["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("category", ["meta", "action_failed", "no_change"])
def test_director_returns_none_without_a_scene_change(server, category):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"category": category, "location": "Field", "prompt": ""})

    assert Director(None, url).describe_scene("You are carrying nothing.") is None


def test_director_survives_a_failing_server(server):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = json_reply({"error": "boom"}, status=500)

    assert Director(None, url).describe_scene("You are in an open field.") is None


def test_image_generator_polls_the_job_and_sends_references(server, tmp_path):
    url, routes, seen = server
    reference = tmp_path / "previous.png"
    reference.write_bytes(b"previous image")
    statuses = iter([{"status": "generating"},
                     {"status": "completed", "result": {"images": [{"b64_json": base64.b64encode(b"PNG").decode()}]}}])
    routes[("POST", "/sdcpp/v1/img_gen")] = json_reply({"id": "job_1"}, status=202)
    routes[("GET", "/sdcpp/v1/jobs/job_1")] = lambda headers: json_reply(next(statuses))(headers)

    image = ImageGenerator(url).generate("A dark forest.", [str(reference)])

    assert image == b"PNG"
    request = json.loads(seen[0][2])
    assert request["sample_params"] == TURBO_SAMPLING
    assert request["ref_images"] == [base64.b64encode(b"previous image").decode()]


def test_image_generator_raises_when_the_job_fails(server):
    url, routes, _ = server
    routes[("POST", "/sdcpp/v1/img_gen")] = json_reply({"id": "job_1"}, status=202)
    routes[("GET", "/sdcpp/v1/jobs/job_1")] = json_reply({"status": "failed", "error": {"message": "out of memory"}})

    with pytest.raises(RuntimeError, match="out of memory"):
        ImageGenerator(url).generate("A dark forest.")


def serve_file(routes, path, data):
    def handler(headers):
        start = int(headers["Range"].split("=")[1].rstrip("-")) if headers.get("Range") else 0
        return (206 if start else 200), data[start:], {}
    routes[("GET", path)] = handler


def item_for(url, name, data, sha256=None):
    return runtime.Download(name, f"{url}/{name}", len(data), sha256 or hashlib.sha256(data).hexdigest())


def test_download_resumes_a_partial_file(server, tmp_path):
    url, routes, seen = server
    data = os.urandom(3_000_000)
    serve_file(routes, "/model.gguf", data)
    dest = tmp_path / "model.gguf"
    (tmp_path / "model.gguf.part").write_bytes(data[:1_000_000])

    runtime.download(item_for(url, "model.gguf", data), str(dest), progress=lambda msg: None)

    assert dest.read_bytes() == data
    assert not (tmp_path / "model.gguf.part").exists()


def test_download_rejects_a_checksum_mismatch(server, tmp_path):
    url, routes, _ = server
    serve_file(routes, "/model.gguf", b"tampered")

    with pytest.raises(RuntimeError, match="corrupt"):
        runtime.download(item_for(url, "model.gguf", b"tampered", sha256="0" * 64), str(tmp_path / "model.gguf"),
                         progress=lambda msg: None)
    assert list(tmp_path.iterdir()) == []


def test_install_fetches_everything_once(server, tmp_path, monkeypatch):
    url, routes, seen = server
    archives = {}
    for kind, binary in runtime.SERVER_BINARIES.items():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr(f"build/bin/{binary}{runtime.EXE}", "#!/bin/sh\n")
        archives[kind] = buffer.getvalue()
        serve_file(routes, f"/{kind}.zip", archives[kind])
    models = {kind: f"{kind} weights".encode() for kind in runtime.MODELS}
    for kind, data in models.items():
        serve_file(routes, f"/{kind}.gguf", data)

    monkeypatch.setattr(runtime, "RUNTIMES_DIR", str(tmp_path / "runtimes"))
    monkeypatch.setattr(runtime, "MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(runtime, "RUNTIMES", {(platform.system(), platform.machine()): {
        kind: item_for(url, f"{kind}.zip", data) for kind, data in archives.items()}})
    monkeypatch.setattr(runtime, "MODELS", {kind: item_for(url, f"{kind}.gguf", data) for kind, data in models.items()})

    paths = runtime.install(progress=lambda msg: None)
    requests_after_first_install = len(seen)
    assert runtime.install(progress=lambda msg: None) == paths

    assert requests_after_first_install == len(archives) + len(models)
    assert len(seen) == requests_after_first_install
    assert os.path.basename(paths["sd"]) == "sd-server" + runtime.EXE
    assert os.access(paths["llama"], os.X_OK)
    assert open(paths["vae"], "rb").read() == models["vae"]
