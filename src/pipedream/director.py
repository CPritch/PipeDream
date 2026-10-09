import base64
import json
import re
from typing import NamedTuple

import requests

from pipedream.gametext import clean_prompt, command_object, underground

# llama-server constrains each reply to a JSON schema, so there's no free text to parse. Each question is kept
# narrow because a 4B model answers one clear question far more reliably than a combined one.
_PROMPT_LIMIT = 320  # about the 50 words asked for; more is the model padding with invented details
PLACE_SCHEMA = {
    "type": "object",
    # Deciding the setting first, before writing the prompt, stops "Outdoors: true." leaking into the prompt itself
    "properties": {
        "setting": {"type": "string", "enum": ["outdoors", "indoors", "underground"]},
        # The grammar closes the string at this length, so a looping reply can't run on to max_tokens
        "prompt": {"type": "string", "maxLength": _PROMPT_LIMIT},
        "landmarks": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    },
    "required": ["setting", "prompt", "landmarks"],
}
CHANGE_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["none", "change", "add", "remove", "dark"]},
                   "target": {"type": "string"}, "edit": {"type": "string"}},
    "required": ["kind", "target", "edit"],
}
LOCATION_SCHEMA = {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}

# Image models paint direction words as text and draw anything negated, so prompts never contain either
_PICTURE_RULES = (
    "Never mention directions (north, south, east, west), exits or where paths lead. "
    "Never mention words, signs, labels or writing. "
    "Never say what is absent, hidden or not visible, and never use the words no, not or without: describe what is "
    "there instead. "
    "The viewer is the player: never include the player, 'you', or anything the player is carrying."
)

DESCRIBE_PLACE = (
    "You turn a text adventure's description of a place into a prompt for an image model.\n"
    "Write under 50 words of plain visual description: the setting, its architecture and lighting, the notable "
    "objects in it, and any creatures or people the text says are here. Include only what the text says is here; "
    "never invent people, creatures, objects, doors or light sources. Describe states so a painter can draw them, e.g. 'boarded up' as "
    "'with wooden planks nailed across'. "
    f"{_PICTURE_RULES}\n"
    "Example: 'A cramped kitchen with a worn wooden table holding a glass bottle of water and a brown sack. "
    "A dark staircase climbs into shadow beside a small open window.'\n"
    "setting: outdoors (open air), indoors (inside a building) or underground (caves, cellars, tunnels, mines). "
    "Places reached from underground stay underground unless the text says otherwise.\n"
    "landmarks: up to two large, lasting features of this place that could also be seen from neighbouring places, "
    "such as a building, tower, bridge, river or cliff (never small objects, doors, windows or paths), each by what "
    "the game calls it, e.g. 'white house'. An empty list if there are none."
)

DESIGN_LANDMARK = (
    "You design the look of a recurring landmark in an illustrated text adventure, so every picture of it matches. "
    "Given its name and what the game says, describe exactly how it looks from any side in one phrase under 30 "
    "words: its shape, size, materials and colours, and for a building its storeys, roof and chimney. Be specific "
    "and concrete; invent details the game leaves open, but nothing that isn't part of the landmark itself. Never "
    "mention doors, windows, signs or anything seen from only one side. Start with 'a' or 'an'."
)
LOOK_SCHEMA = {"type": "object", "properties": {"look": {"type": "string", "maxLength": 240}}, "required": ["look"]}
SEEN_SCHEMA = {"type": "object", "properties": {"visible": {"type": "boolean"}}, "required": ["visible"]}

ASSESS_CHANGE = (
    "You are the visual director for a text adventure. The player is in a place that has already been painted. "
    "Decide whether the game's latest reply visibly changes that picture.\n"
    "kind:\n"
    "- none: nothing in the picture changes. This includes failed actions, inventory, score, sounds, smells, "
    "conversation, and anything about the player's own possessions. "
    "For example 'You are carrying a lamp.', 'The troll fends you off.', 'Nothing happens.'\n"
    "- remove: something shown in the picture is taken away, or a creature dies and vanishes. "
    "For example 'take lamp' answered 'Taken.' removes the lamp.\n"
    "- change: something already in the picture visibly changes. A door, window, hatch or container opens or "
    "closes; something is moved aside, broken, lit or put out; something is placed in or on it; a creature is "
    "knocked out.\n"
    "- add: something that wasn't there appears: a creature or person arrives or is here, an object is dropped.\n"
    "- dark: the player can no longer see anything.\n"
    "target: the thing in the picture that changes or is removed, in two or three words, using the game's own name "
    "for it (e.g. 'brass lantern', 'troll', 'trap door', 'trophy case'); for add, the thing that appears (e.g. "
    "'bloody axe', 'thief'). Empty if kind is none or dark.\n"
    "edit: an instruction for an image editor about only this latest change, starting with a verb and naming the "
    "thing the player acted on, e.g. 'Remove the brass lantern.', 'Open the trap door, revealing a rickety staircase "
    "descending into darkness.', 'Drag the rug into a heap at the side, revealing a closed wooden trap door in the "
    "floor.', 'Make the troll lie unconscious on the floor.', 'Add a lean man in a dark cloak with a large sack, "
    "leaning against the wall.' Only things that can be seen: never air, sounds, smells or feelings. Empty if kind "
    f"is none or dark. {_PICTURE_RULES}"
)

FIND = ('Locate the {thing} in the image. Answer in JSON: {{"bbox_2d": [x1, y1, x2, y2], "surface": "the surface it '
        'stands on or hangs against, in two or three words, e.g. wooden floor or stone wall"}}. If there is no {thing}, '
        'answer {{}}.')

# Asked to locate something, the grounding model nearly always boxes something; asked whether it's there, it can
# say no (it boxed blood on a cave floor as "a bloody axe", but said there was no axe)
SEES = 'Is there a {thing} in this picture? Answer in JSON: {{"visible": true or false}}.'
LOCATE = (
    "You read a text adventure's latest reply. If it describes the place the player is in, answer with a short "
    "canonical name for that place (at most 4 words). Otherwise answer 'none'. Titles, banners, credits, "
    "instructions, questions and the results of actions are not places."
)


_OPEN_AIR = re.compile(r"\b(sky|sun|sunlight|daylight|moon|stars|outside|open air|fresh air|clearing|forest|field)\b",
                       re.IGNORECASE)


class Description(NamedTuple):
    prompt: str
    landmarks: dict  # landmarks seen here for the first time: name -> look
    setting: str = "outdoors"  # outdoors, indoors or underground


class Finding(NamedTuple):
    boxes: list  # [x1, y1, x2, y2] in 0-1000 coordinates
    surface: str = ""  # what it stands on or hangs against, e.g. "wooden floor"


class Verdict(NamedTuple):
    kind: str  # none, change, add, remove or dark
    target: str = ""  # what in the picture the change applies to
    edit: str = ""  # instruction for the image editor
    expect: str = ""  # something the edited picture must show, e.g. the trap door a moved rug reveals


NO_CHANGE = Verdict("none")


_PARSER_REFUSAL = re.compile(r"(you can't|you cannot|you can not|you don't|you do not|you aren't|i don't|i beg your "
                             r"pardon|that's not|there is no|there's no|there's nothing|you see no|it is already|"
                             r"it's already|nothing happens|what do you want)\b")


def _revealed_thing(phrase):
    """The thing a reveal is about: "a rickety staircase descending into darkness" -> "rickety staircase",
    "the dusty cover of a closed trap door" -> "trap door"."""
    phrase = re.split(r"\s+(?:descending|ascending|leading|going|running|that|which|into|with|beneath|below)\b", phrase)[0]
    return " ".join(re.sub(r"^(?:the|a|an)\s+", "", phrase.strip()).split()[-2:])


def obvious_verdict(command, reply):
    """The verdict for the standard one-word replies, e.g. "take lamp" answered "Taken.", without asking the model."""
    words, thing = (command or "").lower().split(), command_object(command)
    reply = reply.strip().lower()
    if _PARSER_REFUSAL.match(reply):
        return NO_CHANGE  # "You can't see any troll here!" changes nothing, and needs no model to say so
    if not words or not thing:
        return None
    if words[0] in ("take", "get", "grab", "pick") and reply == "taken.":
        return Verdict("remove", thing, f"Remove the {thing}.")
    refused = re.search(r"\b(already|locked|can't|cannot|won't|isn't|not|no)\b", reply)
    if words[0] == "open" and not refused and (reply == "opened." or re.search(r"\bopens?\b|\bopened\b", reply)):
        revealed = re.search(r"\breveal(?:s|ing)?\s+(.+?)[.!]", reply)  # "opens to reveal a rickety staircase"
        # Plain "open the trophy case" leaves it looking much the same, or redraws it shut
        return Verdict("change", thing, f"Open the {thing}" + (f", revealing {revealed[1]}." if revealed else
                                                               " wide, swinging it fully open."),
                       _revealed_thing(revealed[1]) if revealed else "")
    if words[0] == "close" and not refused and (reply == "closed." or re.search(r"\bcloses?\b|\bclosed\b|\bshut\b", reply)):
        return Verdict("change", thing, f"Close the {thing}.")
    revealed = re.search(r"\breveal(?:s|ing)?\s+(.+?)[.!]", reply)
    if words[0] in ("move", "push", "pull", "lift", "roll", "drag", "shift") and revealed and not refused:
        # Painting what's there now works where "move the rug aside" leaves the rug in place
        return Verdict("change", thing, f"Replace the {thing} with bare floor and {revealed[1]}; the {thing} lies "
                                        f"crumpled at the edge.", _revealed_thing(revealed[1]))
    if words[0] in ("put", "place") and reply == "done." and (
            put := re.match(r"(?:the |an? )?(.+?)\s+(in|into|inside|on|onto|under)\s+", " ".join(words[1:]))):
        # Asked to "place the painting in the case", the model fills the case with more of what's in it already;
        # it has to be told to add something it can't see yet
        item, where = put[1], put[2]
        some = "some" if re.search(r"[^s]s$", item) else "an" if item[0] in "aeiou" else "a"
        if where in ("in", "into", "inside"):
            # Without "small enough to fit", the painting put in the trophy case came out bigger than the case
            return Verdict("change", thing, f"Add {some} {item}, small enough to fit, standing inside the {thing}, among "
                                            f"what is already there.")
        return Verdict("change", thing, f"Add {some} {item} {'lying on' if where.startswith('on') else 'under'} the {thing}.")
    return None


class Director:
    def __init__(self, llm_url):
        self.llm_url = llm_url

    def describe_place(self, name, text, known=(), came_from=None, command=None):
        """A Description for painting a place the player has just found, or None.

        `known` names the landmarks already designed. They aren't shown to the model: told about a house, a small
        model paints it into every room, kitchen included. `came_from` is the previous Place, if any, so a chasm
        reached from a cellar is known to be underground.
        """
        user = f"PLACE: {name}\n\n{text}"
        if came_from:
            user = f"THE PLAYER CAME FROM: {came_from.name} ({came_from.setting})\n\n" + user
        for temperature in (0.2, 0.7):
            reply = self._ask(DESCRIBE_PLACE, user, PLACE_SCHEMA, max_tokens=240, temperature=temperature) or {}
            raw = reply.get("prompt", "")
            if len(raw) >= _PROMPT_LIMIT - 10 and "." in raw:
                raw = raw[:raw.rfind(".") + 1]  # cut off at the length limit: drop the unfinished sentence
            prompt = clean_prompt(raw)
            # Too little survives when the model describes mostly what isn't there; a warmer second try differs
            if len(prompt) >= 20:
                break
            print(f"   > Description of {name} cleaned down to {prompt!r}, asking again")
        if len(prompt) < 5:
            # Better the game's own words than no picture at all
            words = re.sub(r"^.*\n", "", text.strip(), count=1) if "\n" in text else text
            # "You are standing in an open field" would paint someone standing there
            words = re.sub(r"(?i)\b(?:you are|you're)\s+(?:standing\s+|sitting\s+)?(?:in|on|at|inside|by|near)?\s*", "", words)
            prompt = clean_prompt(words)
            if len(prompt) < 5:
                # The game's words can clean down to nothing too ("the bottom of which cannot be seen"); the
                # place's name still says what it is, and a wrong picture staying up is worse than a plain one
                prompt = clean_prompt(f"{name}.") if len(clean_prompt(f"{name}.")) >= 5 else "A dim, quiet place."
        found = {}
        setting = reply.get("setting") if reply.get("setting") in ("outdoors", "indoors", "underground") else "outdoors"
        # A small model calls a chasm "outdoors" or a gallery "indoors" even when it's reached from a cellar. Climbing
        # up out of the cellar into the living room does leave the underground, so that's left to the model.
        climbing = (command or "").strip().lower() in ("u", "up", "climb", "climb up", "go up")
        if came_from and came_from.setting == "underground" and setting != "underground" and not climbing \
                and not _OPEN_AIR.search(text):
            setting = "underground"
        if setting == "underground" and not _OPEN_AIR.search(text):
            prompt = underground(prompt) or prompt
        # A landmark is something seen from outside: the white house's look doesn't belong in its kitchen
        for landmark in (reply.get("landmarks") or []) if setting == "outdoors" else []:
            landmark = str(landmark).strip().lower()
            # Only what the game actually mentions here, so a made-up landmark can't follow the player around
            if landmark and landmark.split()[-1] in text.lower() and landmark not in known:
                if look := self.design_landmark(landmark, text):
                    found[landmark] = look
        return Description(prompt, found, setting)

    def design_landmark(self, name, text):
        """One detailed look for a landmark, used in every picture of it from then on, or None."""
        reply = self._ask(DESIGN_LANDMARK, f"LANDMARK: {name}\nTHE GAME SAYS: {text}", LOOK_SCHEMA, max_tokens=80)
        look = clean_prompt((reply or {}).get("look", "")).rstrip(".")
        return look[:1].lower() + look[1:] if len(look) > len(name) + 5 else None  # it goes mid-sentence later

    def assess(self, place, text, command):
        """What the reply does to the current place's picture, as a Verdict."""
        if verdict := obvious_verdict(command, text):
            return verdict
        done = "".join(f"\n- {edit['edit']}" for edit in place.edits)
        acted_on = command_object(command)
        reply = self._ask(ASSESS_CHANGE, f"PLACE: {place.name}\nTHE PICTURE SHOWS: {place.prompt}\n"
                                         f"ALREADY CHANGED (don't repeat these):{done or ' nothing'}\n\n"
                                         f"PLAYER: {command}\n" + (f"THE PLAYER ACTED ON: {acted_on}\n" if acted_on else "")
                                         + f"GAME: {text}", CHANGE_SCHEMA)
        kind = (reply or {}).get("kind", "none")
        if kind == "dark":
            return Verdict("dark")
        if kind not in ("change", "add", "remove"):
            return NO_CHANGE
        target = reply.get("target", "").strip().strip(".")
        edit = clean_prompt(reply.get("edit", ""))
        if kind == "remove" and len(edit) < 5 and target:
            edit = f"Remove the {target}."
        return Verdict(kind, target, edit) if len(edit) >= 5 else NO_CHANGE

    def find(self, image, thing):
        """Where `thing` is in the picture, and what it stands on, as a Finding (no boxes if it isn't there)."""
        with open(image, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                   {"type": "text", "text": FIND.format(thing=thing)}]
        reply = self._chat([{"role": "user", "content": content}], max_tokens=120, temperature=0) or ""
        boxes = [[int(n) for n in box] for box in re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", reply)]
        surface = re.search(r'"surface"\s*:\s*"([^"]*)"', reply)
        return Finding([b for b in boxes if b[0] < b[2] <= 1000 and b[1] < b[3] <= 1000][:3], surface[1] if surface else "")

    def sees(self, image, thing):
        """Whether `thing` is in the picture."""
        with open(image, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                   {"type": "text", "text": SEES.format(thing=thing)}]
        reply = self._chat([{"role": "user", "content": content}], max_tokens=20, temperature=0,
                           response_format={"type": "json_schema", "json_schema": {"name": "reply", "schema": SEEN_SCHEMA}})
        try:
            return bool(json.loads(reply or "")["visible"])
        except (ValueError, KeyError, TypeError):
            return False

    def locate(self, text):
        """For games without room titles: the name of the place the text describes, or None."""
        reply = self._ask(LOCATE, text, LOCATION_SCHEMA, max_tokens=24)
        location = (reply or {}).get("location", "").strip().strip(".")
        return location if location and location.lower() != "none" else None

    def _ask(self, system, user, schema, max_tokens=200, temperature=0.2):
        content = self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens,
                             temperature, {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}})
        if content is None:
            return None
        try:
            return json.loads(content)
        except ValueError as e:
            # A rambling reply can hit max_tokens mid-string; keep its complete sentences rather than nothing
            match = re.search(r'"(\w+)"\s*:\s*"(.*?[.!?])[^.!?"]*$', content, re.DOTALL)
            if match and match[1] in ("prompt", "look"):
                return {match[1]: match[2]}
            print(f"[!] Director Error: {e}")
            return None

    def _chat(self, messages, max_tokens, temperature, response_format=None):
        body = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Hybrid reasoning models (e.g. Qwen3-4B) otherwise think first and leave the JSON reply empty
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if response_format:
            body["response_format"] = response_format
        try:
            response = requests.post(f"{self.llm_url}/v1/chat/completions", timeout=120, json=body)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[!] Director Error: {e}")
            return None
