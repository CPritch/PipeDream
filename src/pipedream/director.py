import base64
import json
import re
from typing import NamedTuple

import requests

from pipedream.gametext import clean_prompt, command_object

# llama-server constrains each reply to a JSON schema, so there's no free text to parse. Each question is kept
# narrow because a 4B model answers one clear question far more reliably than a combined one.
PLACE_SCHEMA = {
    "type": "object",
    # Deciding outdoors first, before writing the prompt, stops "Outdoors: true." leaking into the prompt itself
    "properties": {
        "outdoors": {"type": "boolean"},
        "prompt": {"type": "string"},
        "landmarks": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    },
    "required": ["outdoors", "prompt", "landmarks"],
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
    "outdoors: true if the place is out in the open air, false if it's inside a building, a cave or underground.\n"
    "landmarks: up to two large, lasting features of this place that could also be seen from neighbouring places, "
    "such as a building, tower, bridge, river or cliff (never small objects, doors, windows or paths), each by what "
    "the game calls it, e.g. 'white house'. An empty list if there are none."
)

DESIGN_LANDMARK = (
    "You design the look of a recurring landmark in an illustrated text adventure, so every picture of it matches. "
    "Given its name and what the game says, describe exactly how it looks from any side in one phrase under 30 "
    "words: its shape, size, storeys, building materials, colours, roof and chimney. Be specific and concrete; invent "
    "details the game leaves open. Never mention doors, windows, signs or anything seen from only one side. Start "
    "with 'a' or 'an'."
)
LOOK_SCHEMA = {"type": "object", "properties": {"look": {"type": "string"}}, "required": ["look"]}

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
    "for it (e.g. 'brass lantern', 'troll', 'trap door', 'trophy case'). Empty if kind is none, add or dark.\n"
    "edit: an instruction for an image editor about only this latest change, starting with a verb and naming the "
    "thing the player acted on, e.g. 'Remove the brass lantern.', 'Open the trap door, revealing a rickety staircase "
    "descending into darkness.', 'Remove the troll, leaving wisps of black fog where it stood.', 'Make the troll lie "
    "unconscious on the floor.', 'Place a framed painting inside the trophy case.' Empty if kind is none or "
    f"dark. {_PICTURE_RULES}"
)

FIND = ('Locate the {thing} in the image. Answer in JSON: {{"bbox_2d": [x1, y1, x2, y2], "surface": "the surface it '
        'stands on or hangs against, in two or three words, e.g. wooden floor or stone wall"}}. If there is no {thing}, '
        'answer {{}}.')

LOCATE = (
    "You read a text adventure's latest reply. If it describes the place the player is in, answer with a short "
    "canonical name for that place (at most 4 words). Otherwise answer 'none'. Titles, banners, credits, "
    "instructions, questions and the results of actions are not places."
)


class Description(NamedTuple):
    prompt: str
    landmarks: dict  # landmarks seen here for the first time: name -> look
    outdoors: bool = True


class Finding(NamedTuple):
    boxes: list  # [x1, y1, x2, y2] in 0-1000 coordinates
    surface: str = ""  # what it stands on or hangs against, e.g. "wooden floor"


class Verdict(NamedTuple):
    kind: str  # none, change, remove or dark
    target: str = ""  # what in the picture the change applies to
    edit: str = ""  # instruction for the image editor


NO_CHANGE = Verdict("none")


def obvious_verdict(command, reply):
    """The verdict for the standard one-word replies, e.g. "take lamp" answered "Taken.", without asking the model."""
    words, thing = (command or "").lower().split(), command_object(command)
    reply = reply.strip().lower()
    if not words or not thing:
        return None
    if words[0] in ("take", "get", "grab", "pick") and reply == "taken.":
        return Verdict("remove", thing, f"Remove the {thing}.")
    if words[0] == "open" and reply == "opened.":
        return Verdict("change", thing, f"Open the {thing}.")
    if words[0] == "close" and reply == "closed.":
        return Verdict("change", thing, f"Close the {thing}.")
    if words[0] in ("put", "place") and reply == "done.":
        item = re.sub(r"^(the|a|an)\s+", "", re.split(r"\s+(?:in|into|on|onto|inside|under)\s+", " ".join(words[1:]))[0])
        return Verdict("change", thing, f"Place the {item} in the {thing}.")
    return None


class Director:
    def __init__(self, llm_url):
        self.llm_url = llm_url

    def describe_place(self, name, text, known=()):
        """A Description for painting a place the player has just found, or None.

        `known` names the landmarks already designed. They aren't shown to the model: told about a house, a small
        model paints it into every room, kitchen included.
        """
        user = f"PLACE: {name}\n\n{text}"
        for temperature in (0.2, 0.7):
            reply = self._ask(DESCRIBE_PLACE, user, PLACE_SCHEMA, max_tokens=240, temperature=temperature) or {}
            prompt = clean_prompt(reply.get("prompt", ""))
            # Too little survives when the model describes mostly what isn't there; a warmer second try differs
            if len(prompt) >= 20:
                break
            print(f"   > Description of {name} cleaned down to {prompt!r}, asking again")
        if len(prompt) < 5:
            return None
        found, outdoors = {}, reply.get("outdoors") is not False
        # A landmark is something seen from outside: the white house's look doesn't belong in its kitchen
        for landmark in (reply.get("landmarks") or []) if outdoors else []:
            landmark = str(landmark).strip().lower()
            # Only what the game actually mentions here, so a made-up landmark can't follow the player around
            if landmark and landmark.split()[-1] in text.lower() and landmark not in known:
                if look := self.design_landmark(landmark, text):
                    found[landmark] = look
        return Description(prompt, found, outdoors)

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
