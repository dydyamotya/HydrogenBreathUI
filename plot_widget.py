import pyqtgraph as pg
import logging

class PlotWidget(pg.PlotWidget):
    def __init__(self):
        super().__init__()
        self.getPlotItem().showGrid(x=True, y=True)
        self.getPlotItem().setLogMode(y=True)

    def plot_answer(self, times, resistances):
        self.getPlotItem().clear()
        self.plot(x=times, y=resistances)
