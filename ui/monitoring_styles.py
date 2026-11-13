"""Palette and native Qt styling for the monitoring workspace."""

from vlui_native_typography import scaled_native_stylesheet


MONITORING_CANVAS = "#171a1d"
MONITORING_PALETTE = {
    "steel": "#c4bfb2",
    "cobalt": "#d99a50",
    "ice": "#fff8e9",
    "baby": "#f6bb68",
    "ultraviolet": "#92d7a7",
    "carbon": "#23272b",
    "pink": "#ff9f91",
    "highlight": "#ffd08a",
    "border": "#565b60",
    "button_text": "#2c1e11",
}


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _mix(surface: str, tint: str, weight: float) -> str:
    channels = (round(a + (b - a) * weight) for a, b in zip(_rgb(surface), _rgb(tint)))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _opacity(color: str, percent: int) -> str:
    if percent >= 100:
        return color
    red, green, blue = _rgb(color)
    return f"rgba({red}, {green}, {blue}, {round(255 * percent / 100)})"


def monitoring_stylesheet(appearance, palette: dict[str, str], scale: str) -> str:
    """Restyle shared controls without replacing themes, palettes, or widgets."""
    from interface_themes import theme_for

    theme = theme_for(appearance.layout)
    panel = palette["carbon"]
    opacities = appearance.opacities

    def color(role: str) -> str:
        return _opacity(palette[role], 100 if theme.high_contrast else opacities.get(role, 100))

    text = color("ice")
    muted = color("steel")
    accent = color("baby")
    running = color("ultraviolet")
    danger = color("pink")
    attention = color("highlight")
    outline = color("border")
    button_text = color("button_text")
    panel_opacity = 100 if theme.high_contrast else opacities.get("carbon", 100)
    chrome = _opacity(_mix(panel, palette["ice"], .05), panel_opacity)
    card = _opacity(panel, panel_opacity)
    raised = _opacity(_mix(panel, palette["ice"], .07), panel_opacity)
    active = _opacity(_mix(panel, palette["baby"], .15), panel_opacity)
    hover = _opacity(_mix(panel, palette["baby"], .09), panel_opacity)
    background = MONITORING_CANVAS if appearance.layout == "monitoring" else theme.canvas
    field = _opacity(_mix(panel, background, .42), panel_opacity)
    border_width = 2 if theme.high_contrast else 1
    border = f"{border_width}px solid {outline}"

    return scaled_native_stylesheet(f"""
QWidget#root, QMainWindow {{
    color:{text};
    background:{background};
}}
QFrame#commandBar, QFrame#monitoringToolbar, QWidget#monitoringToolbar {{
    background:{chrome};
    border:0;
    border-bottom:{border};
}}
QFrame#monitoringNavigation, QWidget#monitoringNavigation {{
    background:{card};
    border:0;
    border-bottom:{border};
}}
QFrame#monitoringFooter, QWidget#monitoringFooter, QFrame#telemetryStrip {{
    background:{chrome};
    border:0;
    border-top:{border};
}}
QLabel#monitoringBrand, QLabel#brandWordmark {{
    color:{text};
    font-size:13pt;
    font-weight:700;
}}
QLabel#monitoringHeading, QLabel#headerPage {{
    color:{text};
    font-size:11pt;
    font-weight:650;
}}
QLabel#headerKicker, QLabel#headerProfile {{
    color:{muted};
    font-size:9pt;
    font-weight:500;
}}
QPushButton#monitoringTab, QToolButton#monitoringTab {{
    background:transparent;
    color:{muted};
    border:1px solid transparent;
    border-radius:4px;
    min-height:25px;
    padding:3px 10px;
    font-size:10pt;
    font-weight:600;
}}
QPushButton#monitoringTab:hover, QToolButton#monitoringTab:hover {{
    background:{hover};
    color:{text};
}}
QPushButton#monitoringTab:checked, QToolButton#monitoringTab:checked {{
    background:{active};
    color:{accent};
    border-color:{accent};
    font-weight:700;
}}
QPushButton#monitoringTab:focus, QToolButton#monitoringTab:focus {{
    border-color:{accent};
}}
QPushButton#monitoringMore, QToolButton#monitoringMore {{
    background:{raised};
    color:{text};
    border:{border};
    border-radius:4px;
    min-width:0;
    max-width:16777215px;
    min-height:24px;
    padding:3px 11px;
    font-size:10pt;
    font-weight:650;
}}
QPushButton#monitoringMore:hover, QToolButton#monitoringMore:hover,
QPushButton#monitoringMore:focus, QToolButton#monitoringMore:focus {{
    background:{hover};
    border-color:{accent};
}}
QPushButton#monitoringMore[hiddenPageActive="true"],
QToolButton#monitoringMore[hiddenPageActive="true"] {{
    background:{active};
    color:{accent};
    border-color:{accent};
}}
QPushButton#monitoringMore:disabled, QToolButton#monitoringMore:disabled {{
    background:{card};
    color:{muted};
    border-color:{outline};
}}
QMenu#monitoringPageMenu {{
    background:{panel};
    color:{text};
    border:{border};
    padding:4px;
}}
QMenu#monitoringPageMenu::item {{
    padding:6px 14px 6px 25px;
}}
QMenu#monitoringPageMenu::item:selected {{
    background:{accent};
    color:{button_text};
}}
QFrame#monitoringPreview, QFrame#monitoringMetrics, QFrame#monitoringActivity,
QFrame#monitoringReadiness, QFrame#monitoringPipeline, QFrame#missionTaskCard {{
    background:{card};
    border:{border};
    border-radius:6px;
}}
QFrame#monitoringMetric, QFrame#metricCard {{
    background:{raised};
    border:{border};
    border-radius:5px;
}}
QFrame#monitoringMetrics QFrame#monitoringMetric:hover,
QFrame#monitoringMetrics QFrame#metricCard:hover {{
    background:{raised};
    border-color:{outline};
}}
QFrame#missionTaskCard QFrame#settingsCard {{
    background:transparent;
    border:0;
    border-bottom:1px solid {outline};
    border-radius:0;
}}
QLabel#missionCardTitle {{
    color:{text};
    font-size:11pt;
    font-weight:650;
    background:transparent;
    border:0;
}}
QLabel#monitoringValue, QLabel#metricValue {{
    color:{text};
    font-size:17pt;
    font-weight:700;
}}
QLabel#monitoringTimingValue {{
    color:{text};
    font-size:10pt;
    font-weight:650;
}}
QLabel#metricCaption, QLabel#signalCaption, QLabel#hudLabel {{
    color:{muted};
    font-size:9pt;
    font-weight:500;
    letter-spacing:0;
}}
QLabel#hudValue, QLabel#missionFooterState {{
    color:{text};
    font-size:9pt;
    font-weight:650;
}}
QLabel#pageTitle {{
    color:{text};
    font-size:21pt;
    font-weight:700;
}}
QFrame#monitoringPreview QLabel#previewSurface,
QFrame#monitoringPreview QLabel#policyPreviewSurface {{
    background:{background};
    color:{muted};
    border:0;
    border-radius:4px;
}}
QFrame#commandBar QComboBox, QFrame#monitoringToolbar QComboBox,
QWidget#monitoringToolbar QComboBox {{
    background:{field};
    border:{border};
    min-height:25px;
}}
QFrame#commandBar QPushButton#primaryButton,
QFrame#monitoringToolbar QPushButton#primaryButton,
QWidget#monitoringToolbar QPushButton#primaryButton {{
    background:{accent};
    color:{button_text};
    border:{border_width}px solid {accent};
    border-radius:4px;
}}
QFrame#commandBar QPushButton#primaryButton:hover,
QFrame#monitoringToolbar QPushButton#primaryButton:hover,
QWidget#monitoringToolbar QPushButton#primaryButton:hover {{
    background:{_mix(palette["baby"], palette["ice"], .16)};
}}
QFrame#commandBar QPushButton#primaryButton:disabled,
QFrame#monitoringToolbar QPushButton#primaryButton:disabled,
QWidget#monitoringToolbar QPushButton#primaryButton:disabled {{
    background:{card};
    color:{muted};
    border-color:{outline};
}}
QFrame#commandBar QPushButton#dangerButton,
QFrame#monitoringToolbar QPushButton#dangerButton,
QWidget#monitoringToolbar QPushButton#dangerButton {{
    background:{card};
    color:{danger};
    border:{border_width}px solid {danger};
    border-radius:4px;
}}
QFrame#commandBar QPushButton#dangerButton:hover,
QFrame#monitoringToolbar QPushButton#dangerButton:hover,
QWidget#monitoringToolbar QPushButton#dangerButton:hover {{
    background:{_mix(panel, palette["pink"], .14)};
}}
QLabel#statusPill {{
    background:{raised};
    color:{muted};
    border:{border};
    border-radius:4px;
    font-size:9pt;
    font-weight:650;
    padding:4px 8px;
}}
QLabel#statusPill[state="running"] {{
    background:{_mix(panel, palette["ultraviolet"], .12)};
    border-color:{running};
    color:{running};
}}
QLabel#statusPill[state="stopping"] {{
    background:{_mix(panel, palette["highlight"], .12)};
    border-color:{attention};
    color:{attention};
}}
QLabel#statusPill[state="error"] {{
    background:{_mix(panel, palette["pink"], .12)};
    border-color:{danger};
    color:{danger};
}}
""", scale)
