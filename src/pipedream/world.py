"""What PipeDream remembers about the game world: each place the player has seen and its picture."""
import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import platformdirs

# e.g. ~/.cache/pipedream on Linux, rather than wherever the user launched from
DEFAULT_CACHE_DIR = platformdirs.user_cache_dir("pipedream", appauthor=False)

# A first paragraph that opens like this, and is long enough, is a room description rather than something
# happening there ("Someone carrying a large bag is leaning against the wall"). Telling two same-named places apart
# needs that certainty: wrongly merging look-alike rooms costs a picture, wrongly splitting one room costs the map.
_DESCRIPTION_LENGTH = 50
_DESCRIPTION_START = re.compile(r"(you are|you're|this is|you stand|you find yourself|you have come|you have entered)\b",
                                re.IGNORECASE)


@dataclass
class Place:
    id: str
    name: str
    text: str  # everything the game has said while describing it
    prompt: str = ""  # what the base picture was painted from
    setting: str = "outdoors"  # outdoors, indoors or underground
    base: str = ""  # picture of the place as first described
    image: str = ""  # current picture: the base with any edits applied
    edits: list = field(default_factory=list)  # {"edit": instruction, "target": thing} applied to the base, in order
    exits: dict = field(default_factory=dict)  # command -> place id, as learned by moving
    # What else the game has said is here on a later visit ("Someone carrying a large bag is leaning against the
    # wall"), each with the picture showing it. Not part of `image`, since a visitor can leave while you're away.
    visitors: dict = field(default_factory=dict)
    visited: float = 0.0

    @property
    def key(self):
        return _key(self.name)

    @property
    def description(self):
        return _description(self.text, self.name)


def _key(name):
    return re.sub(r"^the\s+", "", re.sub(r"\s+", " ", name.strip().lower()))


def _description(text, name):
    """The first paragraph after the title, if it's long enough to be the room's description."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _key(lines[0]) == _key(name):
        lines = lines[1:]
    if lines and len(lines[0]) >= _DESCRIPTION_LENGTH and _DESCRIPTION_START.match(lines[0]):
        return lines[0]
    return ""


def _similar(a, b):
    # The same room in another state ("window which is open") scores above 0.9, different forests about 0.5
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.8


class World:
    """Places are recognised by the name the game gives them, never by guessing where a direction leads.

    Zork-style games print a room's title on every visit but its description only on the first, so the name is
    what stays stable. Places that share a name ("Forest") are told apart by their descriptions, and by the
    route taken when a revisit prints only the name.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.file = self.folder / "world.json"
        self.images = self.folder / "images"
        self.places = {}
        self.current = None  # the Place the player is in, None before the first place or in the dark
        self.titled = False  # whether the game prints room titles
        self.landmarks = {}  # big features seen from several places, name -> how they look, e.g. the white house
        self.news = []  # lines the last arrival printed that the place's first description didn't have
        self._load()

    def reset(self):
        shutil.rmtree(self.folder, ignore_errors=True)
        self.places, self.current, self.titled, self.landmarks = {}, None, False, {}

    def arrive(self, name, text, command):
        """Move the player to the named place. Returns (place, is_new) and sets `news`."""
        previous = self.current
        place = self._find(name, text, command)
        is_new = place is None
        if is_new:
            place = Place(str(uuid.uuid4()), name, text)
            self.places[place.id] = place
        lines = [line.strip() for line in text.splitlines()]
        start = next((i + 1 for i, line in enumerate(lines) if _key(line) == place.key), 0)  # after the title
        self.news = [] if is_new else [line for line in lines[start:] if len(line) > 3 and line not in place.text
                                       and not line.startswith("Your ")]
        if previous and previous is not place and command:
            previous.exits[command.strip().lower()] = place.id
        place.visited = time.time()
        self.current = place
        self.save()
        return place, is_new

    def _find(self, name, text, command):
        candidates = [p for p in self.places.values() if p.key == _key(name)]
        if not candidates:
            return None
        # A route that led to a place with this name before still does (the name has already confirmed it)
        if self.current and command:
            known = self.current.exits.get(command.strip().lower())
            if any(p.id == known for p in candidates):
                return self.places[known]
        description = _description(text, name)
        if description:
            for place in candidates:
                if not place.description or description in place.text or _similar(description, place.description):
                    return place
            return None  # same name, different description: another place, like Zork's several forests
        return max(candidates, key=lambda p: p.visited)  # only the name was printed: the last one visited

    def image_path(self):
        self.images.mkdir(parents=True, exist_ok=True)
        return str(self.images / f"{uuid.uuid4().hex}.png")

    def _load(self):
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text())
            self.places = {p["id"]: Place(**p) for p in data.get("places", [])}
            for place in self.places.values():  # worlds saved before edits had targets
                place.edits = [e if isinstance(e, dict) else {"edit": e, "target": ""} for e in place.edits]
            self.titled = data.get("titled", False)
            self.landmarks = data.get("landmarks", {})
        except (ValueError, TypeError, KeyError) as e:
            print(f"[!] Ignoring unreadable world file {self.file}: {e}")
            self.places = {}

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"titled": self.titled, "landmarks": self.landmarks, "places": [asdict(p) for p in self.places.values()]}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.file)
