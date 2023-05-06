import sys
from PySide2 import QtWidgets, QtCore
from PySide2.QtGui import QIntValidator

from device import MSDesktopDevice, CRCCalculator, PlaceHolderDevice
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
from itertools import repeat, chain
if typing.TYPE_CHECKING:
    from device import HeaterCalTransformTuple, HeaterParamsTuple

logger = logging.getLogger(__name__)

def app():
    app = QtWidgets.QApplication()

    main_widget = MainWidget()

    global_application_settings = QtCore.QSettings("MotyaSoft", "HydrogenBreathUI")


    main_window = QtWidgets.QMainWindow()
    main_window.setWindowTitle("HydrogenBreathUI")
    main_window.setCentralWidget(main_widget)

    settings_widget = SettingsWidget(main_window, global_application_settings)

    main_window.settings_widget = settings_widget

    menubar = main_window.menuBar()
    main_window.statusBar()
    old_show_message = main_window.statusBar().showMessage
    def show_message_wrapper(message):
        logger.info(message)
        old_show_message(message)

    main_window.statusBar().showMessage = show_message_wrapper


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


        self.device_bench: typing.Optional[MSDesktopDevice] = None
        self.data_logger_path = pathlib.Path.cwd()
        self.data_logger = DataLogger(self.data_logger_path)

        main_layout = QtWidgets.QVBoxLayout(self)

        gas_stand_groupbox = QtWidgets.QGroupBox("Gas Stand")

        gas_stand_groupbox_layout = QtWidgets.QVBoxLayout(gas_stand_groupbox)

        conc_lineedit_layout = QtWidgets.QHBoxLayout()
        times_repeat_layout = QtWidgets.QHBoxLayout()
        gas_stand_groupbox_layout.addLayout(conc_lineedit_layout)
        gas_stand_groupbox_layout.addLayout(times_repeat_layout)

        #---------------------
        self.conc_lineedit = QtWidgets.QLineEdit()
        self.conc_lineedit.returnPressed.connect(self.conc_lineedit_return_pressed)

        opengasstand_button = QtWidgets.QPushButton("...")
        opengasstand_button.clicked.connect(self.open_conces_gas_stand_file)

        conc_lineedit_layout.addWidget(QtWidgets.QLabel("Path to file with gas program (you can enter state number here and hit Enter button to change manually)"))
        conc_lineedit_layout.addWidget(self.conc_lineedit)
        conc_lineedit_layout.addWidget(opengasstand_button)

        self.times_repeat_lineedit = QtWidgets.QLineEdit()
        self.times_repeat_lineedit.setValidator(QIntValidator(self))
        times_repeat_layout.addWidget(QtWidgets.QLabel("Times to repeat gas state"))
        times_repeat_layout.addWidget(self.times_repeat_lineedit)


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
        add_button_to_groupbox("Upload cal", self.upload_calibration)
        add_button_to_groupbox("Upload firmware", self.upload_firmware)

        times_layout = QtWidgets.QFormLayout()
        device_groupbox_layout.addLayout(times_layout)
        self.before_trigger_time_lineedit = QtWidgets.QLineEdit()
        self.before_trigger_time_lineedit.setValidator(QIntValidator(self))
        self.trigger_time_lineedit = QtWidgets.QLineEdit()
        self.trigger_time_lineedit.setValidator(QIntValidator(self))

        times_layout.addRow("Time before trigger (in IDLE state)", self.before_trigger_time_lineedit)
        times_layout.addRow("Time to trigger measurement: ", self.trigger_time_lineedit)


        self.need_to_trigger_measurement = QtWidgets.QCheckBox("Автоматическое переключение состояний устройства")
        device_groupbox_layout.addWidget(self.need_to_trigger_measurement)

        need_to_wait_scientist_layout = QtWidgets.QHBoxLayout()

        self.need_to_wait_for_scientist = QtWidgets.QCheckBox("Ручное управление")
        need_to_wait_scientist_layout.addWidget(self.need_to_wait_for_scientist)

        next_gas_state_button = QtWidgets.QPushButton("Следующее состояние")
        next_gas_state_button.clicked.connect(self.next_gas_iterator_state)
        need_to_wait_scientist_layout.addWidget(next_gas_state_button)

        device_groupbox_layout.addLayout(need_to_wait_scientist_layout)

        self.plot_widget = PlotWidget()
        device_groupbox_layout.addWidget(self.plot_widget)


        labels_layout_device_group = QtWidgets.QHBoxLayout()
        device_groupbox_layout.addLayout(labels_layout_device_group)
        self.concentration_label = QtWidgets.QLabel("H2 conc: ---")
        self.t_ambient_label = QtWidgets.QLabel("T_amb: ---")
        labels_layout_device_group.addWidget(self.concentration_label)
        labels_layout_device_group.addWidget(self.t_ambient_label)



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
            msg_box.setText("Не выбран порт устройства")
            msg_box.exec_()
            return
        if self.device_bench is not None:
            self.device_bench.ser.close()
        if device_port != "test":
            self.device_bench = MSDesktopDevice(device_port, crc)
        else:
            self.device_bench = PlaceHolderDevice()
        self.parent().statusBar().showMessage("Device initiated")

    def _pre_device_command(self):
        if self.device_bench is None:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Сначала инициализируйте устройство кнопочкой Init")
            msg_box.exec_()
            return 0
        else:
            return 1

    def read_device_status(self):
        if self._pre_device_command():
            self.device_bench.get_status(self.parent().statusBar().showMessage)

    def device_trigger_measurement(self):
        if self._pre_device_command():
            self.device_bench.trigger_measurement(self.trigger_time_lineedit.text(), print=self.parent().statusBar().showMessage)

    def device_get_have_data(self):
        if self._pre_device_command():
            self.device_bench.get_have_data(self.parent().statusBar().showMessage)

    def start_timer(self):

        try:
            if self.need_to_trigger_measurement.isChecked():
                int(self.before_trigger_time_lineedit.text())
                int(self.trigger_time_lineedit.text())
        except ValueError:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Не, не начали. Укажите числовые целые значения в полях для времен ниже")
            msg_box.exec_()
        else:
            self.already_waited = 0
            self.gas_already_sent = False
            self.gas_sensor_state = 0
            self.gas_iterator_state = 0
            self.gas_iterator_counter = 0
            self.prev_gas_iterator_state = None
            self.data_logger = DataLogger(self.data_logger_path)
            self.timer.start()
            self.repeat_times = int(self.times_repeat_lineedit.text())
            if pathlib.Path(self.conc_lineedit.text()).exists():
                with open(self.conc_lineedit.text(), "r") as fd:
                    if not self.need_to_wait_for_scientist.isChecked():
                        lines = chain(*(repeat(int(line.strip()), self.repeat_times * 2) for line in fd.readlines()))
                    else:
                        lines = (int(line.strip()) for line in fd.readlines())
                self.gas_iterator = iter(lines)
                if self.need_to_wait_for_scientist.isChecked():
                    self.gas_iterator_state = next(self.gas_iterator)


    def stop_timer(self):
        self.timer.stop()

    def next_gas_iterator_state(self):
        if not self.need_to_wait_for_scientist.isChecked():
            msg_box = QtWidgets.QMessageBox()
            msg_box.setWindowTitle("Внимание!!!")
            msg_box.setText("Вы уверены?")
            msg_box.setInformativeText("Вы не выбрали галочку про ручное управление. Уверены, что хотите это сделать?")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            answer = msg_box.exec_()
            if answer == QtWidgets.QMessageBox.Yes:
                self.gas_iterator_state = next(self.gas_iterator)
                self.gas_iterator_state = next(self.gas_iterator)
        else:
            self.gas_iterator_state = next(self.gas_iterator)

    def get_all_results(self):
        if self._pre_device_command():
            state = self.device_bench.get_state()
            t_ambient = self.device_bench.get_ambient_temp()
            self.t_ambient_label.setText(f"T_amb: {t_ambient:2.2f} °K")
            self.parent().statusBar().showMessage(f"Status: {state}, gas_already_sent: {self.gas_already_sent}, already_waited: {self.already_waited}, state: {self.gas_iterator_state}, counter: {self.gas_iterator_counter}")
            if self.need_to_trigger_measurement.isChecked():
                if state == 0: # idle
                    if self.already_waited == int(self.before_trigger_time_lineedit.text()):
                        self.device_bench.trigger_measurement(float(self.trigger_time_lineedit.text()), print=self.parent().statusBar().showMessage)
                        self.already_waited = int(self.before_trigger_time_lineedit.text()) + 1
                    elif self.already_waited < int(self.before_trigger_time_lineedit.text()):
                        if not self.gas_already_sent:
                            host, port = self.parent().settings_widget.get_gas_stand_settings()
                            if not self.need_to_wait_for_scientist.isChecked():
                                self.gas_iterator_state = next(self.gas_iterator)
                            set_gas_state(str(2*self.gas_iterator_state + 1), host, port)
                            self.gas_already_sent = True
                        self.already_waited += 1
                elif state == 1: # exhale
                    self.gas_already_sent = False
                    self.already_waited = 0
                elif state == 2: # measuring
                    if not self.gas_already_sent:
                        host, port = self.parent().settings_widget.get_gas_stand_settings()
                        if not self.need_to_wait_for_scientist.isChecked():
                            self.gas_iterator_state = next(self.gas_iterator)
                        if self.gas_iterator_state == self.prev_gas_iterator_state:
                            self.gas_iterator_counter += 1
                        else:
                            self.gas_iterator_counter = 1
                            self.prev_gas_iterator_state = self.gas_iterator_state
                        self.gas_sensor_state = 2*self.gas_iterator_state + 2
                        set_gas_state(str(self.gas_sensor_state), host, port)
                        self.gas_already_sent = True
                elif state == 3: # purging
                    self.gas_already_sent = False
                    if self.device_bench.get_have_data()[0] == 0 and self.device_bench.get_have_result()[0] == 0:
                        times, temperatures, resistances = self.device_bench.get_cycle()
                        self.plot_widget.plot_answer(times, resistances)
                        h2conc, *_ = self.device_bench.get_result()
                        self.concentration_label.setText("H2 conc: {:2.4f} ppm".format(h2conc))
                        heater_cal_transform: HeaterCalTransformTuple = self.device_bench.get_heater_cal_transform()
                        self.data_logger.save_data(resistances, h2conc,self.gas_sensor_state, temperatures, t_ambient, heater_cal_transform.k, heater_cal_transform.b)
                else:
                    pass
            else:
                if self.device_bench.get_have_data()[0] == 0 and self.device_bench.get_have_result()[0] == 0:
                    times, temperatures, resistances = self.device_bench.get_cycle()
                    self.plot_widget.plot_answer(times, resistances)
                    h2conc, *_ = self.device_bench.get_result()
                    self.concentration_label.setText(f"H2 conc: {h2conc:2.4f} ppm")
                    heater_cal_transform: HeaterCalTransformTuple = self.device_bench.get_heater_cal_transform()
                    self.data_logger.save_data(resistances, h2conc, self.gasstand_timer.current_state, temperatures, t_ambient, heater_cal_transform.k, heater_cal_transform.b)
        else:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setWindowTitle("Внимание!!!")
            msg_box.setText("Связь с устройством потеряна")
            msg_box.setInformativeText("Ну или прошла рассинхронизация, черт его знает. Скорее первое.")
            msg_box.exec_()
            self.stop_timer()

    def get_heater_calibration(self):
        if self.timer.isActive():
            return
        if self._pre_device_command():
            voltages, temperatures = self.device_bench.get_heater_calibration()
            heater_cal_transform: HeaterCalTransformTuple = self.device_bench.get_heater_cal_transform()
            heater_params = self.device_bench.get_heater_params()
            voltages_cal = voltages * heater_cal_transform.k + heater_cal_transform.b
            filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Get cal file", dir="./")
            filename_par, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Get par file", dir="./")
            if filename:
                sensor_number, *_ = QtWidgets.QInputDialog.getInt(self, "What is the number of sensor you wanna see",
                                                                  "Sensor number:", 0)
                config = configparser.ConfigParser()
                config.read(filename_par)
                R0 = float(config["R0"][f"R0_{sensor_number}"].replace(",", "."))/100
                Rc = float(config["Rc"][f"Rc_{sensor_number}"].replace(",", "."))/100
                alpha = float(config["a"][f"a0_{sensor_number}"].replace(",", "."))
                T0 = float(config["T0"]["T0"].replace(",", "."))

                data = np.loadtxt(filename, skiprows=1)
                ms_temperatures = data[:, sensor_number * 3 + 2]
                R = (1  + alpha * (ms_temperatures - T0)) * (R0 - Rc) + Rc
                ms_voltages = data[:, sensor_number * 3] # * R / (R + 20)
                ms_voltages_recalc = data[:, sensor_number * 3] * R / (R + 20)
            else:
                ms_temperatures = []
                ms_voltages = []
                ms_voltages_recalc = []
            self.plot_widget.plot_heater_calibration(voltages, temperatures,
                                                     ms_voltages, ms_temperatures,
                                                     ms_voltages_recalc, ms_temperatures,
                                                     voltages_cal, temperatures, heater_params=heater_params)

    def open_conces_gas_stand_file(self):
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Открыть файл для газового стенда", "./", "*")
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
            good = True
            ota_answer = self.device_bench.start_ota()
            if ota_answer == 0:
                with open(filename, "rb") as fd:
                    red = fd.read(2000)
                    while good and len(red) != 0:
                        ota_answer = self.device_bench.chunk_ota(red)
                        if ota_answer == 0:
                            while True:
                                sleep(0.5)
                                if self.device_bench.check_ota() == 0:
                                    break
                            counter += 1
                            self.parent().statusBar().showMessage(f"OTA progress: {counter}")
                            red = fd.read(2000)
                        else:
                            self.parent().statusBar().showMessage(f"OTA update failed on {counter} step with code {ota_answer}")
                            good = False
                if good:
                    if self.device_bench.finalize_ota() == 0:
                        self.parent().statusBar().showMessage("Successful OTA update")
                    else:
                        self.parent().statusBar().showMessage("Failed finalize OTA update")
            else:
                self.parent().statusBar().showMessage(f"Cant start OTA with code {ota_answer}")

    def upload_calibration(self):
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose calibration file", "./", "*")
        if filename:
            with open(filename, "r") as fd:
                values = tuple(map(lambda x: float(x.strip()), fd.readlines()))
                if len(values) != 301:
                    self.parent().statusBar().showMessage("Calibration not loaded")
                    msg_box = QtWidgets.QMessageBox()
                    msg_box.setText("Len of array is not equal to 301")
                    msg_box.exec_()
                    return
                answer = self.device_bench.set_cycle(values)
                if answer[0] == 0:
                    self.parent().statusBar().showMessage("Calibration loaded")
                else:
                    self.parent().statusBar().showMessage("Calibration not loaded")
