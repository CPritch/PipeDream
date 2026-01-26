import sys
import os
import threading
import queue
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, Property, QThread
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from pipedream.engine import PipeDream

class GameWorker(QThread):
    """Runs the pipedream engine in a background thread."""
    text_received = Signal(str)
    image_received = Signal(str)

    def __init__(self, command):
        super().__init__()
        self.command = command
        self.input_queue = queue.Queue()
        self.engine = None

    def run(self):
        self.engine = PipeDream(self.command)
        
        self.engine.custom_print = self.handle_text
        self.engine.custom_input = self.handle_input
        self.engine.custom_image = self.handle_image
        
        self.engine.start()

    def handle_text(self, text):
        self.text_received.emit(text)

    def handle_image(self, path):
        # QML needs a file URL
        full_path = Path(path).absolute().as_uri()
        self.image_received.emit(full_path)

    def handle_input(self, prompt=""):
        # Block until the GUI sends us a command
        return self.input_queue.get()

    def send_command(self, cmd):
        self.input_queue.put(cmd)

class Backend(QObject):
    """The bridge between QML and Python."""
    
    textChanged = Signal()
    imageChanged = Signal()

    def __init__(self, command):
        super().__init__()
        self._text = "PipeDream v0.2.0 initialized...\n"
        self._image = ""
        
        self.worker = GameWorker(command)
        self.worker.text_received.connect(self.append_text)
        self.worker.image_received.connect(self.update_image)
        self.worker.start()

    @Property(str, notify=textChanged)
    def console_text(self):
        return self._text

    @Property(str, notify=imageChanged)
    def current_image(self):
        return self._image

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

def main():
    if len(sys.argv) < 2:
        print("Usage: pipedream-gui <game_command>")
        sys.exit(1)

    game_cmd = " ".join(sys.argv[1:])

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    backend = Backend(game_cmd)
    engine.rootContext().setContextProperty("backend", backend)

    qml_file = Path(__file__).parent / "ui/main.qml"
    engine.load(qml_file)

    if not engine.rootObjects():
        sys.exit(-1)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()