import re
import shutil
import sys
import threading
from pathlib import Path

import pytest

from pipedream import games
from pipedream.director import NO_CHANGE, Description, Finding, Verdict
from pipedream.engine import PipeDream, session_name
from pipedream.runtime import PROFILES
from pipedream.world import World

MOCK_GAME = Path(games.__file__).parent / "mock_game.py"

FIELD = "You are standing in an open field west of a white house."
FOREST = "You are deep in a dark, ancient forest."
HOUSE = "You are inside the white house."


class FakePainter:
    """Stands in for the image model: every picture is an empty file named after what was asked for."""

    def __init__(self, folder, delay=None):
        self.folder, self.delay, self.edits, self.confine = Path(folder), delay, True, True
        self.painted, self.edited, self.kept = [], [], []
        self.removed = {}  # picture -> things edited out of it (or out of the pictures it was made from)

    def paint(self, prompt, seed=None):
        if self.delay:
            self.delay()
        self.painted.append(prompt)
        return self._file(f"paint{len(self.painted)}")

    def edit(self, image, instruction, boxes=(), min_change=None, attempts=1, first_attempt=0, around=None, spread=True,
             keep=()):
        self.edited.append((Path(image).name, instruction, boxes))
        self.kept.append(list(keep))
        if "nothing" in instruction:
            return None
        path = self._file(f"edit{len(self.edited)}")
        removed = re.match(r"Remove the ([^,.]+)", instruction)
        self.removed[Path(path).name] = self.removed.get(Path(image).name, set()) | ({removed[1]} if removed else set())
        return path

    def darkness(self):
        return self._file("dark")

    def _file(self, name):
        path = self.folder / f"{name}.png"
        path.touch()
        return str(path)


def stub_ai(engine, tmp_path, changes=None, delay=None, visible=("rug", "lamp", "window")):
    """No network: the director answers from `changes` ({command: Verdict}), finds only the `visible` things,
    and pictures are fake files."""
    changes = changes or {}
    engine.director.describe_place = lambda name, text, *context: Description(f"picture of {name}", {})
    engine.director.assess = lambda place, text, command: changes.get(command, NO_CHANGE)
    engine.director.locate = lambda text: None
    painter = FakePainter(tmp_path, delay)
    # Found by the game's full name too ("oriental rug" for "rug")
    engine.director.find = lambda image, thing: Finding([[100, 200, 300, 400]], "wooden floor") \
        if (thing in visible or thing.split()[-1] in visible) and thing not in painter.removed.get(Path(image).name, set()) else Finding([])
    engine.director.sees = lambda image, thing: bool(engine.director.find(image, thing).boxes)  # whatever find is now
    engine.painter = painter
    return painter


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
MAZE_TEXT = "Maze\nThis is part of a maze of twisty little passages, all alike."
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
    painter = stub_ai(engine, tmp_path, changes={
        "take lamp": Verdict("remove", "lamp", "Remove the lamp."),
        "take leaflet": Verdict("remove", "leaflet", "Remove the leaflet."),  # never painted, so nothing to remove
        "move rug": Verdict("change", "rug", "Drag the rug aside, revealing a trap door."),
        "open trap door": Verdict("change", "trap door", "Open the trap door."),  # not in the picture: left alone
        "drop sword": Verdict("add", "", "Place a sword on the floor."),  # an addition can go anywhere
        "wave": Verdict("add", "", "Change nothing at all."),  # an edit that makes no difference isn't kept
    })
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("take lamp", "Taken."), ("take leaflet", "Taken."), ("move rug", "With a "
          "great effort, the rug is moved to one side of the room, revealing the dusty cover of a closed trap door."),
          ("open trap door", "The door reluctantly opens."), ("drop sword", "Dropped."), ("wave", "You wave."),
          ("look", LIVING_ROOM)])

    assert painter.edited == [
        ("paint1.png", "Remove the lamp, leaving only bare wooden floor where it was.", [[100, 200, 300, 400]]),
        ("edit1.png", "Drag the oriental rug aside, revealing a trap door.", [[100, 200, 300, 400]]),  # its full name
        ("edit2.png", "Place a sword on the floor.", []),
        ("edit3.png", "Change nothing at all.", [])]
    living_room = engine.world.current
    assert [e["target"] for e in living_room.edits] == ["lamp", "oriental rug", "sword"]  # as the game calls it
    assert Path(living_room.image).name == "edit3.png"
    assert [Path(p).name for p in shown] == ["paint1.png", "edit1.png", "edit2.png", "edit3.png", "edit3.png"]


def test_a_whole_picture_editor_changes_things_the_finder_cannot_place(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={
        "open trap door": Verdict("change", "trap door", "Open the trap door."),
        "take leaflet": Verdict("remove", "leaflet", "Remove the leaflet."),  # still nothing to remove
    })
    painter.confine = False

    feed(engine, [(None, LIVING_ROOM), ("open trap door", "The door reluctantly opens."), ("take leaflet", "Taken.")])

    assert painter.edited == [("paint1.png", "Open the trap door.", [])]
    assert [e["target"] for e in engine.world.current.edits] == ["trap door"]


def test_a_separate_editor_makes_the_edits(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path, image_url="http://painter", image_model="mage-flow",
                       edit_url="http://editor", editor="qwen-image")

    assert engine.painter.generator.base_url == "http://painter"
    assert engine.painter.editor.base_url == "http://editor"
    assert engine.painter.editor.sampling == PROFILES["qwen-image"].sampling
    assert not engine.painter.confine  # Qwen-Image-2.1 keeps the rest of the picture by itself
    alone = PipeDream(["zork1.z3"], cache_dir=tmp_path, image_url="http://painter", image_model="mage-flow")
    assert alone.painter.editor is alone.painter.generator and alone.painter.confine


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
    painter = stub_ai(engine, tmp_path, changes={"open window": Verdict("change", "window", "Open the window wide.")})
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("open window", "With great effort, you open the window far enough.")])
    engine._repaint()

    assert painter.painted == ["picture of Living Room", "picture of Living Room"]
    box = [[100, 200, 300, 400]]
    assert painter.edited == [("paint1.png", "Open the window wide.", box), ("paint2.png", "Open the window wide.", box)]
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


def test_landmarks_are_described_the_same_way_from_every_side(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path)
    look = "a two-storey white clapboard farmhouse with a grey roof"
    seen = []

    def describe(name, text, known=(), *context):
        seen.append(set(known))
        if name == "West of House":
            return Description("An open field near a white house with a boarded front door.", {"white house": look})
        if name == "Living Room":  # indoors, so the house's outside doesn't belong here
            return Description("A living room in the white house.", {}, setting="indoors")
        return Description("A white house, all windows boarded up.", {})

    engine.director.describe_place = describe
    feed(engine, [(None, WEST_OF_HOUSE), ("north", NORTH_OF_HOUSE), ("west", LIVING_ROOM)])

    assert painter.painted == [f"An open field near {look} with a boarded front door.",
                               "A two-storey white clapboard farmhouse with a grey roof, all windows boarded up.",
                               "A living room in the white house."]
    assert seen == [set(), {"white house"}, {"white house"}]
    assert World(engine.world.folder).landmarks == {"white house": look}


def test_something_new_at_a_known_place_is_shown_but_not_kept(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    thief = "Someone carrying a large bag is casually leaning against one of the walls here."
    painter = stub_ai(engine, tmp_path, changes={"(comes back here)": Verdict("add", "", "Add a thief leaning on the wall.")})
    shown = []
    engine.custom_image = shown.append
    troll_room = ("The Troll Room\nThis is a small room with passages to the east and south and a forbidding hole "
                  "leading west. Bloodstains and deep scratches (perhaps made by an axe) mar the walls.")

    feed(engine, [(None, troll_room), ("west", MAZE_TEXT), ("east", f"The Troll Room\n{thief}"), ("west", MAZE_TEXT),
                  ("east", f"The Troll Room\n{thief}\nYour sword has begun to glow very brightly."),
                  ("west", MAZE_TEXT), ("east", "The Troll Room")])

    assert painter.edited == [("paint1.png", "Add a thief leaning on the wall.", [])]  # once, then remembered
    # The room shows at once, then the thief once he's painted in; the next time he's there, straight away
    assert [Path(p).name for p in shown] == ["paint1.png", "paint2.png", "paint1.png", "edit1.png", "paint2.png",
                                              "edit1.png", "paint2.png", "paint1.png"]
    assert Path(engine.world.current.image).name == "paint1.png"  # the thief was never part of the room itself


def test_underground_places_are_painted_underground(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path)
    came_from = []

    def describe(name, text, known=(), came_from_place=None, command=None):
        came_from.append(came_from_place.name if came_from_place else None)
        return Description(f"The edge of a chasm.", {}, setting="underground")

    engine.director.describe_place = describe
    feed(engine, [(None, LIVING_ROOM), ("down", DARK), ("turn on lamp", CELLAR),
                  ("south", "East of Chasm\nYou are on the east edge of a chasm, the bottom of which cannot be seen.")])

    assert came_from == [None, "Living Room", "Cellar"]  # the dark stretch doesn't lose track of where we were
    assert painter.painted[-1] == "The edge of a chasm. Deep underground, enclosed by rough rock walls and ceiling."


def test_a_visitor_already_in_the_picture_needs_no_edit(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"(comes back here)": Verdict("add", "lamp", "Add a lamp on the floor.")})
    shown = []
    engine.custom_image = shown.append

    feed(engine, [(None, LIVING_ROOM), ("east", "Kitchen\nYou are in the kitchen of the white house."),
                  ("west", "Living Room\nThere is a brass lamp here.")])

    assert painter.edited == []  # the finder already sees a lamp, so it's in the picture
    assert Path(shown[-1]).name == "paint1.png"


def test_an_edit_that_should_reveal_something_is_retried_until_it_does(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={
        "move rug": Verdict("change", "rug", "Replace the rug with a trap door.", "trap door")})
    looks = []

    def find(image, thing):
        looks.append((Path(image).name, thing))
        # The first edit (edit1) shows no trap door; the second one (edit2) does
        return Finding([[1, 2, 3, 4]], "floor") if thing == "oriental rug" or Path(image).name == "edit2.png" else Finding([])

    engine.director.find = find
    feed(engine, [(None, LIVING_ROOM), ("move rug", "The rug is moved aside.")])

    assert [e[0] for e in painter.edited] == ["paint1.png", "paint1.png"]
    assert ("edit1.png", "trap door") in looks and Path(engine.world.current.image).name == "edit2.png"


def test_a_removal_that_repaints_the_thing_is_dropped(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"take sword": Verdict("remove", "sword", "Remove the sword.")})
    engine.director.find = lambda image, thing: Finding([[100, 200, 300, 400]], "wall")  # the sword never goes

    feed(engine, [(None, LIVING_ROOM), ("take sword", "Taken.")])

    assert len(painter.edited) == 2  # tried twice with different seeds
    assert Path(engine.world.current.image).name == "paint1.png" and engine.world.current.edits == []


def test_a_change_that_loses_the_thing_is_tried_again(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"open case": Verdict("change", "case", "Open the case.")})
    # The first try deletes the trophy case (edit1 has none); the second opens it
    engine.director.find = lambda image, thing: Finding([] if Path(image).name == "edit1.png" else [[1, 2, 3, 4]], "floor")

    feed(engine, [(None, LIVING_ROOM), ("open case", "Opened.")])

    assert [e[1] for e in painter.edited] == ["Open the trophy case."] * 2
    assert Path(engine.world.current.image).name == "edit2.png"


def test_earlier_changes_are_kept_when_something_else_changes(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={
        "move rug": Verdict("change", "rug", "Replace the rug with bare floor and a trap door."),
        "open trap door": Verdict("change", "trap door", "Open the trap door."),
        "open case": Verdict("change", "case", "Open the case.")})
    where = {"oriental rug": [0, 600, 1000, 1000], "trap door": [300, 700, 600, 850], "trophy case": [300, 300, 600, 700]}
    engine.director.find = lambda image, thing: Finding([where[thing]], "floor") if thing in where else Finding([])

    feed(engine, [(None, LIVING_ROOM), ("move rug", "Moved."), ("open trap door", "Opened."), ("open case", "Opened.")])

    # The open trap door stays as it is when the case opens; the rug's place is just floor now
    assert painter.kept == [[], [], [where["trap door"]]]
    assert engine.world.current.edits[-1]["boxes"] == [where["trophy case"]]


def test_a_change_the_picture_already_shows_is_not_made_again(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"open window": Verdict("change", "window", "Open the window.")})

    # Closed from inside, out of sight, then opened again from here
    feed(engine, [(None, "Behind House\nYou are behind the white house."), ("open window", "Opened."), ("open window", "Opened.")])

    assert len(painter.edited) == 1 and Path(engine.world.current.image).name == "edit1.png"


def test_a_removal_takes_one_thing_and_ignores_others_like_it(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, changes={"take painting": Verdict("remove", "painting", "Remove the painting.")})
    taken, other = [400, 300, 600, 550], [40, 250, 140, 530]
    # The finder sees the painting on the far wall first, and another one on a side wall; only that one stays
    engine.director.find = lambda image, thing: Finding([taken, other] if Path(image).name == "paint1.png" else [other], "wall")

    feed(engine, [(None, "Gallery\nThis is an art gallery."), ("take painting", "Taken.")])

    assert painter.edited == [("paint1.png", "Remove the painting, leaving only bare wall where it was.", [taken])]
    assert Path(engine.world.current.image).name == "edit1.png"


def test_news_is_judged_one_line_at_a_time(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, visible=("bloody axe",))
    asked = []

    def assess(place, text, command):
        asked.append(text)
        if "axe" in text:
            return Verdict("add", "bloody axe", "Add a bloody axe.")  # already in the picture, so skipped
        return Verdict("add", "thief", "Add a thief leaning on the wall.")

    engine.director.assess = assess
    thief = "Someone carrying a large bag is casually leaning against one of the walls here."
    troll_room = "The Troll Room\nThis is a small room with passages to the east and south."
    feed(engine, [(None, troll_room), ("west", MAZE_TEXT), ("east", f"The Troll Room\nThere is a bloody axe here.\n{thief}")])

    assert asked == ["There is a bloody axe here.", thief]
    assert [e[1] for e in painter.edited] == ["Add a thief leaning on the wall."]


def test_news_seen_before_is_reused_line_by_line(tmp_path):
    engine = PipeDream(["zork1.z3"], cache_dir=tmp_path)
    painter = stub_ai(engine, tmp_path, visible=())

    def assess(place, text, command):
        return Verdict("add", "axe" if "axe" in text else "thief", f"Add the {'axe' if 'axe' in text else 'thief'}.")

    engine.director.assess = assess
    axe, thief = "There is a bloody axe here.", "Someone carrying a large bag is leaning against one of the walls here."
    troll_room = "The Troll Room\nThis is a small room with passages to the east and south."
    feed(engine, [(None, troll_room), ("west", MAZE_TEXT), ("east", f"The Troll Room\n{axe}"),
                  ("west", MAZE_TEXT), ("east", f"The Troll Room\n{axe}\n{thief}")])

    # The axe was painted on the first return; the second only adds the thief, on top of that picture
    assert [(e[0], e[1]) for e in painter.edited] == [("paint1.png", "Add the axe."), ("edit1.png", "Add the thief.")]
