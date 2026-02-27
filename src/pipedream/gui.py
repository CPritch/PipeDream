import sys
import os
import queue
import argparse
import signal
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, Property, QThread
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from pipedream.engine import PipeDream

class GameWorker(QThread):
    text_received = Signal(str)
    image_received = Signal(str)
    cost_updated = Signal(float)
    status_updated = Signal(str)

    def __init__(self, command, style=None, clear_cache=False):
        super().__init__()
        self.command = command
        self.style = style
        self.clear_cache = clear_cache
        self.input_queue = queue.Queue()
        self.engine = None

    def run(self):
        self.engine = PipeDream(
            self.command, 
            style=self.style, 
            clear_cache=self.clear_cache
        )
        
        # HOOKS
        self.engine.custom_print = self.handle_game_text
        self.engine.custom_input = self.handle_input
        self.engine.custom_image = self.handle_image
        self.engine.cost_callback = self.handle_cost
        self.engine.status_callback = self.handle_status
        
        self.engine.start()

    def handle_game_text(self, text):
        self.text_received.emit(text)

    def handle_image(self, path):
        full_path = Path(path).absolute().as_uri()
        self.image_received.emit(full_path)
        self.status_updated.emit("Ready") # Reset status when image arrives

    def handle_cost(self, amount):
        self.cost_updated.emit(amount)

    def handle_status(self, msg):
        self.status_updated.emit(msg)

    def handle_input(self, prompt=""):
        return self.input_queue.get()

    def send_command(self, cmd):
        self.input_queue.put(cmd)

class Backend(QObject):
    textChanged = Signal()
    imageChanged = Signal()
    costChanged = Signal()   # New
    statusChanged = Signal() # New

    def __init__(self, command, style=None, clear_cache=False):
        super().__init__()
        self._text = ""
        self._image = ""
        self._cost = 0.0000
        self._status = "Initializing..."
        
        self._check_api_key()
        
        self.worker = GameWorker(command, style, clear_cache)
        self.worker.text_received.connect(self.append_text)
        self.worker.image_received.connect(self.update_image)
        self.worker.cost_updated.connect(self.add_cost)
        self.worker.status_updated.connect(self.update_status)
        self.worker.start()

    def _check_api_key(self):
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            self._text += "\n⚠️ MISSING API KEY. VISUALS DISABLED.\n"

    # --- PROPERTIES ---
    @Property(str, notify=textChanged)
    def console_text(self): return self._text

    @Property(str, notify=imageChanged)
    def current_image(self): return self._image

    @Property(str, notify=costChanged)
    def session_cost(self): return f"{self._cost:.4f}"

    @Property(str, notify=statusChanged)
    def status_message(self): return self._status

    # --- SLOTS ---
    @Slot(str)
    def send_command(self, cmd):
        self.append_text(f"> {cmd}\n")
        self.worker.send_command(cmd)

    def append_text(self, new_text):
        self._text += new_text + "\n"
        self.textChanged.emit()

    def update_image(self, path):
        self._image = path
        self.imageChanged.emit()

    def add_cost(self, amount):
        self._cost += amount
        self.costChanged.emit()

    def update_status(self, msg):
        self._status = msg
        self.statusChanged.emit()

    def shutdown(self):
        if self.worker.engine:
            self.worker.engine.cleanup()
        self.worker.quit()
        self.worker.wait()

def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    parser = argparse.ArgumentParser(description="PipeDream GUI")
    
    parser.add_argument('--art-style', dest='style', type=str, default=None, help="Visual style prompt")
    parser.add_argument('--clear-cache', action='store_true', help="Clear image cache")
    parser.add_argument('game_command', nargs=argparse.REMAINDER, help="Command to run")

    args = parser.parse_args()

    if not args.game_command:
        print("[*] No game specified. Launching internal demo...")
        game_cmd = f"{sys.executable} -m pipedream.games.mock_game" 
    else:
        game_cmd = " ".join(args.game_command)

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    backend = Backend(game_cmd, style=args.style, clear_cache=args.clear_cache)
    engine.rootContext().setContextProperty("backend", backend)

    qml_file = Path(__file__).parent / "ui/main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    app.aboutToQuit.connect(backend.shutdown)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()