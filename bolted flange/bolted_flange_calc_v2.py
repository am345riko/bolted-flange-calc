"""
PyQt6 GUI for Bolted Flange Calculator (NASA-STD-5020)
"""

import sys
import os
import html as _html
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QGridLayout,
    QTextEdit, QCheckBox, QTabWidget, QScrollArea, QMessageBox,
    QSplitter, QFileDialog, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QFont, QDoubleValidator

try:
    from bolted_flange_calc import (
        compute, Inputs, Layer, MomentLoad, ShearLoad, ThermalLoad,
        FatigueCheck, ThreadStrip, CalcTrace, Results, make_pdf_report
    )
except ImportError as e:
    print(f"Error: Could not import bolted_flange_calc module: {e}")
    sys.exit(1)

# QWebEngineView (used to render the Calculation Trace tab with KaTeX) is an
# optional dependency (PyQt6-WebEngine). Import must happen before
# QApplication is constructed. If unavailable, the app still runs; the trace
# tab falls back to a plain QTextEdit.
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False

# Directory containing this file ("bolted flange"), used as the base for
# locating the vendored KaTeX assets under assets/katex/. When frozen by
# PyInstaller, bundled data files live in sys._MEIPASS instead.
if getattr(sys, "frozen", False):
    _MODULE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    _MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


def build_trace_html(entries) -> str:
    """Build a complete HTML document rendering a list of TraceEntry-like
    objects (each with .plain and optional .latex) with KaTeX typesetting.

    Assets are referenced via relative paths (assets/katex/...); the caller
    must load this HTML with a baseUrl pointing at the 'bolted flange'
    directory so those relative paths resolve.
    """
    body_parts = []
    for e in entries:
        plain = getattr(e, "plain", "")
        latex = getattr(e, "latex", None)
        if latex:
            escaped_latex = _html.escape(latex, quote=True)
            body_parts.append(f'<div class="eq" data-latex="{escaped_latex}"></div>')
        else:
            escaped_plain = _html.escape(plain)
            body_parts.append(f'<div class="txt">{escaped_plain}</div>')

    body_html = "\n".join(body_parts)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="assets/katex/katex.min.css">
<script src="assets/katex/katex.min.js"></script>
<style>
  html, body {{
    background: #ffffff;
    color: #1a1a1a;
    margin: 0;
    padding: 0;
    max-width: none;
  }}
  body {{
    padding: 14px 18px;
  }}
  .txt {{
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
    color: #555555;
    padding: 3px 0;
    white-space: pre-wrap;
  }}
  .eq {{
    font-size: 13px;
    padding: 4px 0 8px 0;
    border-bottom: 1px solid #e2e2e2;
    margin-bottom: 2px;
  }}
  .eq:last-child {{
    border-bottom: none;
  }}
  .katex-display {{
    text-align: left;
    margin: 0.35em 0;
  }}
  .katex-display > .katex {{
    text-align: left;
  }}
  @media (prefers-color-scheme: dark) {{
    html, body {{
      background: #1e1e1e;
      color: #e6e6e6;
    }}
    .txt {{
      color: #9a9a9a;
    }}
    .eq {{
      border-bottom: 1px solid #3a3a3a;
    }}
  }}
</style>
</head>
<body>
{body_html}
<script>
  document.querySelectorAll('.eq').forEach(function (div) {{
    try {{
      katex.render(div.dataset.latex, div, {{ throwOnError: false, displayMode: true }});
    }} catch (err) {{
      div.textContent = div.dataset.latex;
    }}
  }});
</script>
</body>
</html>
"""


class CalculationThread(QThread):
    finished = pyqtSignal(object, object)
    error = pyqtSignal(str)

    def __init__(self, inputs, layers):
        super().__init__()
        self.inputs = inputs
        self.layers = layers

    def run(self):
        try:
            trace = CalcTrace()
            results = compute(self.inputs, self.layers, trace=trace)
            self.finished.emit(results, trace)
        except Exception as e:
            self.error.emit(str(e))


class LayerTable(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.table = QTableWidget(1, 4)
        self.table.setHorizontalHeaderLabels(['Name', 'E (MPa)', 'Thickness (mm)', 'D_interface (mm)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setItem(0, 0, QTableWidgetItem("Flange stack (symmetric)"))
        self.table.setItem(0, 1, QTableWidgetItem("71000.0"))
        self.table.setItem(0, 2, QTableWidgetItem("12.0"))
        self.table.setItem(0, 3, QTableWidgetItem("12.0"))
        layout.addWidget(self.table)
        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Add Layer")
        remove_btn = QPushButton("Remove Layer")
        add_btn.clicked.connect(self.add_layer)
        remove_btn.clicked.connect(self.remove_layer)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def add_layer(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(f"Layer {row + 1}"))
        self.table.setItem(row, 1, QTableWidgetItem("71000.0"))
        self.table.setItem(row, 2, QTableWidgetItem("10.0"))
        self.table.setItem(row, 3, QTableWidgetItem("12.0"))

    def remove_layer(self):
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.rowCount() - 1)

    def get_layers(self):
        layers = []
        for row in range(self.table.rowCount()):
            try:
                name = self.table.item(row, 0).text()
                E_MPa = float(self.table.item(row, 1).text())
                thickness_mm = float(self.table.item(row, 2).text())
                D_interface_mm = float(self.table.item(row, 3).text())
                layers.append(Layer(name=name, E_MPa=E_MPa,
                                    thickness_mm=thickness_mm, D_interface_mm=D_interface_mm))
            except (ValueError, AttributeError) as e:
                raise ValueError(f"Invalid layer data in row {row + 1}: {e}")
        return layers


class FlangeCalculatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bolted Flange Calculator (NASA-STD-5020)")
        self.setGeometry(100, 100, 1700, 950)
        self.results = None
        self.trace = None
        self.last_inputs = None
        self.last_layers = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.create_input_panel())
        splitter.addWidget(self.create_results_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)
        self.statusBar().showMessage("Ready")

    # -------------------------------------------------------------------------
    # Input panel
    # -------------------------------------------------------------------------

    def create_input_panel(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(530)

        container = QWidget()
        layout = QVBoxLayout(container)

        title = QLabel("Input Parameters")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        self.input_fields = {}

        # --- Separating Load ---
        layout.addWidget(self._group("Separating Load (Pressure)", [
            ("Pc_psi",                "Chamber Pressure (psi)",          "300.0"),
            ("effective_diameter_in", "Effective Diameter (in)",          "3.53"),
            ("n_bolts",               "Number of Bolts",                  "8",     "int"),
            ("F_axial_extra_N",       "Extra Axial Force (N, separating)","0.0"),
        ]))

        # --- Bolt Geometry ---
        layout.addWidget(self._group("Bolt Geometry", [
            ("d_bolt_mm", "Nominal Bolt Diameter (mm)",   "8.0"),
            ("d_hole_mm", "Clearance Hole Diameter (mm)", "8.5"),
            ("At_mm2",    "Tensile Stress Area A_t (mm²)","36.6"),
            ("Ad_mm2",    "Shank Area A_d (mm²)",         "50.3"),
            ("Am_mm2",    "Min. Minor-Dia. Thread Area A_m (mm²)", "32.8"),
        ]))

        # --- Bolt Material & Grip ---
        layout.addWidget(self._group("Bolt Material & Grip", [
            ("Eb_MPa",  "Bolt Modulus E_b (MPa)",             "200000.0"),
            ("lt_mm",   "Threaded Length in Grip l_t (mm)",   "10.0"),
            ("ld_mm",   "Unthreaded Length in Grip l_d (mm)", "20.0"),
            ("Sp_MPa",  "Proof Strength S_p (MPa)",           "600.0"),
            ("Sy_MPa",  "Yield Strength S_y (MPa)",           "720.0"),
            ("Su_MPa",  "Ultimate Strength S_u (MPa)",        "900.0"),
        ]))

        # --- NASA Preload ---
        preload_group = QGroupBox("NASA-STD-5020 Preload Model")
        pl_layout = QGridLayout()
        self.input_fields["Ppi_nom_N"] = self._field_row(pl_layout, 0, "Nominal Preload per Bolt (N)", "12000.0")
        self.input_fields["Gamma_p"] = self._field_row(pl_layout, 1, "Preload Variation Factor Γ_p", "0.25")
        self.input_fields["Ppr_fraction"] = self._field_row(pl_layout, 2, "Relaxation Fraction Ppr", "0.05")
        self.input_fields["L_min_total_N"] = self._field_row(pl_layout, 3, "Min Total Clamp Load (N)", "0.0")
        self.input_fields["c_max"] = self._field_row(pl_layout, 4, "Torque Tolerance Factor c_max", "1.0")
        self.input_fields["c_min"] = self._field_row(pl_layout, 5, "Torque Tolerance Factor c_min", "1.0")
        self.separation_critical = QCheckBox("Separation-critical joint (Γ per Eq 6-5a)")
        self.separation_critical.setChecked(True)
        pl_layout.addWidget(self.separation_critical, 6, 0, 1, 2)
        preload_group.setLayout(pl_layout)
        layout.addWidget(preload_group)

        # --- Safety Factors ---
        layout.addWidget(self._group("Safety Factors (NASA-STD-5001)", [
            ("FSu",          "Ultimate FS (FSu)",            "1.4"),
            ("FSy",          "Yield FS (FSy)",               "1.25"),
            ("FF",           "Fitting Factor (FF)",          "1.15"),
            ("n_load_intro", "Load Introduction Factor n",   "0.5"),
            ("target_sep_factor", "Target Separation FoS",  "1.4"),
        ]))

        # --- Optional: Torque ---
        torque_group = QGroupBox("Optional: Installation Torque")
        tq_layout = QGridLayout()
        self.torque_enabled = QCheckBox("Enable Torque Calculation")
        tq_layout.addWidget(self.torque_enabled, 0, 0, 1, 2)
        self.input_fields["K_nut"] = self._field_row(tq_layout, 1, "Nut Factor K", "0.20")
        torque_group.setLayout(tq_layout)
        layout.addWidget(torque_group)

        # --- Optional: Moment Load ---
        moment_group = QGroupBox("Optional: Moment Load")
        ml = QGridLayout()
        self.moment_enabled = QCheckBox("Enable Moment Load")
        ml.addWidget(self.moment_enabled, 0, 0, 1, 2)
        self.input_fields["M_Nmm"] = self._field_row(ml, 1, "Moment M (N·mm)", "0.0")
        self.input_fields["bolt_circle_diameter_mm"] = self._field_row(ml, 2, "Bolt Circle Diameter BCD (mm)", "0.0")
        self.input_fields["prying_factor"] = self._field_row(ml, 3, "Prying Factor", "1.0")
        moment_group.setLayout(ml)
        layout.addWidget(moment_group)

        # --- Optional: Shear ---
        shear_group = QGroupBox("Optional: Shear Load")
        sl = QGridLayout()
        self.shear_enabled = QCheckBox("Enable Shear Analysis")
        sl.addWidget(self.shear_enabled, 0, 0, 1, 2)
        self.input_fields["F_shear_per_bolt_N"] = self._field_row(sl, 1, "Shear per Bolt (N)", "0.0")
        self.shear_at_threads = QCheckBox("Shear plane through threads")
        sl.addWidget(self.shear_at_threads, 2, 0, 1, 2)
        shear_group.setLayout(sl)
        layout.addWidget(shear_group)

        # --- Optional: Thermal ---
        thermal_group = QGroupBox("Optional: Thermal Preload Change")
        thl = QGridLayout()
        self.thermal_enabled = QCheckBox("Enable Thermal Analysis")
        thl.addWidget(self.thermal_enabled, 0, 0, 1, 2)
        self.input_fields["alpha_bolt_per_C"] = self._field_row(thl, 1, "Bolt CTE α_b (1/°C)", "11.7e-6")
        self.input_fields["alpha_member_per_C"] = self._field_row(thl, 2, "Member CTE α_m (1/°C)", "23.1e-6")
        self.input_fields["dT_C"] = self._field_row(thl, 3, "Temperature Change ΔT (°C)", "0.0")
        self.input_fields["L_grip_mm"] = self._field_row(thl, 4, "Grip Length L_grip (mm)", "30.0")
        thermal_group.setLayout(thl)
        layout.addWidget(thermal_group)

        # --- Optional: Fatigue ---
        fatigue_group = QGroupBox("Optional: Fatigue (Goodman)")
        fl = QGridLayout()
        self.fatigue_enabled = QCheckBox("Enable Fatigue Analysis")
        fl.addWidget(self.fatigue_enabled, 0, 0, 1, 2)
        self.input_fields["F_ext_max_N"] = self._field_row(fl, 1, "Max Cyclic Load per Bolt (N)", "0.0")
        self.input_fields["F_ext_min_N"] = self._field_row(fl, 2, "Min Cyclic Load per Bolt (N)", "0.0")
        self.input_fields["Se_MPa"] = self._field_row(fl, 3, "Endurance Limit S_e (MPa)", "0.0")
        fatigue_group.setLayout(fl)
        layout.addWidget(fatigue_group)

        # --- Optional: Thread Strip ---
        strip_group = QGroupBox("Optional: Thread Stripping")
        stl = QGridLayout()
        self.strip_enabled = QCheckBox("Enable Thread Strip Check")
        stl.addWidget(self.strip_enabled, 0, 0, 1, 2)
        self.input_fields["L_engage_mm"] = self._field_row(stl, 1, "Thread Engagement L_e (mm)", "0.0")
        self.input_fields["Sy_member_MPa"] = self._field_row(stl, 2, "Internal Thread Sy (MPa)", "0.0")
        strip_group.setLayout(stl)
        layout.addWidget(strip_group)

        # --- Member Layers ---
        layers_group = QGroupBox("Member Layers")
        ll = QVBoxLayout()
        self.layer_table = LayerTable()
        ll.addWidget(self.layer_table)
        layers_group.setLayout(ll)
        layout.addWidget(layers_group)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        self.calc_btn = QPushButton("Calculate")
        self.calc_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; }"
        )
        self.calc_btn.clicked.connect(self.run_calculation)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.load_default_values)
        btn_layout.addWidget(self.calc_btn)
        btn_layout.addWidget(reset_btn)
        layout.addLayout(btn_layout)

        scroll.setWidget(container)
        return scroll

    def _group(self, title, fields):
        group = QGroupBox(title)
        layout = QGridLayout()
        for i, info in enumerate(fields):
            name, label, default = info[0], info[1], info[2]
            ftype = info[3] if len(info) > 3 else "float"
            self.input_fields[name] = self._field_row(layout, i, label, default, ftype)
        group.setLayout(layout)
        return group

    def _field_row(self, layout, row, label_text, default_val, field_type="float"):
        label = QLabel(label_text + ":")
        if field_type == "int":
            field = QSpinBox()
            field.setRange(1, 1_000_000)
            field.setValue(int(float(default_val)))
        else:
            field = QLineEdit(default_val)
            v = QDoubleValidator()
            v.setNotation(QDoubleValidator.Notation.ScientificNotation)
            field.setValidator(v)
        layout.addWidget(label, row, 0)
        layout.addWidget(field, row, 1)
        return field

    # -------------------------------------------------------------------------
    # Results panel
    # -------------------------------------------------------------------------

    def create_results_panel(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        title = QLabel("Results")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        tabs = QTabWidget()
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Courier", 9))
        tabs.addTab(self.summary_text, "Summary")

        if WEBENGINE_AVAILABLE:
            self.trace_text = None
            self.trace_view = QWebEngineView()
            # Show an empty shell until a calculation populates it.
            self.trace_view.setHtml(
                build_trace_html([]),
                baseUrl=QUrl.fromLocalFile(_MODULE_DIR + os.sep),
            )
            tabs.addTab(self.trace_view, "Calculation Trace")
        else:
            self.trace_view = None
            self.trace_text = QTextEdit()
            self.trace_text.setReadOnly(True)
            self.trace_text.setFont(QFont("Courier", 8))
            tabs.addTab(self.trace_text, "Calculation Trace")
        layout.addWidget(tabs)

        btn_layout = QHBoxLayout()
        pdf_btn = QPushButton("Generate PDF Report")
        pdf_btn.clicked.connect(self.generate_pdf)
        copy_btn = QPushButton("Copy Summary")
        copy_btn.clicked.connect(self.copy_summary)
        btn_layout.addWidget(pdf_btn)
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        return container

    # -------------------------------------------------------------------------
    # Default values
    # -------------------------------------------------------------------------

    def load_default_values(self):
        defaults = {
            "Pc_psi": "300.0",
            "effective_diameter_in": "3.53",
            "n_bolts": "8",
            "F_axial_extra_N": "0.0",
            "d_bolt_mm": "8.0",
            "d_hole_mm": "8.5",
            "At_mm2": "36.6",
            "Ad_mm2": "50.3",
            "Am_mm2": "32.8",
            "Eb_MPa": "200000.0",
            "lt_mm": "10.0",
            "ld_mm": "20.0",
            "Sp_MPa": "600.0",
            "Sy_MPa": "720.0",
            "Su_MPa": "900.0",
            "Ppi_nom_N": "12000.0",
            "Gamma_p": "0.25",
            "Ppr_fraction": "0.05",
            "L_min_total_N": "0.0",
            "c_max": "1.0",
            "c_min": "1.0",
            "FSu": "1.4",
            "FSy": "1.25",
            "FF": "1.15",
            "n_load_intro": "0.5",
            "target_sep_factor": "1.4",
            "K_nut": "0.20",
            "M_Nmm": "0.0",
            "bolt_circle_diameter_mm": "0.0",
            "prying_factor": "1.0",
            "F_shear_per_bolt_N": "0.0",
            "alpha_bolt_per_C": "11.7e-6",
            "alpha_member_per_C": "23.1e-6",
            "dT_C": "0.0",
            "L_grip_mm": "30.0",
            "F_ext_max_N": "0.0",
            "F_ext_min_N": "0.0",
            "Se_MPa": "0.0",
            "L_engage_mm": "0.0",
            "Sy_member_MPa": "0.0",
        }
        for name, val in defaults.items():
            if name in self.input_fields:
                w = self.input_fields[name]
                if isinstance(w, QSpinBox):
                    w.setValue(int(float(val)))
                else:
                    w.setText(val)

        self.torque_enabled.setChecked(False)
        self.moment_enabled.setChecked(False)
        self.shear_enabled.setChecked(False)
        self.shear_at_threads.setChecked(False)
        self.thermal_enabled.setChecked(False)
        self.fatigue_enabled.setChecked(False)
        self.strip_enabled.setChecked(False)
        self.separation_critical.setChecked(True)

        self.layer_table.table.setRowCount(1)
        self.layer_table.table.setItem(0, 0, QTableWidgetItem("Flange stack (symmetric)"))
        self.layer_table.table.setItem(0, 1, QTableWidgetItem("71000.0"))
        self.layer_table.table.setItem(0, 2, QTableWidgetItem("12.0"))
        self.layer_table.table.setItem(0, 3, QTableWidgetItem("12.0"))

    # -------------------------------------------------------------------------
    # Build Inputs from GUI
    # -------------------------------------------------------------------------

    def _fv(self, name: str) -> float:
        w = self.input_fields[name]
        return float(w.text()) if isinstance(w, QLineEdit) else float(w.value())

    def get_input_values(self):
        n_bolts = self.input_fields["n_bolts"].value()

        K_nut = self._fv("K_nut") if self.torque_enabled.isChecked() else None

        moment = MomentLoad(
            enabled=self.moment_enabled.isChecked(),
            M_Nmm=self._fv("M_Nmm"),
            bolt_circle_diameter_mm=self._fv("bolt_circle_diameter_mm"),
            prying_factor=self._fv("prying_factor"),
        )
        shear = ShearLoad(
            enabled=self.shear_enabled.isChecked(),
            F_shear_per_bolt_N=self._fv("F_shear_per_bolt_N"),
            at_threads=self.shear_at_threads.isChecked(),
        )
        thermal = ThermalLoad(
            enabled=self.thermal_enabled.isChecked(),
            alpha_bolt_per_C=self._fv("alpha_bolt_per_C"),
            alpha_member_per_C=self._fv("alpha_member_per_C"),
            dT_C=self._fv("dT_C"),
            L_grip_mm=self._fv("L_grip_mm"),
        )
        fatigue = FatigueCheck(
            enabled=self.fatigue_enabled.isChecked(),
            F_ext_max_N=self._fv("F_ext_max_N"),
            F_ext_min_N=self._fv("F_ext_min_N"),
            Se_MPa=self._fv("Se_MPa"),
        )
        thread_strip = ThreadStrip(
            enabled=self.strip_enabled.isChecked(),
            L_engage_mm=self._fv("L_engage_mm"),
            Sy_member_MPa=self._fv("Sy_member_MPa"),
        )

        inputs = Inputs(
            Pc_psi=self._fv("Pc_psi"),
            effective_diameter_in=self._fv("effective_diameter_in"),
            n_bolts=n_bolts,
            d_bolt_mm=self._fv("d_bolt_mm"),
            d_hole_mm=self._fv("d_hole_mm"),
            At_mm2=self._fv("At_mm2"),
            Ad_mm2=self._fv("Ad_mm2"),
            Am_mm2=self._fv("Am_mm2"),
            Eb_MPa=self._fv("Eb_MPa"),
            lt_mm=self._fv("lt_mm"),
            ld_mm=self._fv("ld_mm"),
            Sp_MPa=self._fv("Sp_MPa"),
            Sy_MPa=self._fv("Sy_MPa"),
            Su_MPa=self._fv("Su_MPa"),
            Ppi_nom_N=self._fv("Ppi_nom_N"),
            Gamma_p=self._fv("Gamma_p"),
            c_max=self._fv("c_max"),
            c_min=self._fv("c_min"),
            separation_critical=self.separation_critical.isChecked(),
            Ppr_fraction=self._fv("Ppr_fraction"),
            L_min_total_N=self._fv("L_min_total_N"),
            F_axial_extra_N=self._fv("F_axial_extra_N"),
            FSu=self._fv("FSu"),
            FSy=self._fv("FSy"),
            FF=self._fv("FF"),
            n_load_intro=self._fv("n_load_intro"),
            target_sep_factor=self._fv("target_sep_factor"),
            K_nut=K_nut,
            moment=moment,
            shear=shear,
            thermal=thermal,
            fatigue=fatigue,
            thread_strip=thread_strip,
        )
        layers = self.layer_table.get_layers()
        return inputs, layers

    # -------------------------------------------------------------------------
    # Calculation
    # -------------------------------------------------------------------------

    def run_calculation(self):
        try:
            self.statusBar().showMessage("Running calculation...")
            inputs, layers = self.get_input_values()
            # Snapshot the exact inputs/layers used for this calculation so
            # generate_pdf reports on what was actually computed, even if the
            # user edits the input fields afterward.
            self.last_inputs = inputs
            self.last_layers = layers
            self.calc_btn.setEnabled(False)
            self.calc_thread = CalculationThread(inputs, layers)
            self.calc_thread.finished.connect(self.on_calculation_finished)
            self.calc_thread.error.connect(self.on_calculation_error)
            self.calc_thread.start()
        except Exception as e:
            QMessageBox.critical(self, "Input Error", f"Error reading inputs:\n{str(e)}")
            self.statusBar().showMessage("Error")

    def on_calculation_finished(self, results, trace):
        self.calc_btn.setEnabled(True)
        self.results = results
        self.trace = trace
        self.summary_text.setHtml(self.format_summary(results))
        if WEBENGINE_AVAILABLE:
            self.trace_view.setHtml(
                build_trace_html(trace.entries),
                baseUrl=QUrl.fromLocalFile(_MODULE_DIR + os.sep),
            )
        else:
            self.trace_text.setPlainText("\n".join(trace.lines))
        self.statusBar().showMessage("Calculation complete")

        failed = []
        r = results
        if r.MS_proof < 0:       failed.append("Proof strength")
        if r.MSu_tension < 0:    failed.append("Ultimate tension")
        if r.MSy_tension < 0:    failed.append("Yield tension")
        if r.MS_sep < 0:         failed.append("Separation (Shigley)")
        if r.MSsep_NASA < 0:     failed.append("Separation (NASA)")
        if r.MSu_shear is not None and r.MSu_shear < 0:
            failed.append("Shear ultimate")
        if r.MS_interaction is not None and r.MS_interaction < 0:
            failed.append("T+S interaction")
        if r.fatigue_safety_factor is not None and r.fatigue_safety_factor < 1.0:
            failed.append("Fatigue (Goodman)")
        if r.MS_strip is not None and r.MS_strip < 0:
            failed.append("Thread stripping")
        if failed:
            QMessageBox.warning(
                self, "Design Check Failed",
                "Failed checks:\n• " + "\n• ".join(failed)
            )

    def on_calculation_error(self, error_msg):
        self.calc_btn.setEnabled(True)
        QMessageBox.critical(self, "Calculation Error", f"Error during calculation:\n{error_msg}")
        self.statusBar().showMessage("Calculation failed")

    # -------------------------------------------------------------------------
    # Summary HTML
    # -------------------------------------------------------------------------

    def format_summary(self, r: Results) -> str:
        def pf(ms: float, extra: str = "") -> str:
            ok = ms >= 0.0
            color = "#1a7a1a" if ok else "#cc0000"
            tag = "PASS" if ok else "FAIL"
            return (
                f"<b style='color:{color}'>{tag}</b>"
                f"&nbsp;&nbsp;MS = {ms:.3f}"
                + (f"&nbsp;&nbsp;{extra}" if extra else "")
            )

        def row(label: str, val: str) -> str:
            return f"<tr><td style='padding:2px 8px 2px 0'>{label}</td><td style='padding:2px 0'>{val}</td></tr>"

        def section(title: str) -> str:
            return f"<h3 style='margin-bottom:4px;border-bottom:1px solid #ccc'>{title}</h3><table>"

        html = "<html><body style='font-family:monospace;font-size:9pt'>"

        html += section("Separating Load")
        html += row("P<sub>total</sub>", f"{r.P_total_N:,.1f} N  ({r.P_total_N/1000:.3f} kN)")
        html += row("P<sub>per_bolt</sub>", f"{r.P_per_bolt_N:,.1f} N")
        html += "</table>"

        html += section("Stiffness Split")
        html += row("k<sub>b</sub>", f"{r.kb_N_per_mm:,.1f} N/mm")
        html += row("k<sub>m</sub>", f"{r.km_N_per_mm:,.1f} N/mm")
        html += row("C = k<sub>b</sub>/(k<sub>b</sub>+k<sub>m</sub>)", f"{r.C:.4f}")
        html += row("nC = n&middot;C", f"{r.nC:.4f}")
        if r.C > 0.6:
            html += row("", "<span style='color:orange'><b>WARNING: C &gt; 0.6</b></span>")
        html += "</table>"

        html += section("Installed Preload Bounds (per bolt)")
        html += row("Ppi<sub>nom</sub>", f"{r.Ppi_nom_N:,.1f} N")
        html += row("Ppi<sub>min</sub>", f"{r.Ppi_min_N:,.1f} N")
        html += row("Ppi<sub>max</sub>", f"{r.Ppi_max_N:,.1f} N")
        html += "</table>"

        html += section("Operating Preload (after relaxation + thermal)")
        html += row("P<sub>pr</sub> relaxation loss", f"{r.Ppr_N:,.1f} N")
        html += row("&Delta;Pp<sub>thermal</sub>", f"{r.dPp_thermal_N:,.1f} N")
        html += row("Pp<sub>min</sub>", f"{r.Pp_min_N:,.1f} N")
        html += row("Pp<sub>max</sub>", f"{r.Pp_max_N:,.1f} N")
        html += row("Pp<sub>min_sep</sub> (used for separation)", f"{r.Pp_min_sep_N:,.1f} N")
        html += "</table>"

        html += section("Peak Bolt Tension (informational)")
        html += row("&Delta;F<sub>moment nom</sub>", f"{r.dF_moment_nom_N:,.1f} N")
        html += row("&Delta;F<sub>moment (prying)</sub>", f"{r.dF_moment_N:,.1f} N")
        html += row("F<sub>b_max</sub>", f"<b>{r.Fb_max_N:,.1f} N</b>  ({r.Fb_max_N/1000:.3f} kN)")
        html += "</table>"

        html += section("Strength Margins (NASA-STD-5020 6.2.1 / 6.3)")
        html += row("F<sub>proof_allow</sub>", f"{r.F_proof_allow_N:,.1f} N")
        html += row("Ptu<sub>allow</sub>", f"{r.Ptu_allow_N:,.1f} N")
        html += row("Pty<sub>allow</sub>", f"{r.Pty_allow_N:,.1f} N")
        html += row("P_sep' (Eq 6-10)", f"{r.P_sep_prime_N:,.1f} N")
        html += row("P_tu' (Eq 6-9)", f"{r.P_tu_prime_N:,.1f} N")
        html += row("Separation before rupture", str(r.sep_before_rupture))
        html += row("MS<sub>proof</sub> (informational)", pf(r.MS_proof))
        html += row("MSu<sub>tension</sub> (NASA 6.2.1)", pf(r.MSu_tension))
        html += row("MSy<sub>tension</sub> (NASA 6.3)", pf(r.MSy_tension))
        html += "</table>"

        html += section("Separation")
        html += row("Ppi<sub>req_min</sub>", f"{r.Ppi_req_min_N:,.1f} N")
        html += row("FoS<sub>sep</sub> (Shigley, informational)", f"{r.FoS_sep:.3f}")
        html += row("MS<sub>sep</sub> (Shigley, informational)", pf(r.MS_sep))
        html += row("MSsep<sub>NASA</sub> (governing, App A.11)", pf(r.MSsep_NASA))
        html += row("Required bolts (estimate)", str(r.required_bolts_for_target_sep))
        html += "</table>"

        if r.install_torque_Nmm is not None:
            html += section("Installation Torque")
            html += row("T<sub>install</sub>", f"{r.install_torque_Nmm:,.1f} N&middot;mm  ({r.install_torque_Nmm/1000:.3f} N&middot;m)")
            html += "</table>"

        if r.Psu_allow_N is not None:
            html += section("Shear Analysis")
            html += row("Psu<sub>allow</sub>", f"{r.Psu_allow_N:,.1f} N")
            html += row("MSu<sub>shear</sub>", pf(r.MSu_shear))
            html += row("MS<sub>interaction</sub> (NASA 6.2.3)", pf(r.MS_interaction))
            html += "</table>"

        if r.sigma_a_MPa is not None:
            html += section("Fatigue (Goodman)")
            html += row("&sigma;<sub>a</sub>", f"{r.sigma_a_MPa:.3f} MPa")
            html += row("&sigma;<sub>m</sub>", f"{r.sigma_m_MPa:.3f} MPa")
            sf = r.fatigue_safety_factor
            ok = sf >= 1.0
            color = "#1a7a1a" if ok else "#cc0000"
            html += row("n<sub>f</sub>", f"<b style='color:{color}'>{sf:.3f}</b>&nbsp;&nbsp;{'PASS' if ok else 'FAIL'}&nbsp;(need &ge; 1)")
            html += "</table>"

        if r.F_strip_N is not None:
            html += section("Thread Stripping")
            html += row("F<sub>strip</sub>", f"{r.F_strip_N:,.1f} N")
            html += row("MS<sub>strip</sub>", pf(r.MS_strip))
            html += "</table>"

        html += "</body></html>"
        return html

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def generate_pdf(self):
        if self.results is None or self.last_inputs is None:
            QMessageBox.warning(self, "No Results", "Please run a calculation first.")
            return
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, "Save PDF Report", "flange_report.pdf", "PDF Files (*.pdf)"
            )
            if filename:
                # Use the exact inputs/layers snapshot from the calculation that
                # produced self.results, NOT whatever is currently in the fields.
                actual_filename = make_pdf_report(
                    self.last_inputs, self.last_layers, self.results,
                    filename=filename,
                    project_title="Bolted Flange Joint",
                    calculation_trace=self.trace.lines if self.trace else None,
                )
                QMessageBox.information(self, "Success", f"PDF report generated:\n{actual_filename}")
                self.statusBar().showMessage(f"PDF saved: {actual_filename}")
        except Exception as e:
            QMessageBox.critical(self, "PDF Error", f"Error generating PDF:\n{str(e)}")

    def copy_summary(self):
        if self.results is None:
            QMessageBox.warning(self, "No Results", "Please run a calculation first.")
            return
        QApplication.clipboard().setText(self.summary_text.toPlainText())
        self.statusBar().showMessage("Summary copied to clipboard", 3000)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = FlangeCalculatorGUI()
    window.load_default_values()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
