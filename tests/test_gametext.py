import pytest

from pipedream.gametext import (apply_landmarks, clean_prompt, command_object, find_title, from_title, full_name,
                                has_no_visible_effect, is_dark, underground, unwrap, without_possessions)


def test_unwrap_joins_only_lines_the_game_wrapped():
    text = ("You are in the kitchen of the white house. A table seems to have been used\n"
            "recently for the preparation of food.\n"
            "A bottle is sitting on the table.\n"
            "The glass bottle contains:\n"
            "  A quantity of water")

    assert unwrap(text, 80) == ("You are in the kitchen of the white house. A table seems to have been used recently "
                                "for the preparation of food.\nA bottle is sitting on the table.\n"
                                "The glass bottle contains:\n  A quantity of water")


def test_unwrap_leaves_narrow_output_alone():
    assert unwrap("A short line\nand another", 20) == "A short line\nand another"


@pytest.mark.parametrize("text, title", [
    ("ZORK I: The Great Underground Empire\nRelease 119 / Serial number 880429\n\nWest of House\nYou are standing "
     "in an open field.", "West of House"),
    ("The acoustics of the room change subtly.\n\nLoud Room\nOn the ground is a large platinum bar.", "Loud Room"),
    ("The Troll Room\nThere is a bloody axe here.", "The Troll Room"),
    ("Flood Control Dam #3\nYou are standing on the top of the dam.", "Flood Control Dam #3"),
    ("Kitchen (on the chair)\nYou can see a table here.", "Kitchen"),
    ("Taken.", None),
    ("The glass bottle contains:\n  A quantity of water", None),
    ("WELCOME TO PIPEDREAM (DEMO CARTRIDGE)\n Try moving around to see the AI generate scenes.", None),
    ("Compression mode SPANS, hiding top 1 lines", None),
    ("OK", None),
])
def test_find_title(text, title):
    assert find_title(text) == title


def test_from_title_drops_the_messages_before_the_move():
    text = "The brass lantern is now on.\n\nCellar\nYou are in a dark and damp cellar."
    assert from_title(text, "Cellar") == "Cellar\nYou are in a dark and damp cellar."


@pytest.mark.parametrize("text, title, dark", [
    ("You have moved into a dark place.\n\nIt is pitch black. You are likely to be eaten by a grue.", None, True),
    ("Darkness\nIt is pitch dark, and you can't see a thing.", "Darkness", True),
    ("Cellar\nYou are in a dark and damp cellar.", "Cellar", False),
    ("Chasm\nThe chasm below is pitch black.", "Chasm", False),
])
def test_is_dark(text, title, dark):
    assert is_dark(text, title) == dark


def test_without_possessions():
    text = "Cellar\nYou are in a damp cellar. Your sword is glowing with a faint blue glow.\nA ramp rises."
    assert without_possessions(text) == "Cellar\nYou are in a damp cellar. A ramp rises."


@pytest.mark.parametrize("prompt, cleaned", [
    ("Open field west of white house with boarded front door. Small mailbox nearby. North: path to house. "
     "South: no visible path.", "Open field near white house with front door with wooden planks nailed across. "
     "Small mailbox nearby."),
    ("A white house with boarded windows. A narrow path winds north through trees. No door visible.",
     "A white house with windows with wooden planks nailed across. A narrow path winds through trees."),
    ("Dusty trap door lies beneath the rug. Exit: north to the rug's original position.",
     "Dusty trap door lies beneath the rug."),
    ("Small room with east and south passages, and a dark hole west.", "Small room with passages, and a dark hole."),
    ("A rusted gate lies closed to the north. To the south, a dense thicket blocks the way.",
     "A rusted gate lies closed. A dense thicket blocks the way."),
    ('A sign reads "Danger". Narrow east-west corridor.', "A sign reads Danger. Narrow corridor."),
    ("A white house with no door, all windows boarded up. A narrow path winds through trees to the north.",
     "A white house, all windows boarded up. A narrow path winds through trees."),
    ("The scene is dimly lit, with no visible openings or entrances. No people or objects are present.",
     "The scene is dimly lit."),
    ("A wooden door is nailed shut to the west. A doorway is to the east.", "A wooden door is nailed shut."),
    ("The paper is not legible.", ""),
    ("A white house with boarded-up windows, facing north. A narrow path.",
     "A white house with windows with wooden planks nailed across. A narrow path."),
    ("Behind House: A path leads into the forest.", "A path leads into the forest."),
    ("A living room with a rug. Outdoors: true. Landmarks: white house.", "A living room with a rug."),
    ("A passage to the north and a passage to the south are visible.", "Two passages are visible."),
    ("A small room, passages east and south visible in the room.", "A small room, passages visible in the room."),
    ("Passages lead north, south, and east from here.", "Passages lead from here."),
    ("A large room with a narrow passage running from east to west and a stone stairway.",
     "A large room with a narrow passage and a stone stairway."),
    ("Bloodstained walls, and a forbidding hole leading west.", "Bloodstained walls."),
    ("A steep, east-facing cliff above a chasm.", "A steep cliff above a chasm."),
])
def test_clean_prompt(prompt, cleaned):
    assert clean_prompt(prompt) == cleaned


@pytest.mark.parametrize("command, titled, expected", [
    ("inventory", True, True), ("examine sword", False, True), ("north", True, True), ("go north", True, True),
    ("north", False, False), ("open window", True, False), ("take lamp", True, False), (None, True, False),
])
def test_has_no_visible_effect(command, titled, expected):
    assert has_no_visible_effect(command, titled) == expected


@pytest.mark.parametrize("command, thing", [
    ("open trap door", "trap door"), ("put painting in case", "case"), ("kill troll with sword", "troll"),
    ("take the lamp", "lamp"), ("pick up lamp", "lamp"), ("take all", ""), ("jump", ""), (None, ""),
])
def test_command_object(command, thing):
    assert command_object(command) == thing


LIVING_ROOM = ("Living Room\nYou are in the living room. There is a doorway to the east, a wooden door with strange "
               "gothic lettering to the west, which appears to be nailed shut, a trophy case, and a large oriental rug "
               "in the center of the room.\nAbove the trophy case hangs an elvish sword of great antiquity.")


@pytest.mark.parametrize("thing, text, name", [
    ("case", LIVING_ROOM, "trophy case"), ("rug", LIVING_ROOM, "oriental rug"), ("sword", LIVING_ROOM, "elvish sword"),
    ("door", LIVING_ROOM, "wooden door"), ("door", LIVING_ROOM + " A trap door is open.", "door"),  # no telling which
    ("trap door", LIVING_ROOM, "trap door"), ("lamp", LIVING_ROOM, "lamp"), ("", LIVING_ROOM, ""),
    ("troll", "A nasty-looking troll, brandishing a bloody axe, blocks the way.", "nasty-looking troll"),
    ("painting", "Fortunately, there is still one chance for you to be a vandal, for on the far wall is a painting of "
                 "unparalleled beauty. Most of the paintings have been stolen.", "painting"),
])
def test_full_name(thing, text, name):
    assert full_name(thing, text) == name


@pytest.mark.parametrize("prompt, described", [
    ("A white house, all windows boarded.", "A two-storey farmhouse with a grey roof, all windows boarded."),
    ("Behind the white house, a path.", "Behind the two-storey farmhouse with a grey roof, a path."),
    ("White house with boarded windows.", "Two-storey farmhouse with a grey roof with boarded windows."),
    ("A dark forest.", "A dark forest."),
])
def test_apply_landmarks(prompt, described):
    assert apply_landmarks(prompt, {"white house": "a two-storey farmhouse with a grey roof"}) == described


@pytest.mark.parametrize("prompt, cleaned", [
    ("A chasm's edge under open sky, with a narrow passage.", "A chasm's edge, with a narrow passage."),
    ("An art gallery with tall windows and empty frames. Paintings hang in the dim light.",
     "An art gallery and empty frames. Paintings hang in the dim light."),
    ("A cellar. Stars glitter overhead.", "A cellar."),
    ("A chasm's edge, the bottom unseen, all under open air.", "A chasm's edge, the bottom unseen."),
    ("A damp cellar with a ramp.", "A damp cellar with a ramp."),
])
def test_underground(prompt, cleaned):
    assert underground(prompt) == cleaned
