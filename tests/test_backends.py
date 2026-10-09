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
from pipedream.director import Description, Director, Finding, Verdict, obvious_verdict
from pipedream.imagegen import ImageGenerator, Painter, changed, darkness_png, mask_png
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
    routes[("POST", "/v1/chat/completions")] = chat_reply({"prompt": "An open field before a white house. Exits: north.",
                                                           "landmarks": []})

    described = Director(url).describe_place("West of House", "West of House\nYou are in an open field.")

    assert described == Description("An open field before a white house.", {})
    request = json.loads(seen[0][2])
    assert request["response_format"]["type"] == "json_schema"
    assert "West of House" in request["messages"][1]["content"]


def test_director_keeps_only_new_landmarks_the_game_mentions(server):
    url, routes, seen = server
    # "red tower" isn't in the game's text (made up), and the old mill is already known
    routes[("POST", "/v1/chat/completions")] = chat_reply({"prompt": "A field by a white house and a red tower.",
                                                           "landmarks": ["White House", "red tower", "old mill"]})
    director = Director(url)
    designed = []
    director.design_landmark = lambda name, text: designed.append(name) or "a white clapboard farmhouse"

    described = director.describe_place("West of House", "You are west of a white house near an old mill.", {"old mill"})

    assert described.landmarks == {"white house": "a white clapboard farmhouse"} and designed == ["white house"]
    assert "old mill" not in json.loads(seen[0][2])["messages"][1]["content"].replace("near an old mill", "")


@pytest.mark.parametrize("reply, expected", [
    ({"kind": "change", "target": "trap door", "edit": "Open the trap door."}, Verdict("change", "trap door", "Open the trap door.")),
    ({"kind": "remove", "target": "brass lantern", "edit": ""}, Verdict("remove", "brass lantern", "Remove the brass lantern.")),
    ({"kind": "none", "target": "lamp", "edit": "Remove the lamp."}, Verdict("none")),
    ({"kind": "dark", "target": "", "edit": ""}, Verdict("dark")),
    ({"kind": "change", "target": "", "edit": "Exits: north."}, Verdict("none")),  # nothing left to paint once cleaned
])
def test_director_assesses_changes_in_the_current_place(server, reply, expected):
    url, routes, seen = server
    routes[("POST", "/v1/chat/completions")] = chat_reply(reply)
    living_room = Place("1", "Living Room", "Living Room", prompt="A living room with a rug.",
                        edits=[{"edit": "Drag the rug aside.", "target": "rug"}])

    assert Director(url).assess(living_room, "The troll is knocked out!", "kill troll with sword") == expected
    content = json.loads(seen[0][2])["messages"][1]["content"]
    assert "THE PICTURE SHOWS: A living room with a rug.\nALREADY CHANGED (don't repeat these):\n- Drag the rug aside." in content
    assert "THE PLAYER ACTED ON: troll" in content


@pytest.mark.parametrize("command, reply, verdict", [
    ("take lamp", "Taken.", Verdict("remove", "lamp", "Remove the lamp.")),
    ("open the trophy case", "Opened.", Verdict("change", "trophy case", "Open the trophy case.")),
    ("put painting in case", "Done.", Verdict("change", "case", "Place the painting in the case.")),
    ("open trap door", "The door reluctantly opens to reveal a rickety staircase descending into darkness.",
     Verdict("change", "trap door", "Open the trap door, revealing a rickety staircase descending into darkness.",
             "rickety staircase")),
    ("open window", "With great effort, you open the window far enough to allow entry.",
     Verdict("change", "window", "Open the window.")),
    ("open window", "The window is already open.", None),
    ("move rug", "With a great effort, the rug is moved to one side of the room, revealing the dusty cover of a closed trap door.",
     Verdict("change", "rug", "Replace the rug with bare floor and the dusty cover of a closed trap door; the rug lies "
                              "crumpled at the edge.", "trap door")),
    ("take all", "brass lantern: Taken.", None),
    ("take lamp", "You already have that!", None),
])
def test_obvious_replies_need_no_model(command, reply, verdict):
    assert obvious_verdict(command, reply) == verdict


def test_director_finds_things_in_the_picture(server, tmp_path):
    url, routes, seen = server
    picture = tmp_path / "room.png"
    picture.write_bytes(b"PNG")
    reply = '{"bbox_2d": [252, 276, 370, 603], "surface": "wooden floor"}\n{"bbox_2d": [5, 5, 3, 3]}'
    routes[("POST", "/v1/chat/completions")] = json_reply({"choices": [{"message": {"content": reply}}]})

    # The malformed second box is dropped
    assert Director(url).find(str(picture), "brass lantern") == Finding([[252, 276, 370, 603]], "wooden floor")
    image = json.loads(seen[0][2])["messages"][0]["content"][0]["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(b"PNG").decode()

    routes[("POST", "/v1/chat/completions")] = chat_reply({})
    assert Director(url).find(str(picture), "troll") == Finding([])


@pytest.mark.parametrize("location, expected", [("Field", "Field"), ("none", None), ("", None)])
def test_director_locates_places_for_games_without_titles(server, location, expected):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"location": location})

    assert Director(url).locate("You are in an open field.") == expected


def test_director_keeps_the_complete_sentences_of_a_cut_off_reply(server):
    url, routes, _ = server
    cut_off = '{"prompt": "A dark and damp cellar. A steep metal ramp rises from the floor. Water drips from the'
    routes[("POST", "/v1/chat/completions")] = json_reply({"choices": [{"message": {"content": cut_off}}]})

    assert Director(url).describe_place("Cellar", "Cellar").prompt == "A dark and damp cellar. A steep metal ramp rises from the floor."


def test_director_designs_a_landmark_look_that_fits_mid_sentence(server):
    url, routes, seen = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"look": "A squat white house with a stone base and slate roof."})

    assert Director(url).design_landmark("white house", "west of a white house") == "a squat white house with a stone base and slate roof"
    assert "LANDMARK: white house" in json.loads(seen[0][2])["messages"][1]["content"]


def test_director_keeps_places_reached_from_underground_underground(server):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"setting": "outdoors", "prompt": "The edge of a deep chasm.",
                                                           "landmarks": ["chasm"]})
    cellar = Place("1", "Cellar", "Cellar", setting="underground")

    described = Director(url).describe_place("East of Chasm", "You are on the east edge of a chasm.", came_from=cellar)

    assert described.setting == "underground" and described.landmarks == {}  # and so no landmark either
    assert Director(url).describe_place("Forest", "Sunlight falls through the trees.", came_from=cellar).setting == "outdoors"


def test_director_drops_a_sentence_cut_off_by_the_length_limit(server):
    url, routes, _ = server
    long = "A small bloodstained room with a troll. " * 9 + "The troll is standing in the centre,"
    routes[("POST", "/v1/chat/completions")] = chat_reply({"setting": "indoors", "prompt": long[:400], "landmarks": []})

    assert Director(url).describe_place("The Troll Room", "The Troll Room").prompt.endswith("with a troll.")


def test_director_falls_back_to_the_games_words(server):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = chat_reply({"setting": "indoors", "prompt": "No doors. No windows.",
                                                           "landmarks": []})

    described = Director(url).describe_place("Maze", "Maze\nThis is part of a maze of twisty little passages, all alike.")
    assert described.prompt == "This is part of a maze of twisty little passages, all alike."


def test_director_survives_a_failing_server(server):
    url, routes, _ = server
    routes[("POST", "/v1/chat/completions")] = json_reply({"error": "boom"}, status=500)

    # Without the model, a place is still painted from the game's own words
    assert Director(url).describe_place("Field", "You are in an open field.").prompt == "An open field."
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


def png_of(color, w=64, h=48, box=None, inner=None):
    """A PNG filled with one grey level, optionally with a differently coloured box (0-1000 coordinates)."""
    from PySide6.QtCore import QBuffer, QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(color, color, color))
    if box:
        painter = QPainter(image)
        painter.fillRect(QRect(box[0] * w // 1000, box[1] * h // 1000, (box[2] - box[0]) * w // 1000,
                               (box[3] - box[1]) * h // 1000), QColor(inner, inner, inner))
        painter.end()
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class FakeGenerator:
    width, height = 64, 48

    def __init__(self, results):
        self.results, self.calls = list(results), []

    def generate(self, prompt, reference_images=(), seed=-1, init_image=None, mask=None):
        self.calls.append({"prompt": prompt, "refs": list(reference_images), "seed": seed, "init": init_image, "mask": mask})
        return self.results.pop(0)


def test_painter_edits_keep_the_style_and_only_repaint_the_boxes(tmp_path):
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100))
    lamp = [250, 250, 500, 500]
    generator = FakeGenerator([png_of(100, box=lamp, inner=20)])

    edited = Painter(generator, str(tmp_path), "Ink", seed=7).edit(str(base), "Remove the lamp.", [lamp])

    call, = generator.calls
    assert call["prompt"] == "Ink. Remove the lamp. Keep everything else exactly the same."
    assert call["refs"] == [str(base)] and call["init"] == str(base) and call["seed"] == 7
    assert call["mask"] == mask_png(64, 48, [lamp])
    from PySide6.QtGui import QImage
    result = QImage(edited)
    assert abs(result.pixelColor(24, 18).value() - 20) <= 4  # the middle of the box comes from the edit
    assert result.pixelColor(62, 46).value() == 100  # far from it, the original is kept


def test_pasting_keeps_the_original_outside_the_boxes(tmp_path):
    from PySide6.QtGui import QImage
    from pipedream.imagegen import paste
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100, 480, 360))
    # The model's output: the box repainted, and everything else drifted slightly
    edited = png_of(97, 480, 360, box=[250, 250, 500, 500], inner=20)
    pasted = QImage.fromData(paste(str(base), edited, [[250, 250, 500, 500]]))

    assert pasted.pixelColor(180, 135).value() == 20
    assert all(pasted.pixelColor(x, y).value() == 100 for x, y in ((0, 0), (479, 359), (400, 40), (40, 300)))


def test_an_unconfined_painter_edits_the_whole_picture_but_keeps_only_real_changes(tmp_path):
    from PySide6.QtGui import QImage
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100, 480, 360))
    sword = [250, 250, 500, 500]
    # The model removes the sword (a dark patch here) and shifts everything else very slightly
    generator = FakeGenerator([png_of(97, 480, 360, box=sword, inner=20)])
    generator.width, generator.height = 480, 360

    edited = Painter(generator, str(tmp_path), "Ink", confine=False).edit(str(base), "Remove the sword.", [sword])

    call, = generator.calls
    assert call["mask"] is None and call["init"] is None and call["refs"] == [str(base)]
    result = QImage(edited)
    assert result.pixelColor(180, 135).value() == 20 and result.pixelColor(10, 10).value() == 100


def test_painter_retries_an_edit_that_changed_nothing(tmp_path):
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100))
    generator = FakeGenerator([png_of(104), png_of(100, box=[0, 0, 500, 500], inner=200)])

    edited = Painter(generator, str(tmp_path), "Ink", seed=7).edit(str(base), "Open the window.", attempts=2)

    assert [c["seed"] for c in generator.calls] == [7, 8] and generator.calls[0]["mask"] is None
    assert open(edited, "rb").read() == png_of(100, box=[0, 0, 500, 500], inner=200)


def test_painter_gives_up_on_an_edit_that_never_shows(tmp_path):
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100))
    lamp = [250, 250, 500, 500]
    # A removal that repaints the lamp in place changes its box a little, which isn't enough
    generator = FakeGenerator([png_of(100, box=lamp, inner=140), png_of(100, box=lamp, inner=60)])

    assert Painter(generator, str(tmp_path), "Ink").edit(str(base), "Remove the lamp.", [lamp], min_change=80, attempts=2) is None
    assert len(generator.calls) == 2


def test_changed_measures_only_inside_the_boxes(tmp_path):
    base = tmp_path / "base.png"
    base.write_bytes(png_of(100))
    elsewhere = png_of(100, box=[600, 600, 1000, 1000], inner=250)

    assert changed(str(base), elsewhere, [[0, 0, 400, 400]]) == 0
    assert changed(str(base), elsewhere) > 100


def test_mask_covers_each_box_with_a_margin():
    from PySide6.QtGui import QImage
    mask = QImage.fromData(mask_png(200, 100, [[250, 250, 500, 500]]))
    assert mask.pixelColor(75, 37).value() == 255  # inside the box
    assert mask.pixelColor(46, 22).value() == 255  # in the margin
    assert mask.pixelColor(150, 80).value() == 0


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
