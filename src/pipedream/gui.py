import sys
import queue
import signal
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, Property
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from pipedream.engine import PipeDream, build_parser
from pipedream.runtime import LocalBackends, DEFAULT_PROFILE

class GameWorker(QObject):
    text_received = Signal(str)
    image_received = Signal(str)
    status_updated = Signal(str)
    model_chosen = Signal(str)

    def __init__(self, command, style=None, clear_cache=False, llm_url=None, image_url=None,
                 image_model=DEFAULT_PROFILE, size=(768, 576)):
        super().__init__()
        self.command = command
        self.style = style
        self.clear_cache = clear_cache
        self.size = size
        self.backends = LocalBackends(llm_url, image_url, image_model)
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
            llm_url=self.backends.llm_url,
            image_url=self.backends.image_url,
            image_model=self.backends.profile,
            size=self.size,
            edit_url=self.backends.edit_url,
            editor=self.backends.editor,
        )
        
        # HOOKS
        self.engine.custom_print = self.handle_game_text
        self.engine.custom_input = self.handle_input
        self.engine.custom_image = self.handle_image
        self.engine.status_callback = self.handle_status
        
        self.model_chosen.emit(self.backends.profile)  # may differ from the request if this GPU can't run it
        self.status_updated.emit("Ready")
        self.engine.start()

    def handle_game_text(self, text):
        self.text_received.emit(text)

    def handle_image(self, path):
        full_path = Path(path).absolute().as_uri()
        self.image_received.emit(full_path)  # the engine says when it's Ready: more may be on the way

    def handle_status(self, msg):
        self.status_updated.emit(msg)

    def handle_input(self, prompt=""):
        return self.input_queue.get()

    def send_command(self, cmd):
        self.input_queue.put(cmd)

    def repaint(self):
        if self.engine:
            self.engine.repaint()

class Backend(QObject):
    textChanged = Signal()
    imageChanged = Signal()
    statusChanged = Signal()
    modelChanged = Signal()

    def __init__(self, command, style=None, clear_cache=False, llm_url=None, image_url=None,
                 image_model=DEFAULT_PROFILE, size=(768, 576)):
        super().__init__()
        self._text = ""
        self._image = ""
        self._status = "Initializing..."
        self._image_model = image_model
        
        self.worker = GameWorker(command, style, clear_cache, llm_url, image_url, image_model, size)
        self.worker.text_received.connect(self.append_text)
        self.worker.image_received.connect(self.update_image)
        self.worker.status_updated.connect(self.update_status)
        self.worker.model_chosen.connect(self.update_model)
        self.worker.start()

    # --- PROPERTIES ---
    @Property(str, notify=textChanged)
    def console_text(self): return self._text

    @Property(str, notify=imageChanged)
    def current_image(self): return self._image

    @Property(str, notify=statusChanged)
    def status_message(self): return self._status

    @Property(str, notify=modelChanged)
    def image_model(self): return self._image_model

    # --- SLOTS ---
    @Slot(str)
    def send_command(self, cmd):
        self.append_text(f"> {cmd}\n")
        self.worker.send_command(cmd)

    @Slot()
    def repaint(self):
        self.worker.repaint()

    def append_text(self, new_text):
        self._text += new_text + "\n"
        self.textChanged.emit()

    def update_image(self, path):
        self._image = path
        self.imageChanged.emit()

    def update_model(self, name):
        self._image_model = name
        self.modelChanged.emit()

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

    backend = Backend(game_cmd, style=args.style, clear_cache=args.clear_cache,
                      llm_url=args.llm_url, image_url=args.image_url, image_model=args.image_model, size=args.size)
    engine.rootContext().setContextProperty("backend", backend)

    qml_file = Path(__file__).parent / "ui/main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    app.aboutToQuit.connect(backend.shutdown)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()