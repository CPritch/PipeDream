import json
import re
import requests

from pipedream.gametext import clean_prompt

# llama-server constrains each reply to a JSON schema, so there's no free text to parse. Each question is kept
# narrow because a 4B model answers one clear question far more reliably than a combined one.
PLACE_SCHEMA = {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}
CHANGE_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["none", "change", "dark"]}, "edit": {"type": "string"}},
    "required": ["kind", "edit"],
}
LOCATION_SCHEMA = {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}

# Image models paint direction words as text and draw anything negated, so prompts never contain either
_PICTURE_RULES = (
    "Never mention directions (north, south, east, west), exits or where paths lead. "
    "Never mention words, signs, labels or writing. "
    "Never say what is absent, hidden or not visible, and never use the words no, not or without: describe only "
    "what is there. "
    "The viewer is the player: never include the player, 'you', or anything the player is carrying."
)

DESCRIBE_PLACE = (
    "You turn a text adventure's description of a place into a prompt for an image model.\n"
    "Write under 50 words of plain visual description: the setting, its architecture and lighting, the notable "
    "objects in it, and any creatures or people the text says are here. Include only what the text says is here; "
    "don't invent objects, doors or details. "
    f"{_PICTURE_RULES}\n"
    "Example: 'A cramped kitchen with a worn wooden table holding a glass bottle of water and a brown sack. "
    "A dark staircase climbs into shadow beside a small open window.'"
)

ASSESS_CHANGE = (
    "You are the visual director for a text adventure. The player is in a place that has already been painted. "
    "Decide whether the game's latest reply visibly changes that picture.\n"
    "kind:\n"
    "- none: nothing in the picture changes. This includes failed actions, inventory, score, sounds, smells, "
    "conversation, picking up or dropping small items, and anything about the player's own possessions. "
    "For example 'Taken.', 'Dropped.', 'You are carrying a lamp.', 'The troll fends you off.', 'Nothing happens.'\n"
    "- change: the scene visibly changes. A door, window, hatch or container opens or closes; something is moved "
    "aside, revealed, broken, lit or put out; a creature or person arrives, leaves, is knocked out or killed.\n"
    "- dark: the player can no longer see anything.\n"
    "edit: empty unless kind is change. Otherwise one short sentence describing how that part of the scene looks "
    "now, e.g. 'The trap door in the floor stands open, revealing a rickety staircase.' or 'The troll is gone; "
    f"wisps of black fog hang where it stood.' {_PICTURE_RULES}"
)

LOCATE = (
    "You read a text adventure's latest reply. If it describes the place the player is in, answer with a short "
    "canonical name for that place (at most 4 words). Otherwise answer 'none'. Titles, banners, credits, "
    "instructions, questions and the results of actions are not places."
)


class Director:
    def __init__(self, llm_url):
        self.llm_url = llm_url

    def describe_place(self, name, text):
        """A prompt for painting a place the player has just found, or None."""
        reply = self._ask(DESCRIBE_PLACE, f"PLACE: {name}\n\n{text}", PLACE_SCHEMA)
        prompt = clean_prompt(reply.get("prompt", "")) if reply else ""
        return prompt if len(prompt) >= 5 else None

    def assess(self, place, text, command):
        """What the reply does to the current place's picture: ("none" | "change" | "dark", edit instruction)."""
        painted = " ".join([place.prompt, *place.edits])
        reply = self._ask(ASSESS_CHANGE, f"PLACE: {place.name}\nTHE PICTURE SHOWS: {painted}\n\n"
                                         f"PLAYER: {command}\nGAME: {text}", CHANGE_SCHEMA)
        if not reply:
            return "none", ""
        kind = reply.get("kind", "none")
        edit = clean_prompt(reply.get("edit", "")) if kind == "change" else ""
        if kind not in ("change", "dark") or (kind == "change" and len(edit) < 5):
            return "none", ""
        return kind, edit

    def locate(self, text):
        """For games without room titles: the name of the place the text describes, or None."""
        reply = self._ask(LOCATE, text, LOCATION_SCHEMA, max_tokens=24)
        location = (reply or {}).get("location", "").strip().strip(".")
        return location if location and location.lower() != "none" else None

    def _ask(self, system, user, schema, max_tokens=200):
        try:
            response = requests.post(f"{self.llm_url}/v1/chat/completions", timeout=120, json={
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
                # Hybrid reasoning models (e.g. Qwen3-4B) otherwise think first and leave the JSON reply empty
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "schema": schema}},
            })
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[!] Director Error: {e}")
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
