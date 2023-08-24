import sys
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtGui import QIntValidator

from device_proxy import MSDesktopQtProxy
from settings_widget import SettingsWidget
from plot_widget import PlotWidget
from logger import DataLogger
from gas_stand import set_gas_state, GasStandTimer
from concentration_widget import ConcentrationWidget
import typing
import logging
import pathlib


logger = logging.getLogger(__name__)

def app():
    app = QtWidgets.QApplication()
    global_application_settings = QtCore.QSettings("MotyaSoft", "HydrogenBreathUI")

    device_proxy_object = MSDesktopQtProxy()
    device_proxy_thread = QtCore.QThread()

    app.aboutToQuit.connect(device_proxy_thread.quit)


    device_proxy_object.moveToThread(device_proxy_thread)
    device_proxy_thread.start()

    main_widget = MainWidget(device_proxy_object,
                             settings=global_application_settings)

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

    settings_action = QtGui.QAction("Settings", main_window)
    menubar.addAction(settings_action)
    settings_action.triggered.connect(settings_widget.toggle_visible)

    main_window.show()

    sys.exit(app.exec_())


class MainWidget(QtWidgets.QWidget):
    def __init__(self, device_proxy: MSDesktopQtProxy, *args, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle("HydrogenBreathUI")

        self.device_proxy: MSDesktopQtProxy = device_proxy
        self.device_proxy.message.connect(self.show_message_from_device)
        self.device_proxy.messagebox.connect(self.show_message_box_from_device)
        self.device_proxy.send_to_gas_stand.connect(self.send_signal_to_gas_stand)
        self.device_proxy.stop_main_cycle_signal.connect(self.stop_timer)

        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.device_proxy.get_all_results)

        self.conc_widget = ConcentrationWidget(settings=settings, device_proxy=device_proxy)
        self.device_proxy.conc_widget_signal.connect(self.conc_widget.set_conc_state_for_device)
        self.settings_qt = settings

        self.gasstand_timer = GasStandTimer()
        self.gasstand_timer.next_called.connect(self.device_proxy.set_gastimer_stand_current_state)

        self.status_timer = QtCore.QTimer()
        self.status_timer.setInterval(1000)
        self.status_timer.timeout.connect(self.get_gas_stand_status)
        self.status_timer.start()


        self.data_logger_path = pathlib.Path.cwd()
        self.data_logger = DataLogger(self.data_logger_path)

        self.device_proxy.data_logger_signal.connect(self.data_logger_save_callback)

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

        gas_stand_mapping_button = QtWidgets.QPushButton("Gas stand conces")
        gas_stand_mapping_button.clicked.connect(self.conc_widget.toggle_visible)

        self.status_label = QtWidgets.QLabel()

        buttons_gas_stand_layout.addWidget(gas_stand_start_button)
        buttons_gas_stand_layout.addWidget(gas_stand_stop_button)
        buttons_gas_stand_layout.addWidget(gas_stand_mapping_button)
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
        add_button_to_groupbox("Upload calibration", self.upload_calibration)
        add_button_to_groupbox("Upload temperature cycle", self.upload_temperature_cycle)
        add_button_to_groupbox("Upload firmware", self.upload_firmware)
        add_button_to_groupbox("Stop firmware upload", self.stop_firmware_upload)
        add_button_to_groupbox("Upload model", self.upload_model)
        add_button_to_groupbox("Save variant", self.save_variant)
        add_button_to_groupbox("Save heater params", self.save_heater_params)
        add_button_to_groupbox("Reboot device", self.reboot_device)
        add_button_to_groupbox("Heater off", self.heater_off)


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
        self.need_to_trigger_measurement.stateChanged.connect(self.device_proxy.need_to_trigger_measurement_callback)

        need_to_wait_scientist_layout = QtWidgets.QHBoxLayout()

        self.need_to_wait_for_scientist = QtWidgets.QCheckBox("Ручное управление")
        need_to_wait_scientist_layout.addWidget(self.need_to_wait_for_scientist)
        self.need_to_wait_for_scientist.stateChanged.connect(self.device_proxy.need_to_wait_scientist_callback)

        next_gas_state_button = QtWidgets.QPushButton("Следующее состояние")
        next_gas_state_button.clicked.connect(self.next_gas_iterator_state)
        need_to_wait_scientist_layout.addWidget(next_gas_state_button)

        device_groupbox_layout.addLayout(need_to_wait_scientist_layout)

        self.plot_widget = PlotWidget()
        self.device_proxy.plot_signal.connect(self.plot_widget.plot_answer)
        self.device_proxy.plot_calibration_signal.connect(self.plot_widget.plot_heater_calibration)
        device_groupbox_layout.addWidget(self.plot_widget)


        labels_layout_device_group = QtWidgets.QHBoxLayout()
        device_groupbox_layout.addLayout(labels_layout_device_group)
        self.concentration_label = QtWidgets.QLabel("H2 conc: ---")
        self.device_proxy.change_h2_conc.connect(self.concentration_label.setText)
        self.t_ambient_label = QtWidgets.QLabel("T_amb: ---")
        self.device_proxy.change_t_ambient.connect(self.t_ambient_label.setText)
        self.concentration_set_label = QtWidgets.QLabel("H2 conc set: ---")
        self.device_proxy.change_h2_conc_set.connect(self.concentration_set_label.setText)
        labels_layout_device_group.addWidget(self.concentration_label)
        labels_layout_device_group.addWidget(self.t_ambient_label)
        labels_layout_device_group.addWidget(self.concentration_set_label)

        self.progress_bar_text = QtWidgets.QLabel()
        self.progress_bar = QtWidgets.QProgressBar()
        self.device_proxy.progressbar.connect(self.progress_bar.setValue)
        self.device_proxy.progressbar_range.connect(self.progress_bar.setRange)
        self.device_proxy.progressbar_text.connect(self.progress_bar_text.setText)
        device_groupbox_layout.addWidget(self.progress_bar_text)
        device_groupbox_layout.addWidget(self.progress_bar)


        main_layout.addWidget(device_groupbox)

    def show_message_from_device(self, message):
        self.parent().statusBar().showMessage(message)

    def show_message_box_from_device(self, shorttext, text):
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("WARNING")
        msg_box.setText(shorttext)
        if text:
            msg_box.setInformativeText(text)
        msg_box.exec_()

    def conc_lineedit_return_pressed(self):
        if not self.gasstand_timer.isActive():
            try:
                host, port = self.parent().settings_widget.get_gas_stand_settings()
            except:
                pass
            else:
                set_gas_state(self.conc_lineedit.text(), host, port)

    def init_device_bench(self):
        device_port = self.parent().settings_widget.get_device_port()
        self.device_proxy.init_new_device(device_port)


    def read_device_status(self):
        self.device_proxy.status()

    def send_signal_to_gas_stand(self, gas_state: str):
        host, port = self.parent().settings_widget.get_gas_stand_settings()
        set_gas_state(gas_state, host, port)

    def data_logger_save_callback(self, resistances, conc, state, temperatures, t_ambient, k_i, b_i, conc_set):
        if self.data_logger is not None:
            self.data_logger.save_data(resistances, conc, state, temperatures, t_ambient, k_i, b_i, conc_set)

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
            self.data_logger = DataLogger(self.data_logger_path)
            self.device_proxy.initialize_gas_iterator(self.times_repeat_lineedit.text(), self.conc_lineedit.text())
            self.device_proxy.set_before_trigger_time(self.before_trigger_time_lineedit.text())
            self.device_proxy.set_trigger_time(self.trigger_time_lineedit.text())
            self.timer.start()


    def stop_timer(self):
        self.conc_widget.drop_loaded()
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
                self.device_proxy.next_gas_iterator()
                self.device_proxy.next_gas_iterator()
        else:
            self.device_proxy.next_gas_iterator()


    def get_heater_calibration(self):
        if self.timer.isActive():
            return
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Get cal file", dir="./")
        filename_par, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Get par file", dir="./")
        sensor_number, *_ = QtWidgets.QInputDialog.getInt(self, "What is the number of sensor you wanna see",
                                                          "Sensor number:", 0)
        self.device_proxy.heater_calibration_signal.emit(filename, filename_par, sensor_number)

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
        try:
            folder = self.settings_qt.value("firmware_folder", "./")
        except:
            folder = './'
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose firmware file", folder, "*")
        if filename:
            try:
                self.settings_qt.setValue("firmware_folder", pathlib.Path(filename).parent.as_posix())
            except:
                pass
            self.device_proxy.upload_firmware_signal.emit(filename)

    def stop_firmware_upload(self):
        self.device_proxy.set_break_ota()

    def upload_temperature_cycle(self):
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose calibration file", "./", "*")
        if filename:
            self.device_proxy.upload_temperature_cycle_signal.emit(filename)

    def upload_model(self):
        try:
            folder = self.settings_qt.value("model_folder", "./")
        except:
            folder = './'
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose model file", folder, "*")
        if filename:
            try:
                self.settings_qt.setValue("model_folder", pathlib.Path(filename).parent.as_posix())
            except:
                pass
            
            input_dialog = QtWidgets.QInputDialog(self)
            version, *_ = input_dialog.getText(self, "Get model version in int8", "Version")
            try:
                version = int(version)
            except:
                msg_box = QtWidgets.QMessageBox()
                msg_box.setText("Введите версию в виде числа")
                msg_box.exec_()
                return 
            else:
                print(version)
                self.device_proxy.upload_model_signal.emit(filename, version)

    def upload_calibration(self):
        try:
            folder = self.settings_qt.value("calibration_folder", "./")
        except:
            folder = './'
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose calibration file", folder, "*")
        if filename:
            try:
                self.settings_qt.setValue("calibration_folder", pathlib.Path(filename).parent.as_posix())
            except:
                pass
            self.device_proxy.upload_calibration_signal.emit(filename)

    def save_variant(self):
        self.device_proxy.save_variant()

    def save_heater_params(self):
        self.device_proxy.save_heater_params()

    def reboot_device(self):
        self.device_proxy.reboot_device()

    def heater_off(self):
        self.device_proxy.heater_off()
