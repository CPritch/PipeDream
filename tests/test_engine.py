import shutil
import sys
import threading
from pathlib import Path

import pytest

from pipedream import games
from pipedream.engine import PipeDream, session_name

MOCK_GAME = Path(games.__file__).parent / "mock_game.py"

FIELD = "You are standing in an open field west of a white house."
FOREST = "You are deep in a dark, ancient forest."
HOUSE = "You are inside the white house."


class FakePainter:
    """Stands in for the image model: every picture is an empty file named after what was asked for."""

    def __init__(self, folder, delay=None):
        self.folder, self.delay, self.edits = Path(folder), delay, True
        self.painted, self.edited = [], []

    def paint(self, prompt, seed=None):
        if self.delay:
            self.delay()
        self.painted.append(prompt)
        return self._file(f"paint{len(self.painted)}")

    def edit(self, image, instruction):
        self.edited.append((Path(image).name, instruction))
        return self._file(f"edit{len(self.edited)}")

    def darkness(self):
        return self._file("dark")

    def _file(self, name):
        path = self.folder / f"{name}.png"
        path.touch()
        return str(path)


def stub_ai(engine, tmp_path, changes=None, delay=None):
    """No network: the director answers from `changes` ({command: (kind, edit)}) and pictures are fake files."""
    changes = changes or {}
    engine.director.describe_place = lambda name, text: f"picture of {name}"
    engine.director.assess = lambda place, text, command: changes.get(command, ("none", ""))
    engine.director.locate = lambda text: None
    engine.painter = FakePainter(tmp_path, delay)
    return engine.painter


@pytest.fixture
def demo_argv(tmp_path):
    # The space in the path catches the command being re-split on whitespace
    game_dir = tmp_path / "my games"
    game_dir.mkdir()
    shutil.copy(MOCK_GAME, game_dir / "mock_game.py")
    return [sys.executable, str(game_dir / "mock_game.py")]


def play(argv, cache_dir, commands):
    """Run the demo game through the real engine with the AI calls stubbed out.

    No GUI callbacks are set, which is how the plain `pipedream` CLI runs.
    """
    engine = PipeDream(argv, cache_dir=cache_dir)
    printed, shown = [], []
    replies = iter(commands + ["quit"])

    engine.custom_print = printed.append
    engine.custom_input = lambda prompt: next(replies)
    engine.custom_image = shown.append
    painter = stub_ai(engine, cache_dir)
    engine.start()
    engine.scene_thread.join(10)
    return engine, "\n".join(printed), painter, shown


def test_demo_walkthrough_paints_each_place_once(demo_argv, tmp_path):
    engine, output, painter, shown = play(demo_argv, tmp_path, ["north", "south", "north", "south", "east", "west"])

    for description in (FIELD, FOREST, HOUSE):
        assert description in output
    assert painter.painted == ["picture of Open Field", "picture of Dark Forest", "picture of Inside the House"]
    assert [Path(p).name for p in shown] == ["paint1.png", "paint2.png", "paint1.png", "paint2.png", "paint1.png",
                                              "paint3.png", "paint1.png"]

    places = {p.name: p for p in engine.world.places.values()}
    field, forest, house = places["Open Field"], places["Dark Forest"], places["Inside the House"]
    assert field.exits == {"north": forest.id, "east": house.id}
    assert forest.exits == {"south": field.id}
    assert house.exits == {"west": field.id}


def test_relaunch_reuses_the_saved_world(demo_argv, tmp_path):
    first, _, _, _ = play(demo_argv, tmp_path, ["north"])
    second, _, painter, shown = play(demo_argv, tmp_path, ["north"])

    assert painter.painted == []
    assert [Path(p).name for p in shown] == ["paint1.png", "paint2.png"]
    assert second.world.places.keys() == first.world.places.keys()


def test_echo_strip_only_removes_the_echoed_command(tmp_path):
    engine = PipeDream(["true"], cache_dir=tmp_path)
    engine.last_input = "n"

    assert engine.clean_output("n\r\nYou are in a forest.") == "You are in a forest."
    assert engine.clean_output("You are in a forest.\nPaths lead north.") == "You are in a forest.\nPaths lead north."


def test_game_line_wrapping_is_undone(tmp_path):
    engine = PipeDream(["true"], cache_dir=tmp_path)
    wrapped = ("Copyright (c) 1981, 1982, 1983, 1984, 1985, 1986 Infocom, Inc. All rights\r\nreserved.\r\n"
               "ZORK is a registered trademark of Infocom, Inc.\r\n\r\nWest of House\r\n"
               "You are standing in an open field west of a white house, with a boarded front\r\ndoor.\r\n"
               "There is a small mailbox here.")

    assert engine.clean_output(wrapped) == (
        "Copyright (c) 1981, 1982, 1983, 1984, 1985, 1986 Infocom, Inc. All rights reserved.\n"
        "ZORK is a registered trademark of Infocom, Inc.\n\nWest of House\n"
        "You are standing in an open field west of a white house, with a boarded front door.\n"
        "There is a small mailbox here.")


def test_session_names_are_short_and_readable():
    name = session_name(["/usr/bin/dfrotz", "-q", "/home/me/My Games/zork1.z3"], None)

    assert name.startswith("zork1-z3-") and len(name) < 60
    assert name != session_name(["/usr/bin/dfrotz", "-q", "/home/me/My Games/zork1.z3"], "Pencil sketch")


# Replies from Zork I, as the engine sees them after reflowing
WEST_OF_HOUSE = ("ZORK I: The Great Underground Empire\nRelease 119 / Serial number 880429\n\nWest of House\n"
                 "You are standing in an open field west of a white house, with a boarded front door.\n"
                 "There is a small mailbox here.")
NORTH_OF_HOUSE = ("North of House\nYou are facing the north side of a white house. There is no door here, and all "
                  "the windows are boarded up. To the north a narrow path winds through the trees.")
LIVING_ROOM = ("Living Room\nYou are in the living room. There is a doorway to the east, a wooden door with strange "
               "gothic lettering to the west, which appears to be nailed shut, a trophy case, and a large oriental "
               "rug in the center of the room.")
DARK = ("You have moved into a dark place.\nThe trap door crashes shut, and you hear someone barring it.\n\n"
        "It is pitch black. You are likely to be eaten by a grue.")
CELLAR = ("The brass lantern is now on.\n\nCellar\nYou are in a dark and damp cellar with a narrow passageway "
          "leading north, and a crawlway to the south. On the west is the bottom of a steep metal ramp which is "
          "unclimbable.")


def feed(engine, replies):
    """Send (command, reply) pairs straight through the scene pipeline, as if the game had printed them."""
    for command, text in replies:
        engine.latest_scene += 1
        engine.trigger_pipeline(engine.latest_scene, text, command)


def test_brief_revisits_reuse_the_place_and_any_route_back(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path)
    shown = []
    engine.custom_image = shown.append

    # Zork prints only the title when you come back, and "south" is not the way back from North of House
    feed(engine, [(None, WEST_OF_HOUSE), ("north", NORTH_OF_HOUSE), ("east", "Behind House\nYou are behind the "
          "white house. A path leads into the forest to the east. In one corner of the house there is a small "
          "window which is slightly ajar."), ("south", "South of House\nYou are facing the south side of a white "
          "house. There is no door here, and all the windows are boarded."), ("west", "West of House"),
          ("north", "North of House")])

    assert painter.painted == ["picture of West of House", "picture of North of House", "picture of Behind House",
                               "picture of South of House"]
    assert [Path(p).name for p in shown][-2:] == ["paint1.png", "paint2.png"]


def test_changes_in_a_place_edit_its_picture(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"move rug": ("change", "The rug is heaped aside over a trap door.")})
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("take lamp", "Taken."), ("move rug", "With a great effort, the rug is "
          "moved to one side of the room, revealing the dusty cover of a closed trap door."), ("look", LIVING_ROOM)])

    assert painter.edited == [("paint1.png", "The rug is heaped aside over a trap door.")]
    living_room = engine.world.current
    assert living_room.edits == ["The rug is heaped aside over a trap door."]
    assert Path(living_room.image).name == "edit1.png"
    assert [Path(p).name for p in shown] == ["paint1.png", "edit1.png", "edit1.png"]


def test_darkness_is_shown_as_darkness_and_light_finds_the_place(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path)
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("down", DARK), ("turn on lamp", CELLAR)])

    assert [Path(p).name for p in shown] == ["paint1.png", "dark.png", "paint2.png"]
    assert painter.painted == ["picture of Living Room", "picture of Cellar"]
    assert engine.world.current.name == "Cellar"


def test_the_known_route_never_overrides_the_game(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    stub_ai(engine, tmp_path)
    shown = []
    engine.custom_image = shown.append

    # The first time down was dark; the second time the lamp is lit and the game names the Cellar
    feed(engine, [(None, LIVING_ROOM), ("down", DARK), ("turn on lamp", CELLAR), ("up", "Living Room"),
                  ("down", "The trap door crashes shut, and you hear someone barring it.\n\nCellar\n"
                           "Your sword is glowing with a faint blue glow.")])

    assert [Path(p).name for p in shown][-1] == "paint2.png"


def test_repaint_paints_the_place_again_and_reapplies_its_edits(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"open window": ("change", "The window is wide open.")})
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("open window", "With great effort, you open the window far enough.")])
    engine._repaint()

    assert painter.painted == ["picture of Living Room", "picture of Living Room"]
    assert painter.edited == [("paint1.png", "The window is wide open."), ("paint2.png", "The window is wide open.")]
    assert Path(shown[-1]).name == "edit2.png"


def test_slow_images_never_block_the_game_or_show_stale_scenes(demo_argv, tmp_path):
    engine = PipeDream(demo_argv, cache_dir=tmp_path)
    all_commands_sent = threading.Event()
    commands = iter(["north", "south", "quit"])
    shown, input_was_free = [], []

    def next_command(prompt):
        command = next(commands)
        if command == "quit":
            all_commands_sent.set()
        return command

    def slow():
        # If painting blocked the game, the remaining commands could never be sent while we wait here
        input_was_free.append(all_commands_sent.wait(5))

    engine.custom_print = lambda text: None
    engine.custom_input = next_command
    engine.custom_image = shown.append
    stub_ai(engine, tmp_path, delay=slow)
    engine.start()
    engine.scene_thread.join(15)

    assert input_was_free and all(input_was_free)
    # The opening field finished after the player had already walked north and back, so only the field is shown,
    # and the forest the player only passed through isn't painted until they return
    assert [Path(p).name for p in shown] == ["paint1.png"]
    assert engine.painter.painted == ["picture of Open Field"]
