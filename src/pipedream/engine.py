import re
import sys
import argparse
import pexpect
from pipedream.director import Director
from pipedream.cache import SmartCache
from pipedream.navigator import Navigator

if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

class PipeDream:
    def __init__(self, command, style=None, clear_cache=False):
        self.command = command
        self.process = None
        self.prompt_pattern = r'[>:]\s*$' 
        self.last_input = None
        self.previous_text = ""

        safe_cmd = re.sub(r'[^a-zA-Z0-9]', '_', command.strip().lower())
        safe_style = re.sub(r'[^a-zA-Z0-9]', '_', str(style).strip().lower())[:20] 
        self.session_id = f"{safe_cmd}_{safe_style}"

        self.director = Director(self, style_prompt=style)
        self.cache = SmartCache(self, session_id=self.session_id, style_prompt=style)
        self.navigator = Navigator(session_id=self.session_id)

        if clear_cache:
            self.cache.clear()
            self.navigator.reset()

        self.custom_print = print
        self.custom_input = input
        self.custom_image = self.default_image_handler

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
            self.process = PopenSpawn(self.command, encoding='utf-8')
        else:
            self.process = pexpect.spawn(self.command, encoding='utf-8')
            
        try:
            self.read_until_prompt()
            
            while True:
                user_input = self.custom_input("USER > ") 

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
        if self.last_input and lines and self.last_input in lines[0]:
            return "\n".join(lines[1:]).strip()
            
        return text.strip()

    def trigger_pipeline(self, text):
        if len(text.strip()) < 5:
            return

        print(f"\n[PIPEDREAM] Analyzing Scene...")
        self.report_status("Analyzing...")
        
        visual_prompt = self.director.describe_scene(text, self.previous_text)
        
        if visual_prompt:
             print(f"   > Prompt: {visual_prompt}")
        else:
             print("   > No visual changes.")
             self.report_status("Ready")

        image_path = self.navigator.process_move(
            visual_prompt, 
            self.cache.generate, 
            text
        )

        if image_path:
            print(f"   > Image ready.")
            self.custom_image(image_path)

        self.previous_text = text

def main():
    parser = argparse.ArgumentParser(description="PipeDream: AI Visualizer for Interactive Fiction")
    
    parser.add_argument('--art-style', dest='style', type=str, default=None, help="Visual style prompt")
    parser.add_argument('--clear-cache', action='store_true', help="Wipe cache before starting")
    parser.add_argument('game_command', nargs=argparse.REMAINDER, help="The command to run the game")
    
    args = parser.parse_args()
    if not args.game_command:
        parser.print_help()
        sys.exit(1)

    full_command = " ".join(args.game_command)
    engine = PipeDream(full_command, style=args.style, clear_cache=args.clear_cache)
    engine.start()

if __name__ == "__main__":
    main()