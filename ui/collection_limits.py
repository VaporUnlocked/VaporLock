"""Collection budgets are exact bounded integers, never estimates of free disk."""
FIELDS = ('dataset_max_images', 'dataset_max_mib')
MAXIMUM = 1_000_000


def validate_limits(settings):
    for name in FIELDS:
        value = settings.get(name, 0)
        if type(value) is not int or not 0 <= value <= MAXIMUM:
            raise ValueError('Invalid collection limit')


def add_controls(center, layout):
    from PySide6.QtWidgets import QSpinBox, QLabel
    for name, title, suffix in (
        ('dataset_max_images', 'Maximum new images per session', ''),
        ('dataset_max_mib', 'Maximum new JPEG storage per session', ' MiB'),
    ):
        control = QSpinBox()
        control.setObjectName(name)
        control.setRange(0, MAXIMUM)
        control.setSuffix(suffix)
        control.setValue(getattr(center.settings, name))
        control.setAccessibleName(center.t(title))
        control.valueChanged.connect(lambda value, key=name: center.change(key, value))
        center.setting(layout, title, 'Saving pauses at either limit; capture and targeting continue.', control)
    hint = QLabel(center.t('0 means unlimited. Limits reset with a new capture session; changing folders does not reset them.'))
    hint.setWordWrap(True)
    hint.setObjectName('detail')
    layout.addWidget(hint)
