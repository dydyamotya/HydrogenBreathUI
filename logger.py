import pathlib
import datetime
import numpy as np

class DataLogger():
    def __init__(self, path_to_save_logs):

        self.path_to_save_logs = pathlib.Path(path_to_save_logs)
        self.file = (self.path_to_save_logs / datetime.datetime.now().isoformat()).with_suffix(".log")

    def save_data(self, resistances, conc):
        with self.file.open("a") as fd:
            np.hstack([resistances, conc]).tofile(fd, sep="\t")
            fd.write("\n")
