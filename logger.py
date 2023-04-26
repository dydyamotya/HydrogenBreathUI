import pathlib
import datetime
import numpy as np

class DataLogger():
    def __init__(self, path_to_save_logs):

        self.path_to_save_logs = pathlib.Path(path_to_save_logs)
        self.file = (self.path_to_save_logs / datetime.datetime.now().isoformat().replace(":", ".")).with_suffix(".log")

    def save_data(self, resistances, conc, state, temperatures):
        if not state:
            state = -1
        state = int(state)
        with self.file.open("a") as fd:
            np.hstack([resistances, conc, state, temperatures]).tofile(fd, sep="\t")
            fd.write("\n")
