import socket
from PySide2 import QtWidgets, QtCore
import pathlib

def set_gas_state(gas_state: str, host: str, port: int):
    sock = socket.socket(family=socket.AF_INET, type=socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        return 1
    else:
        try:
            sock.send(gas_state.encode("utf-8"))
        except Exception as e:
            return 1
        else:
            sock.close()
            return 0

class GasStandTimer(QtCore.QTimer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout.connect(self.call_next)
        self.setSingleShot(True)
        self.current_state = ""

    def load_file(self, path: pathlib.Path, host: str, port: int):
        if self.check_file(path):
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("File is bad-formatted")
            msg_box.exec_()
        self._file_iterator = path.open("r")
        self.host = host
        self.port = port

    def stop(self):
        super().stop()
        self.current_state = ""
        self._file_iterator = None

    def call_next(self):
        try:
            interval, gas_state = map(int, next(self._file_iterator).strip().split())
        except StopIteration:
            self.stop()
        except TypeError:
            self.stop()
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Please, reload gas file")
            msg_box.exec_()
            return
        else:
            result = set_gas_state(str(gas_state), self.host, self.port)
            if result:
                self.stop()
                msg_box = QtWidgets.QMessageBox()
                msg_box.setText("Can't connect to gas stand server")
                msg_box.exec_()
                return
            self.current_state = str(gas_state)
            self.setInterval(interval * 1000)
            self.start()

    def check_file(self, path: pathlib.Path):
        with path.open("r") as fd:
            for line in fd:
                try:
                    interval, gas_state = map(int, line.strip().split())
                except:
                    return 1
            else:
                return 0


