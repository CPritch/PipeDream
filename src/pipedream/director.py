import base64
import json
import re
from typing import NamedTuple

import requests

from pipedream.gametext import clean_prompt

# llama-server constrains each reply to a JSON schema, so there's no free text to parse. Each question is kept
# narrow because a 4B model answers one clear question far more reliably than a combined one.
PLACE_SCHEMA = {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}
CHANGE_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["none", "change", "remove", "dark"]},
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
    "A dark staircase climbs into shadow beside a small open window.'"
)

ASSESS_CHANGE = (
    "You are the visual director for a text adventure. The player is in a place that has already been painted. "
    "Decide whether the game's latest reply visibly changes that picture.\n"
    "kind:\n"
    "- none: nothing in the picture changes. This includes failed actions, inventory, score, sounds, smells, "
    "conversation, and anything about the player's own possessions. "
    "For example 'You are carrying a lamp.', 'The troll fends you off.', 'Nothing happens.'\n"
    "- remove: something shown in the picture is taken away, or a creature dies and vanishes. "
    "For example 'take lamp' answered 'Taken.' removes the lamp.\n"
    "- change: the scene visibly changes. A door, window, hatch or container opens or closes; something is moved "
    "aside, revealed, broken, lit or put out; something is put down or placed; a creature arrives or is knocked out.\n"
    "- dark: the player can no longer see anything.\n"
    "target: the thing in the picture that changes or is removed, in two or three words, using the game's own name "
    "for it (e.g. 'brass lantern', 'troll', 'trap door', 'trophy case'). Empty if kind is none or dark.\n"
    "edit: an instruction for an image editor, starting with a verb, e.g. 'Remove the brass lantern.', 'Open the "
    "trap door, revealing a rickety staircase descending into darkness.', 'Remove the troll and its axe, leaving "
    "wisps of black fog where it stood.', 'Make the troll lie unconscious on the floor.', 'Place a framed painting "
    "inside the trophy case.' Empty if kind is none or "
    f"dark. {_PICTURE_RULES}"
)

FIND = "Locate the {thing} in the image and output its bbox_2d coordinates in JSON. If there is no {thing} in the image, output an empty list."

LOCATE = (
    "You read a text adventure's latest reply. If it describes the place the player is in, answer with a short "
    "canonical name for that place (at most 4 words). Otherwise answer 'none'. Titles, banners, credits, "
    "instructions, questions and the results of actions are not places."
)


class Verdict(NamedTuple):
    kind: str  # none, change, remove or dark
    target: str = ""  # what in the picture the change applies to
    edit: str = ""  # instruction for the image editor


NO_CHANGE = Verdict("none")


class Director:
    def __init__(self, llm_url):
        self.llm_url = llm_url

    def describe_place(self, name, text):
        """A prompt for painting a place the player has just found, or None."""
        for temperature in (0.2, 0.7):
            reply = self._ask(DESCRIBE_PLACE, f"PLACE: {name}\n\n{text}", PLACE_SCHEMA, temperature=temperature)
            prompt = clean_prompt(reply.get("prompt", "")) if reply else ""
            # Too little survives when the model describes mostly what isn't there; a warmer second try differs
            if len(prompt) >= 20:
                return prompt
        return prompt if len(prompt) >= 5 else None

    def assess(self, place, text, command):
        """What the reply does to the current place's picture, as a Verdict."""
        painted = " ".join([place.prompt, *(edit["edit"] for edit in place.edits)])
        reply = self._ask(ASSESS_CHANGE, f"PLACE: {place.name}\nTHE PICTURE SHOWS: {painted}\n\n"
                                         f"PLAYER: {command}\nGAME: {text}", CHANGE_SCHEMA)
        kind = (reply or {}).get("kind", "none")
        if kind == "dark":
            return Verdict("dark")
        if kind not in ("change", "remove"):
            return NO_CHANGE
        target = reply.get("target", "").strip().strip(".")
        edit = clean_prompt(reply.get("edit", ""))
        if kind == "remove" and len(edit) < 5 and target:
            edit = f"Remove the {target}."
        return Verdict(kind, target, edit) if len(edit) >= 5 else NO_CHANGE

    def find(self, image, thing):
        """Where `thing` is in the picture: a list of [x1, y1, x2, y2] boxes in 0-1000 coordinates, or []."""
        with open(image, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                   {"type": "text", "text": FIND.format(thing=thing)}]
        reply = self._chat([{"role": "user", "content": content}], max_tokens=120, temperature=0)
        boxes = [[int(n) for n in box] for box in re.findall(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", reply or "")]
        return [b for b in boxes if b[0] < b[2] <= 1000 and b[1] < b[3] <= 1000][:3]

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
            match = re.fullmatch(r'\s*\{\s*"(\w+)"\s*:\s*"(.*?[.!?])[^.!?]*', content, re.DOTALL)
            if match and len(schema["required"]) == 1:
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
