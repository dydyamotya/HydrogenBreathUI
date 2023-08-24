import pyqtgraph as pg
import logging
import numpy as np
import pathlib
import json2html
import typing
import datetime
import json

if typing.TYPE_CHECKING:
    from device import HeaterParamsTuple, HeaterCalTransformTuple

class PlotWidget(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        plot_item = self.getPlotItem()
        if plot_item is not None:
            plot_item.showGrid(x=True, y=True)
            self.legenditem = pg.LegendItem()
            self.legenditem.setParentItem(plot_item)


        self.legenditem.setVisible(False)

    def plot_answer(self, times, resistances):
        plot_item = self.getPlotItem()
        if plot_item is not None:
            plot_item.clear()
            self.legenditem.setVisible(False)
            plot_item.setLogMode(y=True)
            plot_item.setLabel("left", "Resistance", units="Ω")
            plot_item.setLabel("bottom", "Time", units="ds")
            self.plot(x=times, y=resistances)

    def plot_heater_calibration(self, voltages, temperatures,
                                ms_voltages, ms_temperatures,
                                ms_voltages_recalc, ms_temperatures_recalc,
                                voltages_cal, temperatures_cal, heater_params):
        plot_item = self.getPlotItem()
        if plot_item is None:
            return
        plot_item.clear()
        plot_item.setLogMode(y=False)
        plot_item.setLabel("left", "Temperature", units="°C")
        plot_item.setLabel("bottom", "Voltage", units="V")
        data = np.hstack([voltages, temperatures]).reshape((2, -1)).T
        data2 = np.hstack([ms_voltages, ms_temperatures]).reshape((2, -1)).T
        data3 = np.hstack([ms_voltages_recalc, ms_temperatures_recalc]).reshape((2, -1)).T
        data4 = np.hstack([voltages_cal, temperatures_cal]).reshape((2, -1)).T
        np.savetxt(pathlib.Path.cwd() / "device_version{}.csv".format(datetime.datetime.now().isoformat().replace(":", ".")), data)
        np.savetxt(pathlib.Path.cwd() / "ms_version{}.csv".format(datetime.datetime.now().isoformat().replace(":", ".")), data2)
        np.savetxt(pathlib.Path.cwd() / "ms_version_recalc{}.csv".format(datetime.datetime.now().isoformat().replace(":", ".")), data3)
        np.savetxt(pathlib.Path.cwd() / "device_cal_version{}.csv".format(datetime.datetime.now().isoformat().replace(":", ".")), data4)
        if heater_params is not None:
            with (pathlib.Path.cwd() / "heater_params{}.json".format(datetime.datetime.now().isoformat().replace(":", "."))).open("w") as fd:
                json.dump(heater_params._asdict(), fd)

        line1 = self.plot(x=voltages, y=temperatures, pen=pg.mkPen("green"))
        line2 = self.plot(x=ms_voltages, y=ms_temperatures, pen=pg.mkPen("blue"))
        line3 = self.plot(x=ms_voltages_recalc, y=ms_temperatures_recalc, pen=pg.mkPen("red"))
        line4 = self.plot(x=voltages_cal, y=temperatures_cal, pen=pg.mkPen("yellow"))

        self.legenditem.setVisible(True)

        self.legenditem.clear()
        self.legenditem.addItem(line1, "Cal voltages")
        self.legenditem.addItem(line2, "Ms")
        self.legenditem.addItem(line3, "Ms recalc")
        self.legenditem.addItem(line4, "Corrected cal")

        if heater_params is not None:
            html = json2html.json2html.convert(heater_params._asdict(),
                                               table_attributes="style=\"color: #FFF\" border=\"1\"")
            self.text_item = pg.TextItem(html=html, border="w", fill=(0, 0, 255, 100))
            self.addItem(self.text_item)
            self.text_item.setPos(0, -20)

