import math
import os.path
import typing

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Signal, Slot

import serial
import struct
import crcmod
import enum
import threading
import functools
import numpy as np
import logging
import pathlib
from itertools import repeat, chain
import configparser
from time import sleep

from collections import namedtuple

logger = logging.getLogger(__name__)

HeaterParamsTuple = namedtuple("HeaterParamsTuple", "tempco, rt_resistance, rt_temp, r_corr, cal_curve_ambient, gain, offset, cal_calib_interval, cal_calibration_enable")
HeaterCalTransformTuple = namedtuple("HeaterCalTransformTuple", "k, b")
class CRCCalculator():
    def __init__(self):
        self.calc = crcmod.mkCrcFun(poly=0x104c11db7, rev=True, initCrc=0, xorOut=0)

    def __call__(self, data):
        return struct.pack("<I", self.calc(data))


crcCalc = CRCCalculator()


bytes_to_escape = tuple(b"\x7E\x81\x55")


def escape_data(data):
    escaped = bytearray()
    for byte in data:
        if byte in bytes_to_escape:
            escaped.extend(b"\x55" + byte.to_bytes(1, "little"))
        else:
            escaped.append(byte)
    return escaped

DOTS_NUMBER = 301
DOTS_IN_PASHA_CALIBRATION = 410

class TECH_BYTES(enum.Enum):
    START_BYTE, END_BYTE, ESCAPE_BYTE = b"\x7E\x81\x55"


class COMMAND_NUM(enum.Enum):
    TRIGGER_MEASUREMENT = 2
    GET = 3
    STATUS = 4
    SET_HEATER = 5
    SET_MEAS = 6
    SET_CYCLE = 7
    HAVE_DATA = 8
    DUMP_NVS = 0xA0
    SAVE = 0x20
    GET_RESULT = 0x21
    GET_HAVE_RESULT = 0x22
    GET_HEATER_CAL = 0xA2
    GET_STATE = 0x30
    CMD_REBOOT = 0x33
    START_OTA = 0xB0
    CHUNK_OTA = 0xB1
    OTA_GET_READY = 0xB2
    CMD_OTA_FINALIZE = 0xB3
    CMD_OTA_ABORT = 0xB4
    CMD_GET_AMBIENT_TEMP = 0x34
    CMD_GET_HEATER_PARAMS = 0x35
    CMD_GET_HEATER_CAL_TRANSFORM = 0x36
    CMD_MODEL_UPDATE_INIT = 0xA4
    CMD_MODEL_CHUNK = 0xA5
    CMD_MODEL_FINALIZE = 0xA6
    CMD_SUSPEND_HEATER = 0x37

def form_error_bytes(num):
    return (1 << num).to_bytes(4, 'little')


lock = threading.Lock()


def locked(func):
    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        with lock:
            return func(*args, **kwargs)

    return _wrapper




class MSDesktopDevice():
    def __init__(self, port, crc):
        super().__init__()
        if isinstance(port, str):
            self.ser = serial.Serial(port=port, timeout=1)
        else:
            self.ser = port
        self.crc = crc
        self.counter = 0
        self.get_counter = 0
        self.ota_chunk_size = 0x2000
        self.model_chunk_size = 0x1000

    def _send_command(self, command_num, useful):
        buffer = bytearray()
        buffer.append(command_num)
        buffer.extend(useful)
        buffer.append(self.counter)
        self.counter += 1
        if self.counter > 255:
            self.counter = 0
        buffer.extend(self.crc(buffer))
        buffer = escape_data(buffer)
        buffer.append(TECH_BYTES.END_BYTE.value)
        buffer.insert(0, TECH_BYTES.START_BYTE.value)
        logger.debug(f"{len(buffer)}: {buffer}")
        return buffer

    def _get_answer(self, need_command):
        buffer = bytearray()
        reading = False
        escaped = False
        while True:
            try:
                red = self.ser.read(1)[0]
            except TimeoutError:
                raise
            else:
                if not escaped:
                    if red == TECH_BYTES.START_BYTE.value:
                        reading = True
                        continue
                    elif red == TECH_BYTES.END_BYTE.value:
                        logger.debug("END BYTE encountered")
                        reading = False
                        break
                    elif red == TECH_BYTES.ESCAPE_BYTE.value:
                        escaped = True
                        continue
                    else:
                        pass
                else:
                    escaped = False
                if reading:
                    buffer.append(red)
        logger.debug(f"{buffer}")
        body_and_counter, crc_got = buffer[:-4], buffer[-4:]
        logger.debug(f"{crc_got}")
        if self.crc(body_and_counter) == crc_got:
            logger.debug("CRC OK")
        else:
            logger.debug(f"My CRC {self.crc(body_and_counter)}")
            logger.debug("CRC ERROR")
        command, *useful, counter = body_and_counter
        if command != need_command:
            logger.debug(f"Command error {command} {need_command}")
        if counter != self.get_counter:
            logger.debug(f"Counters are not equal: got counter = {counter}, inter counter = {self.get_counter}")
            self.get_counter = counter + 1
        else:
            self.get_counter += 1
        return bytearray(useful)

    @locked
    def trigger_measurement(self, time_to_suck):
        useful = struct.pack("<f", time_to_suck)
        to_send = self._send_command(COMMAND_NUM.TRIGGER_MEASUREMENT.value, useful=useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.TRIGGER_MEASUREMENT.value)
        if answer == bytearray(b"\x00"):
            decoded = "Started trigger measurement"
        elif answer == bytearray(b"\x01"):
            decoded = "Something bad"
        else:
            decoded = "Strange answer"
        return answer, decoded

    @locked
    def get_cycle(self):
        to_send = self._send_command(COMMAND_NUM.GET.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.GET.value)
        try:
            data = struct.unpack("<" + "f" * DOTS_NUMBER * 2 , answer)
            data = np.array(data).reshape(-1, 2)
            resistances, temperatures = data[:, 0], data[:, 1]
            times = np.arange(temperatures.shape[0])
            return times, temperatures, resistances
        except:
            return answer

    @locked
    def get_status(self):
        to_send = self._send_command(COMMAND_NUM.STATUS.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.STATUS.value)
        if answer == bytes(4):
            decoded = "Status OK"
        elif answer == form_error_bytes(0):
            decoded = "Heater error"
        elif answer == form_error_bytes(1):
            decoded = "Measurement system error"
        elif answer == form_error_bytes(2):
            decoded = "Software init"
        elif answer == form_error_bytes(3):
            decoded = "Unknown command"
        elif answer == form_error_bytes(4):
            decoded = "Missed packet"
        elif answer == form_error_bytes(5):
            decoded = "UART parser error"
        elif answer == form_error_bytes(6):
            decoded = "Incorrect command format"
        else:
            decoded = "Strange answer"
        return answer, decoded

    @locked
    def set_heater(self, *parameters, print=logger.info):
        """parameters: [0] – alpha, [1] – R0, [2] - Rn"""
        useful = struct.pack("<" + "f" * len(parameters), *parameters)
        to_send = self._send_command(COMMAND_NUM.SET_HEATER.value, useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.SET_HEATER.value)
        if answer == bytearray(b"\x00"):
            print("Status OK")
        elif answer == bytearray(b"\x01"):
            print("Status ERROR")
        else:
            print("Strange answer")
        return answer

    @locked
    def set_meas(self, *parameters, print=logger.info):
        """parameters: [0] – Rs1, [1] – Rs2"""
        useful = struct.pack("<" + "f" * len(parameters), *parameters)
        to_send = self._send_command(COMMAND_NUM.SET_MEAS.value, useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.SET_MEAS.value)
        if answer == bytearray(b"\x00"):
            print("Status OK")
        elif answer == bytearray(b"\x01"):
            print("Status ERROR")
        else:
            print("Strange answer")
        return answer

    @locked
    def set_cycle(self, floats, print=logger.info):
        useful = struct.pack("<" + "f" * DOTS_NUMBER, *floats)
        to_send = self._send_command(COMMAND_NUM.SET_CYCLE.value, useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.SET_CYCLE.value)
        if answer == bytearray(b"\x00"):
            print("Status OK")
        elif answer == bytearray(b"\x01"):
            print("Status ERROR")
        else:
            print("Strange answer")
        return answer

    @locked
    def get_have_data(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.HAVE_DATA.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.HAVE_DATA.value)
        if answer == bytearray(b"\x01"):
            decoded = "There is no data"
        elif answer == bytearray(b"\x00"):
            decoded = "Data Have I"
        else:
            decoded = "Strange answer"
        return answer, decoded

    @locked
    def get_have_result(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.GET_HAVE_RESULT.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.GET_HAVE_RESULT.value)
        if answer == bytearray(b"\x01"):
            print("The is no new result")
        elif answer == bytearray(b"\x00"):
            print("Result Have I")
        else:
            print("Strange answer")
        return answer

    @locked
    def get_result(self):
        to_send = self._send_command(COMMAND_NUM.GET_RESULT.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.GET_RESULT.value)
        try:
            h2conc = struct.unpack("<f" , answer)
            return h2conc
        except:
            return answer

    @locked
    def get_heater_calibration(self):
        to_send = self._send_command(COMMAND_NUM.GET_HEATER_CAL.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.GET_HEATER_CAL.value)
        try:
            data = struct.unpack("<" + "f" * DOTS_IN_PASHA_CALIBRATION , answer)
            voltages = np.array(data)
            temperatures = np.arange(DOTS_IN_PASHA_CALIBRATION) + 40
            return voltages, temperatures
        except:
            return answer
    @locked
    def get_state(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.GET_STATE.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.GET_STATE.value)
        if answer == bytearray(b"\x00"):
            print("IDLE")
            return 0
        elif answer == bytearray(b"\x01"):
            print("EXHALE")
            return 1
        elif answer == bytearray(b"\x02"):
            print("MEASURING")
            return 2
        elif answer == bytearray(b"\x03"):
            print("PURGING")
            return 3
        elif answer == bytearray(b"\x04"):
            print("ERROR")
            return 4
        elif answer == bytearray(b"\x05"):
            print("TURNED OFF")
            return 5
        else:
            print("Strange answer")
            return answer
    @locked
    def start_ota(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.START_OTA.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.START_OTA.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return answer

    @locked
    def chunk_ota(self, chunk, print=logger.info):
        if len(chunk) < self.ota_chunk_size:
            chunk = chunk + b"\xFF" * (self.ota_chunk_size - len(chunk))
        logger.debug(f"Chunk size {len(chunk)}")
        to_send = self._send_command(COMMAND_NUM.CHUNK_OTA.value, chunk)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CHUNK_OTA.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return answer

    @locked
    def check_ota(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.OTA_GET_READY.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.OTA_GET_READY.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x02"):
            print("BUSY")
            return 2
        else:
            print("Strange answer")
            return answer

    @locked
    def finalize_ota(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.CMD_OTA_FINALIZE.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_OTA_FINALIZE.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return answer

    @locked
    def break_ota(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.CMD_OTA_ABORT.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_OTA_ABORT.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return answer
    @locked
    def get_ambient_temp(self, print=logger.info):
        # CMD_GET_AMBIENT_TEMP = 0x34
        to_send = self._send_command(COMMAND_NUM.CMD_GET_AMBIENT_TEMP.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_GET_AMBIENT_TEMP.value)
        try:
            ambient_temperature, = struct.unpack("<" + "f", answer)
            return ambient_temperature
        except:
            print(f"No ambient temperature in command {COMMAND_NUM.CMD_GET_AMBIENT_TEMP.name} answer")
            return answer

    @locked
    def get_heater_params(self, print=logger.info) -> typing.Optional[HeaterParamsTuple]:
        # CMD_GET_HEATER_PARAMS = 0x35
        to_send = self._send_command(COMMAND_NUM.CMD_GET_HEATER_PARAMS .value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_GET_HEATER_PARAMS.value)
        try:
            heater_params = HeaterParamsTuple._make(struct.unpack("<fffffffqi", answer))
            print(repr(heater_params))
            return heater_params
        except Exception as e:
            print(f"No heater params in command {COMMAND_NUM.CMD_GET_HEATER_PARAMS.name} answer, {str(e)}")
            return None

    @locked
    def get_heater_cal_transform(self, print=logger.info) -> HeaterCalTransformTuple:
        # CMD_GET_HEATER_CAL_TRANSFORM = 0x36
        to_send = self._send_command(COMMAND_NUM.CMD_GET_HEATER_CAL_TRANSFORM.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_GET_HEATER_CAL_TRANSFORM.value)
        try:
            heater_cal_params = HeaterCalTransformTuple._make(struct.unpack("<" + "ff", answer))
            print(repr(heater_cal_params))
            return heater_cal_params
        except Exception as e:
            print(f"No heater cal transform in command {COMMAND_NUM.CMD_GET_HEATER_CAL_TRANSFORM.name} answer, {str(e)}")
            return HeaterCalTransformTuple(0, 0)

    @locked
    def post_model_update_init(self, version, length, crc, print=logger.info) -> int:
        # CMD_MODEL_UPDATE_INIT = 0xA4
        payload = struct.pack("<BLL", version, length, crc)
        to_send = self._send_command(COMMAND_NUM.CMD_MODEL_UPDATE_INIT.value, payload)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_MODEL_UPDATE_INIT.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return 2

    @locked
    def post_model_chunk_send(self, chunk, print=logger.info) -> int:
        # CMD_MODEL_CHUNK = 0xA5
        if len(chunk) < self.model_chunk_size:
            chunk = chunk + b"\xFF" * (self.model_chunk_size - len(chunk))
        logger.debug(f"Chunk size {len(chunk)}")
        to_send = self._send_command(COMMAND_NUM.CMD_MODEL_CHUNK.value, chunk)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_MODEL_CHUNK.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return 2

    @locked
    def post_model_finalize(self, print=logger.info) -> int:
        # CMD_MODEL_FINALIZE = 0xA6
        to_send = self._send_command(COMMAND_NUM.CMD_MODEL_FINALIZE.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_MODEL_FINALIZE.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return 2

    @locked
    def suspend_heater(self, print=logger.info) -> int:
        # CMD_SUSPEND_HEATER  = 0x37
        to_send = self._send_command(COMMAND_NUM.CMD_SUSPEND_HEATER.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.CMD_SUSPEND_HEATER.value)
        if answer == bytearray(b"\x00"):
            print("OK")
            return 0
        elif answer == bytearray(b"\x01"):
            print("ERROR")
            return 1
        else:
            print("Strange answer")
            return 2

    @locked
    def reboot_device(self, print=logger.info) -> int:
        #CMD_REBOOT = 0x33
        to_send = self._send_command(COMMAND_NUM.CMD_REBOOT.value, b"")
        self.ser.write(to_send)
        return 0


class PlaceHolderDevice():
    def __init__(self):
        class SerPlaceHolder():
            def close(self):
                pass

        self.ser = SerPlaceHolder()
        self.counter = 0
        self.already_was_out = False
        self.turned_off = False
        self.get_counter = 0
        self.ota_chunk_size = 0x2000
        self.model_chunk_size = 0x1000

    @locked
    def trigger_measurement(self, time_to_suck, print=logger.info):
        print("Started trigger measurement")
        return b"\x00"


    @locked
    def get_result(self):
        h2conc = [0.9, ]
        return h2conc
    @locked
    def get_cycle(self):
        resistances, temperatures = np.ones(DOTS_NUMBER), np.ones(DOTS_NUMBER)
        times = np.arange(temperatures.shape[0])
        return times, temperatures, resistances

    @locked
    def get_status(self, print=logger.info):
        answer = bytes(4)
        return answer, "Status OK"

    @locked
    def get_have_data(self, print=logger.info):
        print("There is no data")
        if self.already_was_out:
            return b"\x00"
        else:
            return b"\x01"

    @locked
    def get_have_result(self):
        print("The is no new result")
        if self.already_was_out:
            return b"\x00"
        else:
            return b"\x01"
    @locked
    def get_state(self, print=logger.info):
        if self.turned_off:
            return 5
        if self.counter == 0:
            print("IDLE")
            self.counter += 1
            return 0
        elif self.counter < 10:
            print("EXHALE")
            self.counter += 1
            return 1
        elif self.counter < 15:
            print("MEASURING")
            self.counter += 1
            if self.already_was_out:
                self.already_was_out = False
            return 2
        elif self.counter < 20:
            print("PURGING")
            if not self.already_was_out:
                self.already_was_out = True
            self.counter += 1
            return 3
        elif self.counter == 20:
            self.counter = 0
            return 3
        else:
            print("Strange answer")
            self.counter += 1
            return 4

    @locked
    def get_heater_calibration(self, print=logger.info):
        return np.linspace(0.3, 5, num=401), np.linspace(30, 500, num=401)

    def get_heater_cal_transform(self, print=logger.info):
        return HeaterCalTransformTuple(100, 0.1)

    def get_heater_params(self, print=logger.info):
        return HeaterParamsTuple(120, 10, 31, 14, 12, 1242, 12, 12, True)
    @locked
    def get_ambient_temp(self, print=logger.info):
        # CMD_GET_AMBIENT_TEMP = 0x34
        return 123.1

    @locked
    def suspend_heater(self):
        self.turned_off = True
        pass

    @locked
    def start_ota(self):
        return 0

    @locked
    def chunk_ota(self, chunk):
        sleep(0.01)
        return 0

    @locked
    def check_ota(self):
        return 0

    @locked
    def finalize_ota(self):
        return 0

    @locked
    def break_ota(self):
        return 0


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
    upload_model_signal = Signal(str)



    def __init__(self):
        super().__init__()
        self.device: typing.Optional[typing.Union[MSDesktopDevice, PlaceHolderDevice]] = None

        self.busy = False

        self.gas_already_sent = False
        self.already_waited = 0
        self.gas_iterator_state = 0
        self.gas_iterator_counter = 0
        self.gas_sensor_state = 0
        self.prev_gas_iterator_state = None
        self.conc_set = "-4"
        self.gasstand_timer_current_state = "0"

        self.need_to_trigger_measurement = False
        self.need_to_wait_for_scientist = False

        self.send_ota_break = False

        self.heater_calibration_signal.connect(self.get_heater_calibration)
        self.upload_firmware_signal.connect(self.upload_firmware)
        self.upload_temperature_cycle_signal.connect(self.upload_temperature_cycle)
        self.upload_model_signal.connect(self.upload_model)


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
            answer, decoded_answer = self.device.get_status()
            self.message.emit(decoded_answer)

    @Slot(int)
    def need_to_trigger_measurement_callback(self, check_state: int):
        self.need_to_trigger_measurement = True if check_state else False

    @Slot(int)
    def need_to_wait_scientist_callback(self, check_state: int):
        self.need_to_wait_for_scientist = True if check_state else False

    @Slot(str)
    def set_before_trigger_time(self, value):
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
                        host, port = self.parent().settings_widget.get_gas_stand_settings()
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
                    if self.device.get_have_data()[0] == 0 and self.device.get_have_result()[0] == 0:
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
                    self.message.emit("Calibration not loaded, array size not equal to 301 element")
                answer = self.device.set_cycle(values)
                if answer[0] == 0:
                    self.message.emit("Calibration loaded")
                else:
                    self.message.emit("Calibration not loaded, something with device connection")
            self.busy = False

    @Slot(str)
    def upload_model(self, filename):
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
            model_post_send_answer = self.device.post_model_update_init(1, filebinarysize, crc)
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

