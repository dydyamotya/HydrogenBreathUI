from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Signal, Slot
import math
import os.path
import pathlib
from itertools import repeat, chain
import configparser
import numpy as np
import crcmod
import struct
from time import sleep
import logging
logger = logging.getLogger(__name__)
from device import HeaterParamsTuple, MSDesktopDevice, PlaceHolderDevice, HeaterCalTransformTuple, DOTS_IN_PASHA_CALIBRATION

class CRCCalculator():
    def __init__(self):
        self.calc = crcmod.mkCrcFun(poly=0x104c11db7, rev=True, initCrc=0, xorOut=0)

    def __call__(self, data):
        return struct.pack("<I", self.calc(data))


crcCalc = CRCCalculator()

class MSDesktopQtProxy(QtCore.QObject):
    message = Signal(str)
    messagebox = Signal(str, str)
    progressbar = Signal(int)
    progressbar_text = Signal(str)
    progressbar_range = Signal(int, int)

    device_initiated = Signal()
    stop_main_cycle_signal = Signal()

    change_t_ambient = Signal(str)
    change_h2_conc = Signal(str)
    change_h2_conc_set = Signal(str)

    send_to_gas_stand = Signal(str)

    plot_signal = Signal(np.ndarray, np.ndarray)
    plot_calibration_signal = Signal(np.ndarray, np.ndarray,  np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                                     np.ndarray, np.ndarray, HeaterParamsTuple)

    conc_widget_signal = Signal(str)
    data_logger_signal = Signal(np.ndarray, float, int, np.ndarray, float, float, float, float)

    # Function signals to call it from other threads

    heater_calibration_signal = Signal(str, str, int)
    upload_firmware_signal = Signal(str)
    upload_temperature_cycle_signal = Signal(str)
    upload_model_signal = Signal(str, str)
    upload_calibration_signal = Signal(str)



    def __init__(self):
        super().__init__()

        self.busy = False
        self.device = None

        self.gas_already_sent = False
        self.already_waited = 0
        self.gas_iterator_state = 0
        self.gas_iterator_counter = 0
        self.gas_sensor_state = 0
        self.prev_gas_iterator_state = None
        self.conc_set = "-4"
        self.gasstand_timer_current_state = "0"
        self.gas_iterator_initialized = False

        self.before_trigger_time = None
        self.trigger_time = None

        self.need_to_trigger_measurement = False
        self.need_to_wait_for_scientist = False

        self.send_ota_break = False

        self.heater_calibration_signal.connect(self.get_heater_calibration)
        self.upload_firmware_signal.connect(self.upload_firmware)
        self.upload_temperature_cycle_signal.connect(self.upload_temperature_cycle)
        self.upload_model_signal.connect(self.upload_model)
        self.upload_calibration_signal.connect(self.upload_calibration)


    @Slot(str)
    def init_new_device(self, port):
        if not port:
            self.messagebox.emit("Порт не выбран", "")
            return
        if self.device is not None:
            self.device.ser.close()
        if port == "test":
            self.device = PlaceHolderDevice()
        else:
            self.device = MSDesktopDevice(port, crcCalc)
        self.message.emit("Устройство инициализированно")


    def _pre_device_command(self):
        if self.device is None:
            self.messagebox.emit("Сначала инициализируйте устройство кнопочкой Init", "")
            return 1
        return 0

    @Slot()
    def status(self):
        if not self._pre_device_command():
            _, decoded_answer = self.device.get_status()
            self.message.emit(decoded_answer)

    @Slot(int)
    def need_to_trigger_measurement_callback(self, check_state: int):
        self.need_to_trigger_measurement = True if check_state else False

    @Slot(int)
    def need_to_wait_scientist_callback(self, check_state: int):
        self.need_to_wait_for_scientist = True if check_state else False

    @Slot(str)
    def set_before_trigger_time(self, value: str):
        self.before_trigger_time = value

    @Slot(str)
    def set_trigger_time(self, value):
        self.trigger_time = value

    @Slot(str, str)
    def initialize_gas_iterator(self, times_to_repeat, path):
        self.gas_already_sent = False
        self.already_waited = 0
        self.gas_iterator_state = 0
        self.gas_iterator_counter = 0
        self.gas_sensor_state = 0
        self.prev_gas_iterator_state = None
        self.conc_set = "-4"
        try:
            repeat_times = int(times_to_repeat)
        except:
            pass
        else:
            if pathlib.Path(path).exists():
                with open(path, "r") as fd:
                    if not self.need_to_wait_for_scientist:
                        lines = chain(*(repeat(int(line.strip()), repeat_times * 2) for line in fd.readlines()))
                    else:
                        lines = (int(line.strip()) for line in fd.readlines())
                self.gas_iterator = iter(lines)
                if self.need_to_wait_for_scientist:
                    self.gas_iterator_state = next(self.gas_iterator)
                self.gas_iterator_initialized = True
            else:
                self.gas_iterator_initialized = False

    @Slot()
    def set_break_ota(self):
        self.send_ota_break = True

    @Slot()
    def next_gas_iterator(self):
        self.gas_iterator_state = next(self.gas_iterator)

    @Slot(str)
    def set_conc_set(self, value):
        self.conc_set = value

    @Slot(str)
    def set_gastimer_stand_current_state(self, value):
        self.gasstand_timer_current_state = value

    @Slot()
    def get_all_results(self):
        if self.busy:
            self.messagebox.emit("Связь с устройством потеряна",
                                 "Ну или прошла рассинхронизация, черт его знает. Скорее первое.")
            self.stop_main_cycle_signal.emit()
            return
        if self.before_trigger_time is None:
            self.messagebox.emit("Не установлено время до триггера",
                                 "Надо установить")
            self.stop_main_cycle_signal.emit()
            return
        if self.trigger_time is None:
            self.messagebox.emit("Не установлено время триггера",
                                 "Надо установить")
            self.stop_main_cycle_signal.emit()
            return
        if not self.gas_iterator_initialized:
            self.messagebox.emit("Не указан газовый файл или количество циклов для работы",
                                     "Укажите, пожалуйста")
            self.stop_main_cycle_signal.emit()
            return
        if not self._pre_device_command():
            state = self.device.get_state()
            t_ambient = self.device.get_ambient_temp()
            self.change_t_ambient.emit(f"T_amb: {t_ambient:2.2f} °K")
            self.message.emit(f"Status: {state}, gas_already_sent: {self.gas_already_sent}, already_waited: {self.already_waited}, state: {self.gas_iterator_state}, counter: {self.gas_iterator_counter}")
            if self.need_to_trigger_measurement:
                if state == 0: # idle
                    if self.already_waited == int(self.before_trigger_time):
                        self.device.trigger_measurement(float(self.trigger_time))
                        self.already_waited = int(self.before_trigger_time) + 1
                    elif self.already_waited < int(self.before_trigger_time):
                        if not self.gas_already_sent:
                            if not self.need_to_wait_for_scientist:
                                self.gas_iterator_state = next(self.gas_iterator)
                            self.send_to_gas_stand.emit(str(2 * self.gas_iterator_state + 1))
                            self.gas_already_sent = True
                        self.already_waited += 1
                elif state == 1: # exhale
                    self.gas_already_sent = False
                    self.already_waited = 0
                elif state == 2: # measuring
                    if not self.gas_already_sent:
                        if not self.need_to_wait_for_scientist:
                            self.gas_iterator_state = next(self.gas_iterator)
                        if self.gas_iterator_state == self.prev_gas_iterator_state:
                            self.gas_iterator_counter += 1
                        else:
                            self.gas_iterator_counter = 1
                            self.prev_gas_iterator_state = self.gas_iterator_state
                        self.gas_sensor_state = 2*self.gas_iterator_state + 2
                        self.send_to_gas_stand.emit(str(self.gas_sensor_state))
                        self.gas_already_sent = True
                elif state == 3: # purging
                    self.gas_already_sent = False
                    get_have_data_answer, get_have_data_decoded = self.device.get_have_data()
                    logger.debug(get_have_data_decoded)
                    get_have_result_answer =  self.device.get_have_result()
                    logger.debug(f"{get_have_result_answer}")
                    if get_have_data_answer[0] == 0 and get_have_result_answer[0] == 0:
                        times, temperatures, resistances = self.device.get_cycle()
                        self.plot_signal.emit(times[1:], resistances[1:])
                        h2conc, *_ = self.device.get_result()
                        self.conc_widget_signal.emit(str(self.gas_sensor_state))
                        self.change_h2_conc.emit("H2 conc: {:2.4f} ppm".format(h2conc))
                        self.change_h2_conc_set.emit("H2 conc set: {} ppm".format(self.conc_set))
                        heater_cal_transform: HeaterCalTransformTuple = self.device.get_heater_cal_transform()
                        self.data_logger_signal.emit(resistances,
                                                     h2conc,
                                                     self.gas_sensor_state,
                                                     temperatures,
                                                     t_ambient,
                                                     heater_cal_transform.k,
                                                     heater_cal_transform.b,
                                                     float(self.conc_set)
                                                     )
                else:
                    pass
            else:
                if self.device.get_have_data()[0] == 0 and self.device.get_have_result()[0] == 0:
                    times, temperatures, resistances = self.device.get_cycle()
                    self.plot_signal.emit(times[1:], resistances[1:])
                    h2conc, *_ = self.device.get_result()
                    self.conc_widget_signal.emit(self.gasstand_timer_current_state)
                    self.change_h2_conc.emit("H2 conc: {:2.4f} ppm".format(h2conc))
                    heater_cal_transform: HeaterCalTransformTuple = self.device.get_heater_cal_transform()
                    self.data_logger_signal.emit(resistances,
                                                 h2conc,
                                                 int(self.gasstand_timer_current_state),
                                                 temperatures,
                                                 t_ambient,
                                                 heater_cal_transform.k,
                                                 heater_cal_transform.b,
                                                 float(self.conc_set)
                                                 )
        else:
            self.messagebox.emit("Связь с устройством потеряна",
                                 "Ну или прошла рассинхронизация, черт его знает. Скорее первое.")
            self.stop_main_cycle_signal.emit()

    @Slot(str, str, int)
    def get_heater_calibration(self, filename, filename_par, sensor_number):
        if not self._pre_device_command():
            voltages, temperatures = self.device.get_heater_calibration()
            heater_cal_transform: HeaterCalTransformTuple = self.device.get_heater_cal_transform()
            heater_params = self.device.get_heater_params()
            voltages_cal = voltages * heater_cal_transform.k + heater_cal_transform.b
            if filename:
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
            self.plot_calibration_signal.emit(voltages, temperatures,
                                              ms_voltages, ms_temperatures,
                                              ms_voltages_recalc, ms_temperatures,
                                              voltages_cal, temperatures, heater_params)

    @Slot(str)
    def upload_firmware(self, filename):
        if not self._pre_device_command():
            self.busy = True
            self.message.emit("OTA update started")
            self.device.suspend_heater()
            while True:
                state = self.device.get_state()
                if state == 5:
                    break
                elif state == 4:
                    self.messagebox.emit("Strange answer about device state", "")
                    self.busy = False
                    return
                else:
                    sleep(1)
            counter = 0
            good = True
            chunk_size = self.device.ota_chunk_size
            filesize = os.path.getsize(filename)
            steps = math.ceil(filesize / chunk_size)
            self.progressbar_range.emit(0, steps)
            ota_answer = self.device.start_ota()
            if ota_answer == 0:
                with open(filename, "rb") as fd:
                    red = fd.read(chunk_size)
                    while not self.send_ota_break and good and len(red) != 0:
                        ota_answer = self.device.chunk_ota(red)
                        if ota_answer == 0:
                            while True:
                                sleep(0.05)
                                if self.device.check_ota() == 0:
                                    break
                            counter += 1
                            self.progressbar_text.emit("OTA update")
                            self.progressbar.emit(counter)
                            red = fd.read(chunk_size)
                        else:
                            self.messagebox.emit(f"OTA update failed on {counter} step with code {ota_answer}", "")
                            good = False
                    if self.send_ota_break:
                        self.device.break_ota()
                        self.send_ota_break = False
                        self.busy = False
                        self.progressbar_text.emit("OTA update stopped")
                        return
                if good:
                    if self.device.finalize_ota() == 0:
                        self.progressbar.emit(steps)
                        self.message.emit("Successful OTA update")
                    else:
                        self.messagebox.emit("Failed finalize OTA update", "")
            else:
                self.messagebox.emit(f"Cant start OTA with code {ota_answer}", "")
            self.busy = False

    @Slot(str)
    def upload_temperature_cycle(self, filename):
        if not self._pre_device_command():
            self.busy = True
            with open(filename, "r") as fd:
                values = tuple(map(lambda x: float(x.strip()), fd.readlines()))
                if len(values) != 301:
                    self.message.emit("Temperature cycle not loaded, array size not equal to 301 element")
                answer = self.device.set_cycle(values)
                if answer[0] == 0:
                    self.message.emit("Temperature cycle loaded")
                else:
                    self.message.emit("Calibration not loaded, something with device connection")
            self.busy = False

    @Slot(str)
    def upload_model(self, filename: str, version: int):
        if not self._pre_device_command():
            counter = 0
            good = True
            self.busy = True
            size_to_read = self.device.model_chunk_size
            with open(filename, "rb") as fd:
                values = fd.read()
            if len(values) % size_to_read:
                filebinarysize = len(values) + (size_to_read - (len(values) % size_to_read))
            else:
                filebinarysize = len(values)
            values = values + b"\xFF" * (filebinarysize - len(values))
            self.progressbar_range.emit(0, int(filebinarysize / size_to_read) + 1)
            self.progressbar_text.emit("Model update")
            crc = self.device.crc.calc(values)
            model_post_send_answer = self.device.post_model_update_init(version, filebinarysize, crc)
            if model_post_send_answer == 0:
                with open(filename, "rb") as fd:
                    red = fd.read(size_to_read)
                    while good and len(red) != 0:
                        model_update_answer = self.device.post_model_chunk_send(red)
                        if model_update_answer == 0:
                            counter += 1
                            self.progressbar.emit(counter)
                            red = fd.read(size_to_read)
                        else:
                            self.messagebox.emit(
                                    f"Model update failed on {counter} step with code {model_update_answer}", "")
                            good = False
                if good:
                    if self.device.post_model_finalize() == 0:
                        self.progressbar.emit(int(filebinarysize / size_to_read) + 1)
                        self.message.emit("Successful model update")
                    else:
                        self.messagebox.emit("Failed finalize model update", "")
            else:
                self.messagebox.emit(f"Cant start model update with code {model_post_send_answer}", '')
            self.busy = False

    @Slot()
    def reboot_device(self):
        if not self._pre_device_command():
            self.device.reboot_device()
            self.message.emit("Device rebooted")

    @Slot()
    def upload_calibration(self, filename):
        if not self._pre_device_command():
            self.busy = True
            with open(filename, "r") as fd:
                values = tuple(map(lambda x: float(x.strip()), fd.readlines()))
                values_to_heater_params, calibration_curve = values[:11], values[11:]

                values_to_heater_params = list(values_to_heater_params)
                values_to_heater_params[10] = int(values_to_heater_params[10])
                values_to_heater_params[9] = int(values_to_heater_params[9])

                answer = self.device.set_heater_params(values_to_heater_params)
                if answer[0] == 0:
                    self.message.emit("Heater params loaded")
                else:
                    self.message.emit(f"Heater params not loaded, error code {answer[0]}")

                if len(calibration_curve) != DOTS_IN_PASHA_CALIBRATION:
                    self.message.emit("Calibration not loaded, array size not equal to 410 element")
                answer = self.device.set_heater_calibration(calibration_curve)
                if answer[0] == 0:
                    self.message.emit("Calibration loaded")
                else:
                    self.message.emit("Calibration not loaded, something with device connection")
            self.busy = False

    @Slot()
    def save_variant(self):
        if not self._pre_device_command():
            self.busy = True
            answer = self.device.save_variant()
            if answer[0] == 0:
                self.message.emit("NVS variant saved")
            else:
                self.message.emit(f"NVS variant not saved, error code {answer[0]}")
            self.busy = False

    @Slot()
    def save_heater_params(self):
        if not self._pre_device_command():
            self.busy = True
            answer = self.device.save_heater_params()
            if answer[0] == 0:
                self.message.emit("Heater params saved")
            else:
                self.message.emit(f"Heater params not saved, error code {answer[0]}")
            self.busy = False

