import logging

from PySide6 import QtWidgets, QtCore, QtGui
from serial.tools.list_ports import comports
import logging

logger = logging.getLogger(__name__)

class SettingsWidget(QtWidgets.QWidget):
    def __init__(self, parent, global_application_settings: QtCore.QSettings):
        super().__init__(parent, f=QtCore.Qt.WindowType.Tool)
        self.setWindowTitle("Settings")
        self.global_application_settings = global_application_settings

        main_layout = QtWidgets.QVBoxLayout(self)

        device_groupbox = QtWidgets.QGroupBox('Device')
        device_groupbox_layout = QtWidgets.QFormLayout(device_groupbox)
        self.device_port_combobox = QtWidgets.QComboBox()
        self.device_port_combobox.setPlaceholderText("COM5")
        device_groupbox_layout.addRow("Port", self.device_port_combobox)
        self.refresh_ports()
        refresh_ports_button = QtWidgets.QPushButton("Refresh")
        refresh_ports_button.clicked.connect(self.refresh_ports)
        device_groupbox_layout.addWidget(refresh_ports_button)
        if self.global_application_settings.value("comm/com"):
            com_saved = str(self.global_application_settings.value("comm/com"))
            for i in range(self.device_port_combobox.count()):
                com_found = self.device_port_combobox.itemText(i)
                if com_found == com_saved:
                    self.device_port_combobox.setCurrentIndex(i)

        self.device_port_combobox.currentTextChanged.connect(self.device_port_text_changed)

        main_layout.addWidget(device_groupbox)

        gas_stand_groupbox = QtWidgets.QGroupBox("Gas Stand")
        gas_stand_groupbox_layout = QtWidgets.QFormLayout(gas_stand_groupbox)
        self.gas_stand_host_lineedit = QtWidgets.QLineEdit()
        self.gas_stand_host_lineedit.setPlaceholderText("127.0.0.1")
        self.gas_stand_host_validator = QtGui.QRegularExpressionValidator(QtCore.QRegularExpression("\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}"))
        self.gas_stand_host_lineedit.setValidator(self.gas_stand_host_validator)
        if self.global_application_settings.value("comm/host"):
            self.gas_stand_host_lineedit.setText(str(self.global_application_settings.value("comm/host")))

        self.gas_stand_port_lineedit = QtWidgets.QLineEdit()
        self.gas_stand_port_lineedit.setPlaceholderText("5000")
        self.gas_stand_port_validator = QtGui.QRegularExpressionValidator(QtCore.QRegularExpression("\\d{1,5}"))
        self.gas_stand_port_lineedit.setValidator(self.gas_stand_port_validator)
        if self.global_application_settings.value("comm/port"):
            self.gas_stand_port_lineedit.setText(str(self.global_application_settings.value("comm/port")))

        gas_stand_groupbox_layout.addRow("Host", self.gas_stand_host_lineedit)
        gas_stand_groupbox_layout.addRow("Port", self.gas_stand_port_lineedit)

        main_layout.addWidget(gas_stand_groupbox)


    def get_gas_stand_settings(self):
        gas_stand_host_validator_state, *_ = self.gas_stand_host_validator.validate(self.gas_stand_host_lineedit.text(), 0)
        if gas_stand_host_validator_state != QtGui.QValidator.State.Acceptable:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Wrong IP in gas stand host")
            msg_box.exec_()
            return

        gas_stand_port_validator_state, *_ = self.gas_stand_port_validator.validate(self.gas_stand_port_lineedit.text(), 0)
        if gas_stand_port_validator_state != QtGui.QValidator.State.Acceptable:
            msg_box = QtWidgets.QMessageBox()
            msg_box.setText("Wrong port in gas stand port")
            msg_box.exec_()
            return

        return self.gas_stand_host_lineedit.text(), int(self.gas_stand_port_lineedit.text())

    def get_device_port(self):
        return self.device_port_combobox.currentText()

    @QtCore.Slot(str)
    def device_port_text_changed(self, new_value: str):
        logger.debug("Device port in settings changed")
        self.global_application_settings.setValue("comm/com", new_value)


    def refresh_ports(self):
        self.device_port_combobox.clear()
        devices = tuple(map(lambda x: x.device, comports())) + ("test",)
        logger.debug(f"Devices: {devices}")
        self.device_port_combobox.addItems(devices)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.save_global_settings()

    def save_global_settings(self):
        if self.gas_stand_host_lineedit.text():
            self.global_application_settings.setValue("comm/host", self.gas_stand_host_lineedit.text())
        if self.gas_stand_port_lineedit.text():
            self.global_application_settings.setValue("comm/port", self.gas_stand_port_lineedit.text())

    def toggle_visible(self):
        if self.isVisible():
            self.save_global_settings()
        self.setVisible(not self.isVisible())


