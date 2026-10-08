import sys
import queue
import signal
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, Property
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from pipedream.engine import PipeDream, build_parser
from pipedream.runtime import LocalBackends

class GameWorker(QObject):
    text_received = Signal(str)
    image_received = Signal(str)
    status_updated = Signal(str)

    def __init__(self, command, style=None, clear_cache=False, img2img=False, llm_url=None, image_url=None):
        super().__init__()
        self.command = command
        self.style = style
        self.clear_cache = clear_cache
        self.img2img = img2img
        self.backends = LocalBackends(llm_url, image_url)
        self.input_queue = queue.Queue()
        self.engine = None
        # Daemon so a slow image request can't keep the app alive after the window closes
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self, timeout=2.0):
        self.input_queue.put(None)  # unblocks handle_input; the engine treats None as "stop"
        if self.engine:
            self.engine.cleanup()
        self.backends.stop()
        self.thread.join(timeout)

    def run(self):
        try:
            # Started from this thread on purpose: the servers are tied to the thread that spawns them
            self.backends.start(progress=self.handle_status)
        except Exception as e:
            self.text_received.emit(f"[!] Could not start the local AI models: {e}")
            self.status_updated.emit("Error")
            return

        self.engine = PipeDream(
            self.command, 
            style=self.style, 
            clear_cache=self.clear_cache,
            img2img=self.img2img,
            llm_url=self.backends.llm_url,
            image_url=self.backends.image_url
        )
        
        # HOOKS
        self.engine.custom_print = self.handle_game_text
        self.engine.custom_input = self.handle_input
        self.engine.custom_image = self.handle_image
        self.engine.status_callback = self.handle_status
        
        self.status_updated.emit("Ready")
        self.engine.start()

    def handle_game_text(self, text):
        self.text_received.emit(text)

    def handle_image(self, path):
        full_path = Path(path).absolute().as_uri()
        self.image_received.emit(full_path)
        self.status_updated.emit("Ready") # Reset status when image arrives

    def handle_status(self, msg):
        self.status_updated.emit(msg)

    def handle_input(self, prompt=""):
        return self.input_queue.get()

    def send_command(self, cmd):
        self.input_queue.put(cmd)

class Backend(QObject):
    textChanged = Signal()
    imageChanged = Signal()
    statusChanged = Signal()

    def __init__(self, command, style=None, clear_cache=False, img2img=False, llm_url=None, image_url=None):
        super().__init__()
        self._text = ""
        self._image = ""
        self._status = "Initializing..."
        
        self.worker = GameWorker(command, style, clear_cache, img2img, llm_url, image_url)
        self.worker.text_received.connect(self.append_text)
        self.worker.image_received.connect(self.update_image)
        self.worker.status_updated.connect(self.update_status)
        self.worker.start()

    # --- PROPERTIES ---
    @Property(str, notify=textChanged)
    def console_text(self): return self._text

    @Property(str, notify=imageChanged)
    def current_image(self): return self._image

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

    def update_status(self, msg):
        self._status = msg
        self.statusChanged.emit()

    def shutdown(self):
        self.worker.stop()

def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    args = build_parser("PipeDream GUI").parse_args()

    if not args.game_command:
        print("[*] No game specified. Launching internal demo...")
        game_cmd = [sys.executable, "-m", "pipedream.games.mock_game"]
    else:
        game_cmd = args.game_command

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    backend = Backend(game_cmd, style=args.style, clear_cache=args.clear_cache, img2img=args.img2img,
                      llm_url=args.llm_url, image_url=args.image_url)
    engine.rootContext().setContextProperty("backend", backend)

    qml_file = Path(__file__).parent / "ui/main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    app.aboutToQuit.connect(backend.shutdown)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()