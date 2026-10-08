import re
import sys
import shlex
import argparse
import pexpect
from dotenv import load_dotenv, find_dotenv
from pipedream import __version__
from pipedream.director import Director
from pipedream.cache import SmartCache
from pipedream.navigator import Navigator

if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

class PipeDream:
    def __init__(self, command, style=None, clear_cache=False, img2img=False, cache_dir=None):
        # Keep the argv list intact so paths with spaces survive the trip to pexpect
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        self.command = shlex.join(self.argv)
        self.process = None
        self.prompt_pattern = r'[>:]\s*$' 
        self.last_input = None
        self.previous_text = ""

        safe_cmd = re.sub(r'[^a-zA-Z0-9]', '_', self.command.strip().lower())
        safe_style = re.sub(r'[^a-zA-Z0-9]', '_', str(style).strip().lower())[:20] 
        self.session_id = f"{safe_cmd}_{safe_style}"
        self.img2img = img2img

        self.director = Director(self, style_prompt=style, img2img=self.img2img)
        self.cache = SmartCache(self, session_id=self.session_id, style_prompt=style, img2img=self.img2img, cache_dir=cache_dir)
        self.navigator = Navigator(session_id=self.session_id, cache_dir=cache_dir)

        if clear_cache:
            self.cache.clear()
            self.navigator.reset()

        self.custom_print = print
        self.custom_input = input
        self.custom_image = self.default_image_handler
        self.cost_callback = None
        self.status_callback = None

    def safe_print(self, text):
        """Wrapper to ensure we call the latest custom_print"""
        if self.custom_print:
            self.custom_print(str(text))

    def default_image_handler(self, path):
        print(f"[*] IMAGE GENERATED: {path}")

    def report_cost(self, amount):
        """Helper to safely trigger cost callback"""
        if self.cost_callback:
            self.cost_callback(amount)

    def report_status(self, msg):
        """Helper to safely trigger status callback"""
        if self.status_callback:
            self.status_callback(msg)

    def start(self):
        self.custom_print(f"[*] Launching: {self.command}")
        
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
                self.navigator.set_last_command(user_input)

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
            self.trigger_pipeline(clean_text)
            
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
            return "\n".join(lines[1:]).strip()
            
        return text.strip()

    def trigger_pipeline(self, text):
        if len(text.strip()) < 5:
            return

        print(f"\n[PIPEDREAM] Analyzing Scene...")
        self.report_status("Analyzing...")
        
        cached_img = self.cache.lookup(text)
        visual_prompt = None
        
        if cached_img:
            print("   > Scene recognized from cache. Bypassing Director.")
        else:
            # Only hit the LLM if we don't know what this room looks like
            visual_prompt = self.director.describe_scene(text, self.previous_text)
            
            if visual_prompt:
                 print(f"   > Prompt: {visual_prompt}")
            else:
                 print("   > No visual changes.")
                 self.report_status("Ready")

        # Pass both the potential prompt and the potential cached image
        image_path = self.navigator.process_move(
            visual_prompt, 
            self.cache.generate, 
            text,
            pre_cached_image=cached_img
        )

        if image_path:
            print(f"   > Image ready.")
            self.custom_image(image_path)

        self.previous_text = text

def main():
    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(description="PipeDream: AI Visualizer for Interactive Fiction")
    
    parser.add_argument('--version', action='version', version=f"%(prog)s {__version__}")
    parser.add_argument('--art-style', dest='style', type=str, default=None, help="Visual style prompt")
    parser.add_argument('--clear-cache', action='store_true', help="Wipe cache before starting")
    parser.add_argument('--img2img', action='store_true', help="Enable image-to-image generation")
    parser.add_argument('game_command', nargs=argparse.REMAINDER, help="The command to run the game")
    
    args = parser.parse_args()
    if not args.game_command:
        parser.print_help()
        sys.exit(1)

    engine = PipeDream(args.game_command, style=args.style, clear_cache=args.clear_cache, img2img=args.img2img)
    engine.start()

if __name__ == "__main__":
    main()