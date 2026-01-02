"""
PyQt6 GUI for Bolted Flange Calculator (NASA-STD-5020)

This GUI wraps the flange calculator and allows users to input all parameters
through a user interface instead of editing code.

Usage:
    python flange_calculator_gui.py

Note: Requires the following files in the same directory:
    - flange_calculator.py (the original calculator code, renamed without "from __future__")
    - report_pdf.py (the PDF generation module)
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QGridLayout,
    QTextEdit, QCheckBox, QTabWidget, QScrollArea, QMessageBox,
    QSplitter, QFileDialog, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QDoubleValidator, QIntValidator

# Import the calculator modules
try:
    from bolted_flange_calc import (
        compute, Inputs, Layer, MomentLoad, CalcTrace, 
        Results, make_pdf_report, pretty_print
    )
except ImportError:
    print("Error: Could not import flange_calculator module.")
    print("Please ensure 'flange_calculator.py' is in the same directory.")
    sys.exit(1)


class CalculationThread(QThread):
    """Thread for running calculations without blocking the UI"""
    finished = pyqtSignal(object, object)  # results, trace
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
    """Widget for managing member layers"""
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        
        # Table for layers
        self.table = QTableWidget(1, 4)
        self.table.setHorizontalHeaderLabels(['Name', 'E (MPa)', 'Thickness (mm)', 'D_interface (mm)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        
        # Set default values
        self.table.setItem(0, 0, QTableWidgetItem("Flange stack (symmetric)"))
        self.table.setItem(0, 1, QTableWidgetItem("71000.0"))
        self.table.setItem(0, 2, QTableWidgetItem("12.0"))
        self.table.setItem(0, 3, QTableWidgetItem("12.0"))
        
        layout.addWidget(self.table)
        
        # Buttons
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
        """Extract Layer objects from table"""
        layers = []
        for row in range(self.table.rowCount()):
            try:
                name = self.table.item(row, 0).text()
                E_MPa = float(self.table.item(row, 1).text())
                thickness_mm = float(self.table.item(row, 2).text())
                D_interface_mm = float(self.table.item(row, 3).text())
                
                layers.append(Layer(
                    name=name,
                    E_MPa=E_MPa,
                    thickness_mm=thickness_mm,
                    D_interface_mm=D_interface_mm
                ))
            except (ValueError, AttributeError) as e:
                raise ValueError(f"Invalid layer data in row {row + 1}: {e}")
        return layers


class FlangeCalculatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bolted Flange Calculator (NASA-STD-5020)")
        self.setGeometry(100, 100, 1600, 900)
        
        self.results = None
        self.trace = None
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left side: Inputs
        left_panel = self.create_input_panel()
        splitter.addWidget(left_panel)
        
        # Right side: Results
        right_panel = self.create_results_panel()
        splitter.addWidget(right_panel)
        
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        
        main_layout.addWidget(splitter)
        
        # Status bar
        self.statusBar().showMessage("Ready")
    
    def create_input_panel(self):
        """Create the left panel with all input fields"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(500)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        
        # Title
        title = QLabel("Input Parameters")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Create input fields organized by groups
        self.input_fields = {}
        
        # === Pressure/Load Group ===
        pressure_group = self.create_group("Separating Load (Pressure)", [
            ("Pc_psi", "Chamber Pressure (psi)", "300.0"),
            ("effective_diameter_in", "Effective Diameter (in)", "3.53"),
            ("n_bolts", "Number of Bolts", "8", "int"),
        ])
        layout.addWidget(pressure_group)
        
        # === Bolt Geometry Group ===
        bolt_geom_group = self.create_group("Bolt Geometry", [
            ("d_bolt_mm", "Nominal Bolt Diameter (mm)", "8.0"),
            ("d_hole_mm", "Clearance Hole Diameter (mm)", "8.5"),
            ("At_mm2", "Tensile Stress Area A_t (mm²)", "36.6"),
            ("Ad_mm2", "Shank Area A_d (mm²)", "50.3"),
        ])
        layout.addWidget(bolt_geom_group)
        
        # === Bolt Material/Stiffness Group ===
        bolt_material_group = self.create_group("Bolt Material & Grip", [
            ("Eb_MPa", "Bolt Modulus E_b (MPa)", "200000.0"),
            ("lt_mm", "Threaded Length in Grip l_t (mm)", "10.0"),
            ("ld_mm", "Unthreaded Length in Grip l_d (mm)", "20.0"),
            ("Sp_MPa", "Proof Strength S_p (MPa)", "600.0"),
        ])
        layout.addWidget(bolt_material_group)
        
        # === NASA Preload Group ===
        preload_group = self.create_group("NASA-STD-5020 Preload Model", [
            ("Ppi_nom_N", "Nominal Preload per Bolt (N)", "12000.0"),
            ("Gamma_p", "Preload Variation Factor Γ_p", "0.20"),
            ("L_min_total_N", "Min Total Clamp Load (N)", "0.0"),
            ("target_sep_factor", "Target Separation Factor", "2.5"),
        ])
        layout.addWidget(preload_group)
        
        # === Moment Load Group ===
        moment_group = QGroupBox("Optional Moment Load")
        moment_layout = QGridLayout()
        
        self.moment_enabled = QCheckBox("Enable Moment Load")
        moment_layout.addWidget(self.moment_enabled, 0, 0, 1, 2)
        
        self.input_fields["M_Nmm"] = self.create_field_row(
            moment_layout, 1, "Moment M (N·mm)", "0.0"
        )
        self.input_fields["bolt_circle_diameter_mm"] = self.create_field_row(
            moment_layout, 2, "Bolt Circle Diameter (mm)", "0.0"
        )
        self.input_fields["prying_factor"] = self.create_field_row(
            moment_layout, 3, "Prying Factor", "1.0"
        )
        
        moment_group.setLayout(moment_layout)
        layout.addWidget(moment_group)
        
        # === Member Layers ===
        layers_group = QGroupBox("Member Layers")
        layers_layout = QVBoxLayout()
        self.layer_table = LayerTable()
        layers_layout.addWidget(self.layer_table)
        layers_group.setLayout(layers_layout)
        layout.addWidget(layers_group)
        
        layout.addStretch()
        
        # Buttons
        btn_layout = QHBoxLayout()
        calc_btn = QPushButton("Calculate")
        calc_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; }")
        calc_btn.clicked.connect(self.run_calculation)
        
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.load_default_values)
        
        btn_layout.addWidget(calc_btn)
        btn_layout.addWidget(reset_btn)
        layout.addLayout(btn_layout)
        
        scroll.setWidget(container)
        return scroll
    
    def create_group(self, title, fields):
        """Create a group box with input fields"""
        group = QGroupBox(title)
        layout = QGridLayout()
        
        for i, field_info in enumerate(fields):
            field_name = field_info[0]
            label_text = field_info[1]
            default_val = field_info[2]
            field_type = field_info[3] if len(field_info) > 3 else "float"
            
            self.input_fields[field_name] = self.create_field_row(
                layout, i, label_text, default_val, field_type
            )
        
        group.setLayout(layout)
        return group
    
    def create_field_row(self, layout, row, label_text, default_val, field_type="float"):
        """Create a label and input field"""
        label = QLabel(label_text + ":")
        
        if field_type == "int":
            field = QSpinBox()
            field.setRange(1, 1000000)
            field.setValue(int(float(default_val)))
        else:
            field = QLineEdit(default_val)
            validator = QDoubleValidator()
            validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
            field.setValidator(validator)
        
        layout.addWidget(label, row, 0)
        layout.addWidget(field, row, 1)
        
        return field
    
    def create_results_panel(self):
        """Create the right panel for results display"""
        container = QWidget()
        layout = QVBoxLayout(container)
        
        # Title
        title = QLabel("Results")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        # Tabs for different result views
        tabs = QTabWidget()
        
        # Summary tab
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Courier", 9))
        tabs.addTab(self.summary_text, "Summary")
        
        # Detailed calculations tab
        self.trace_text = QTextEdit()
        self.trace_text.setReadOnly(True)
        self.trace_text.setFont(QFont("Courier", 8))
        tabs.addTab(self.trace_text, "Calculation Trace")
        
        layout.addWidget(tabs)
        
        # Export buttons
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
    
    def load_default_values(self):
        """Load default values into all fields"""
        defaults = {
            "Pc_psi": "300.0",
            "effective_diameter_in": "3.53",
            "n_bolts": "8",
            "d_bolt_mm": "8.0",
            "d_hole_mm": "8.5",
            "At_mm2": "36.6",
            "Ad_mm2": "50.3",
            "Eb_MPa": "200000.0",
            "lt_mm": "10.0",
            "ld_mm": "20.0",
            "Sp_MPa": "600.0",
            "Ppi_nom_N": "12000.0",
            "Gamma_p": "0.20",
            "L_min_total_N": "0.0",
            "target_sep_factor": "2.5",
            "M_Nmm": "0.0",
            "bolt_circle_diameter_mm": "0.0",
            "prying_factor": "1.0",
        }
        
        for field_name, value in defaults.items():
            if field_name in self.input_fields:
                field = self.input_fields[field_name]
                if isinstance(field, QSpinBox):
                    field.setValue(int(float(value)))
                else:
                    field.setText(value)
        
        self.moment_enabled.setChecked(False)
        
        # Reset layer table
        self.layer_table.table.setRowCount(1)
        self.layer_table.table.setItem(0, 0, QTableWidgetItem("Flange stack (symmetric)"))
        self.layer_table.table.setItem(0, 1, QTableWidgetItem("71000.0"))
        self.layer_table.table.setItem(0, 2, QTableWidgetItem("12.0"))
        self.layer_table.table.setItem(0, 3, QTableWidgetItem("12.0"))
    
    def get_input_values(self):
        """Extract all input values and create Inputs and Layer objects"""
        try:
            # Get moment load settings
            moment = MomentLoad(
                enabled=self.moment_enabled.isChecked(),
                M_Nmm=float(self.input_fields["M_Nmm"].text()),
                bolt_circle_diameter_mm=float(self.input_fields["bolt_circle_diameter_mm"].text()),
                prying_factor=float(self.input_fields["prying_factor"].text()),
            )
            
            # Get main inputs
            n_bolts_val = self.input_fields["n_bolts"].value() if isinstance(self.input_fields["n_bolts"], QSpinBox) else int(self.input_fields["n_bolts"].text())
            
            inputs = Inputs(
                Pc_psi=float(self.input_fields["Pc_psi"].text()),
                effective_diameter_in=float(self.input_fields["effective_diameter_in"].text()),
                n_bolts=n_bolts_val,
                d_bolt_mm=float(self.input_fields["d_bolt_mm"].text()),
                d_hole_mm=float(self.input_fields["d_hole_mm"].text()),
                At_mm2=float(self.input_fields["At_mm2"].text()),
                Ad_mm2=float(self.input_fields["Ad_mm2"].text()),
                Eb_MPa=float(self.input_fields["Eb_MPa"].text()),
                lt_mm=float(self.input_fields["lt_mm"].text()),
                ld_mm=float(self.input_fields["ld_mm"].text()),
                Sp_MPa=float(self.input_fields["Sp_MPa"].text()),
                Ppi_nom_N=float(self.input_fields["Ppi_nom_N"].text()),
                Gamma_p=float(self.input_fields["Gamma_p"].text()),
                L_min_total_N=float(self.input_fields["L_min_total_N"].text()),
                target_sep_factor=float(self.input_fields["target_sep_factor"].text()),
                moment=moment,
            )
            
            # Get layers
            layers = self.layer_table.get_layers()
            
            return inputs, layers
            
        except ValueError as e:
            raise ValueError(f"Invalid input value: {e}")
    
    def run_calculation(self):
        """Run the calculation with current inputs"""
        try:
            self.statusBar().showMessage("Running calculation...")
            
            # Get inputs
            inputs, layers = self.get_input_values()
            
            # Run calculation in thread
            self.calc_thread = CalculationThread(inputs, layers)
            self.calc_thread.finished.connect(self.on_calculation_finished)
            self.calc_thread.error.connect(self.on_calculation_error)
            self.calc_thread.start()
            
        except Exception as e:
            QMessageBox.critical(self, "Input Error", f"Error reading inputs:\n{str(e)}")
            self.statusBar().showMessage("Error")
    
    def on_calculation_finished(self, results, trace):
        """Handle completed calculation"""
        self.results = results
        self.trace = trace
        
        # Display summary
        summary = self.format_summary(results)
        self.summary_text.setHtml(summary)
        
        # Display trace
        trace_text = "\n".join(trace.lines)
        self.trace_text.setPlainText(trace_text)
        
        self.statusBar().showMessage("Calculation complete")
        
        # Show warning if checks fail
        if results.MS_proof < 0 or results.MS_sep < 0:
            QMessageBox.warning(
                self, 
                "Design Check Failed", 
                "One or more design checks have failed. Please review the results."
            )
    
    def on_calculation_error(self, error_msg):
        """Handle calculation error"""
        QMessageBox.critical(self, "Calculation Error", f"Error during calculation:\n{error_msg}")
        self.statusBar().showMessage("Calculation failed")
    
    def format_summary(self, res):
        """Format results as HTML"""
        html = "<html><body style='font-family: monospace;'>"
        
        # Helper function for color coding
        def pass_fail(value, threshold, greater=True):
            passes = (value >= threshold) if greater else (value <= threshold)
            color = "green" if passes else "red"
            status = "PASS" if passes else "FAIL"
            return f"<span style='color: {color}; font-weight: bold;'>{status}</span>"
        
        html += "<h3>Separating Load</h3>"
        html += f"P<sub>total</sub> = {res.P_total_N:,.1f} N ({res.P_total_N/1000:.3f} kN)<br>"
        html += f"P<sub>per_bolt</sub> = {res.P_per_bolt_N:,.1f} N ({res.P_per_bolt_N/1000:.3f} kN)<br>"
        
        html += "<h3>Stiffness Split</h3>"
        html += f"k<sub>b</sub> = {res.kb_N_per_mm:,.1f} N/mm<br>"
        html += f"k<sub>m</sub> = {res.km_N_per_mm:,.1f} N/mm<br>"
        html += f"C = k<sub>b</sub>/(k<sub>b</sub>+k<sub>m</sub>) = {res.C:.4f}<br>"
        if res.C > 0.6:
            html += "<span style='color: orange;'>⚠ WARNING: C > 0.6</span><br>"
        
        html += "<h3>NASA-STD-5020 Preload Bounds (per bolt)</h3>"
        html += f"Ppi<sub>nom</sub> = {res.Ppi_nom_N:,.1f} N<br>"
        html += f"Ppi<sub>min</sub> = {res.Ppi_min_N:,.1f} N<br>"
        html += f"Ppi<sub>max</sub> = {res.Ppi_max_N:,.1f} N<br>"
        
        html += "<h3>Peak Bolt Tension (most-loaded bolt)</h3>"
        html += f"ΔF<sub>moment</sub> = {res.dF_moment_N:,.1f} N<br>"
        html += f"F<sub>b_max</sub> = {res.Fb_max_N:,.1f} N ({res.Fb_max_N/1000:.3f} kN)<br>"
        
        html += "<h3>Strength Check (Proof)</h3>"
        html += f"F<sub>proof_allow</sub> = {res.F_proof_allow_N:,.1f} N<br>"
        html += f"MS<sub>proof</sub> = {res.MS_proof:.3f} {pass_fail(res.MS_proof, 0)}<br>"
        
        html += "<h3>Separation Check</h3>"
        html += f"Ppi<sub>req_min</sub> = {res.Ppi_req_min_N:,.1f} N<br>"
        
        # Get target from inputs
        try:
            inputs, _ = self.get_input_values()
            target = inputs.target_sep_factor
        except:
            target = 2.5
        
        html += f"FoS<sub>sep</sub> = {res.FoS_sep:.3f} {pass_fail(res.FoS_sep, target)}<br>"
        html += f"MS<sub>sep</sub> = {res.MS_sep:.3f}<br>"
        html += f"Required bolts for target separation: {res.required_bolts_for_target_sep}<br>"
        
        html += "</body></html>"
        return html
    
    def generate_pdf(self):
        """Generate PDF report"""
        if self.results is None:
            QMessageBox.warning(self, "No Results", "Please run a calculation first.")
            return
        
        try:
            filename, _ = QFileDialog.getSaveFileName(
                self, 
                "Save PDF Report", 
                "flange_report.pdf", 
                "PDF Files (*.pdf)"
            )
            
            if filename:
                inputs, layers = self.get_input_values()
                make_pdf_report(
                    inputs, 
                    layers, 
                    self.results, 
                    filename=filename,
                    calculation_trace=self.trace.lines if self.trace else None
                )
                
                QMessageBox.information(self, "Success", f"PDF report generated:\n{filename}")
                self.statusBar().showMessage(f"PDF saved: {filename}")
        
        except Exception as e:
            QMessageBox.critical(self, "PDF Error", f"Error generating PDF:\n{str(e)}")
    
    def copy_summary(self):
        """Copy summary text to clipboard"""
        if self.results is None:
            QMessageBox.warning(self, "No Results", "Please run a calculation first.")
            return
        
        clipboard = QApplication.clipboard()
        clipboard.setText(self.summary_text.toPlainText())
        self.statusBar().showMessage("Summary copied to clipboard", 3000)


def main():
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle('Fusion')
    
    window = FlangeCalculatorGUI()
    window.load_default_values()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()