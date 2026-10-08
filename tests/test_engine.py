import shutil
import sys
from pathlib import Path

import pytest

from pipedream import games
from pipedream.engine import PipeDream
from pipedream.navigator import START_COMMAND

MOCK_GAME = Path(games.__file__).parent / "mock_game.py"

FIELD = "You are standing in an open field west of a white house."
FOREST = "You are deep in a dark, ancient forest."
HOUSE = "You are inside the white house."


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
    printed, generated = [], []
    replies = iter(commands + ["quit"])

    engine.custom_print = printed.append
    engine.custom_input = lambda prompt: next(replies)
    engine.director.describe_scene = lambda text, previous_text="": f"a picture of: {text[:40]}"

    def generate(raw_text, visual_prompt, reference_image=None):
        path = Path(cache_dir) / f"{len(generated)}.png"
        path.touch()
        generated.append(raw_text)
        engine.cache.memory[engine.cache._get_hash(raw_text)] = str(path)
        return str(path)

    engine.cache.generate = generate
    engine.start()
    return engine, "\n".join(printed), generated


def test_demo_walkthrough_builds_one_node_per_room(demo_argv, tmp_path):
    engine, output, generated = play(demo_argv, tmp_path, ["north", "south", "north", "south", "east", "west"])

    for description in (FIELD, FOREST, HOUSE):
        assert description in output
    assert len(generated) == 3

    nodes = engine.navigator.nodes
    by_image = {Path(n["image_path"]).name: n for n in nodes.values() if n["image_path"]}
    field, forest, house = by_image["0.png"], by_image["1.png"], by_image["2.png"]
    id_of = {id(n): node_id for node_id, n in nodes.items()}

    assert len(nodes) == 4  # three rooms plus the genesis node
    assert field["edges"] == {"north": id_of[id(forest)], "east": id_of[id(house)]}
    assert forest["edges"] == {"south": id_of[id(field)]}
    assert house["edges"] == {"west": id_of[id(field)]}
    assert nodes[engine.navigator.genesis_id]["edges"] == {START_COMMAND: id_of[id(field)]}


def test_relaunch_reuses_the_saved_world(demo_argv, tmp_path):
    first, _, _ = play(demo_argv, tmp_path, ["north"])
    second, _, generated = play(demo_argv, tmp_path, [])

    assert generated == []
    assert second.navigator.genesis_id == first.navigator.genesis_id
    assert len(second.navigator.nodes) == len(first.navigator.nodes)


def test_echo_strip_only_removes_the_echoed_command(tmp_path):
    engine = PipeDream(["true"], cache_dir=tmp_path)
    engine.last_input = "n"

    assert engine.clean_output("n\r\nYou are in a forest.") == "You are in a forest."
    assert engine.clean_output("You are in a forest.\nPaths lead north.") == "You are in a forest.\nPaths lead north."
