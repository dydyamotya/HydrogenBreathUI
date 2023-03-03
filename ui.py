import sys
from PySide2 import QtWidgets, QtCore
from device import TestBench, CRCCalculator
from settings_widget import SettingsWidget
from plot_widget import PlotWidget
from logger import DataLogger
from gas_stand import set_gas_state, GasStandTimer
import typing
import logging
import pathlib
import configparser
import numpy as np
from time import sleep

logger = logging.getLogger(__name__)

def app():
    app = QtWidgets.QApplication()

    main_widget = MainWidget()

    global_application_settings = QtCore.QSettings("MotyaSoft", "HydrogenBreathUI")


    main_window = QtWidgets.QMainWindow()
    main_window.setCentralWidget(main_widget)

    settings_widget = SettingsWidget(main_window, global_application_settings)

    main_window.settings_widget = settings_widget

    menubar = main_window.menuBar()
    main_window.statusBar()

    settings_action = QtWidgets.QAction("Settings", main_window)
    menubar.addAction(settings_action)
    settings_action.triggered.connect(settings_widget.toggle_visible)

    main_window.show()

    sys.exit(app.exec_())


class MainWidget(QtWidgets.QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle("HydrogenBreathUI")
        self.gas_already_sent = False

        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.get_all_results)

        self.gasstand_timer = GasStandTimer()

        self.status_timer = QtCore.QTimer()
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self.get_gas_stand_status)
        self.status_timer.start()


        self.device_bench: typing.Optional[TestBench] = None
        self.data_logger = DataLogger(pathlib.Path.cwd())

        main_layout = QtWidgets.QVBoxLayout(self)

        gas_stand_groupbox = QtWidgets.QGroupBox("Gas Stand")

        gas_stand_groupbox_layout = QtWidgets.QVBoxLayout(gas_stand_groupbox)

        conc_lineedit_layout = QtWidgets.QHBoxLayout()
        gas_stand_groupbox_layout.addLayout(conc_lineedit_layout)

        #---------------------
        self.conc_lineedit = QtWidgets.QLineEdit()
        self.conc_lineedit.returnPressed.connect(self.conc_lineedit_return_pressed)

        opengasstand_button = QtWidgets.QPushButton("...")
        opengasstand_button.clicked.connect(self.open_conces_gas_stand_file)

        conc_lineedit_layout.addWidget(self.conc_lineedit)
        conc_lineedit_layout.addWidget(opengasstand_button)

        #----------------------
        buttons_gas_stand_layout = QtWidgets.QHBoxLayout()
        gas_stand_groupbox_layout.addLayout(buttons_gas_stand_layout)

        gas_stand_start_button = QtWidgets.QPushButton("Start")
        gas_stand_start_button.clicked.connect(self.gasstand_timer.call_next)

        gas_stand_stop_button = QtWidgets.QPushButton("Stop")
        gas_stand_stop_button.clicked.connect(self.gasstand_timer.stop)

        self.status_label = QtWidgets.QLabel()

        buttons_gas_stand_layout.addWidget(gas_stand_start_button)
        buttons_gas_stand_layout.addWidget(gas_stand_stop_button)
        buttons_gas_stand_layout.addWidget(self.status_label)
        buttons_gas_stand_layout.addStretch()




        main_layout.addWidget(gas_stand_groupbox)


        device_groupbox = QtWidgets.QGroupBox("Device")

        device_groupbox_layout = QtWidgets.QVBoxLayout(device_groupbox)
        device_groupbox_buttons_layout = QtWidgets.QHBoxLayout()
        device_groupbox_layout.addLayout(device_groupbox_buttons_layout)

        def add_button_to_groupbox(name, command):
            button = QtWidgets.QPushButton(name)
            device_groupbox_buttons_layout.addWidget(button)
            button.clicked.connect(command)

        add_button_to_groupbox("Init", self.init_device_bench)
        add_button_to_groupbox("Read status", self.read_device_status)
        add_button_to_groupbox("Start", self.start_timer)
        add_button_to_groupbox("Stop", self.stop_timer)
        add_button_to_groupbox("Get cal", self.get_heater_calibration)
        add_button_to_groupbox("Upload firmware", self.upload_firmware)

        self.trigger_time_lineedit = QtWidgets.QLineEdit()
        device_groupbox_layout.addWidget(self.trigger_time_lineedit)

        self.need_to_trigger_measurement = QtWidgets.QCheckBox("Trigger measurement")
        device_groupbox_layout.addWidget(self.need_to_trigger_measurement)

        self.plot_widget = PlotWidget()
        device_groupbox_layout.addWidget(self.plot_widget)

        self.concentration_label = QtWidgets.QLabel("H2 conc: ---")
        device_groupbox_layout.addWidget(self.concentration_label)



        main_layout.addWidget(device_groupbox)

    def conc_lineedit_return_pressed(self):
        if not self.gasstand_timer.isActive():
            try:
                host, port = self.parent().settings_widget.get_gas_stand_settings()
            except:
                pass
            else:
                set_gas_state(self.conc_lineedit.text(), host, port)


    def init_device_bench(self):
        crc = CRCCalculator()
        device_port = self.parent().settings_widget.get_device_port()
        if not device_port:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Device port not set")
            msg_box.exec_()
            return
        if self.device_bench is not None:
            self.device_bench.ser.close()
        self.device_bench = TestBench(device_port, crc)
        self.parent().statusBar().showMessage("Device initiated")

    def _pre_device_command(self):
        if self.device_bench is None:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Initiate a device first")
            msg_box.exec_()
            return 0
        else:
            return 1

    def read_device_status(self):
        if self._pre_device_command():
            print(self.device_bench.get_status(self.parent().statusBar().showMessage))

    def device_trigger_measurement(self):
        if self._pre_device_command():
            print(self.device_bench.trigger_measurement(self.trigger_time_lineedit.text(), print=self.parent().statusBar().showMessage))

    def device_get_have_data(self):
        if self._pre_device_command():
            print(self.device_bench.get_have_data(self.parent().statusBar().showMessage))

    def start_timer(self):
        self.already_waited = 0
        self.gas_already_sent = False
        self.timer.start()

    def stop_timer(self):
        self.timer.stop()

    def get_all_results(self):
        if self._pre_device_command():
            state = self.device_bench.get_state()
            self.parent().statusBar().showMessage(f"Status: {state}, gas_already_sent: {self.gas_already_sent}, already_waited: {self.already_waited}")
            if self.need_to_trigger_measurement.isChecked():
                if state == 0: # idle
                    if self.already_waited == 8:
                        self.device_bench.trigger_measurement(float(self.trigger_time_lineedit.text()), print=self.parent().statusBar().showMessage)
                        self.already_waited = 9
                    elif self.already_waited < 8:
                        if not self.gas_already_sent:
                            host, port = self.parent().settings_widget.get_gas_stand_settings()
                            set_gas_state("0", host, port)
                            self.gas_already_sent = True
                        self.already_waited += 1
                elif state == 1: # exhale
                    self.gas_already_sent = False
                    self.already_waited = 0
                elif state == 2: # measuring
                    if not self.gas_already_sent:
                        host, port = self.parent().settings_widget.get_gas_stand_settings()
                        set_gas_state("1", host, port)
                        self.gas_already_sent = True
                elif state == 3: # purging
                    self.gas_already_sent = False
                    if self.device_bench.get_have_data()[0] == 0 and self.device_bench.get_have_result()[0] == 0:
                        times, temperatures, resistances = self.device_bench.get_cycle()
                        self.plot_widget.plot_answer(times, resistances)
                        h2conc, *_ = self.device_bench.get_result()
                        self.concentration_label.setText("H2 conc: {:2.4f} ppm".format(h2conc))
                        self.data_logger.save_data(resistances, h2conc, self.gasstand_timer.current_state)
                else:
                    pass
            else:
                if self.device_bench.get_have_data()[0] == 0 and self.device_bench.get_have_result()[0] == 0:
                    times, temperatures, resistances = self.device_bench.get_cycle()
                    self.plot_widget.plot_answer(times, resistances)
                    h2conc, *_ = self.device_bench.get_result()
                    self.concentration_label.setText("H2 conc: {:2.4f} ppm".format(h2conc))
                    self.data_logger.save_data(resistances, h2conc, self.gasstand_timer.current_state)
        else:
            self.stop_timer()

    def get_heater_calibration(self):
        if self.timer.isActive():
            return
        if self._pre_device_command():
            voltages, temperatures = self.device_bench.get_heater_calibration()
            filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Get cal file", dir="./")
            if filename:
                sensor_number, *_ = QtWidgets.QInputDialog.getInt(self, "What is the number of sensor you wanna see",
                                                                  "Sensor number:", 0)
                config = configparser.ConfigParser()
                config.read(filename[:-3] + "par")
                R0 = float(config["R0"][f"R0_{sensor_number}"].replace(",", "."))/100
                Rc = float(config["Rc"][f"Rc_{sensor_number}"].replace(",", "."))/100
                alpha = float(config["a"][f"a0_{sensor_number}"].replace(",", "."))
                T0 = float(config["T0"]["T0"].replace(",", "."))

                data = np.loadtxt(filename, skiprows=1)
                ms_temperatures = data[:, sensor_number * 2 + 1]
                R = (1  + alpha * (ms_temperatures - T0)) * (R0 - Rc) + Rc
                ms_voltages = data[:, sensor_number * 2] * R / (R + 20)
                self.plot_widget.plot_heater_calibration(voltages, temperatures, ms_voltages, ms_temperatures)

    def open_conces_gas_stand_file(self):
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Open gasstand_file", "./", "*")
        if filename:
            try:
                host, port = self.parent().settings_widget.get_gas_stand_settings()
            except:
                pass
            else:
                self.gasstand_timer.load_file(pathlib.Path(filename), host, port)
                self.conc_lineedit.setText(filename)

    def get_gas_stand_status(self):
        current_state = self.gasstand_timer.current_state
        time_remaining = self.gasstand_timer.remainingTime()
        if current_state:
            self.status_label.setText("{:3} {:4.1f}".format(current_state, time_remaining/1000))
        else:
            self.status_label.setText("Turned off")

    def upload_firmware(self):
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose firmware file", "./", "*")
        if filename:
            counter = 0
            if self.device_bench.start_ota() == 0:
                with open(filename, "rb") as fd:
                    red = fd.read(2000)
                    if len(red) != 0:
                        if self.device_bench.chunk_ota(red) == 0:
                            while True:
                                sleep(0.5)
                                if self.device_bench.check_ota() == 0:
                                    break
                            counter += 1
                            self.parent().statusBar().showMessage(f"OTA progress: {counter}")
                        else:
                            self.parent().statusBar().showMessage(f"OTA update failed on {counter} step")
                if self.device_bench.finalize_ota() == 0:
                    self.parent().statusBar().showMessage("Successful OTA update")
                else:
                    self.parent().statusBar().showMessage("Failed finalize OTA update")
            else:
                self.parent().statusBar().showMessage("Cant start OTA")

