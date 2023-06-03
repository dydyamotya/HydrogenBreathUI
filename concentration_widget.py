from PySide6 import QtWidgets
import pathlib

class ConcentrationWidget(QtWidgets.QWidget):
    def __init__(self, *args, settings=None, device_proxy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.loaded = False
        self.gas_state_to_conc_dict = {}

        self.settings = settings
        self.device_proxy = device_proxy
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("Concentrations")
        main_layout = QtWidgets.QVBoxLayout(self)

        buttons_layout = QtWidgets.QHBoxLayout()
        entries_layout = QtWidgets.QFormLayout()
        main_layout.addLayout(buttons_layout)
        main_layout.addLayout(entries_layout)

        load_file_button = QtWidgets.QPushButton("Load file")
        buttons_layout.addWidget(load_file_button)
        load_file_button.clicked.connect(self.load_file_callback)
        apply_button = QtWidgets.QPushButton("Apply")
        buttons_layout.addWidget(apply_button)
        apply_button.clicked.connect(self.apply_callback)


        cylinder_conc = self.settings.value("cylinder_conc") if self.settings is not None else ""
        common_flow = self.settings.value("common_flow") if self.settings is not None else ""

        self.cylinder_conc_entry = QtWidgets.QLineEdit()
        self.cylinder_conc_entry.setText(cylinder_conc)
        self.common_flow_entry = QtWidgets.QLineEdit()
        self.common_flow_entry.setText(common_flow)

        entries_layout.addRow("H2 conc in cylinder, ppm", self.cylinder_conc_entry)
        entries_layout.addRow("Common flow, ml/min", self.common_flow_entry)

        self.table_widget = QtWidgets.QTableWidget(self)
        self.reset_table_widget()
        main_layout.addWidget(self.table_widget)



    def reset_table_widget(self):
        self.table_widget.clear()
        self.table_widget.setColumnCount(2)
        self.table_widget.setHorizontalHeaderLabels(("Gas state", "H2 conc"))

    def load_file_callback(self):
        gas_state_dir = self.settings.value("gas_state_dir") if self.settings is not None else "."
        filename, *_ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose gas state file",
                                                             gas_state_dir)

        all_values = []
        if filename:
            if self.settings:
                self.settings.setValue("gas_state_dir", pathlib.Path(filename).parent.as_posix())
            with open(filename, "r") as fd:
                for line in fd:
                    values = line.strip().replace(",", ".").split()
                    gas_state = values[0]
                    h2_flow = values[2]
                    all_values.append((gas_state, h2_flow))

        all_values = sorted(all_values, key=lambda x: int(x[0]))

        self.fill_table_with_values(all_values[1::2])

    def apply_callback(self):
        self.calculate_gas_state_to_conc_dict_from_table_data()

    def fill_table_with_values(self, values):
        try:
            common_flow_float = float(self.common_flow_entry.text())
            h2_conc_in_gas_cylinder_float = float(self.cylinder_conc_entry.text())
        except:
            pass
        else:
            if self.settings:
                self.settings.setValue("cylinder_conc", self.cylinder_conc_entry.text())
                self.settings.setValue("common_flow", self.common_flow_entry.text())
            self.reset_table_widget()
            self.table_widget.setRowCount(len(values))
            for idx, (gas_state, h2_flow) in enumerate(values):
                gas_state_item = QtWidgets.QTableWidgetItem(gas_state)
                self.table_widget.setItem(idx, 0, gas_state_item)
                try:
                    conc_item = QtWidgets.QTableWidgetItem(str(round(float(h2_flow) * h2_conc_in_gas_cylinder_float / common_flow_float, 3)))
                except ZeroDivisionError:
                    conc_item = QtWidgets.QTableWidgetItem("-1")
                self.table_widget.setItem(idx, 1, conc_item)
            self.calculate_gas_state_to_conc_dict_from_table_data()


    def calculate_gas_state_to_conc_dict_from_table_data(self):
        self.gas_state_to_conc_dict = {self.table_widget.item(idx, 0).text(): self.table_widget.item(idx, 1).text() for idx in range(self.table_widget.rowCount())}
        self.loaded = True

    def get_conc_for_state(self, gas_state: str) -> str:
        if self.loaded:
            try:
                return self.gas_state_to_conc_dict[gas_state]
            except KeyError:
                return "-2"
        else:
            return "-3"

    def drop_loaded(self):
        self.loaded = False

    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def set_conc_state_for_device(self, gas_sensor_state):
        if self.device_proxy is not None:
            self.device_proxy.set_conc_set(self.get_conc_for_state(gas_sensor_state))
