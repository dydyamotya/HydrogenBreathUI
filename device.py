import serial
import struct
import crcmod
import enum
import threading
import functools
import numpy as np


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


def form_error_bytes(num):
    return (1 << num).to_bytes(4, 'little')


lock = threading.Lock()


def locked(func):
    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        with lock:
            return func(*args, **kwargs)

    return _wrapper



class TestBench():
    def __init__(self, port, crc):
        if isinstance(port, str):
            self.ser = serial.Serial(port=port, timeout=1)
        else:
            self.ser = port
        self.crc = crc
        self.counter = 0
        self.get_counter = 0

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
        print(buffer)
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
                        print("END BYTE encountered")
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
        print(buffer)
        body_and_counter, crc_got = buffer[:-4], buffer[-4:]
        print(crc_got)
        if self.crc(body_and_counter) == crc_got:
            print("CRC OK")
        else:
            print("My CRC", self.crc(body_and_counter))
            print("CRC ERROR")
        command, *useful, counter = body_and_counter
        if command != need_command:
            print(f"Command error {command} {need_command}")
        if counter != self.get_counter:
            print(f"Counters are not equal: got counter = {counter}, inter counter = {self.get_counter}")
            self.get_counter = counter + 1
        else:
            self.get_counter += 1
        return bytearray(useful)

    @locked
    def trigger_measurement(self, time_to_suck, print=print):
        useful = struct.pack("<f", time_to_suck)
        to_send = self._send_command(COMMAND_NUM.TRIGGER_MEASUREMENT.value, useful=useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.TRIGGER_MEASUREMENT.value)
        if answer == bytearray(b"\x00"):
            print("Started trigger measurement")
        elif answer == bytearray(b"\x01"):
            print("Something bad")
        else:
            print("Strange answer", answer)
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
    def get_status(self, print=print):
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
    def set_heater(self, *parameters):
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
            print("Strange answer", answer)
        return answer

    @locked
    def set_meas(self, *parameters):
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
            print("Strange answer", answer)
        return answer

    @locked
    def set_cycle(self, floats):
        useful = struct.pack("<" + "f" * DOTS_NUMBER, *floats)
        to_send = self._send_command(COMMAND_NUM.SET_CYCLE.value, useful)
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.SET_CYCLE.value)
        if answer == bytearray(b"\x00"):
            print("Status OK")
        elif answer == bytearray(b"\x01"):
            print("Status ERROR")
        else:
            print("Strange answer", answer)
        return answer

    @locked
    def get_have_data(self, print=print):
        to_send = self._send_command(COMMAND_NUM.HAVE_DATA.value, b"")
        self.ser.write(to_send)
        answer = self._get_answer(COMMAND_NUM.HAVE_DATA.value)
        if answer == bytearray(b"\x01"):
            print("The is no data")
        elif answer == bytearray(b"\x00"):
            print("Data Have I")
        else:
            print("Strange answer")
        return answer

    @locked
    def get_have_result(self):
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
    def get_state(self):
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
        else:
            print("Strange answer")
            return answer
