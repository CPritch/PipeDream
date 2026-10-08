import os
import re
import sys
import queue
import shlex
import hashlib
import argparse
import threading
from pathlib import Path
import pexpect
from pipedream import __version__
from pipedream.director import Director
from pipedream.gametext import find_title, from_title, has_no_visible_effect, is_dark, unwrap, without_possessions
from pipedream.imagegen import ImageGenerator, Painter
from pipedream.runtime import LocalBackends, PROFILES, DEFAULT_PROFILE
from pipedream.world import DEFAULT_CACHE_DIR, World

if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

def session_name(argv, style):
    """A folder name for one game and art style: readable, and short enough for Windows paths."""
    game = re.sub(r"[^a-zA-Z0-9]+", "-", os.path.basename(argv[-1]) if argv else "game").strip("-")[:40]
    digest = hashlib.sha1(f"{shlex.join(argv)}|{style}".encode()).hexdigest()[:10]
    return f"{game or 'game'}-{digest}"

class PipeDream:
    def __init__(self, command, style=None, clear_cache=False, cache_dir=None, llm_url=None, image_url=None,
                 image_model=DEFAULT_PROFILE, size=(768, 576)):
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
        self.director = Director(llm_url)
        # PIPEDREAM_SEED fixes the image seed, so a replayed game paints comparable pictures
        self.painter = Painter(ImageGenerator(image_url, profile.sampling, *size), str(self.world.images), style,
                               edits=profile.edits, seed=int(os.getenv("PIPEDREAM_SEED", "-1")))

        self.custom_print = print
        self.custom_input = input
        self.custom_image = self.default_image_handler
        self.status_callback = None

        # Scenes are illustrated on a background thread so the game never waits for a picture
        self.scenes = queue.Queue()
        self.latest_scene = 0
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
        self.custom_print(f"[*] Launching: {self.command}")
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
        place, is_new = world.arrive(name, text, command)
        if place is previous and place.image:
            # Re-described without moving ("look"): anything before the title is the result of the action
            before = text[:text.rfind(title)].strip() if title else ""
            return self._change(scene_id, place, before, command) if before else self._show(scene_id, place.image)

        print(f"   > Place: {place.name} ({'new' if is_new else 'known'})")
        if not place.image:
            if scene_id != self.latest_scene:
                return  # the player has already moved on; paint it if they come back
            scene = without_possessions(from_title(text, title) if title else text)
            prompt = self.director.describe_place(place.name, scene)
            if not prompt:
                print("   > No visual changes.")
                return self.report_status("Ready")
            print(f"   > Prompt: {prompt}")
            self.report_status("Generating Image...")
            place.prompt, place.edits = prompt, []
            place.base = place.image = self.painter.paint(prompt)
            world.save()
        self._show(scene_id, place.image)

    def _change(self, scene_id, place, text, command):
        """Something happened where the player is: update the place's picture if the scene visibly changed."""
        if place is None or not place.image or has_no_visible_effect(command, self.world.titled):
            print("   > No visual changes.")
            return self.report_status("Ready")
        kind, edit = self.director.assess(place, text, command)
        if kind == "dark":
            print("   > Dark.")
            self.world.current = None
            return self._show(scene_id, self.painter.darkness())
        if kind != "change" or not self.painter.edits:
            print("   > No visual changes.")
            return self.report_status("Ready")
        print(f"   > Edit: {edit}")
        self.report_status("Generating Image...")
        place.image = self.painter.edit(place.image, edit)
        place.edits.append(edit)
        self.world.save()
        self._show(scene_id, place.image)

    def _repaint(self):
        place = self.world.current
        if place is None or not place.prompt:
            return
        print(f"   > Repainting {place.name}")
        self.report_status("Generating Image...")
        image = place.base = self.painter.paint(place.prompt, seed=-1)
        for edit in place.edits:
            image = self.painter.edit(image, edit)
        place.image = image
        self.world.save()
        self._show(self.latest_scene, image)

    def _show(self, scene_id, path):
        # A newer scene arrived while this one was being worked on, so this picture is already out of date
        if scene_id == self.latest_scene:
            print(f"   > Image ready.")
            self.custom_image(path)
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
                           image_model=backends.profile, size=args.size)
        engine.start()

if __name__ == "__main__":
    main()