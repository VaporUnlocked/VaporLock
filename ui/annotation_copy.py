"""Native annotation backend copy: Chinese, German, Russian, French."""
ROWS = {
    'Annotation commit outcome is uncertain. Files and backups were retained; recovery is required before the next dataset task.': (
        '无法确认标注提交结果。文件和备份已保留；下一次数据集任务前需要恢复。',
        'Das Ergebnis der Annotation konnte nicht bestätigt werden. Dateien und Sicherungen bleiben erhalten; vor der nächsten Datensatzaufgabe ist eine Wiederherstellung erforderlich.',
        'Результат сохранения разметки не подтверждён. Файлы и резервные копии сохранены; перед следующей задачей набора данных требуется восстановление.',
        'Le résultat de l’enregistrement des annotations est incertain. Les fichiers et sauvegardes sont conservés ; une récupération est requise avant la prochaine tâche du jeu de données.'),
 'Initializing annotation backend…': ('正在初始化标注后端…', 'Annotations-Backend wird initialisiert…', 'Инициализация среды разметки…', 'Initialisation du moteur d’annotation…'),
 'Annotation backend did not start.': ('标注后端未能启动。', 'Das Annotations-Backend wurde nicht gestartet.', 'Среда разметки не запустилась.', 'Le moteur d’annotation n’a pas démarré.'),
 'Annotation backend': ('标注后端', 'Annotations-Backend', 'Среда разметки', 'Moteur d’annotation'),
 'Auto (GPU preferred)': ('自动（优先 GPU）', 'Auto (GPU bevorzugt)', 'Авто (приоритет GPU)', 'Auto (GPU prioritaire)'),
 'CUDA (not included)': ('CUDA（未包含）', 'CUDA (nicht enthalten)', 'CUDA (не включена)', 'CUDA (non inclus)'),
 'TensorRT RTX (unavailable)': ('TensorRT RTX（不可用）', 'TensorRT RTX (nicht verfügbar)', 'TensorRT RTX (недоступен)', 'TensorRT RTX (indisponible)'),
 'DirectML': ('DirectML', 'DirectML', 'DirectML', 'DirectML'),
 'CPU': ('CPU', 'CPU', 'CPU', 'CPU'),
 'Auto prefers a supported GPU, then reports any CPU fallback. DirectML is included. Explicit DirectML stops if it cannot initialize. CUDA and TensorRT RTX annotation are not included in this release.': (
  '自动模式优先使用支持的 GPU，并显示回退到 CPU 的情况。已包含 DirectML。显式选择 DirectML 时，若无法初始化则停止。本版本未包含 CUDA 或 TensorRT RTX 标注。',
  'Auto bevorzugt eine unterstützte GPU und zeigt einen Rückfall auf die CPU an. DirectML ist enthalten. Bei explizitem DirectML wird abgebrochen, wenn die Initialisierung fehlschlägt. CUDA- und TensorRT-RTX-Annotation sind in dieser Version nicht enthalten.',
  'Авто предпочитает поддерживаемый GPU и сообщает о переходе на CPU. DirectML включён. Явный выбор DirectML останавливает задачу при ошибке инициализации. Разметка CUDA и TensorRT RTX в эту версию не включена.',
  'Auto privilégie un GPU compatible et signale tout repli sur CPU. DirectML est inclus. Le choix explicite de DirectML arrête la tâche si l’initialisation échoue. L’annotation CUDA et TensorRT RTX n’est pas incluse dans cette version.'),
 'Annotation backend has not started.': ('标注后端尚未启动。', 'Das Annotations-Backend wurde noch nicht gestartet.', 'Среда разметки ещё не запущена.', 'Le moteur d’annotation n’a pas encore démarré.'),
 'Annotation backend: {backend} · Adapter {device}. Some operations may run on CPU.': (
  '标注后端：{backend} · 适配器 {device}。部分操作可能使用 CPU。',
  'Annotations-Backend: {backend} · Adapter {device}. Einige Vorgänge können auf der CPU laufen.',
  'Среда разметки: {backend} · Адаптер {device}. Некоторые операции могут выполняться на CPU.',
  'Moteur d’annotation : {backend} · Adaptateur {device}. Certaines opérations peuvent utiliser le CPU.'),
 'Annotation backend: CPU.': ('标注后端：CPU。', 'Annotations-Backend: CPU.', 'Среда разметки: CPU.', 'Moteur d’annotation : CPU.'),
 'GPU unavailable; using CPU.': ('GPU 不可用；正在使用 CPU。', 'GPU nicht verfügbar; CPU wird verwendet.', 'GPU недоступен; используется CPU.', 'GPU indisponible ; utilisation du CPU.'),
 'Annotation stopped: the selected backend is not installed. Select Auto, DirectML or CPU.': (
  '标注已停止：所选后端未安装。请选择自动、DirectML 或 CPU。',
  'Annotation gestoppt: Das gewählte Backend ist nicht installiert. Auto, DirectML oder CPU wählen.',
  'Разметка остановлена: выбранная среда не установлена. Выберите Авто, DirectML или CPU.',
  'Annotation arrêtée : le moteur choisi n’est pas installé. Choisissez Auto, DirectML ou CPU.'),
 'Annotation stopped: the selected GPU could not initialize. Check the graphics driver or select Auto or CPU.': (
  '标注已停止：所选 GPU 无法初始化。请检查显卡驱动，或选择自动或 CPU。',
  'Annotation gestoppt: Die gewählte GPU konnte nicht initialisiert werden. Grafiktreiber prüfen oder Auto bzw. CPU wählen.',
  'Разметка остановлена: выбранный GPU не удалось инициализировать. Проверьте драйвер или выберите Авто либо CPU.',
  'Annotation arrêtée : le GPU choisi n’a pas pu être initialisé. Vérifiez le pilote graphique ou choisissez Auto ou CPU.'),
 'Annotation stopped: the requested GPU backend was not retained. No labels were changed.': (
  '标注已停止：未保留请求的 GPU 后端。标签未更改。',
  'Annotation gestoppt: Das angeforderte GPU-Backend wurde nicht beibehalten. Keine Labels wurden geändert.',
  'Разметка остановлена: запрошенная среда GPU не сохранена. Метки не изменены.',
  'Annotation arrêtée : le moteur GPU demandé n’a pas été conservé. Aucune étiquette n’a été modifiée.'),
 'Annotation stopped: TensorRT RTX annotation is unavailable in this release. Select Auto, DirectML or CPU.': (
  '标注已停止：本版本不支持 TensorRT RTX 标注。请选择自动、DirectML 或 CPU。',
  'Annotation gestoppt: TensorRT-RTX-Annotation ist in dieser Version nicht verfügbar. Auto, DirectML oder CPU wählen.',
  'Разметка остановлена: TensorRT RTX недоступен в этой версии. Выберите Авто, DirectML или CPU.',
  'Annotation arrêtée : TensorRT RTX est indisponible dans cette version. Choisissez Auto, DirectML ou CPU.'),
 'Annotation stopped: no usable backend is available.': (
  '标注已停止：没有可用后端。', 'Annotation gestoppt: Kein nutzbares Backend verfügbar.',
  'Разметка остановлена: нет доступной среды.', 'Annotation arrêtée : aucun moteur utilisable n’est disponible.'),
}
