from pipedream.world import World

FOREST_1 = "Forest\nThis is a forest, with trees in all directions. To the east, there appears to be sunlight."
FOREST_2 = "Forest\nThis is a dimly lit forest, with large trees all around. One particularly large tree is here."
MAZE = "Maze\nThis is part of a maze of twisty little passages, all alike."


def test_a_name_seen_again_is_the_same_place(tmp_path):
    world = World(tmp_path)
    kitchen, new = world.arrive("Kitchen", "Kitchen\nYou are in the kitchen of the white house. A table seems to "
                                           "have been used recently.", None)
    assert new

    world.arrive("Living Room", "Living Room\nYou are in the living room. There is a doorway to the east.", "west")
    again, new = world.arrive("Kitchen", "Kitchen\nA bottle is sitting on the table.", "up")  # a one-way chimney

    assert again is kitchen and not new


def test_places_sharing_a_name_are_told_apart_by_description(tmp_path):
    world = World(tmp_path)
    first, _ = world.arrive("Forest", FOREST_1, None)
    second, new = world.arrive("Forest", FOREST_2, "north")

    assert new and second is not first
    back, new = world.arrive("Forest", FOREST_1, "south")
    assert back is first and not new


def test_a_name_only_revisit_follows_the_known_route(tmp_path):
    world = World(tmp_path)
    first, _ = world.arrive("Forest", FOREST_1, None)
    second, _ = world.arrive("Forest", FOREST_2, "north")
    world.arrive("Forest", FOREST_1, "south")

    # Zork prints only "Forest" now; "north" from the first forest led to the second one before
    again, new = world.arrive("Forest", "Forest", "north")
    assert again is second and not new


def test_identical_rooms_share_one_place(tmp_path):
    world = World(tmp_path)
    first, _ = world.arrive("Maze", MAZE, "west")
    for command in ("north", "south", "east"):
        place, new = world.arrive("Maze", MAZE, command)
        assert place is first and not new


def test_the_world_survives_a_restart(tmp_path):
    world = World(tmp_path)
    place, _ = world.arrive("West of House", "West of House\nYou are standing in an open field west of a white house.", None)
    place.prompt, place.image = "A white house.", str(tmp_path / "house.png")
    world.titled = True
    world.save()

    reloaded = World(tmp_path)
    again, new = reloaded.arrive("West of House", "West of House", None)
    assert not new and again.image == place.image and reloaded.titled


def test_reset_forgets_everything(tmp_path):
    world = World(tmp_path / "zork")
    world.arrive("Kitchen", "Kitchen", None)
    world.reset()

    assert world.places == {} and World(tmp_path / "zork").places == {}
