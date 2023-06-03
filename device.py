import typing

import serial
import struct
import crcmod
import enum
import threading
import functools
import numpy as np
import logging

from collections import namedtuple

logger = logging.getLogger(__name__)

HeaterParamsTuple = namedtuple("HeaterParamsTuple", "tempco, rt_resistance, rt_temp, r_corr, cal_curve_ambient, gain, offset, cal_calib_interval, cal_calibration_enable")
HeaterCalTransformTuple = namedtuple("HeaterCalTransformTuple", "k, b")
class CRCCalculator():
    def __init__(self):
        self.calc = crcmod.mkCrcFun(poly=0x104c11db7, rev=True, initCrc=0, xorOut=0)

    def __call__(self, data):
        return struct.pack("<I", self.calc(data))


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
        if isinstance(port, str):
            self.ser = serial.Serial(port=port, timeout=1)
        else:
            self.ser = port
        self.crc = crc
        self.counter = 0
        self.get_counter = 0
        self.ota_chunk_size = 0x2000

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
    def trigger_measurement(self, time_to_suck, print=logger.info):
        useful = struct.pack("<f", time_to_suck)
        to_send = self._send_command(COMMAND_NUM.TRIGGER_MEASUREMENT.value, useful=useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.TRIGGER_MEASUREMENT.value)
        if answer == bytearray(b"\x00"):
            print("Started trigger measurement")
        elif answer == bytearray(b"\x01"):
            print("Something bad")
        else:
            print("Strange answer")
        return answer

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
    def get_status(self, print=logger.info):
        to_send = self._send_command(COMMAND_NUM.STATUS.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.STATUS.value)
        if answer == bytes(4):
            print("Status OK")
        elif answer == form_error_bytes(0):
            print("Heater error")
        elif answer == form_error_bytes(1):
            print("Measurement system error")
        elif answer == form_error_bytes(2):
            print("Software init")
        elif answer == form_error_bytes(3):
            print("Unknown command")
        elif answer == form_error_bytes(4):
            print("Missed packet")
        elif answer == form_error_bytes(5):
            print("UART parser error")
        elif answer == form_error_bytes(6):
            print("Incorrect command format")
        else:
            print("Strange answer")
        return answer

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
            print("There is no data")
        elif answer == bytearray(b"\x00"):
            print("Data Have I")
        else:
            print("Strange answer")
        return answer

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

class PlaceHolderDevice():
    def __init__(self):
        class SerPlaceHolder():
            def close(self):
                pass

        self.ser = SerPlaceHolder()
        self.counter = 0
    @locked
    def trigger_measurement(self, time_to_suck, print=logger.info):
        print("Started trigger measurement")
        return b"\x00"

    @locked
    def get_result(self):
        h2conc = 0.9
        return h2conc
    @locked
    def get_cycle(self):
        resistances, temperatures = np.ones(DOTS_NUMBER), np.ones(DOTS_NUMBER)
        times = np.arange(temperatures.shape[0])
        return times, temperatures, resistances

    @locked
    def get_status(self, print=logger.info):
        answer = bytes(4)
        print("Status OK")
        return answer

    @locked
    def get_have_data(self, print=logger.info):
        print("There is no data")
        return b"\x01"
    @locked
    def get_have_result(self):
        print("The is no new result")
        return b"\x01"
    @locked
    def get_state(self, print=logger.info):
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
            return 2
        elif self.counter < 20:
            print("PURGING")
            self.counter += 1
            return 3
        elif self.counter == 20:
            self.counter = 0
            return 3
        else:
            print("Strange answer")
            self.counter += 1
            return 5

    @locked
    def get_heater_calibration(self, print=logger.info):
        return np.linspace(0.3, 5, num=401), np.linspace(30, 500, num=401)

    def get_heater_cal_transform(self, print=logger.info):
        return HeaterCalTransformTuple(100, 0.1)

    def get_heater_params(self, print=logger.info):
        return HeaterParamsTuple(120, 10, 31, 14, 12, 1242, 12, 12, True)

