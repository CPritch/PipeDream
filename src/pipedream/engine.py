import os
import re
import sys
import queue
import shlex
import hashlib
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pexpect
from pipedream import __version__
from pipedream.director import Director, Finding
from pipedream.gametext import (apply_landmarks, command_object, find_title, from_title, full_name, has_no_visible_effect,
                                is_dark, unwrap, without_possessions)
from pipedream.imagegen import REMOVAL_CHANGE, ImageGenerator, Painter
from pipedream.runtime import LocalBackends, PROFILES, DEFAULT_PROFILE
from pipedream.world import DEFAULT_CACHE_DIR, World

if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

# What players type versus what pictures show
_ALSO_CALLED = {"lamp": "lantern", "lantern": "lamp", "torch": "lantern", "rope": "coil of rope", "knife": "dagger"}
_UNDERGROUND = re.compile(r"\b(underground|cave|cavern|tunnel|cellar|mine|subterranean|grotto|crypt|dungeon)", re.I)

def _overlapping(boxes, originals):
    """Whether any box and an original box (0-1000 coordinates) mostly cover each other: the same thing, still there.

    A neighbour that only clips the box (the next painting along the gallery wall) doesn't count, and nor does a
    bigger thing behind it (the framed painting on the wall behind the one on the easel).
    """
    for a in boxes:
        for b in originals:
            w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
            if w > 0 and h > 0 and w * h > 0.5 * max((b[2] - b[0]) * (b[3] - b[1]), (a[2] - a[0]) * (a[3] - a[1])):
                return True
    return False

def _changed_before(edits, target):
    """Where things changed earlier are (an opened trap door), to be left as they are when something else changes.

    Not what was replaced ("Replace the rug with bare floor..."): that's floor now, and anything can go there.
    """
    return [box for e in edits if e.get("kind") == "change" and e["target"] != target
            and not e["edit"].startswith("Replace") for box in e.get("boxes", [])]

def session_name(argv, style):
    """A folder name for one game and art style: readable, and short enough for Windows paths."""
    game = re.sub(r"[^a-zA-Z0-9]+", "-", os.path.basename(argv[-1]) if argv else "game").strip("-")[:40]
    digest = hashlib.sha1(f"{shlex.join(argv)}|{style}".encode()).hexdigest()[:10]
    return f"{game or 'game'}-{digest}"

class PipeDream:
    def __init__(self, command, style=None, clear_cache=False, cache_dir=None, llm_url=None, image_url=None,
                 image_model=DEFAULT_PROFILE, size=(768, 576), edit_url=None, editor=""):
        # Keep the argv list intact so paths with spaces survive the trip to pexpect
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        self.command = shlex.join(self.argv)
        self.process = None
        self.prompt_pattern = r'[>:]\s*$' 
        self.last_input = None
        self.wrap_width = 0  # the game's screen width, learned from the longest line it prints

        self.session_id = session_name(self.argv, style)
        self.world = World(Path(cache_dir or DEFAULT_CACHE_DIR) / self.session_id)
        if clear_cache:
            self.world.reset()

        profile = PROFILES[image_model]
        editing = PROFILES[editor] if edit_url and editor else profile  # the model that makes the edits
        self.director = Director(llm_url)
        # PIPEDREAM_SEED fixes the image seed, so a replayed game paints comparable pictures
        self.painter = Painter(ImageGenerator(image_url, profile.sampling, *size), str(self.world.images), style,
                               edits=editing.edits, seed=int(os.getenv("PIPEDREAM_SEED", "-1")),
                               confine=editing.confine_edits,
                               editor=ImageGenerator(edit_url, editing.sampling, *size) if editing is not profile else None)

        self.custom_print = print
        self.custom_input = input
        self.custom_image = self.default_image_handler
        self.status_callback = None

        # Scenes are illustrated on a background thread so the game never waits for a picture
        self.scenes = queue.Queue()
        self.latest_scene = 0
        self.finder = ThreadPoolExecutor(1, thread_name_prefix="finder")  # looks for things while the director thinks
        self.last_place = None
        self.scene_thread = threading.Thread(target=self._scene_loop, daemon=True)

    def safe_print(self, text):
        """Wrapper to ensure we call the latest custom_print"""
        if self.custom_print:
            self.custom_print(str(text))

    def default_image_handler(self, path):
        print(f"[*] IMAGE GENERATED: {path}")

    def report_status(self, msg):
        """Helper to safely trigger status callback"""
        if self.status_callback:
            self.status_callback(msg)

    def start(self):
        print(f"[*] Launching: {self.command}")  # for the log; players don't need the command line
        self.scene_thread.start()
        
        if sys.platform == 'win32':
            self.process = PopenSpawn(self.argv, encoding='utf-8')
        else:
            self.process = pexpect.spawn(self.argv[0], self.argv[1:], encoding='utf-8')
            
        try:
            self.read_until_prompt()
            
            while True:
                user_input = self.custom_input("USER > ") 

                # None is the front-end asking us to stop (e.g. the window closed)
                if user_input is None:
                    break

                self.last_input = user_input.strip()

                self.process.sendline(user_input)
                
                if user_input.strip().lower() in ['quit', 'exit', 'q']:
                    break
                
                self.read_until_prompt()
                
        except pexpect.EOF:
            self.custom_print("\n[*] Game process ended.")
        except KeyboardInterrupt:
            pass 
        finally:
            self.cleanup()

    def cleanup(self):
        self.custom_print("\n[*] Stopping PipeDream...")
        self.scenes.put(None)  # the scene thread finishes what's queued, then exits
        if self.process:
            try:
                if sys.platform == 'win32':
                    self.process.proc.terminate()
                else:
                    self.process.terminate()
            except Exception:
                pass
        self.custom_print("[*] Cleanup complete.")

    def read_until_prompt(self):
        try:
            self.process.expect(self.prompt_pattern, timeout=5)
            raw_output = self.process.before.strip()
            clean_text = self.clean_output(raw_output)
            
            self.custom_print(clean_text)
            self.latest_scene += 1
            self.scenes.put(("scene", self.latest_scene, clean_text, self.last_input))
            
        except pexpect.TIMEOUT:
            pass

    def clean_output(self, text):
        if not text:
            return ""
            
        # Strip ANSI Escape Sequences
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)

        lines = text.splitlines()
        
        # Remove the echoed command (if present)
        if self.last_input and lines and lines[0].strip() == self.last_input:
            lines = lines[1:]

        # Undo the game's own line wrapping so the window can wrap to its own width
        self.wrap_width = max([self.wrap_width] + [len(line) for line in lines])
        return unwrap("\n".join(lines), self.wrap_width).strip()

    def repaint(self):
        """Paint the current place again from scratch, for when a picture has come out wrong."""
        self.scenes.put(("repaint",))

    def _scene_loop(self):
        while (job := self.scenes.get()) is not None:
            try:
                if job[0] == "repaint":
                    self._repaint()
                else:
                    self.trigger_pipeline(*job[1:])
            except Exception as e:  # one failed picture mustn't stop the rest
                print(f"[!] Scene error: {e}")
                self.report_status("Error")

    def trigger_pipeline(self, scene_id, text, command):
        if len(text.strip()) < 5:
            return

        print(f"\n[PIPEDREAM] Analyzing Scene...")
        self.report_status("Analyzing...")
        world = self.world
        title = find_title(text)

        if is_dark(text, title):
            print("   > Dark.")
            world.current = None
            return self._show(scene_id, self.painter.darkness())

        if title:
            world.titled = True
            name = title
        else:
            # Games that print room titles print one on every move, so no title means the player stayed put
            name = None if world.titled else self.director.locate(text)

        if name is None:
            return self._change(scene_id, world.current, text, command)

        previous = world.current
        came_from = previous or self.last_place  # after a dark stretch, the last place the player could see
        place, is_new = world.arrive(name, from_title(text, title) if title else text, command)
        self.last_place = place
        if place is previous and place.image:
            # Re-described without moving ("look"): anything before the title is the result of the action
            before = text[:text.rfind(title)].strip() if title else ""
            return self._change(scene_id, place, before, command) if before else self._show(scene_id, place.image)

        print(f"   > Place: {place.name} ({'new' if is_new else 'known'})")
        if not place.image:
            if scene_id != self.latest_scene:
                return  # the player has already moved on; paint it if they come back
            scene = without_possessions(from_title(text, title) if title else text)
            described = self.director.describe_place(place.name, scene, set(world.landmarks), came_from, command)
            if not described:
                print("   > No visual changes.")
                return self.report_status("Ready")
            for landmark, look in described.landmarks.items():
                print(f"   > Landmark: {landmark} is {look}")
            world.landmarks.update(described.landmarks)
            prompt = described.prompt
            if described.setting == "outdoors":
                prompt = apply_landmarks(prompt, world.landmarks)
            elif described.setting == "underground" and not _UNDERGROUND.search(prompt):
                prompt += " Deep underground, enclosed by rough rock walls and ceiling."  # else it gets a sky
            print(f"   > Prompt: {prompt}")
            self.report_status("Generating Image...")
            place.prompt, place.edits, place.setting = prompt, [], described.setting
            place.base = place.image = self.painter.paint(prompt)
            place.visitors = {}
            world.save()
        elif world.news and scene_id == self.latest_scene:
            return self._visitors(scene_id, place, world.news)
        self._show(scene_id, place.image)

    def _visitors(self, scene_id, place, lines):
        """Back at a known place, the game mentions something new (a thief, an axe on the floor): show it.

        Each line is judged on its own: asked about "There is a bloody axe here. Someone carrying a large bag is
        leaning against the wall." together, the director picks the axe, which is already in the picture.
        """
        image, shown = place.image, False
        # Remembered line by line, so news seen before (the axe) is instant and only what's new (the thief) is painted
        for i, line in enumerate(lines):
            key = " ".join(lines[:i + 1])
            if key in place.visitors and os.path.exists(place.visitors[key]):
                image = place.visitors[key]
                continue
            if not shown:
                self._show(scene_id, image, ready=False)  # what we have straight away, then what's new
                shown = True
            verdict = self.director.assess(place, line, "(comes back here)")
            if scene_id != self.latest_scene:
                return  # the player has moved on; a visitor is only worth painting while they're here
            if verdict.kind == "add" and verdict.target and self.director.sees(image, verdict.target):
                pass  # already in the picture ("There is a bloody axe here" where the troll dropped it)
            elif verdict.kind in ("change", "add", "remove") and self.painter.edits:
                self.report_status("Generating Image...")
                image = self._edit(image, verdict.kind, verdict.edit, verdict.target)[0] or image
            place.visitors[key] = image
            self.world.save()
        self._show(scene_id, image)

    def _change(self, scene_id, place, text, command):
        """Something happened where the player is: update the place's picture if the scene visibly changed."""
        if place is None or not place.image or has_no_visible_effect(command, self.world.titled):
            print("   > No visual changes.")
            return self.report_status("Ready")
        # The player's own words name the thing best; the director's guess can drift ("wooden door" for "trap door").
        # Start looking for it now, so the picture is searched while the director decides what happened.
        acted_on = full_name(command_object(command), place.text)
        search = self.finder.submit(self.director.find, place.image, acted_on) if acted_on and self.painter.edits else None
        verdict = self.director.assess(place, text, command)
        if verdict.kind == "dark":
            print("   > Dark.")
            self.world.current = None
            return self._show(scene_id, self.painter.darkness())
        if verdict.kind not in ("change", "add", "remove") or not self.painter.edits:
            print("   > No visual changes.")
            return self.report_status("Ready")
        self.report_status("Generating Image...")
        target = acted_on or verdict.target
        found = search.result() if search else None
        # By the game's full name in the instruction too: told to "open the case", the model adds a chest
        edit, short = verdict.edit, command_object(command)
        if acted_on and short and acted_on != short:
            edit = re.sub(rf"\bthe {re.escape(short)}\b", f"the {acted_on}", edit)
        last = next((e for e in reversed(place.edits) if e["target"] == target), None)
        if last and last["edit"] == edit:
            # The picture shows it already: the trap door slammed shut out of sight, and has just been opened again
            print("   > Already shown.")
            return self._show(scene_id, place.image)
        keep = _changed_before(place.edits, target)
        image, instruction, boxes = self._edit(place.image, verdict.kind, edit, target, found, keep=keep)
        if image and verdict.expect and not self.director.find(image, verdict.expect).boxes:
            # The edit was meant to reveal something the story now depends on (the trap door): one more go
            print(f"   > No {verdict.expect} to be seen yet, trying again")
            retry = self._edit(place.image, verdict.kind, edit, target, found, first_attempt=1, keep=keep)[0]
            if retry and self.director.find(retry, verdict.expect).boxes:
                image = retry
            else:
                print("   > Keeping the first attempt")  # changed less, so the more faithful of two misses
        if image is None:
            return self.report_status("Ready")
        place.image, place.visitors = image, {}
        place.edits.append({"kind": verdict.kind, "edit": instruction, "target": target, "boxes": boxes})
        self.world.save()
        self._show(scene_id, place.image)

    def _edit(self, image, kind, instruction, target, found=None, first_attempt=0, keep=()):
        """Apply an edit, confined to where the target is in the picture, and leaving `keep` (boxes) as it is.

        Changes and removals need their target in the picture; only an addition ("a thief is here") may repaint
        the whole picture. Returns (new picture, or None if nothing was done or it made no difference, instruction,
        the boxes it was made at).
        """
        if kind == "add":
            found = Finding([])
        elif found is None:
            found = self.director.find(image, target) if target else Finding([])
        if kind != "add" and not found.boxes and target in _ALSO_CALLED:
            found = self.director.find(image, _ALSO_CALLED[target])  # "take lamp" when the picture shows a lantern
        # A model that edits the whole picture can still make a change the finder can't place (a trap door it
        # can't make out); there's nothing to remove that can't be seen
        if not found.boxes and (kind == "remove" or (kind == "change" and self.painter.confine)):
            print(f"   > No {target or 'target'} in the picture to {'remove' if kind == 'remove' else 'change'}.")
            return None, instruction, []
        if kind != "add":
            # One thing changes: the finder's best match. The other paintings it saw in the gallery stay, and
            # finding them again after a removal doesn't mean the one taken is still there.
            found = Finding(found.boxes[:1], found.surface)
        if kind == "remove":
            # Naming what's left, not just what goes, stops the model painting the thing straight back in
            instruction = f"Remove the {target}, leaving only bare {found.surface or 'surroundings'} where it was."
        print(f"   > Edit: {instruction}" + (f" (at the {target})" if found.boxes else ""))
        if kind == "add" or not found.boxes or instruction.startswith("Replace"):
            edited = self.painter.edit(image, instruction, found.boxes, first_attempt=first_attempt, keep=keep)
            if edited is None:
                print("   > The edit made no visible difference, so it was dropped.")
            return edited, instruction, found.boxes
        if kind == "change":
            # A change leaves the thing there: asked to open the trophy case, the model can delete it instead
            for attempt in (first_attempt, first_attempt + 1):
                edited = self.painter.edit(image, instruction, found.boxes, first_attempt=attempt, keep=keep)
                if edited and self.director.find(edited, target).boxes:
                    return edited, instruction, found.boxes
                print(f"   > {'The ' + target + ' has gone' if edited else 'The edit made no visible difference'}"
                      + (", trying again" if attempt == first_attempt else ", so it was dropped."))
            return None, instruction, []
        # A removal can repaint the thing in place ("Remove the sword" painting a new sword), which a pixel check
        # can't tell from success, so look for it again
        for attempt in (first_attempt, first_attempt + 1):
            # Only just round the thing: the model may take the trophy beside the lamp too
            edited = self.painter.edit(image, instruction, found.boxes, REMOVAL_CHANGE, first_attempt=attempt,
                                       around=0.15, spread=False)
            if edited and not _overlapping(self.director.find(edited, target).boxes, found.boxes):
                return edited, instruction, found.boxes
            print(f"   > {'The ' + target + ' is still there' if edited else 'The edit made no visible difference'}"
                  + (", trying again" if attempt == first_attempt else ", so it was dropped."))
        return None, instruction, []

    def _repaint(self):
        place = self.world.current
        if place is None or not place.prompt:
            return
        print(f"   > Repainting {place.name}")
        self.report_status("Generating Image...")
        image = place.base = self.painter.paint(place.prompt, seed=-1)
        done = []
        for edit in place.edits:
            edited, _, boxes = self._edit(image, edit.get("kind", "change"), edit["edit"], edit["target"],
                                          keep=_changed_before(done, edit["target"]))
            image = edited or image
            done.append({**edit, "boxes": boxes})
        place.image, place.visitors = image, {}
        self.world.save()
        self._show(self.latest_scene, image)

    def _show(self, scene_id, path, ready=True):
        # A newer scene arrived while this one was being worked on, so this picture is already out of date
        if scene_id == self.latest_scene:
            print(f"   > Image ready.")
            self.custom_image(path)
            if ready:
                self.report_status("Ready")

def parse_size(value):
    match = re.fullmatch(r'(\d+)x(\d+)', value)
    if not match or int(match[1]) % 16 or int(match[2]) % 16:
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT, both multiples of 16, e.g. 768x576")
    return int(match[1]), int(match[2])

def build_parser(description):
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument('--version', action='version', version=f"%(prog)s {__version__}")
    parser.add_argument('--art-style', dest='style', type=str, default=None, help="Visual style prompt")
    parser.add_argument('--clear-cache', action='store_true', help="Wipe cache before starting")
    parser.add_argument('--llm-url', default=None, help="Use an already-running llama-server instead of starting one")
    parser.add_argument('--image-url', default=None, help="Use an already-running sd-server instead of starting one")
    parser.add_argument('--image-model', choices=list(PROFILES), default=DEFAULT_PROFILE, help="Image model (default: %(default)s)")
    parser.add_argument('--size', type=parse_size, default=(768, 576), help="Image size as WIDTHxHEIGHT (default: 768x576)")
    parser.add_argument('game_command', nargs=argparse.REMAINDER, help="The command to run the game")
    return parser

def main():
    parser = build_parser("PipeDream: AI Visualizer for Interactive Fiction")
    args = parser.parse_args()
    if not args.game_command:
        parser.print_help()
        sys.exit(1)

    with LocalBackends(args.llm_url, args.image_url, args.image_model).start() as backends:
        engine = PipeDream(args.game_command, style=args.style, clear_cache=args.clear_cache,
                           llm_url=backends.llm_url, image_url=backends.image_url,
                           image_model=backends.profile, size=args.size,
                           edit_url=backends.edit_url, editor=backends.editor)
        engine.start()

if __name__ == "__main__":
    main()