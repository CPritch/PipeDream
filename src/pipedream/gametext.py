"""Reading the game's output: reflowing it, finding room titles and darkness, and cleaning up image prompts."""
import re

# Lowercase words allowed inside a room title, as in "West of House" or "Top of the Well"
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "off", "on", "onto", "or",
                "over", "the", "to", "under", "with"}
# One-word replies that look like titles but aren't
_NOT_TITLES = {"ok", "okay", "done", "taken", "dropped", "yes", "no", "what", "bah", "huh", "darkness"}
# Commands that never change what the player can see, so the director isn't asked
_NO_VISIBLE_EFFECT = {"i", "inv", "inventory", "score", "examine", "x", "look", "l", "read", "wait", "z", "save",
                      "restore", "restart", "undo", "verbose", "brief", "superbrief", "help", "hint", "hints", "about",
                      "info", "credits", "version", "listen", "smell", "sniff", "think", "diagnose", "jump", "sing",
                      "shout", "yell", "scream", "hello", "hi", "echo", "sleep", "notify", "script", "unscript"}
_DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "u", "d", "north", "south", "east", "west", "northeast",
               "northwest", "southeast", "southwest", "up", "down", "in", "out", "enter", "exit", "go"}
_DARK = re.compile(r"\b(pitch[- ](black|dark)|too dark to see|dark place)\b", re.IGNORECASE)


def unwrap(text, width):
    """Join lines the game broke only because they hit its screen width, so the GUI can wrap them itself.

    A line was wrapped if the next line's first word wouldn't have fitted on it. Blank lines and indented
    lines (e.g. container contents) always start a new line.
    """
    if width < 60:
        return text
    lines = text.split("\n")
    out, previous = [lines[0]], lines[0].rstrip()
    for line in lines[1:]:
        first_word = line.split(" ", 1)[0]
        if previous and line and not line[0].isspace() and len(previous) + 1 + len(first_word) > width:
            out[-1] = f"{out[-1].rstrip()} {line}"
        else:
            out.append(line)
        previous = line.rstrip()  # the decision depends on the game's line, not on what it's been joined to
    return "\n".join(out)


def is_title(line):
    """Whether a line looks like a room title, e.g. "West of House" or "The Troll Room"."""
    line = re.sub(r"\s*\([^)]*\)$", "", line.strip())  # Inform adds the player's position: "Kitchen (on the chair)"
    if not line or len(line) > 40 or line[-1] in ".!?,;:" or re.search(r"[:/|=*\[\]<>{}\"]", line):
        return False
    words = line.split()
    if not line[0].isupper() or len(words) > 6 or line.lower() in _NOT_TITLES:
        return False
    if line.isupper() and len(words) > 2:  # banners like "WELCOME TO ADVENTURE"
        return False
    for word in words[1:]:
        core = word.strip("'-#,")
        if core and not core[0].isdigit() and core.lower() not in _SMALL_WORDS and not core[0].isupper():
            return False
    return True


def find_title(text):
    """The last room title in the text (it follows any messages about the move), or None."""
    titles = [line.strip() for line in text.splitlines() if is_title(line)]
    return re.sub(r"\s*\([^)]*\)$", "", titles[-1]) if titles else None


def from_title(text, title):
    """The part of the text from the title on: the place itself, without messages printed before the move."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if is_title(lines[i]) and lines[i].strip().startswith(title):
            return "\n".join(lines[i:]).strip()
    return text.strip()


def has_no_visible_effect(command, titled):
    """Commands whose reply can't change the scene. In games with room titles, a move that printed no title failed."""
    words = (command or "").strip().lower().split()
    if not words:
        return False
    return words[0] in _NO_VISIBLE_EFFECT or (titled and (len(words) == 1 or words[0] == "go") and words[0] in _DIRECTIONS)


def command_object(command):
    """What a command acts on, as the game names it: "open trap door" -> "trap door", "put painting in case" ->
    "case", "kill troll with sword" -> "troll". Empty when there's nothing specific ("take all", "jump")."""
    words = (command or "").strip().lower().split()
    rest = re.sub(r"^(up|down|on|off|out|over|at|to)\s+", "", " ".join(words[1:]))  # "pick up lamp", "turn on lamp"
    if placed := re.match(r".+?\s+(?:in|into|on|onto|inside|under)\s+(.+)", rest):
        rest = placed[1]  # what changes is where it was put
    rest = re.sub(r"\s+(?:with|using|from)\s+.+$", "", rest)
    rest = re.sub(r"^(the|a|an)\s+", "", rest).strip()
    return "" if rest in ("", "all", "everything", "it", "them") else rest


_NOT_NAMES = {"a", "an", "the", "this", "that", "your", "its", "his", "her", "their", "my", "one", "some", "no", "any",
              "each", "every", "another", "other", "of", "and", "or", "with", "on", "in", "to", "is", "are"}


def full_name(thing, text):
    """What the game calls `thing` in full, if it calls it only one thing: "case" -> "trophy case" in the Living Room.

    The player's word alone can find the wrong thing in the picture (a chest by the trap door for "case").
    """
    if not thing or " " in thing:
        return thing
    names = {m.lower() for m in re.findall(rf"\b([a-z][a-z-]* {re.escape(thing)})\b", text, re.IGNORECASE)
             if m.split()[0].lower() not in _NOT_NAMES}
    return names.pop() if len(names) == 1 else thing


def is_dark(text, title=None):
    """Whether the player can't see. Inform games title a dark room "Darkness"."""
    return (title is None or title.lower() == "darkness") and bool(_DARK.search(text))


def without_possessions(text):
    """Drop sentences about the player's own things ("Your sword is glowing"); they aren't part of the scene."""
    return re.sub(r"(^|(?<=[.!?]\s))Your [^.!?]*[.!?]\s*", "", text, flags=re.MULTILINE).strip()


_DIRECTION = r"(?:(?:north|south)(?:-?(?:east|west))?|east|west)"
# "to the north" goes entirely, but in "at the north end" only the direction does
# Words after which a direction was the end of its phrase ("to the south are visible"), not an adjective
_NOT_NOUNS = r"(?:and|or|are|is|was|were|lie|lies|lead|leads|stand|stands|open|opens|of|with|through|into|beyond|where|which|that)"
_COMPASS = re.compile(rf"(?:\b(?:to|toward|towards|from|on|at)\s+(?:the\s+)?(?={_DIRECTION}(?:ern|ward|wards)?\b(?!\s+(?!{_NOT_NOUNS}\b)[a-z]{{3,}}\b)))?"
                      rf"\b{_DIRECTION}(?:ern|ward|wards)?\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(no|not|none|nothing|without|never|nor|cannot|can't|isn't|aren't|doesn't|don't|nowhere)\b",
                       re.IGNORECASE)
# "with no door", "without windows", "no visible openings or entrances"
_NEGATED_PHRASE = re.compile(r",?\s*\b(?:with|and|but|having)?\s*\b(?:no|without)\s+(?:[\w-]+\s+){0,2}?[\w-]+"
                             r"(?:\s+(?:or|and)\s+[\w-]+)?(?=\s*[,.;!?]|$)", re.IGNORECASE)
# Exit listings: "Exits: north", "Up: dark staircase", "A path leads to the forest"
_EXITS = re.compile(rf"\b(exits?|leads? to|paths? to)\b|\b({_DIRECTION}|up|down|in|out)\s*:", re.IGNORECASE)
# What's left of "A doorway is to the east" once the direction has gone
_DANGLING = re.compile(r"\b(is|are|was|were|lies|lie|leads?|goes|go|runs|opens|stands|sits|winds|continues|extends|heads|"
                       r"facing|faces|toward|towards|beyond|into|onto|to|from|of|with|at|near|holds|contains|has|features|shows|"
                       r"leading|running|winding|stretching|heading|going)[.!?]?$",
                       re.IGNORECASE)


_OPEN_AIR = r"(?:sky|skies|sun|sunlight|daylight|moon|moonlight|stars|open air|fresh air|windows?)"


def underground(prompt):
    """A prompt for a place underground, without the sky or windows a small model gives it anyway ("a chasm's edge
    under open sky", "a gallery with tall windows")."""
    prompt = re.sub(rf"\s*,?\s*(?:\ball\s+)?\b(?:under|beneath|below|in|into|to|towards?|against|through|with|of|by)\s+(?:the\s+|an?\s+)?"
                    rf"(?:[\w-]+\s+){{0,2}}{_OPEN_AIR}\b", "", prompt, flags=re.IGNORECASE)
    kept = [s for s in re.split(r"(?<=[.!?])\s+", prompt) if not re.search(rf"\b{_OPEN_AIR}\b", s, re.IGNORECASE)]
    return " ".join(kept)


def clean_prompt(prompt):
    """Remove what image models get wrong: they paint direction words as text and draw whatever is negated."""
    kept = []
    prompt = re.sub(r"^[\w' -]{1,40}:\s*", "", prompt.replace('"', "").strip())  # a "Behind House:" label gets painted
    for sentence in re.split(r"(?<=[.!?])\s+", prompt):
        if not sentence or _EXITS.search(sentence) or re.match(r"(no|nothing|none|there (is|are) no)\b", sentence, re.I):
            continue
        if re.fullmatch(r"[\w ]{1,20}:\s*[\w ,'-]{0,40}[.!?]?", sentence):  # "Outdoors: true." "Landmarks: white house."
            continue
        sentence = _NEGATED_PHRASE.sub("", sentence)
        if _NEGATION.search(sentence):
            continue
        sentence = re.sub(rf"\b{_DIRECTION}\s+of\b", "near", sentence, flags=re.IGNORECASE)  # "field west of a house"
        # Painters draw "a boarded door" as a plank door; spell out what boarding looks like
        sentence = re.sub(r"\bboarded(?:[- ]up)?\s+((?:[\w-]+\s+)?(?:door|doors|window|windows))\b",
                          r"\1 with wooden planks nailed across", sentence, flags=re.IGNORECASE)
        sentence = re.sub(rf"\b(?:from\s+)?(?:the\s+)?{_DIRECTION}\s+to\s+(?:the\s+)?{_DIRECTION}\b", "", sentence,
                          flags=re.IGNORECASE)  # "running from east to west"
        sentence = re.sub(rf",?\s*\b{_DIRECTION}(?:ern)?-[a-z]+\b", "", sentence, flags=re.IGNORECASE)  # "east-facing"
        # A list of directions goes whole: one at a time, "passages east and south visible" keeps its "and"
        sentence = re.sub(rf"(?:\b(?:to|toward|towards|from|on|at)\s+(?:the\s+)?)?\b{_DIRECTION}(?:ern|ward|wards)?"
                          rf"(?:\s*,\s*(?:and\s+)?(?:the\s+)?{_DIRECTION}|\s+(?:and|or)\s+(?:the\s+)?{_DIRECTION})+(?:ern|ward|wards)?\b",
                          "", sentence, flags=re.IGNORECASE)
        sentence = _tidy(_COMPASS.sub("", sentence))
        # "A passage to the north and a passage to the south" leaves two of the same, which get painted as two doors
        sentence = re.sub(r"\ban? ([a-z]+) and an? \1\b", r"two \1s", sentence, flags=re.IGNORECASE)
        if _DANGLING.search(sentence) and "," in sentence:
            sentence = sentence[:sentence.rindex(",")] + "."  # "boarded windows, facing." loses only its last clause
        if len(sentence.split()) >= 2 and not _DANGLING.search(sentence):
            kept.append(sentence)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m[1] + m[2].upper(), " ".join(kept))


def _tidy(sentence):
    """Clean up after removed words: "with east and south passages", "an east-west corridor", "To the south, a"."""
    sentence = re.sub(r"(^|\s)-(?=\s)", r"\1", sentence)
    sentence = re.sub(r"\b(with|and|or)\s+(?:and|or)\b", r"\1", sentence)
    sentence = re.sub(r"\s+(?:and|or)(?=\s*[.,;!?])", "", sentence)
    sentence = re.sub(r"\s+(?:leading|running|winding|stretching|heading|going)(?=\s+(?:and|or)\b|\s*[,;])", "", sentence)
    # A clause left with only its verb ("The room holds, and the floor is bare") goes, up to its comma
    sentence = re.sub(r"^[^,]*\b(?:holds|contains|has|have|features|shows|is|are)\s*,\s*(?:and\s+|but\s+)?", "", sentence)
    sentence = re.sub(r"\s+([.,;!?])", r"\1", re.sub(r"\s{2,}", " ", sentence)).strip()
    sentence = re.sub(r"^[,;]\s*", "", sentence)
    sentence = re.sub(r"[,;]\s*([.!?])$", r"\1", sentence)
    return sentence if not sentence or sentence[-1] in ".!?" else sentence + "."


def apply_landmarks(prompt, landmarks):
    """Describe each recurring landmark the same way wherever it's seen, so the white house stays one house:
    "A white house, all windows boarded" -> "A two-storey white clapboard farmhouse ..., all windows boarded"."""
    for name, look in landmarks.items():
        body = re.sub(r"^(a|an|the)\s+", "", look.strip().rstrip("."), flags=re.IGNORECASE)

        def described(match):
            if not match[1]:
                return body[:1].upper() + body[1:] if match[0][0].isupper() else body
            article = match[1] if match[1].lower() == "the" else ("an" if body[:1].lower() in "aeiou" else "a")
            return f"{article.capitalize() if match[1][0].isupper() else article} {body}"

        prompt = re.sub(rf"\b(?:(a|an|the)\s+)?{re.escape(name)}\b", described, prompt, count=1, flags=re.IGNORECASE)
    return prompt
