from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def label(text, kind=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if kind:
        widget.setObjectName(kind)
    return widget


def button(text, action, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(lambda checked=False: action())
    return widget


def line(placeholder="", password=False):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    if password:
        widget.setEchoMode(QLineEdit.EchoMode.Password)
    return widget


def spin(value=0, minimum=0, maximum=1_000_000, decimals=2):
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    return widget


def integer(value, minimum=0, maximum=23):
    widget = QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def combo(items):
    widget = QComboBox()
    for text, data in items:
        widget.addItem(text, data)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(12)
    return widget, layout


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def form_dialog(parent, title, fields, *, accept="Сохранить"):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.addWidget(label(title, "section"))
    form = QFormLayout()
    form.setVerticalSpacing(12)
    for title, widget in fields:
        form.addRow(title, widget)
    layout.addLayout(form)
    buttons = QDialogButtonBox()
    buttons.addButton(accept, QDialogButtonBox.ButtonRole.AcceptRole)
    buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog.exec() == QDialog.DialogCode.Accepted


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().setVisible(False)
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return widget


def fill_table(widget, rows):
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            widget.setItem(row_index, column, QTableWidgetItem(str(value)))


class EnergyChart(QChartView):
    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(240)

    def update_data(self, points, title="Расход электроэнергии, кВт·ч"):
        chart = QChart()
        chart.setTitle(title)
        chart.setBackgroundBrush(QColor("#ffffff"))
        chart.setBackgroundRoundness(10)
        series = QBarSeries()
        bars = QBarSet("кВт·ч")
        bars.setColor(QColor("#4b9b78"))
        bars.setBorderColor(QColor("#4b9b78"))
        bars.append([value for _, value in points] or [0.0])
        series.append(bars)
        series.setBarWidth(0.62)
        chart.addSeries(series)
        axis_x = QBarCategoryAxis()
        axis_x.append([title for title, _ in points] or ["Нет данных"])
        if len(points) > 16:
            axis_x.setLabelsAngle(-60)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(axis_x)
        axis_y = QValueAxis()
        axis_y.setRange(0, max(1.0, max((v for _, v in points), default=0) * 1.2))
        axis_y.setLabelFormat("%.1f")
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_y)
        chart.legend().hide()
        old = self.chart()
        self.setChart(chart)
        if old:
            old.deleteLater()
