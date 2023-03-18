import pyqtgraph as pg
import logging
import numpy as np
import pathlib

class PlotWidget(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.getPlotItem().showGrid(x=True, y=True)

    def plot_answer(self, times, resistances):
        self.getPlotItem().clear()
        self.getPlotItem().setLogMode(y=True)
        self.getPlotItem().setLabel("left", "Resistance", units="Ω")
        self.getPlotItem().setLabel("bottom", "Time", units="ds")
        self.plot(x=times, y=resistances)

    def plot_heater_calibration(self, voltages, temperatures, ms_voltages, ms_temperatures, ms_voltages_recalc, ms_temperatures_recalc):
        self.getPlotItem().clear()
        self.getPlotItem().setLogMode(y=False)
        self.getPlotItem().setLabel("left", "Temperature", units="°C")
        self.getPlotItem().setLabel("bottom", "Voltage", units="V")
        data = np.hstack([voltages, temperatures, ms_voltages, ms_temperatures]).reshape((2, -1)).T
        data2 = np.hstack([ms_voltages, ms_temperatures]).reshape((2, -1)).T
        data3 = np.hstack([ms_voltages_recalc, ms_temperatures_recalc]).reshape((2, -1)).T
        np.savetxt(pathlib.Path.cwd() / "pasha_version.csv", data)
        np.savetxt(pathlib.Path.cwd() / "ms_version.csv", data2)
        np.savetxt(pathlib.Path.cwd() / "ms_version_recalc.csv", data3)
        self.plot(x=voltages, y=temperatures, pen=pg.mkPen("green"))
        self.plot(x=ms_voltages, y=ms_temperatures, pen=pg.mkPen("blue"))
        self.plot(x=ms_voltages_recalc, y=ms_temperatures_recalc, pen=pg.mkPen("red"))

