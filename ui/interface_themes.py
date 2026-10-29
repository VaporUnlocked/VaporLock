"""Interface presets with independent surface/text/control roles."""
from dataclasses import dataclass
from pathlib import Path
import sys

# UI Dev's explicit default roles. Other presets and saved user colors retain
# their existing mapping; these are not migrated over user preferences.
MISSION_CONTROL_ROLES = {
    'canvas': '#081516', 'chrome': '#0A1B1C', 'surface': '#102426',
    'raised': '#142D2E', 'input': '#0B1C1E', 'structure': '#29484A',
    'control': '#567C79', 'text': '#F2F8F6', 'secondary': '#B2C6C5',
    'accent': '#B5EF82', 'hover': '#D0FFAB', 'pressed': '#9ED66D',
    'primary_ink': '#07120E', 'selected': '#1C392D',
    'disabled': '#8EA5A2', 'disabled_border': '#385454',
    'danger': '#EF727C', 'warning': '#FFD173',
}

def painted_role(widget, role, fallback):
    """Resolve only the painted backdrop/text, never pixels or annotation colors."""
    owner=getattr(widget,'center',None) or widget
    while owner is not None:
        appearance=getattr(owner,'ui_appearance',None)
        if appearance is not None:
            if appearance.layout!='mission-control':return fallback
            from vapor_lock_gui import DEFAULT_UI_COLORS
            custom=getattr(appearance,'colors',{}) or {}
            if custom == DEFAULT_UI_COLORS:custom={}
            key='ice' if role=='text' else 'steel' if role=='secondary' else 'carbon'
            return custom.get(key,MISSION_CONTROL_ROLES[role])
        owner=owner.parentWidget()
    return fallback

@dataclass(frozen=True)
class InterfaceTheme:
    name: str
    canvas: str
    panel: str
    accent: str
    high_contrast: bool = False
    light: bool = False
    atmospheric: bool = False

    @property
    def card_opacity(self):
        if self.high_contrast: return 96 if self.light else 92
        return 88 if self.light else 68 if self.atmospheric else 74

    @property
    def chrome_opacity(self):
        return 98 if self.high_contrast else 94 if self.light else 90

    def palette(self):
        if self.name == "Mission Control":
            return {"steel": "#B2C6C5", "cobalt": self.accent, "ice": "#F2F8F6",
                    "baby": self.accent, "ultraviolet": self.accent, "carbon": self.panel,
                    "pink": "#EF727C", "highlight": "#FFD173", "border": "#567C79",
                    "button_text": "#07120E"}
        return {
            "steel": "#253C56" if self.light and self.high_contrast else "#42556D" if self.light else "#D5DFE9" if self.high_contrast else "#B4BFCD",
            "cobalt": self.accent, "ice": "#17263A" if self.light else "#FFFFFF" if self.high_contrast else "#F2F6FA",
            "baby": self.accent, "ultraviolet": "#6042A6" if self.light else self.accent,
            "carbon": self.panel, "pink": "#8C1E44" if self.light and self.high_contrast else "#9B244E" if self.light else "#FFB3C6",
            "highlight": "#654500" if self.light and self.high_contrast else "#725007" if self.light else "#FFD173",
            "border": "#42556D" if self.light else "#A7B8CA" if self.high_contrast else "#708498",
            "button_text": "#FFFFFF" if self.light else "#05070A",
        }


THEMES = {
    "mission-control": InterfaceTheme("Mission Control", "#081516", "#102426", "#B5EF82"),
    "smoky": InterfaceTheme("Smoky glass", "#080B12", "#080B12", "#7DBCF5", atmospheric=True),
    "contrast": InterfaceTheme("Fog contrast", "#05070A", "#0B1118", "#9ED8FF", high_contrast=True),
    "slate-light": InterfaceTheme("Slate light", "#EAF0F6", "#FFFFFF", "#245AA8", light=True),
    "slate-light-contrast": InterfaceTheme("Slate light high contrast", "#FFFFFF", "#FFFFFF", "#164B90", high_contrast=True, light=True),
    "teal-dark": InterfaceTheme("Teal dark", "#081319", "#101A22", "#67D8C8"),
    "teal-dark-contrast": InterfaceTheme("Teal dark high contrast", "#05070A", "#0B1118", "#75E3D6", high_contrast=True),
    "violet-dark": InterfaceTheme("Violet dark", "#110C1B", "#1A1628", "#B59BFF"),
    "violet-dark-contrast": InterfaceTheme("Violet dark high contrast", "#05070A", "#0B1118", "#CEB0FF", high_contrast=True),
    "amber-dark": InterfaceTheme("Amber dark", "#151009", "#211B14", "#F2BE67"),
    "amber-dark-contrast": InterfaceTheme("Amber dark high contrast", "#05070A", "#0B1118", "#FFD173", high_contrast=True),
    "rose-dark": InterfaceTheme("Rose dark", "#150B12", "#21151D", "#F1A0BB"),
    "rose-dark-contrast": InterfaceTheme("Rose dark high contrast", "#05070A", "#0B1118", "#FFAECB", high_contrast=True),
}


def theme_for(identifier):
    return THEMES.get(identifier, THEMES["smoky"])


def contrast_ratio(foreground, background):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(channels, (.2126, .7152, .0722)))
    first, second = sorted((luminance(foreground), luminance(background)))
    return (second + .05) / (first + .05)


def theme_stylesheet(theme, colors, opacities):
    # Main surfaces inherit theme translucency. Controls and text retain opaque
    # roles. Premium opacity preferences scale the main surfaces independently.
    def color(role):
        value = colors[role]
        opacity = 100 if theme.high_contrast else opacities.get(role, 100)
        if opacity == 100: return value
        channels = [int(value[index:index + 2], 16) for index in (1, 3, 5)]
        return f"rgba({channels[0]}, {channels[1]}, {channels[2]}, {round(255 * opacity / 100)})"
    text, muted, border, accent = (colors[role] for role in ("ice", "steel", "border", "baby"))
    panel = colors["carbon"]
    def surface(percent):
        channels = [int(panel[index:index + 2], 16) for index in (1, 3, 5)]
        alpha = round(255 * percent * opacities.get("carbon", 100) / 10000)
        return f"rgba({channels[0]}, {channels[1]}, {channels[2]}, {alpha})"
    card, chrome = surface(theme.card_opacity), surface(theme.chrome_opacity)
    mission = theme.name == 'Mission Control'
    selected = MISSION_CONTROL_ROLES['selected'] if mission else accent
    selected_ink = text if mission else ("#FFFFFF" if contrast_ratio("#FFFFFF", accent) >= contrast_ratio("#05070A", accent) else "#05070A")
    input_base = MISSION_CONTROL_ROLES['input'] if mission else panel
    button_text = color("button_text")
    selection_text = "#FFFFFF" if contrast_ratio("#FFFFFF", colors["baby"]) >= contrast_ratio("#05070A", colors["baby"]) else "#05070A"
    assets = Path(sys._MEIPASS) / "assets" if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "assets"
    if (Path(__file__).resolve().parents[2]/'information/alpha-manifest.json').is_file():
        assets=Path(__file__).resolve().parents[2]/'assets'
    checkmark = (assets / ("check-light.svg" if selection_text == "#FFFFFF" else "check-dark.svg")).as_posix()
    switch_off=(assets/'switch-off.svg').as_posix()
    switch_on=(assets/'switch-on.svg').as_posix()
    arrow_style = "dark" if theme.light else "light"
    up_arrow = (assets / f"arrow-up-{arrow_style}.svg").as_posix()
    down_arrow = (assets / f"arrow-down-{arrow_style}.svg").as_posix()
    width = 2 if theme.high_contrast else 1
    # Nested containers share the outer glass surface instead of stacking films.
    return f'''
    QMainWindow, QDialog {{ background:{theme.canvas}; color:{text}; }}
    QWidget {{ color:{text}; selection-color:{selection_text}; selection-background-color:{accent}; }}
    QDialog QWidget {{ background:{panel}; }}
    QFrame#commandBar, QFrame#navigationDeck, QFrame#telemetryStrip {{ background:{chrome}; border-color:{border}; }}
    QFrame#updateBanner {{ background:{panel}; border:2px solid {colors['highlight']}; }}
    QScrollArea#policyReasonScroll, QScrollArea#policyReasonScroll QWidget {{ background:{panel}; border:0; }}
    QFrame#settingsCard, QFrame#metricCard, QFrame#signalPanel, QFrame#policyCommandDeck,
    QFrame#policyInspectorCard, QFrame#settingsTabs, QFrame#policyInspector, QFrame#secondaryTabRail,
    QFrame#policyTopMetric, QFrame#policyPreviewFooter {{ background:{card}; border-color:{border}; }}
    QFrame#settingsTabs QFrame#settingsCard, QFrame#policyInspector QFrame#policyInspectorCard {{ background:transparent; }}
    QFrame#settingsCard:hover, QFrame#metricCard:hover,
    QFrame#settingsTabs QFrame#settingsCard:hover {{ border-color:{accent}; }}
    QPushButton#guideLink {{ background:transparent; color:{accent}; border:0; text-decoration:underline; text-align:left; }}
    QPushButton#guideLink:hover {{ color:{text}; }}
    QPushButton#guideLink:focus {{ border:2px solid {accent}; }}
    QLabel#brandWordmark, QLabel#headerPage, QLabel#pageTitle, QLabel#fieldTitle, QLabel#metricValue,
    QLabel#policyWorkspaceTitle, QLabel#policyInspectorTitle, QLabel#policyCardTitle,
    QLabel#policyMetricValue, QLabel#policyTrackMetric, QLabel#policyConfidenceMetric,
    QLabel#signalValue, QLabel#hudValue, QLabel#policyControlValue {{ color:{text}; }}
    QLabel#muted, QLabel#detail, QLabel#fieldDescription, QLabel#headerProfile, QLabel#headerKicker, QLabel#brandPath, QLabel#metricCaption,
    QLabel#policyPreviewCaption, QLabel#policyWorkspaceSubtitle, QLabel#policyControlLabel,
    QLabel#policyMetricDetail, QLabel#signalCaption, QLabel#hudLabel {{ color:{muted}; }}
    QLabel#navGroup, QLabel#pageKicker, QLabel#sectionLabel, QLabel#brandKicker,
    QLabel#policyMetricCaption, QLabel#policyPreviewStatus {{ color:{accent}; }}
    QFrame#sectionRule, QFrame#secondaryTabDivider, QFrame#brandDivider, QFrame#signalDivider, QFrame#headerDivider, QFrame#navDivider {{ background:{border}; }}
    QFrame#pageHeading, QFrame#policyMainSurface {{ border-color:{border}; }}
    QFrame#sectionDot {{ background:{colors['pink']}; }}
    QLabel#navigationLogo {{ background:transparent; border:0; }}
    QLabel#editionBadge {{ background:{panel}; color:{muted}; border:1px solid {border}; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QTextBrowser,
    QKeySequenceEdit {{ background:{input_base}; color:{text}; border:{width}px solid {border};
        selection-background-color:{accent}; selection-color:{selection_text}; }}
    QLineEdit {{ placeholder-text-color:{muted}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin:border; subcontrol-position:top right; width:20px; background:{panel}; border-left:1px solid {border}; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin:border; subcontrol-position:bottom right; width:20px; background:{panel}; border-left:1px solid {border}; border-top:1px solid {border}; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image:url("{up_arrow}"); width:10px; height:6px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow {{ image:url("{down_arrow}"); width:10px; height:6px; }}
    QComboBox QAbstractItemView, QListView, QListWidget, QTreeWidget, QTableWidget {{ background:{panel};
        color:{text}; alternate-background-color:{theme.canvas}; border:{width}px solid {border};
        selection-background-color:{selected}; selection-color:{selected_ink}; gridline-color:{border}; }}
    QListView::item:hover, QListWidget::item:hover, QTreeWidget::item:hover {{ background:{theme.canvas}; color:{text}; }}
    QListView::item:selected, QListWidget::item:selected, QTreeWidget::item:selected,
    QDialog#officialModelDialog QTreeWidget::item:selected {{ background:{selected}; color:{selected_ink}; }}
    QDialog#officialModelDialog QTreeWidget::item:selected:active,
    QDialog#officialModelDialog QTreeWidget::item:selected:!active {{ background:{selected}; color:{selected_ink}; }}
    QDialog#officialModelDialog, QDialog#officialModelDialog QTreeWidget {{ background:{panel}; color:{text}; }}
    QTabBar#officialModelGameTabs::tab, QTabBar#officialModelTypeTabs::tab {{ background:{panel}; color:{text}; border:1px solid {border}; padding:9px 14px; }}
    QTabBar#officialModelGameTabs::tab:selected, QTabBar#officialModelTypeTabs::tab:selected {{ background:{theme.canvas}; color:{accent}; border-bottom:3px solid {accent}; }}
    QTabBar#officialModelGameTabs::tab:focus, QTabBar#officialModelTypeTabs::tab:focus {{ border:2px dashed {accent}; }}
    QHeaderView::section, QDialog#officialModelDialog QHeaderView::section {{ background:{theme.canvas};
        color:{text}; border-color:{border}; }}
    QPushButton, QToolButton {{ background:{panel}; color:{text}; border:{width}px solid {border}; }}
    QPushButton:hover, QToolButton:hover {{ background:{theme.canvas}; color:{text}; border-color:{accent}; }}
    QPushButton#primaryButton, QPushButton#primaryButton:hover, QPushButton[missionPrimary="true"] {{ background:{accent}; color:{button_text}; border-color:{accent}; }}
    QPushButton#dangerButton, QPushButton#dangerButton:hover {{ background:{panel}; color:{colors['pink']}; border:2px solid {colors['pink']}; }}
    QPushButton:disabled, QToolButton:disabled {{ background:{panel}; color:{muted}; border-color:{border}; }}
    QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled {{ background:{panel}; color:{muted}; border:{width}px solid {border}; }}
    QCheckBox, QRadioButton {{ color:{text}; }}
    QCheckBox:disabled, QRadioButton:disabled, QComboBox:disabled, QLineEdit:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled {{ color:{muted}; background:{panel}; }}
    QCheckBox::indicator {{ width:18px; height:18px; border-radius:3px; border:{width}px solid {border}; background:{panel}; }}
    QCheckBox::indicator:checked {{ background:{accent}; border:2px solid {selection_text}; image:url("{checkmark}"); }}
    QCheckBox::indicator:indeterminate {{ background:{accent}; border:3px dashed {selection_text}; }}
    QPushButton#navButton {{ background:transparent; color:{text}; border:0; border-left:3px solid transparent; }}
    QPushButton#navButton:hover {{ background:{theme.canvas}; color:{text}; }}
    QPushButton#navButton:checked {{ background:{theme.canvas}; color:{accent}; border-left:3px solid {accent}; font-weight:750; }}
    QPushButton#secondaryTabButton {{ background:transparent; color:{muted}; border:0; border-bottom:2px solid transparent; }}
    QPushButton#secondaryTabButton:hover {{ background:{theme.canvas}; color:{text}; }}
    QPushButton#secondaryTabButton:checked {{ background:{theme.canvas}; color:{accent}; border-bottom:3px solid {accent}; }}
    QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextBrowser:focus, QKeySequenceEdit:focus,
    QCheckBox:focus, QRadioButton:focus {{ border:2px solid {accent}; }}
    QMenu, QMenuBar, QToolTip {{ background:{panel}; color:{text}; border:{width}px solid {border}; }}
    QMenu::item:selected {{ background:{accent}; color:{selection_text}; }}
    QProgressBar {{ background:{panel}; color:{text}; border:{width}px solid {border}; }}
    QProgressBar::chunk {{ background:{accent}; }}
    QSlider::groove:horizontal {{ background:{border}; }}
    QSlider::sub-page:horizontal {{ background:{accent}; }}
    QSlider::handle:horizontal {{ background:{accent}; border:2px solid {selection_text}; width:10px; margin:-3px 0; }}
    QScrollBar:vertical, QScrollBar:horizontal {{ background:{panel}; }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background:{border}; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background:{accent}; }}
    QLabel#statusPill, QLabel#statusPill[state="running"], QLabel#statusPill[state="stopping"] {{ background:{panel}; color:{text}; }}
    QLabel#statusPill[state="running"] {{ border-color:{accent}; }}
    QLabel#statusPill[state="stopping"] {{ border-color:{colors['highlight']}; }}
    QLabel#statusPill[state="error"] {{ background:{panel}; color:{text}; border-color:{colors['pink']}; }}
    QLabel#previewSurface, QLabel#policyPreviewSurface {{ background:#080D16; color:#B4BFCD; border-color:{border}; }}
    QWidget#datasetReviewCanvas {{ background:{panel}; color:{muted}; border-color:{border}; }}
    ''' + (f'''
    QFrame#settingsCard, QFrame#metricCard, QFrame#signalPanel,
    QFrame#settingsTabs, QFrame#policyInspector {{ border-radius:6px; border-color:#29484A; }}
    QFrame#commandBar, QFrame#navigationDeck, QFrame#telemetryStrip {{ background:#0A1B1C; border-color:#29484A; }}
    QFrame#missionTaskCard {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #142D2E,stop:0.25 {card},stop:1 {card}); border:1px solid #29484A; border-radius:6px; }}
    QLabel#missionCardTitle {{ font-size:16pt; font-weight:600; color:{text}; background:transparent; border:0; }}
    QLabel#missionFooterState {{ font-size:11pt; font-weight:600; color:{text}; }}
    QFrame#missionTaskCard QFrame#settingsCard {{ background:transparent; border:0; border-bottom:1px solid #29484A; border-radius:0; }}
    QFrame#missionTaskCard QFrame#settingAccent {{ background:transparent; }}
    QPushButton#navButton {{ border-radius:5px; min-height:24px; padding:4px 10px; font-size:11pt; font-weight:500; }}
    QPushButton#navButton:checked {{ background:#1C392D; color:{accent}; border-left:4px solid {accent}; }}
    QLabel#navGroup {{ color:{muted}; font-weight:600; letter-spacing:0px; }}
    QLabel#pageTitle {{ font-size:24pt; font-weight:700; }}
    QLabel#pageKicker {{ color:{muted}; letter-spacing:0px; }}
    QCheckBox[missionSwitch="true"]::indicator {{ width:40px; height:22px; border:1px solid {border}; border-radius:11px; background:{input_base}; image:url("{switch_off}"); }}
    QCheckBox[missionSwitch="true"]::indicator:checked {{ border:1px solid {accent}; background:{accent}; image:url("{switch_on}"); }}
    QCheckBox[missionSwitch="true"]::indicator:disabled {{ border-color:#385454; background:#102426; }}
    QFrame#sectionDot, QFrame#settingAccent {{ background:{accent}; }}
    QSlider[barAccent="baby"]::sub-page:horizontal, QSlider[barAccent="pink"]::sub-page:horizontal,
    QSlider[barAccent="ultraviolet"]::sub-page:horizontal, QSlider[barAccent="highlight"]::sub-page:horizontal,
    QSlider[barAccent="cobalt"]::sub-page:horizontal {{ background:{accent}; }}
    QPushButton#primaryButton:hover, QPushButton[missionPrimary="true"]:hover {{ background:#D0FFAB; color:#07120E; }}
    QPushButton#primaryButton:pressed, QPushButton[missionPrimary="true"]:pressed {{ background:#9ED66D; color:#07120E; }}
    QPushButton:disabled, QToolButton:disabled, QPushButton#primaryButton:disabled,
    QPushButton#dangerButton:disabled {{ background:#102426; color:#8EA5A2; border:1px solid #385454; }}
    QPushButton#secondaryTabButton:checked, QTabBar::tab:selected {{ background:#1C392D; color:{accent}; border-bottom:3px solid {accent}; }}
    QWidget {{ font-size:11pt; }}
    QLabel#detail, QLabel#fieldDescription {{ font-size:10.5pt; }}
    QLabel#fieldTitle {{ font-size:11pt; }}
    QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled,
    QSlider[barAccent]::sub-page:horizontal:disabled {{ background:#385454; border-color:#385454; }}
    QListView::item:selected, QListWidget::item:selected {{ border-left:3px solid {accent}; }}
    QTreeWidget::item:selected {{ border-left:0; }}
    QLabel#previewSurface, QLabel#policyPreviewSurface {{ background:#0B1C1E; color:{muted}; }}
    ''' if theme.name == "Mission Control" else '')
