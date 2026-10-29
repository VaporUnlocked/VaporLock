"""Saved, offline interface localization for Vapor Lock's Qt control surface."""

from __future__ import annotations

from pathlib import Path
import json
import os
import sys
from shiboken6 import isValid
from PySide6.QtGui import QAction
from PySide6.QtCore import QEvent, QObject, QLibraryInfo, QTranslator

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTreeWidget,
    QToolButton,
    QWidget,
    QDialog,
)


LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("English", "en"),
    ("Chinese (Simplified)", "zh-CN"),
    ("Deutsch", "de"),
    ("Русский", "ru"),
    ("Français", "fr"),
)
SUPPORTED_LANGUAGE_CODES = frozenset(code for _name, code in LANGUAGE_OPTIONS)
_LANGUAGE_INDEX = {"zh-CN": 0, "de": 1, "ru": 2, "fr": 3}


# Each row is Chinese, German, Russian, then French. Technical provider and
# format names intentionally remain recognizable across all languages.
_ROWS: dict[str, tuple[str, str, str, str]] = {
    "Install or repair controller support?": ("安装或修复控制器支持？", "Controller-Unterstützung installieren oder reparieren?", "Установить или восстановить поддержку контроллера?", "Installer ou réparer la prise en charge du contrôleur ?"),
    "Remove VaporLock-managed controller support?": ("移除由 VaporLock 管理的控制器支持？", "Von VaporLock verwaltete Controller-Unterstützung entfernen?", "Удалить поддержку контроллера, управляемую VaporLock?", "Supprimer la prise en charge du contrôleur gérée par VaporLock ?"),
    "Install / repair": ("安装 / 修复", "Installieren / reparieren", "Установить / восстановить", "Installer / réparer"),
    "Remove managed support": ("移除托管支持", "Verwaltete Unterstützung entfernen", "Удалить управляемую поддержку", "Supprimer la prise en charge gérée"),
    "Installs or repairs VaporLock-managed controller components. Setup also adds a local controller signing certificate to the Windows machine-wide Root and TrustedPublisher certificate stores. This trust is not limited to these controller packages. Windows will ask for administrator approval.": (
        "安装或修复由 VaporLock 管理的控制器组件。安装程序还会将本地控制器签名证书添加到 Windows 整台计算机的 Root（受信任的根证书颁发机构）和 TrustedPublisher（受信任的发布者）证书存储区。这种信任并不限于这些控制器包。Windows 将请求管理员批准。",
        "Installiert oder repariert die von VaporLock verwalteten Controller-Komponenten. Dabei wird auch ein lokales Controller-Signaturzertifikat in die computerweiten Windows-Zertifikatspeicher Root und TrustedPublisher aufgenommen. Dieses Vertrauen ist nicht auf diese Controller-Pakete beschränkt. Windows fordert eine Administratorgenehmigung an.",
        "Устанавливает или восстанавливает компоненты контроллера, управляемые VaporLock. Также добавляет локальный сертификат подписи контроллера в общесистемные хранилища Windows Root и TrustedPublisher. Это доверие не ограничивается данными пакетами контроллера. Windows запросит разрешение администратора.",
        "Installe ou répare les composants de contrôleur gérés par VaporLock. Un certificat local de signature du contrôleur sera aussi ajouté aux magasins Windows Root et TrustedPublisher de l’ordinateur entier. Cette confiance ne se limite pas à ces packages de contrôleur. Windows demandera une autorisation administrateur.",
    ),
    "Removes only positively identified VaporLock-managed devices, driver packages and certificate trust added by setup. Preexisting, foreign and uncertain components are preserved. Windows will ask for administrator approval. A restart may be required.": (
        "仅移除已明确确认为 VaporLock 管理的设备、驱动包以及安装时添加的证书信任。已有的、其他程序的或归属不确定的组件将被保留。Windows 将请求管理员批准。可能需要重启。",
        "Entfernt ausschließlich eindeutig als VaporLock-verwaltet identifizierte Geräte, Treiberpakete und das bei der Einrichtung hinzugefügte Zertifikatsvertrauen. Bereits vorhandene, fremde und unklar zugeordnete Komponenten bleiben erhalten. Windows fordert eine Administratorgenehmigung an. Ein Neustart kann erforderlich sein.",
        "Удаляет только достоверно определённые устройства и пакеты драйверов под управлением VaporLock, а также доверие к сертификату, добавленное при установке. Существовавшие ранее, сторонние и неоднозначные компоненты сохраняются. Windows запросит разрешение администратора. Может потребоваться перезагрузка.",
        "Supprime uniquement les périphériques, packages de pilotes et relations de confiance ajoutées à l’installation dont la gestion par VaporLock est confirmée. Les composants préexistants, tiers ou incertains sont conservés. Windows demandera une autorisation administrateur. Un redémarrage peut être nécessaire.",
    ),
    "Setup adds a local signing certificate to Windows machine-wide Root and TrustedPublisher stores. Review the confirmation before installing or repairing. Selecting this transport does not install anything.": (
        "安装会向 Windows 整台计算机的 Root 和 TrustedPublisher 存储区添加本地签名证书。请在安装或修复前查看确认信息。仅选择此传输方式不会安装任何内容。",
        "Die Einrichtung fügt ein lokales Signaturzertifikat zu den computerweiten Windows-Speichern Root und TrustedPublisher hinzu. Lesen Sie vor Installation oder Reparatur die Bestätigung. Allein die Auswahl dieses Transports installiert nichts.",
        "Установка добавляет локальный сертификат подписи в общесистемные хранилища Windows Root и TrustedPublisher. Перед установкой или восстановлением прочтите подтверждение. Сам выбор этого транспорта ничего не устанавливает.",
        "L’installation ajoute un certificat local de signature aux magasins Windows Root et TrustedPublisher de l’ordinateur entier. Lisez la confirmation avant d’installer ou de réparer. Choisir ce transport n’installe rien.",
    ),
    "Removes only positively identified VaporLock-managed devices, packages and trust added by setup. Preexisting, foreign or uncertain components are preserved. Administrator approval or a restart may be required.": (
        "仅移除已明确归属于 VaporLock 的设备、程序包和安装时添加的信任。已有的、其他程序的或归属不确定的组件将被保留。可能需要管理员批准或重启。",
        "Entfernt nur eindeutig zugeordnete VaporLock-Geräte, Pakete und bei der Einrichtung hinzugefügtes Vertrauen. Bereits vorhandene, fremde oder unklar zugeordnete Komponenten bleiben erhalten. Administratorgenehmigung oder Neustart kann erforderlich sein.",
        "Удаляет только достоверно определённые устройства и пакеты VaporLock, а также доверие, добавленное при установке. Существовавшие ранее, сторонние или неоднозначные компоненты сохраняются. Может потребоваться разрешение администратора или перезагрузка.",
        "Supprime uniquement les périphériques, packages et relations de confiance ajoutées à l’installation dont la gestion par VaporLock est confirmée. Les composants préexistants, tiers ou incertains sont conservés. Une autorisation administrateur ou un redémarrage peut être nécessaire.",
    ),
    "Root means Trusted Root Certification Authorities; TrustedPublisher means Trusted Publishers. Setup preserves Windows signing policies and blocks unknown or conflicting controller components. Removal uses recorded ownership and does not sweep other applications' devices. Incomplete operations remain pending and may require a restart.": (
        "Root 指受信任的根证书颁发机构；TrustedPublisher 指受信任的发布者。安装会保留 Windows 签名策略，并阻止未知或冲突的控制器组件。移除依据已记录的归属，不会清理其他应用的设备。未完成的操作仍会标为待处理，可能需要重启。",
        "Root steht für vertrauenswürdige Stammzertifizierungsstellen, TrustedPublisher für vertrauenswürdige Herausgeber. Die Einrichtung behält die Windows-Signaturrichtlinien bei und blockiert unbekannte oder konkurrierende Controller-Komponenten. Die Entfernung richtet sich nach dokumentierter Eigentümerschaft und bereinigt keine Geräte anderer Anwendungen. Unvollständige Vorgänge bleiben ausstehend; ein Neustart kann erforderlich sein.",
        "Root — доверенные корневые центры сертификации; TrustedPublisher — доверенные издатели. Установка сохраняет политики подписи Windows и блокирует неизвестные или конфликтующие компоненты контроллеров. Удаление опирается на записи о принадлежности и не затрагивает устройства других приложений. Незавершённые операции остаются в ожидании; может потребоваться перезагрузка.",
        "Root désigne les autorités de certification racines de confiance ; TrustedPublisher désigne les éditeurs approuvés. L’installation conserve les politiques de signature Windows et bloque les composants de contrôleur inconnus ou conflictuels. La suppression repose sur la propriété enregistrée et ne nettoie pas les périphériques d’autres applications. Les opérations incomplètes restent en attente et peuvent nécessiter un redémarrage.",
    ),
    "Access verification detected a clock mismatch. In Windows Date & time settings, select Sync now, then verify access again.": (
        "访问验证检测到时钟不一致。请在 Windows 的“日期和时间”设置中选择“立即同步”，然后重新验证访问权限。",
        "Bei der Zugriffsprüfung wurde eine abweichende Uhrzeit erkannt. Wählen Sie in den Windows-Einstellungen für Datum und Uhrzeit „Jetzt synchronisieren“ und prüfen Sie den Zugriff erneut.",
        "При проверке доступа обнаружено расхождение часов. В параметрах Windows «Дата и время» выполните синхронизацию, затем повторите проверку доступа.",
        "La vérification d’accès a détecté un décalage de l’horloge. Dans les paramètres Windows « Date et heure », sélectionnez « Synchroniser maintenant », puis vérifiez à nouveau l’accès.",
    ),
    "Download size": ("下载大小", "Downloadgröße", "Размер загрузки", "Taille du téléchargement"),
    "Select a model": ("选择模型", "Modell auswählen", "Выберите модель", "Sélectionnez un modèle"),
    "Interface and language": ("界面和语言", "Oberfläche und Sprache", "Интерфейс и язык", "Interface et langue"),
    "Updates": ("更新", "Updates", "Обновления", "Mises à jour"),
    "Classes": ("类别", "Klassen", "Классы", "Classes"),
    "Official game defaults are separate from your own saved profiles.": ("官方游戏默认配置与您保存的个人配置文件相互独立。", "Offizielle Spielvorgaben sind von Ihren eigenen gespeicherten Profilen getrennt.", "Официальные настройки игр отделены от ваших сохранённых профилей.", "Les paramètres officiels des jeux sont distincts de vos profils enregistrés."),
    "Mission Control": ("任务控制", "Mission Control", "Центр управления", "Mission Control"),
    "Appearance": ("外观", "Darstellung", "Оформление", "Apparence"),
    "WORKSPACE": ("工作区", "ARBEITSBEREICH", "РАБОЧАЯ ОБЛАСТЬ", "ESPACE DE TRAVAIL"),
    "TOOLS": ("工具", "WERKZEUGE", "ИНСТРУМЕНТЫ", "OUTILS"),
    "Choose a theme with its own background and translucent panels. Preview and overlay colors stay separate.": ("选择具有独特背景和半透明面板的主题。预览和叠加层颜色保持独立。", "Wählen Sie ein Thema mit eigenem Hintergrund und transparenten Bereichen. Vorschau- und Overlay-Farben bleiben unabhängig.", "Выберите тему с собственным фоном и полупрозрачными панелями. Цвета предпросмотра и оверлея остаются независимыми.", "Choisissez un thème avec son propre fond et des panneaux translucides. Les couleurs de l’aperçu et de la superposition restent indépendantes."),
    "Search models in this game and type": ("搜索此游戏和类型中的模型", "Modelle in diesem Spiel und Typ suchen", "Поиск моделей в выбранной игре и типе", "Rechercher dans ce jeu et ce type"),
    "Update installed (all games)": ("更新已安装模型（所有游戏）", "Installierte aktualisieren (alle Spiele)", "Обновить установленные (все игры)", "Mettre à jour les modèles installés (tous les jeux)"),
    "Model type": ("模型类型", "Modelltyp", "Тип модели", "Type de modèle"),
    "{game} / {task} - {count} model(s)": ("{game} / {task} - {count} 个模型", "{game} / {task} - {count} Modell(e)", "{game} / {task} - моделей: {count}", "{game} / {task} - {count} modèle(s)"),
    "No models match this game, type and search.": ("没有符合当前游戏、类型和搜索条件的模型。", "Keine Modelle für dieses Spiel, diesen Typ und diese Suche.", "Нет моделей для выбранной игры, типа и поиска.", "Aucun modèle ne correspond à ce jeu, ce type et cette recherche."),
    "Premium access needs verification. Connect online to verify access.": ("需要验证 Premium 访问权限。请联网验证访问权限。", "Der Premium-Zugriff muss bestätigt werden. Stellen Sie dazu eine Internetverbindung her.", "Требуется проверка доступа Premium. Подключитесь к интернету для проверки доступа.", "Votre accès Premium doit être vérifié. Connectez-vous à Internet pour vérifier votre accès."),
    "Verify Premium access": ("验证 Premium 访问权限", "Premium-Zugriff prüfen", "Проверить доступ Premium", "Vérifier l’accès Premium"),
    "Access verified. Choose Start when ready.": ("访问权限已验证。准备好后请点击“启动”。", "Zugriff bestätigt. Wählen Sie Start, wenn Sie bereit sind.", "Доступ подтверждён. Нажмите «Запуск», когда будете готовы.", "Accès vérifié. Cliquez sur Démarrer lorsque vous êtes prêt."),
    "Waiting for previous work and verification to stop. Your settings are kept.": ("正在等待之前的任务和验证停止。您的设置将保留。", "Warten auf das Ende vorheriger Aufgaben und Prüfungen. Ihre Einstellungen bleiben erhalten.", "Ожидание завершения предыдущих задач и проверки. Ваши настройки сохраняются.", "En attente de l’arrêt des tâches et de la vérification précédentes. Vos réglages sont conservés."),
    "Online verification could not complete. Check your connection and retry.": ("无法完成在线验证。请检查网络连接后重试。", "Die Online-Prüfung konnte nicht abgeschlossen werden. Prüfen Sie Ihre Verbindung und versuchen Sie es erneut.", "Не удалось завершить онлайн-проверку. Проверьте подключение и повторите попытку.", "La vérification en ligne n’a pas pu aboutir. Vérifiez votre connexion et réessayez."),
    "Verification was cancelled or the workspace changed. Retry when stopped.": ("验证已取消或工作区已更改。请在停止后重试。", "Die Prüfung wurde abgebrochen oder der Arbeitsbereich hat sich geändert. Versuchen Sie es nach dem Stoppen erneut.", "Проверка отменена или рабочее пространство изменилось. Повторите попытку после остановки.", "La vérification a été annulée ou l’espace de travail a changé. Réessayez après l’arrêt."),
    "Downloading and verifying the current installer...": ("正在下载并验证最新安装程序……", "Aktuelles Installationsprogramm wird heruntergeladen und geprüft…", "Загрузка и проверка актуального установщика…", "Téléchargement et vérification du programme d’installation actuel…"),
    "The current installer could not be verified. Retry or download the installer from the website.": ("无法验证最新安装程序。请重试或从网站下载安装程序。", "Das aktuelle Installationsprogramm konnte nicht geprüft werden. Versuchen Sie es erneut oder laden Sie es von der Website herunter.", "Не удалось проверить актуальный установщик. Повторите попытку или скачайте его с сайта.", "Le programme d’installation actuel n’a pas pu être vérifié. Réessayez ou téléchargez-le depuis le site."),
    "Offline access has expired. Connect online to verify access.": ("离线访问已过期。请联网验证访问权限。", "Der Offline-Zugriff ist abgelaufen. Stellen Sie eine Internetverbindung her, um den Zugriff zu bestätigen.", "Срок автономного доступа истёк. Подключитесь к интернету для проверки доступа.", "L’accès hors ligne a expiré. Connectez-vous à Internet pour vérifier votre accès."),
    "This Premium action is blocked. Connect online to verify access.": ("此 Premium 操作已被阻止。请联网验证访问权限。", "Diese Premium-Aktion ist gesperrt. Stellen Sie eine Internetverbindung her, um den Zugriff zu bestätigen.", "Это действие Premium заблокировано. Подключитесь к интернету для проверки доступа.", "Cette action Premium est bloquée. Connectez-vous à Internet pour vérifier votre accès."),
    "Premium verification is required. The operation has stopped. Connect online to verify access.": ("需要验证 Premium 访问权限。操作已停止。请联网验证访问权限。", "Eine Premium-Verifizierung ist erforderlich. Der Vorgang wurde gestoppt. Stellen Sie eine Internetverbindung her, um den Zugriff zu bestätigen.", "Требуется проверка Premium. Операция остановлена. Подключитесь к интернету для проверки доступа.", "Une vérification Premium est nécessaire. L’opération s’est arrêtée. Connectez-vous à Internet pour vérifier votre accès."),
    "CUDA runtime files are installed and verified. Select CUDA, then Start to use them.": ("CUDA 运行时文件已安装并验证。选择 CUDA，然后点击“启动”即可使用。", "Die CUDA-Laufzeitdateien sind installiert und geprüft. Wählen Sie CUDA und anschließend Start, um sie zu verwenden.", "Файлы среды CUDA установлены и проверены. Выберите CUDA, затем нажмите «Запуск», чтобы использовать их.", "Les fichiers d’exécution CUDA sont installés et vérifiés. Sélectionnez CUDA, puis Démarrer pour les utiliser."),
    "Model download progress": ("模型下载进度", "Modell-Downloadfortschritt", "Ход загрузки модели", "Progression du téléchargement du modèle"),
    "Activate Vapor Lock": ("激活 Vapor Lock", "Vapor Lock aktivieren", "Активация Vapor Lock", "Activer Vapor Lock"),
    "Activate Premium": ("激活 Premium", "Premium aktivieren", "Активация Premium", "Activer Premium"),
    "Verify and activate": ("验证并激活", "Prüfen und aktivieren", "Проверить и активировать", "Vérifier et activer"),
    "A connection is required to verify the current revocation registry.": ("验证当前撤销记录需要网络连接。", "Zum Prüfen des aktuellen Widerrufsregisters ist eine Verbindung erforderlich.", "Для проверки актуального реестра отзыва требуется подключение.", "Une connexion est nécessaire pour vérifier le registre actuel des révocations."),
    "Lifetime": ("永久", "Unbefristet", "Бессрочно", "À vie"),
    "Enter your Vapor Lock license key, supplied with your purchase or through Discord. Vapor Lock verifies it before opening the workspace.": ("输入购买时或通过 Discord 获取的 Vapor Lock 许可证密钥。Vapor Lock 会在打开工作区前验证密钥。", "Geben Sie Ihren beim Kauf oder über Discord erhaltenen Vapor-Lock-Lizenzschlüssel ein. Vapor Lock prüft ihn vor dem Öffnen des Arbeitsbereichs.", "Введите ключ лицензии Vapor Lock, полученный при покупке или через Discord. Vapor Lock проверит его перед открытием рабочего пространства.", "Saisissez votre clé de licence Vapor Lock reçue lors de votre achat ou via Discord. Vapor Lock la vérifie avant d’ouvrir l’espace de travail."),
    "Vapor Lock license key": ("Vapor Lock 许可证密钥", "Vapor-Lock-Lizenzschlüssel", "Ключ лицензии Vapor Lock", "Clé de licence Vapor Lock"),
    "Enter your Vapor Lock license key.": ("请输入 Vapor Lock 许可证密钥。", "Geben Sie Ihren Vapor-Lock-Lizenzschlüssel ein.", "Введите ключ лицензии Vapor Lock.", "Saisissez votre clé de licence Vapor Lock."),
    "Checking license and revocation status...": ("正在检查许可证与撤销状态…", "Lizenz- und Widerrufsstatus werden geprüft…", "Проверка лицензии и статуса отзыва…", "Vérification de la licence et de son statut de révocation…"),
    "Vapor Lock verified the key but could not save it for this Windows account: {error}": ("Vapor Lock 已验证密钥，但无法为此 Windows 账户保存：{error}", "Vapor Lock hat den Schlüssel geprüft, konnte ihn aber für dieses Windows-Konto nicht speichern: {error}", "Vapor Lock проверил ключ, но не смог сохранить его для этой учётной записи Windows: {error}", "Vapor Lock a vérifié la clé, mais n’a pas pu l’enregistrer pour ce compte Windows : {error}"),
    "Valid until {expiry}": ("有效至 {expiry}", "Gültig bis {expiry}", "Действует до {expiry}", "Valide jusqu’au {expiry}"),
    "Valid through {expiry} UTC": ("有效至 {expiry} UTC 当日结束", "Gültig einschließlich {expiry} UTC", "Действует включительно по {expiry} UTC", "Valide jusqu’à la fin du {expiry} UTC"),
    "{tier} verified. {entitlement}.": ("{tier} 已验证。{entitlement}。", "{tier} bestätigt. {entitlement}.", "{tier} проверен. {entitlement}.", "{tier} vérifié. {entitlement}."),
    "Catalog refresh failed. Refresh to select more models.": ("目录刷新失败。请刷新后选择更多模型。", "Katalogaktualisierung fehlgeschlagen. Aktualisieren Sie, um weitere Modelle auszuwählen.", "Не удалось обновить каталог. Обновите его для выбора других моделей.", "Échec de l’actualisation du catalogue. Actualisez pour choisir d’autres modèles."),
    "Calibration shortcut": ("校准快捷键", "Kalibrierungs-Shortcut", "Комбинация калибровки", "Raccourci de calibration"),
    "Box coordinate units": ("边界框坐标单位", "Einheiten der Boxkoordinaten", "Единицы координат рамки", "Unité des coordonnées des boîtes"),
    "Input pixels": ("输入像素", "Eingabepixel", "Пиксели входа", "Pixels d'entrée"),
    "Normalized (0 to 1)": ("归一化（0 到 1）", "Normalisiert (0 bis 1)", "Нормализованные (от 0 до 1)", "Normalisées (0 à 1)"),
    "Automatic uses declared model metadata, or input pixels for standard YOLO exports.": ("自动使用模型声明的元数据；标准 YOLO 导出默认使用输入像素。", "Automatisch: Modelldaten oder Eingabepixel bei Standard-YOLO-Exporten.", "Автоматически: метаданные модели или пиксели входа для стандартного экспорта YOLO.", "Utilise les métadonnées du modèle ou les pixels d'entrée pour les exports YOLO standard."),
    "Export public certificate": ("导出公共证书", "Öffentliches Zertifikat exportieren", "Экспорт открытого сертификата", "Exporter le certificat public"),
    "Aim Testing Suite": ("瞄准测试套件", "Zieltest-Suite", "Полигон для проверки наведения", "Suite de test de visée"),
    "Open testing grounds": ("打开测试场", "Testgelände öffnen", "Открыть полигон", "Ouvrir le terrain d'essai"),
    "Open the standalone practice range. Capture and movement remain under your control.": ("打开独立训练场。捕获和移动仍由您控制。", "Öffnet den eigenständigen Übungsbereich. Erfassung und Bewegung bleiben unter Ihrer Kontrolle.", "Открывает отдельный полигон. Захват и движение остаются под вашим управлением.", "Ouvre le terrain d'entraînement autonome. La capture et le mouvement restent sous votre contrôle."),
    "The testing suite is missing. Repair or reinstall this Vapor Lock release.": ("测试套件缺失。请修复或重新安装此 Vapor Lock 版本。", "Die Test-Suite fehlt. Reparieren oder installieren Sie diese Vapor-Lock-Version erneut.", "Полигон отсутствует. Восстановите или переустановите эту версию Vapor Lock.", "La suite de test est absente. Réparez ou réinstallez cette version de Vapor Lock."),
    "Windows could not start the testing suite. Check that the application folder is writable and the executable is available.": ("Windows 无法启动测试套件。请确认应用文件夹可写且可执行文件存在。", "Windows konnte die Test-Suite nicht starten. Prüfen Sie, ob der Anwendungsordner beschreibbar und die Programmdatei verfügbar ist.", "Windows не удалось запустить полигон. Убедитесь, что папка приложения доступна для записи, а исполняемый файл доступен.", "Windows n'a pas pu démarrer la suite de test. Vérifiez que le dossier de l'application est accessible en écriture et que l'exécutable est disponible."),
    "Assistance gain": ("辅助增益", "Assistenzverstärkung", "Усиление помощи", "Gain d'assistance"),
    "Observation expiry": ("观测有效期", "Beobachtungsablauf", "Срок наблюдения", "Expiration d'observation"),
    "Track memory": ("轨迹保留时间", "Spurspeicher", "Память траектории", "Mémoire de suivi"),
    "Reference settle radius": ("参考点稳定半径", "Referenz-Beruhigungsradius", "Радиус стабилизации", "Rayon de stabilisation"),
    "Snap confirmation bypass": ("跳过吸附确认", "Soforterfassung ohne Bestätigung", "Пропуск подтверждения захвата", "Capture sans confirmation"),
    "Allow immediate snap acquisition": ("允许立即吸附", "Sofortige Erfassung erlauben", "Разрешить немедленный захват", "Autoriser la capture immédiate"),
    "Near-candidate wait": ("近处候选等待时间", "Wartezeit für nahe Kandidaten", "Ожидание близкой цели", "Attente des candidats proches"),
    "Maximum speed": ("最大速度", "Maximale Geschwindigkeit", "Максимальная скорость", "Vitesse maximale"),
    "Aiming reference and calibration": ("瞄准参考与校准", "Zielreferenz und Kalibrierung", "Опорная точка и калибровка", "Référence de visée et calibrage"),
    "Aiming reference": ("瞄准参考点", "Zielreferenz", "Опорная точка наведения", "Référence de visée"),
    "Capture center": ("捕获区域中心", "Erfassungsmitte", "Центр захвата", "Centre de capture"),
    "Crosshair offset": ("准星偏移", "Fadenkreuzversatz", "Смещение прицела", "Décalage du réticule"),
    "Reference horizontal offset": ("参考点水平偏移", "Horizontaler Referenzversatz", "Смещение опоры по горизонтали", "Décalage horizontal de référence"),
    "Reference vertical offset": ("参考点垂直偏移", "Vertikaler Referenzversatz", "Смещение опоры по вертикали", "Décalage vertical de référence"),
    "Horizontal input scale": ("水平输入比例", "Horizontale Eingabeskalierung", "Масштаб ввода по горизонтали", "Échelle d'entrée horizontale"),
    "Vertical input scale": ("垂直输入比例", "Vertikale Eingabeskalierung", "Масштаб ввода по вертикали", "Échelle d'entrée verticale"),
    "Calculate input scale": ("计算输入比例", "Eingabeskalierung berechnen", "Рассчитать масштаб ввода", "Calculer l'échelle d'entrée"),
    "Measured calibration": ("测量校准", "Messwertkalibrierung", "Калибровка по измерениям", "Calibrage par mesure"),
    "Input scale calibration": ("输入比例校准", "Eingabeskalibrierung", "Калибровка масштаба ввода", "Calibrage de l'échelle d'entrée"),
    "Horizontal input displacement": ("水平输入位移", "Horizontaler Eingabeweg", "Горизонтальное смещение ввода", "Déplacement d'entrée horizontal"),
    "Vertical input displacement": ("垂直输入位移", "Vertikaler Eingabeweg", "Вертикальное смещение ввода", "Déplacement d'entrée vertical"),
    "Horizontal observed displacement": ("观测水平位移", "Gemessener horizontaler Weg", "Измеренное горизонтальное смещение", "Déplacement horizontal observé"),
    "Vertical observed displacement": ("观测垂直位移", "Gemessener vertikaler Weg", "Измеренное вертикальное смещение", "Déplacement vertical observé"),
    "Prediction horizon": ("预测时域", "Vorhersagehorizont", "Горизонт прогнозирования", "Horizon de prédiction"),
    "Prediction speed bound": ("预测速度上限", "Geschwindigkeitsgrenze der Vorhersage", "Предел скорости прогноза", "Limite de vitesse prédite"),
    "Prediction acceleration bound": ("预测加速度上限", "Beschleunigungsgrenze der Vorhersage", "Предел ускорения прогноза", "Limite d'accélération prédite"),
    "Maximum prediction offset": ("最大预测偏移", "Maximaler Vorhersageversatz", "Максимальное смещение прогноза", "Décalage prédit maximal"),
    "Motion noise floor": ("运动噪声阈值", "Bewegungsrauschschwelle", "Порог шума движения", "Seuil de bruit du mouvement"),
    "ONNX aim assistance, model tools, and session health.": ("ONNX 瞄准辅助、模型工具和会话状态。", "ONNX-Zielhilfe, Modellwerkzeuge und Sitzungsstatus.", "Помощь в наведении ONNX, инструменты моделей и состояние сеанса.", "Aide à la visée ONNX, outils de modèles et état de session."),
    "Overview": ("概览", "Übersicht", "Обзор", "Aperçu"),
    "Capture": ("捕获", "Erfassung", "Захват", "Capture"),
    "Model Library": ("模型库", "Modellbibliothek", "Библиотека моделей", "Bibliothèque de modèles"),
    "Detection": ("检测", "Erkennung", "Обнаружение", "Détection"),
    "Visuals": ("可视化", "Visualisierung", "Визуализация", "Visuels"),
    "Target Policy": ("目标策略", "Zielrichtlinie", "Политика цели", "Politique de cible"),
    "Targeting": ("目标锁定", "Zielerfassung", "Наведение", "Ciblage"),
    "Tracking": ("跟踪", "Verfolgung", "Отслеживание", "Suivi"),
    "Output": ("输出", "Ausgabe", "Вывод", "Sortie"),
    "Movement": ("移动", "Bewegung", "Движение", "Mouvement"),
    "Training Data": ("训练数据", "Trainingsdaten", "Данные обучения", "Données d'entraînement"),
    "Profiles": ("配置文件", "Profile", "Профили", "Profils"),
    "Shortcuts": ("快捷键", "Tastenkürzel", "Сочетания клавиш", "Raccourcis"),
    "Automation": ("自动化", "Automatisierung", "Автоматизация", "Automatisation"),
    "API Suite": ("API 套件", "API-Suite", "Набор API", "Suite API"),
    "Status & Diagnostics": ("状态与诊断", "Status und Diagnose", "Состояние и диагностика", "État et diagnostic"),
    "Control Center": ("控制中心", "Kontrollzentrum", "Центр управления", "Centre de contrôle"),
    "Workspace": ("工作区", "Arbeitsbereich", "Рабочая область", "Espace de travail"),
    "Observe": ("观察", "Beobachten", "Наблюдение", "Observation"),
    "Track": ("跟踪", "Verfolgung", "Отслеживание", "Suivi"),
    "Manage": ("管理", "Verwalten", "Управление", "Gérer"),
    "System signal": ("系统信号", "Systemsignal", "Системный сигнал", "Signal système"),
    "Session cockpit": ("会话概览", "Sitzungsübersicht", "Панель сеанса", "Tableau de session"),
    "Pipeline": ("处理流程", "Pipeline", "Конвейер", "Traitement"),
    "Capture frame": ("捕获区域", "Erfassungsbereich", "Область захвата", "Zone de capture"),
    "Output authority": ("输出状态", "Ausgabestatus", "Состояние вывода", "État de sortie"),
    "Initialization": ("初始化", "Einrichtung", "Настройка", "Initialisation"),
    "Capture settings": ("捕获设置", "Erfassungseinstellungen", "Настройки захвата", "Réglages de capture"),
    "Movement settings": ("移动设置", "Bewegungseinstellungen", "Настройки движения", "Réglages de mouvement"),
    "Setup & calibration": ("设置与校准", "Einrichtung und Kalibrierung", "Настройка и калибровка", "Configuration et calibrage"),
    "Setup and calibration": ("设置与校准", "Einrichtung und Kalibrierung", "Настройка и калибровка", "Configuration et calibrage"),
    "Model missing": ("模型缺失", "Modell fehlt", "Модель отсутствует", "Modèle absent"),
    "Invalid region": ("区域无效", "Ungültiger Bereich", "Неверная область", "Zone invalide"),
    "Output blocked": ("输出已阻止", "Ausgabe gesperrt", "Вывод заблокирован", "Sortie bloquée"),
    "Configured": ("已配置", "Konfiguriert", "Настроено", "Configuré"),
    "Output off": ("输出关闭", "Ausgabe aus", "Вывод выключен", "Sortie désactivée"),
    "Binding confirmed": ("绑定已确认", "Bindung bestätigt", "Привязка подтверждена", "Affectation confirmée"),
    "Action required": ("需要操作", "Aktion erforderlich", "Требуется действие", "Action requise"),
    "Virtual desktop": ("虚拟桌面", "Virtueller Desktop", "Виртуальный рабочий стол", "Bureau virtuel"),
    "Display {number}": ("显示器 {number}", "Bildschirm {number}", "Монитор {number}", "Écran {number}"),
    "Live telemetry": ("实时遥测", "Live-Telemetrie", "Текущая телеметрия", "Télémétrie en direct"),
    "Local session": ("本地会话", "Lokale Sitzung", "Локальный сеанс", "Session locale"),
    "Provider": ("提供程序", "Anbieter", "Поставщик", "Fournisseur"),
    "Model": ("模型", "Modell", "Модель", "Modèle"),
    "Session state": ("会话状态", "Sitzungsstatus", "Состояние сеанса", "État de session"),
    "Processing rate": ("处理速率", "Verarbeitungsrate", "Скорость обработки", "Débit de traitement"),
    "Active tracks": ("活动轨迹", "Aktive Tracks", "Активные треки", "Suivis actifs"),
    "Current lock": ("当前锁定", "Aktuelle Sperre", "Текущая фиксация", "Verrouillage actuel"),
    "Rate": ("速率", "RATE", "СКОРОСТЬ", "DÉBIT"),
    "Inference": ("推理", "INFERENZ", "ИНФЕРЕНС", "INFÉRENCE"),
    "Tracks": ("轨迹", "TRACKS", "ТРЕКИ", "SUIVIS"),
    "Idle": ("空闲", "LEERLAUF", "ОЖИДАНИЕ", "INACTIF"),
    "Ready": ("就绪", "BEREIT", "ГОТОВО", "PRÊT"),
    "Running": ("运行中", "WIRD AUSGEFÜHRT", "ВЫПОЛНЯЕТСЯ", "EN COURS"),
    "Starting": ("正在启动", "WIRD GESTARTET", "ЗАПУСК", "DÉMARRAGE"),
    "Stopping": ("正在停止", "WIRD GESTOPPT", "ОСТАНОВКА", "ARRÊT"),
    "Stopped": ("已停止", "GESTOPPT", "ОСТАНОВЛЕНО", "ARRÊTÉ"),
    "Start": ("开始", "Starten", "Запуск", "Démarrer"),
    "Start capture": ("开始捕获", "Erfassung starten", "Запустить захват", "Démarrer la capture"),
    "Stop": ("停止", "Stopp", "Стоп", "Arrêter"),
    "Default profile": ("默认配置文件", "Standardprofil", "Профиль по умолчанию", "Profil par défaut"),
    "Free edition": ("免费版", "Kostenlose Edition", "Бесплатная версия", "Édition gratuite"),
    "Premium edition": ("高级版", "Premium-Edition", "Премиум-версия", "Édition Premium"),
    "Active": ("活动", "AKTIV", "АКТИВНО", "ACTIF"),
    "Community": ("社区", "Community", "Сообщество", "Communauté"),
    "Language": ("语言", "Sprache", "Язык", "Langue"),
    "Display language": ("界面语言", "Anzeigesprache", "Язык интерфейса", "Langue d'affichage"),
    "Quick profile": ("快速配置文件", "Schnellprofil", "Быстрый профиль", "Profil rapide"),
    "No saved profiles": ("没有已保存的配置文件", "Keine gespeicherten Profile", "Нет сохранённых профилей", "Aucun profil enregistré"),
    "Interface": ("界面", "Oberfläche", "Интерфейс", "Interface"),
    "Interface mode": ("界面模式", "Oberflächenmodus", "Режим интерфейса", "Mode d'interface"),
    "Smoky glass": ("烟雾玻璃", "Rauchglas", "Дымчатое стекло", "Verre fumé"),
    "Fog contrast": ("雾面高对比度", "Nebelkontrast", "Туманный контраст", "Contraste brume"),
    "Interface palette · Premium": ("界面调色板 · 高级版", "Oberflächenpalette · Premium", "Палитра интерфейса · Премиум", "Palette d'interface · Premium"),
    "Steel blue": ("钢蓝色", "Stahlblau", "Стальной синий", "Bleu acier"),
    "Cobalt blue": ("钴蓝色", "Kobaltblau", "Кобальтовый синий", "Bleu cobalt"),
    "Ice blue": ("冰蓝色", "Eisblau", "Ледяной синий", "Bleu glacier"),
    "Baby blue": ("浅蓝色", "Babyblau", "Нежно-голубой", "Bleu ciel"),
    "Ultraviolet purple": ("紫外紫色", "Ultraviolett", "Ультрафиолетовый", "Violet ultraviolet"),
    "Carbon black": ("碳黑色", "Carbonschwarz", "Угольно-чёрный", "Noir carbone"),
    "Vapor pink": ("蒸汽粉色", "Vapor-Pink", "Паровой розовый", "Rose vapeur"),
    "Highlight yellow": ("高亮黄色", "Markierungsgelb", "Жёлтый акцент", "Jaune de surbrillance"),
    "Muted text & borders": ("次要文本和边框", "Gedämpfter Text und Rahmen", "Приглушённый текст и границы", "Texte atténué et bordures"),
    "Selection & active controls": ("选择和活动控件", "Auswahl und aktive Steuerelemente", "Выбор и активные элементы", "Sélection et contrôles actifs"),
    "Primary text": ("主要文本", "Primärtext", "Основной текст", "Texte principal"),
    "Headings & focus": ("标题和焦点", "Überschriften und Fokus", "Заголовки и фокус", "Titres et focus"),
    "Live status & progress": ("实时状态和进度", "Live-Status und Fortschritt", "Текущий статус и прогресс", "État en direct et progression"),
    "Panels & workspace": ("面板和工作区", "Panels und Arbeitsbereich", "Панели и рабочая область", "Panneaux et espace de travail"),
    "Danger & section accent": ("危险操作和分区强调", "Gefahr und Abschnittsakzent", "Опасность и акцент разделов", "Danger et accent de section"),
    "Attention highlight": ("注意高亮", "Aufmerksamkeitsmarkierung", "Акцент внимания", "Mise en évidence"),
    "Opacity": ("不透明度", "Deckkraft", "Непрозрачность", "Opacité"),
    "Palette defaults": ("调色板默认值", "Palettenstandard", "Стандартная палитра", "Palette par défaut"),
    "Reset interface palette": ("重置界面调色板", "Oberflächenpalette zurücksetzen", "Сбросить палитру интерфейса", "Réinitialiser la palette"),
    "Choose color": ("选择颜色", "Farbe auswählen", "Выбрать цвет", "Choisir une couleur"),
    "Choose interface color": ("选择界面颜色", "Oberflächenfarbe auswählen", "Выбрать цвет интерфейса", "Choisir une couleur d'interface"),
    "Set this color across the complete Control Center palette.": (
        "在整个控制中心调色板中设置此颜色。", "Diese Farbe in der gesamten Kontrollzentrum-Palette verwenden.",
        "Задать этот цвет для всей палитры центра управления.", "Définir cette couleur dans toute la palette du centre de contrôle."
    ),
    "Descriptions, subdued labels, dividers, control outlines, and scroll indicators.": (
        "说明、次要标签、分隔线、控件轮廓和滚动指示器。", "Beschreibungen, dezente Beschriftungen, Trennlinien, Steuerelementrahmen und Scrollanzeigen.",
        "Описания, приглушённые подписи, разделители, контуры элементов и индикаторы прокрутки.", "Descriptions, libellés discrets, séparateurs, contours des contrôles et indicateurs de défilement."
    ),
    "Selected navigation, active controls, setting accents, and selection backgrounds.": (
        "选中的导航、活动控件、设置强调和选择背景。", "Ausgewählte Navigation, aktive Steuerelemente, Einstellungsakzente und Auswahlhintergründe.",
        "Выбранная навигация, активные элементы, акценты настроек и фон выбора.", "Navigation sélectionnée, contrôles actifs, accents des réglages et arrière-plans de sélection."
    ),
    "Primary copy, values, control text, and high-legibility foreground content.": (
        "主要文字、数值、控件文本和高可读性前景内容。", "Primärtext, Werte, Steuerelementtext und gut lesbare Vordergrundinhalte.",
        "Основной текст, значения, подписи элементов и хорошо читаемое содержимое переднего плана.", "Texte principal, valeurs, texte des contrôles et contenu de premier plan très lisible."
    ),
    "Headings, keyboard focus, emphasized borders, and primary action surfaces.": (
        "标题、键盘焦点、强调边框和主要操作表面。", "Überschriften, Tastaturfokus, hervorgehobene Rahmen und primäre Aktionsflächen.",
        "Заголовки, фокус клавиатуры, выделенные границы и основные действия.", "Titres, focus clavier, bordures accentuées et surfaces d'action principales."
    ),
    "Running-state indicators, progress fills, and active secondary tabs.": (
        "运行状态指示器、进度填充和活动次级标签。", "Laufstatusanzeigen, Fortschrittsfüllungen und aktive Sekundärregister.",
        "Индикаторы работы, заполнение прогресса и активные вторичные вкладки.", "Indicateurs d'exécution, remplissage de progression et onglets secondaires actifs."
    ),
    "Workspace film, cards, fields, menus, and the main panel foundation.": (
        "工作区遮罩、卡片、字段、菜单和主面板基础。", "Arbeitsbereichsfilm, Karten, Felder, Menüs und die Grundlage der Hauptpanels.",
        "Плёнка рабочей области, карточки, поля, меню и основа главных панелей.", "Voile de l'espace de travail, cartes, champs, menus et base des panneaux principaux."
    ),
    "Destructive actions, section markers, and critical visual accents.": (
        "破坏性操作、分区标记和关键视觉强调。", "Destruktive Aktionen, Abschnittsmarkierungen und kritische visuelle Akzente.",
        "Опасные действия, маркеры разделов и критические визуальные акценты.", "Actions destructives, repères de section et accents visuels critiques."
    ),
    "Attention states, stopping status, and alternating control emphasis.": (
        "注意状态、停止状态和交替控件强调。", "Aufmerksamkeitszustände, Stoppstatus und wechselnde Steuerelementakzente.",
        "Состояния внимания, остановка и чередующиеся акценты элементов.", "États d'attention, état d'arrêt et accentuation alternée des contrôles."
    ),
    "Restore the original Vapor Lock interface colors.": (
        "恢复 Vapor Lock 原始界面颜色。", "Die ursprünglichen Vapor-Lock-Oberflächenfarben wiederherstellen.",
        "Восстановить исходные цвета интерфейса Vapor Lock.", "Restaurer les couleurs d'interface Vapor Lock d'origine."
    ),
    "Smoky glass keeps the continuous atmospheric workspace. Fog contrast increases panel separation, spacing, and edge definition.": (
        "烟雾玻璃保留连续的氛围工作区。雾面高对比度增强面板分隔、间距和边缘清晰度。",
        "Rauchglas bewahrt die durchgängige Atmosphäre. Nebelkontrast verstärkt Paneltrennung, Abstände und Kanten.",
        "Дымчатое стекло сохраняет цельную атмосферу. Туманный контраст усиливает разделение панелей, интервалы и границы.",
        "Le verre fumé conserve l'espace continu. Le contraste brume renforce la séparation, l'espacement et les contours."
    ),
    "Choose the language used throughout Control Center.": (
        "选择控制中心使用的语言。", "Wählen Sie die Sprache für das Kontrollzentrum.",
        "Выберите язык интерфейса центра управления.", "Choisissez la langue du centre de contrôle."
    ),
    "Source": ("来源", "Quelle", "Источник", "Source"),
    "Capture display": ("捕获显示器", "Erfassungsanzeige", "Дисплей захвата", "Écran de capture"),
    "Centered capture region": ("居中捕获区域", "Zentrierter Erfassungsbereich", "Центральная область захвата", "Zone de capture centrée"),
    "Capture rate": ("捕获速率", "Erfassungsrate", "Частота захвата", "Fréquence de capture"),
    "Inference rate": ("推理速率", "Inferenzrate", "Частота инференса", "Fréquence d'inférence"),
    "Frame skip": ("跳帧", "Frame-Skip", "Пропуск кадров", "Saut d'images"),
    "ONNX Runtime": ("ONNX Runtime", "ONNX Runtime", "ONNX Runtime", "ONNX Runtime"),
    "Intra-operation threads": ("操作内线程", "Intra-Operations-Threads", "Потоки внутри операции", "Threads intra-opération"),
    "Inter-operation threads": ("操作间线程", "Inter-Operations-Threads", "Потоки между операциями", "Threads inter-opération"),
    "CUDA Graph replay": ("CUDA 图重放", "CUDA-Graph-Wiedergabe", "Повтор CUDA-графа", "Relecture de graphe CUDA"),
    "CUDA preprocessing": ("CUDA 预处理", "CUDA-Vorverarbeitung", "Предобработка CUDA", "Prétraitement CUDA"),
    "Enable CUDA Graph replay": ("启用 CUDA 图重放", "CUDA-Graph-Wiedergabe aktivieren", "Включить повтор CUDA-графа", "Activer la relecture de graphe CUDA"),
    "Use GPU preprocessing": ("使用 GPU 预处理", "GPU-Vorverarbeitung verwenden", "Использовать предобработку GPU", "Utiliser le prétraitement GPU"),
    "Virtual desktop (compatibility capture)": ("虚拟桌面（兼容捕获）", "Virtueller Desktop (Kompatibilitätsmodus)", "Виртуальный рабочий стол (совместимый захват)", "Bureau virtuel (capture de compatibilité)"),
    "Execution": ("执行", "Ausführung", "Выполнение", "Exécution"),
    "Inference provider": ("推理提供程序", "Inferenzanbieter", "Поставщик инференса", "Fournisseur d'inférence"),
    "DirectML install": ("安装 DirectML", "DirectML installieren", "Установить DirectML", "Installer DirectML"),
    "Local model library": ("本地模型库", "Lokale Modellbibliothek", "Локальная библиотека моделей", "Bibliothèque de modèles locale"),
    "Refresh": ("刷新", "Aktualisieren", "Обновить", "Actualiser"),
    "Open folder": ("打开文件夹", "Ordner öffnen", "Открыть папку", "Ouvrir le dossier"),
    "Detection model": ("检测模型", "Erkennungsmodell", "Модель обнаружения", "Modèle de détection"),
    "Browse…": ("浏览…", "Durchsuchen…", "Обзор…", "Parcourir…"),
    "Image resize mode": ("图像调整模式", "Bildskalierungsmodus", "Режим изменения размера", "Mode de redimensionnement"),
    "Model output layout": ("模型输出布局", "Modellausgabelayout", "Макет вывода модели", "Disposition de sortie du modèle"),
    "Native model inspection": ("原生模型检查", "Native Modellprüfung", "Нативная проверка модели", "Inspection native du modèle"),
    "Inspect": ("检查", "Prüfen", "Проверить", "Inspecter"),
    "Provider benchmark": ("提供程序基准测试", "Anbieter-Benchmark", "Тест поставщика", "Test de fournisseur"),
    "Benchmark providers": ("基准测试提供程序", "Anbieter vergleichen", "Сравнить поставщиков", "Comparer les fournisseurs"),
    "Runtime model validation · Premium": ("运行时模型验证 · 高级版", "Laufzeit-Modellvalidierung · Premium", "Проверка модели во время работы · Премиум", "Validation d'exécution du modèle · Premium"),
    "Run validation": ("运行验证", "Validierung ausführen", "Запустить проверку", "Lancer la validation"),
    "Automatic (DirectML preferred)": ("自动（优先 DirectML）", "Automatisch (DirectML bevorzugt)", "Автоматически (предпочитать DirectML)", "Automatique (DirectML préféré)"),
    "Automatic from model": ("自动从模型读取", "Automatisch aus dem Modell", "Автоматически по модели", "Automatique depuis le modèle"),
    "Letterbox (preserve aspect ratio)": ("信箱模式（保留宽高比）", "Letterbox (Seitenverhältnis beibehalten)", "Letterbox (сохранить пропорции)", "Letterbox (préserver les proportions)"),
    "Stretch to model input": ("拉伸至模型输入", "Auf Modelleingabe strecken", "Растянуть под вход модели", "Étirer vers l'entrée du modèle"),
    "YOLO class scores (v8/v10/v11 style)": ("YOLO 类别分数（v8/v10/v11 样式）", "YOLO-Klassenwerte (v8/v10/v11-Stil)", "Оценки классов YOLO (v8/v10/v11)", "Scores de classes YOLO (style v8/v10/v11)"),
    "YOLO objectness x class score (v5/v7 style)": ("YOLO 目标性 x 类别分数（v5/v7 样式）", "YOLO-Objektwert x Klassenwert (v5/v7-Stil)", "Объектность YOLO x оценка класса (v5/v7)", "Objectness YOLO x score de classe (style v5/v7)"),
    "End-to-end boxes: x1, y1, x2, y2, score, class": ("端到端框：x1、y1、x2、y2、分数、类别", "End-to-End-Boxen: x1, y1, x2, y2, Wert, Klasse", "Сквозные рамки: x1, y1, x2, y2, оценка, класс", "Boîtes de bout en bout : x1, y1, x2, y2, score, classe"),
    "Thresholds": ("阈值", "Schwellenwerte", "Пороги", "Seuils"),
    "Confidence threshold": ("置信度阈值", "Konfidenzschwelle", "Порог уверенности", "Seuil de confiance"),
    "Non-maximum suppression": ("非极大值抑制", "Non-Maximum-Unterdrückung", "Подавление немаксимумов", "Suppression non maximale"),
    "NMS overlap threshold": ("NMS 重叠阈值", "NMS-Überlappungsschwelle", "Порог перекрытия NMS", "Seuil de chevauchement NMS"),
    "Class-agnostic NMS": ("类别无关 NMS", "Klassenunabhängige NMS", "NMS без учета класса", "NMS indépendante des classes"),
    "Maximum detections": ("最大检测数", "Maximale Erkennungen", "Максимум обнаружений", "Détections maximales"),
    "Allowed class IDs": ("允许的类别 ID", "Zulässige Klassen-IDs", "Разрешенные ID классов", "ID de classes autorisés"),
    "Enabled": ("已启用", "Aktiviert", "Включено", "Activé"),
    "Selection": ("选择", "Auswahl", "Выбор", "Sélection"),
    "Anchor": ("锚点", "Anker", "Якорь", "Ancre"),
    "Class rules": ("类别规则", "Klassenregeln", "Правила классов", "Règles de classes"),
    "Manual lock": ("手动锁定", "Manuelle Sperre", "Ручная фиксация", "Verrouillage manuel"),
    "Subject selection": ("目标选择", "Objektauswahl", "Выбор объекта", "Sélection du sujet"),
    "Selection strategy": ("选择策略", "Auswahlstrategie", "Стратегия выбора", "Stratégie de sélection"),
    "Targeting FOV shape": ("目标 FOV 形状", "Zielfeldform", "Форма поля цели", "Forme du champ cible"),
    "Circular FOV": ("圆形 FOV", "Kreisförmiges FOV", "Круглое поле", "Champ circulaire"),
    "Rectangular FOV": ("矩形 FOV", "Rechteckiges FOV", "Прямоугольное поле", "Champ rectangulaire"),
    "Snap radius": ("吸附半径", "Einrastradius", "Радиус привязки", "Rayon d'accroche"),
    "Near radius": ("近距离半径", "Nahbereichsradius", "Ближний радиус", "Rayon proche"),
    "Acquisition radius": ("获取半径", "Erfassungsradius", "Радиус захвата", "Rayon d'acquisition"),
    "Retention radius": ("保持半径", "Haltebereich", "Радиус удержания", "Rayon de maintien"),
    "Switching margin": ("切换余量", "Wechselmarge", "Порог переключения", "Marge de changement"),
    "Identity continuity": ("身份连续性", "Identitätskontinuität", "Непрерывность идентичности", "Continuité d'identité"),
    "Minimum lock duration": ("最短锁定时长", "Mindest-Sperrdauer", "Минимальная длительность фиксации", "Durée minimale de verrouillage"),
    "Lost-target hold": ("丢失目标保持", "Zielverlust-Haltezeit", "Удержание при потере цели", "Maintien sur cible perdue"),
    "Lost-track frames": ("丢失轨迹帧", "Verlorene Track-Frames", "Кадры потери трека", "Images de suivi perdues"),
    "Occlusion recovery": ("遮挡恢复", "Verdeckungswiederherstellung", "Восстановление после перекрытия", "Récupération d'occlusion"),
    "Occlusion grace": ("遮挡宽限", "Verdeckungs-Toleranz", "Допуск перекрытия", "Tolérance d'occlusion"),
    "Reacquisition radius": ("重新获取半径", "Wiedererfassungsradius", "Радиус повторного захвата", "Rayon de réacquisition"),
    "Preferred class watchlist": ("首选类别监视列表", "Bevorzugte Klassenliste", "Список приоритетных классов", "Liste de classes préférées"),
    "Observation anchor": ("观测锚点", "Beobachtungsanker", "Якорь наблюдения", "Ancre d'observation"),
    "Anchor point": ("锚点位置", "Ankerpunkt", "Точка якоря", "Point d'ancrage"),
    "Horizontal anchor offset": ("水平锚点偏移", "Horizontaler Ankerversatz", "Горизонтальное смещение якоря", "Décalage horizontal de l'ancre"),
    "Vertical anchor offset": ("垂直锚点偏移", "Vertikaler Ankerversatz", "Вертикальное смещение якоря", "Décalage vertical de l'ancre"),
    "Box center": ("框中心", "Boxmitte", "Центр рамки", "Centre de la boîte"),
    "Top edge": ("上边缘", "Obere Kante", "Верхний край", "Bord supérieur"),
    "Bottom edge": ("下边缘", "Untere Kante", "Нижний край", "Bord inférieur"),
    "Upper quarter": ("上四分之一", "Oberes Viertel", "Верхняя четверть", "Quart supérieur"),
    "Closest to frame center": ("最接近画面中心", "Nächste zur Bildmitte", "Ближе всего к центру кадра", "Le plus proche du centre"),
    "Highest detection confidence": ("最高检测置信度", "Höchste Erkennungskonfidenz", "Наибольшая уверенность обнаружения", "Meilleure confiance de détection"),
    "Largest visible object": ("最大可见对象", "Größtes sichtbares Objekt", "Самый крупный видимый объект", "Plus grand objet visible"),
    "Smallest visible object": ("最小可见对象", "Kleinstes sichtbares Objekt", "Самый маленький видимый объект", "Plus petit objet visible"),
    "Lowest movement": ("最小移动", "Geringste Bewegung", "Наименьшее движение", "Mouvement minimal"),
    "Match overlap": ("匹配重叠", "Übereinstimmungsüberlappung", "Перекрытие совпадения", "Chevauchement de correspondance"),
    "Lost-track prediction": ("丢失轨迹预测", "Vorhersage verlorener Tracks", "Прогноз потерянного трека", "Prédiction de suivi perdu"),
    "Predict track position between detections": ("在检测之间预测轨迹位置", "Trackposition zwischen Erkennungen vorhersagen", "Прогнозировать позицию между обнаружениями", "Prédire la position entre détections"),
    "Prediction lead": ("预测提前量", "Vorhersagevorlauf", "Упреждение прогноза", "Avance de prédiction"),
    "Prediction limit": ("预测限制", "Vorhersagegrenze", "Предел прогноза", "Limite de prédiction"),
    "Box position smoothing": ("框位置平滑", "Boxpositionsglättung", "Сглаживание позиции рамки", "Lissage de position de boîte"),
    "Velocity smoothing": ("速度平滑", "Geschwindigkeitsglättung", "Сглаживание скорости", "Lissage de vitesse"),
    "Movement output": ("移动输出", "Bewegungsausgabe", "Вывод движения", "Sortie de mouvement"),
    "Activation": ("激活", "Aktivierung", "Активация", "Activation"),
    "Control": ("控制", "Steuerung", "Управление", "Contrôle"),
    "Tuning": ("微调", "Feinabstimmung", "Тонкая настройка", "Réglage"),
    "Compensation": ("补偿", "Kompensation", "Компенсация", "Compensation"),
    "Compensation calibration wizard · Premium": ("补偿校准向导 · 高级版", "Kompensationskalibrierung · Premium", "Мастер калибровки компенсации · Премиум", "Assistant d'étalonnage de compensation · Premium"),
    "Calibrate from live estimate": ("从实时估计校准", "Aus Live-Schätzung kalibrieren", "Калибровать по живой оценке", "Étalonner depuis l'estimation en direct"),
    "Transport": ("传输", "Transport", "Транспорт", "Transport"),
    "Events": ("事件", "Ereignisse", "События", "Événements"),
    "Activation source": ("激活来源", "Aktivierungsquelle", "Источник активации", "Source d'activation"),
    "Activation key": ("激活键", "Aktivierungstaste", "Клавиша активации", "Touche d'activation"),
    "Activation mode": ("激活模式", "Aktivierungsmodus", "Режим активации", "Mode d'activation"),
    "Hold activation key": ("按住激活键", "Aktivierungstaste halten", "Удерживать клавишу активации", "Maintenir la touche d'activation"),
    "Toggle activation key": ("切换激活键", "Aktivierungstaste umschalten", "Переключать клавишей активации", "Basculer avec la touche d'activation"),
    "Keyboard": ("键盘", "Tastatur", "Клавиатура", "Clavier"),
    "Xbox controller": ("Xbox 控制器", "Xbox-Controller", "Контроллер Xbox", "Manette Xbox"),
    "Keyboard or controller": ("键盘或控制器", "Tastatur oder Controller", "Клавиатура или контроллер", "Clavier ou manette"),
    "Movement gain": ("移动增益", "Bewegungsverstärkung", "Коэффициент движения", "Gain de mouvement"),
    "Maximum step": ("最大步长", "Maximaler Schritt", "Максимальный шаг", "Pas maximal"),
    "Dead zone": ("死区", "Totzone", "Мертвая зона", "Zone morte"),
    "Output smoothing": ("输出平滑", "Ausgabeglättung", "Сглаживание вывода", "Lissage de sortie"),
    "Maximum output rate": ("最大输出速率", "Maximale Ausgaberate", "Максимальная частота вывода", "Fréquence de sortie maximale"),
    "Response curve": ("响应曲线", "Reaktionskurve", "Кривая отклика", "Courbe de réponse"),
    "Linear": ("线性", "Linear", "Линейная", "Linéaire"),
    "Quadratic precision": ("二次精度", "Quadratische Präzision", "Квадратичная точность", "Précision quadratique"),
    "Motion safeguards": ("移动保护", "Bewegungsschutz", "Защита движения", "Protections de mouvement"),
    "Horizontal direction": ("水平方向", "Horizontale Richtung", "Горизонтальное направление", "Direction horizontale"),
    "Vertical direction": ("垂直方向", "Vertikale Richtung", "Вертикальное направление", "Direction verticale"),
    "Invert horizontal output": ("反转水平输出", "Horizontale Ausgabe umkehren", "Инвертировать горизонтальный вывод", "Inverser la sortie horizontale"),
    "Invert vertical output": ("反转垂直输出", "Vertikale Ausgabe umkehren", "Инвертировать вертикальный вывод", "Inverser la sortie verticale"),
    "Movement transport": ("移动传输", "Bewegungstransport", "Транспорт движения", "Transport de mouvement"),
    "Relative mouse movement": ("相对鼠标移动", "Relative Mausbewegung", "Относительное движение мыши", "Mouvement relatif de souris"),
    "Live annotated preview": ("实时标注预览", "Live-annotierte Vorschau", "Живой аннотированный просмотр", "Aperçu annoté en direct"),
    "Preview stream": ("预览流", "Vorschaustream", "Поток предпросмотра", "Flux d'aperçu"),
    "Annotations": ("标注", "Anmerkungen", "Аннотации", "Annotations"),
    "Desktop overlay": ("桌面叠加层", "Desktop-Overlay", "Наложение на рабочем столе", "Superposition de bureau"),
    "Live preview": ("实时预览", "Live-Vorschau", "Предпросмотр в реальном времени", "Aperçu en direct"),
    "Preview rate": ("预览速率", "Vorschaurate", "Частота предпросмотра", "Fréquence d'aperçu"),
    "JPEG quality": ("JPEG 质量", "JPEG-Qualität", "Качество JPEG", "Qualité JPEG"),
    "Save annotated PNG": ("保存标注 PNG", "Annotiertes PNG speichern", "Сохранить аннотированный PNG", "Enregistrer le PNG annoté"),
    "Detection overlay": ("检测叠加层", "Erkennungs-Overlay", "Наложение обнаружения", "Superposition de détection"),
    "Show annotations": ("显示标注", "Anmerkungen anzeigen", "Показывать аннотации", "Afficher les annotations"),
    "Track boxes": ("轨迹框", "Track-Boxen", "Рамки треков", "Boîtes de suivi"),
    "Show tracked boxes": ("显示跟踪框", "Verfolgte Boxen anzeigen", "Показывать рамки треков", "Afficher les boîtes suivies"),
    "Motion prediction": ("移动预测", "Bewegungsvorhersage", "Прогноз движения", "Prédiction de mouvement"),
    "Show prediction vector": ("显示预测向量", "Vorhersagevektor anzeigen", "Показывать вектор прогноза", "Afficher le vecteur de prédiction"),
    "Targeting FOV": ("目标 FOV", "Zielfeld", "Поле цели", "Champ cible"),
    "Show snap, near, and acquisition guides": ("显示吸附、近距和获取引导", "Einrast-, Nah- und Erfassungsführungen anzeigen", "Показывать направляющие привязки, ближней зоны и захвата", "Afficher les guides d'accroche, proximité et acquisition"),
    "Visible": ("可见", "Sichtbar", "Видимый", "Visible"),
    "Overlay layout": ("叠加层布局", "Overlay-Layout", "Макет наложения", "Disposition de superposition"),
    "Minimal selected target": ("最简已选目标", "Minimal ausgewähltes Ziel", "Минимальная выбранная цель", "Cible sélectionnée minimale"),
    "Standard": ("标准", "Standard", "Стандарт", "Standard"),
    "Diagnostics": ("诊断", "Diagnose", "Диагностика", "Diagnostic"),
    "Desktop overlay opacity": ("桌面叠加层不透明度", "Desktop-Overlay-Deckkraft", "Прозрачность наложения", "Opacité de superposition"),
    "Desktop overlay scale": ("桌面叠加层缩放", "Desktop-Overlay-Skalierung", "Масштаб наложения", "Échelle de superposition"),
    "Overlay colors": ("叠加层颜色", "Overlay-Farben", "Цвета наложения", "Couleurs de superposition"),
    "Track color": ("轨迹颜色", "Track-Farbe", "Цвет трека", "Couleur de suivi"),
    "Selected target color": ("选定目标颜色", "Farbe des ausgewählten Ziels", "Цвет выбранной цели", "Couleur de la cible sélectionnée"),
    "FOV and boundary color": ("FOV 与边界颜色", "Zielfeld- und Grenzfarbe", "Цвет поля и границы", "Couleur du champ et de la limite"),
    "Status text color": ("状态文本颜色", "Status-Textfarbe", "Цвет текста статуса", "Couleur du texte d'état"),
    "Vapor": ("Vapor", "Vapor", "Vapor", "Vapor"),
    "High contrast": ("高对比度", "Hoher Kontrast", "Высокий контраст", "Contraste élevé"),
    "Capture boundary": ("捕获边界", "Erfassungsgrenze", "Граница захвата", "Limite de capture"),
    "Show active capture boundary": ("显示活动捕获边界", "Aktive Erfassungsgrenze anzeigen", "Показывать границу активного захвата", "Afficher la limite de capture active"),
    "Desktop labels": ("桌面标签", "Desktop-Beschriftungen", "Метки на рабочем столе", "Étiquettes de bureau"),
    "Show track labels and confidence": ("显示轨迹标签和置信度", "Track-Beschriftungen und Konfidenz anzeigen", "Показывать метки треков и уверенность", "Afficher les étiquettes et la confiance"),
    "Desktop status": ("桌面状态", "Desktop-Status", "Статус на рабочем столе", "État de bureau"),
    "Show provider, rate, and lock state": ("显示提供程序、速率和锁定状态", "Anbieter, Rate und Sperrstatus anzeigen", "Показывать поставщика, скорость и фиксацию", "Afficher fournisseur, débit et verrouillage"),
    "Desktop motion guide": ("桌面移动引导", "Desktop-Bewegungsführung", "Направляющая движения", "Guide de mouvement de bureau"),
    "Show selected anchor and prediction vector": ("显示所选锚点和预测向量", "Ausgewählten Anker und Vorhersagevektor anzeigen", "Показывать выбранный якорь и вектор прогноза", "Afficher l'ancre choisie et le vecteur de prédiction"),
    "YOLO dataset capture": ("YOLO 数据集捕获", "YOLO-Datensatzerfassung", "Сбор набора данных YOLO", "Capture de jeu de données YOLO"),
    "Dataset folder": ("数据集文件夹", "Datensatzordner", "Папка набора данных", "Dossier du jeu de données"),
    "Capture interval": ("捕获间隔", "Erfassungsintervall", "Интервал захвата", "Intervalle de capture"),
    "Label confidence": ("标签置信度", "Label-Konfidenz", "Уверенность метки", "Confiance d'étiquette"),
    "Label policy": ("标签策略", "Label-Richtlinie", "Политика меток", "Politique d'étiquettes"),
    "Selected track only": ("仅选定轨迹", "Nur ausgewählter Track", "Только выбранный трек", "Suivi sélectionné uniquement"),
    "Only write the current selected track": ("仅写入当前选定轨迹", "Nur aktuellen ausgewählten Track schreiben", "Записывать только выбранный трек", "Écrire uniquement le suivi sélectionné"),
    "Skip unlabeled frames": ("跳过无标签帧", "Unbeschriftete Frames überspringen", "Пропускать кадры без меток", "Ignorer les images sans étiquette"),
    "Require at least one valid label": ("至少需要一个有效标签", "Mindestens ein gültiges Label verlangen", "Требовать хотя бы одну действительную метку", "Exiger au moins une étiquette valide"),
    "Current profile": ("当前配置文件", "Aktuelles Profil", "Текущий профиль", "Profil actuel"),
    "Profile name": ("配置文件名称", "Profilname", "Имя профиля", "Nom du profil"),
    "Open another profile": ("打开其他配置文件", "Anderes Profil öffnen", "Открыть другой профиль", "Ouvrir un autre profil"),
    "Open profile…": ("打开配置文件…", "Profil öffnen…", "Открыть профиль…", "Ouvrir un profil…"),
    "Save a copy": ("保存副本", "Kopie speichern", "Сохранить копию", "Enregistrer une copie"),
    "Save profile as…": ("配置文件另存为…", "Profil speichern unter…", "Сохранить профиль как…", "Enregistrer le profil sous…"),
    "Reset configuration": ("重置配置", "Konfiguration zurücksetzen", "Сбросить конфигурацию", "Réinitialiser la configuration"),
    "Reset to defaults": ("恢复默认值", "Auf Standardwerte zurücksetzen", "Сбросить по умолчанию", "Réinitialiser par défaut"),
    "Global controls - Premium": ("全局控制 - 高级版", "Globale Steuerung - Premium", "Глобальное управление - Premium", "Commandes globales - Premium"),
    "Start or stop capture": ("开始或停止捕获", "Erfassung starten oder stoppen", "Запустить или остановить захват", "Démarrer ou arrêter la capture"),
    "Show or hide desktop overlay": ("显示或隐藏桌面叠加层", "Desktop-Overlay ein- oder ausblenden", "Показать или скрыть наложение", "Afficher ou masquer la superposition"),
    "Emergency stop": ("紧急停止", "Notstopp", "Аварийная остановка", "Arrêt d'urgence"),
    "Switch to next profile": ("切换到下一个配置文件", "Zum nächsten Profil wechseln", "Перейти к следующему профилю", "Passer au profil suivant"),
    "Restore default shortcuts": ("恢复默认快捷键", "Standardtastenkürzel wiederherstellen", "Восстановить сочетания по умолчанию", "Restaurer les raccourcis par défaut"),
    "Restore defaults": ("恢复默认", "Standardwerte wiederherstellen", "Восстановить по умолчанию", "Restaurer par défaut"),
    "Profile hotkeys · Premium": ("配置文件快捷键 · 高级版", "Profil-Hotkeys · Premium", "Горячие клавиши профиля · Премиум", "Raccourcis de profil · Premium"),
    "No profile assigned": ("未分配配置文件", "Kein Profil zugewiesen", "Профиль не назначен", "Aucun profil attribué"),
    "Local control API · Premium": ("本地控制 API · 高级版", "Lokale Steuerungs-API · Premium", "Локальный API управления · Премиум", "API de contrôle local · Premium"),
    "Local API": ("本地 API", "Lokale API", "Локальный API", "API local"),
    "LAN access": ("局域网访问", "LAN-Zugriff", "Доступ по локальной сети", "Accès réseau local"),
    "TCP port": ("TCP 端口", "TCP-Port", "TCP-порт", "Port TCP"),
    "Access token": ("访问令牌", "Zugriffstoken", "Токен доступа", "Jeton d'accès"),
    "Bearer token": ("Bearer 令牌", "Bearer-Token", "Bearer-токен", "Jeton Bearer"),
    "Copy": ("复制", "Kopieren", "Копировать", "Copier"),
    "Regenerate": ("重新生成", "Neu erstellen", "Пересоздать", "Régénérer"),
    "Available endpoints": ("可用端点", "Verfügbare Endpunkte", "Доступные конечные точки", "Points de terminaison disponibles"),
    "Live metrics": ("实时指标", "Live-Metriken", "Текущие метрики", "Mesures en direct"),
    "Inference time": ("推理时间", "Inferenzzeit", "Время инференса", "Temps d'inférence"),
    "Preprocess time": ("预处理时间", "Vorverarbeitungszeit", "Время предобработки", "Temps de prétraitement"),
    "Model execution": ("模型执行", "Modellausführung", "Выполнение модели", "Exécution du modèle"),
    "Local performance history - Premium": ("本地性能历史 - 高级版", "Lokaler Leistungsverlauf - Premium", "Локальная история производительности - Premium", "Historique local des performances - Premium"),
    "Clear history": ("清除历史", "Verlauf löschen", "Очистить историю", "Effacer l'historique"),
    "Anonymous aggregate statistics": ("匿名汇总统计", "Anonyme Sammelstatistiken", "Анонимная агрегированная статистика", "Statistiques agrégées anonymes"),
    "Engine log": ("引擎日志", "Engine-Protokoll", "Журнал движка", "Journal du moteur"),
    "Copy diagnostics": ("复制诊断", "Diagnose kopieren", "Копировать диагностику", "Copier le diagnostic"),
    "Clear log": ("清除日志", "Protokoll löschen", "Очистить журнал", "Effacer le journal"),
    "Test signal": ("测试信号", "Testsignal", "Тестовый сигнал", "Signal de test"),
    "Model selected: {model} · {provider}": ("已选择模型：{model} · {provider}", "Modell ausgewählt: {model} · {provider}", "Модель выбрана: {model} · {provider}", "Modèle sélectionné : {model} · {provider}"),
    "Selected model file is unavailable": ("所选模型文件不可用", "Ausgewählte Modelldatei ist nicht verfügbar", "Выбранный файл модели недоступен", "Le fichier modèle sélectionné est indisponible"),
    "Test signal mode: no ONNX model selected": ("测试信号模式：未选择 ONNX 模型", "Testsignalmodus: kein ONNX-Modell ausgewählt", "Режим тестового сигнала: модель ONNX не выбрана", "Mode signal de test : aucun modèle ONNX sélectionné"),
    "unsaved changes": ("未保存的更改", "ungespeicherte Änderungen", "несохраненные изменения", "modifications non enregistrées"),
    "live changes applied": ("已应用实时更改", "Live-Änderungen angewendet", "изменения применены в реальном времени", "modifications appliquées en direct"),
    "changes apply next session": ("更改将在下次会话应用", "Änderungen gelten in der nächsten Sitzung", "изменения применятся в следующем сеансе", "modifications appliquées à la prochaine session"),
}

# 0.0.9 completes the visible control vocabulary introduced by the advanced
# 0.0.8 workspaces. Longer technical diagnostics intentionally remain in the
# source language so model names, paths, provider output, and recovery details
# are never mistranslated or made harder to share with support.
_ROWS.update({
    "Back": ("上一步", "Zurück", "Назад", "Retour"),
    "Next": ("下一步", "Weiter", "Далее", "Suivant"),
    "Cancel": ("取消", "Abbrechen", "Отмена", "Annuler"),
    "Apply": ("应用", "Anwenden", "Применить", "Appliquer"),
    "Open": ("打开", "Öffnen", "Открыть", "Ouvrir"),
    "Close": ("关闭", "Schließen", "Закрыть", "Fermer"),
    "Yes": ("是", "Ja", "Да", "Oui"),
    "No": ("否", "Nein", "Нет", "Non"),
    "Automatic": ("自动", "Automatisch", "Автоматически", "Automatique"),
    "Hold": ("按住", "Halten", "Удержание", "Maintenir"),
    "Toggle": ("切换", "Umschalten", "Переключение", "Basculer"),
    "Hold key": ("按住按键", "Taste halten", "Удерживать клавишу", "Maintenir la touche"),
    "Observe only": ("仅观察", "Nur beobachten", "Только наблюдение", "Observation seule"),
    "Stable": ("稳定", "Stabil", "Стабильно", "Stable"),
    "Balanced": ("均衡", "Ausgewogen", "Сбалансировано", "Équilibré"),
    "Responsive": ("灵敏", "Reaktionsschnell", "Быстрый отклик", "Réactif"),
    "Search settings": ("搜索设置", "Einstellungen suchen", "Поиск настроек", "Rechercher un réglage"),
    "Search settings (Ctrl+K)": ("搜索设置 (Ctrl+K)", "Einstellungen suchen (Ctrl+K)", "Поиск настроек (Ctrl+K)", "Rechercher un réglage (Ctrl+K)"),
    "Undo last tuning change": ("撤销上次调整", "Letzte Änderung rückgängig", "Отменить последнюю настройку", "Annuler le dernier réglage"),
    "Save profile": ("保存配置", "Profil speichern", "Сохранить профиль", "Enregistrer le profil"),
    "Run setup & calibration": ("运行设置与校准", "Einrichtung und Kalibrierung", "Настройка и калибровка", "Configurer et calibrer"),
    "Welcome": ("欢迎", "Willkommen", "Добро пожаловать", "Bienvenue"),
    "Model & engine": ("模型与引擎", "Modell und Engine", "Модель и движок", "Modèle et moteur"),
    "Activation binding": ("激活绑定", "Aktivierungsbelegung", "Привязка активации", "Commande d’activation"),
    "Calibration": ("校准", "Kalibrierung", "Калибровка", "Calibration"),
    "Step {current} of {total}": ("第 {current} 步，共 {total} 步", "Schritt {current} von {total}", "Шаг {current} из {total}", "Étape {current} sur {total}"),
    "Apply setup": ("应用设置", "Einrichtung anwenden", "Применить настройки", "Appliquer la configuration"),
    "Vapor Lock setup & calibration": ("Vapor Lock 设置与校准", "Vapor Lock Einrichtung und Kalibrierung", "Настройка и калибровка Vapor Lock", "Configuration et calibration de Vapor Lock"),
    "VAPOR LOCK 0.1 / INITIALIZATION": ("VAPOR LOCK 0.1 / 初始化", "VAPOR LOCK 0.1 / EINRICHTUNG", "VAPOR LOCK 0.1 / НАСТРОЙКА", "VAPOR LOCK 0.1 / INITIALISATION"),
    "No model selected / test signal": ("未选择模型 / 测试信号", "Kein Modell / Testsignal", "Модель не выбрана / тестовый сигнал", "Aucun modèle / signal de test"),
    "Keep shipped profile tuning": ("保留内置配置调整", "Mitgelieferte Abstimmung behalten", "Сохранить исходные настройки", "Conserver les réglages fournis"),
    "Invert horizontal": ("反转水平轴", "Horizontal umkehren", "Инвертировать горизонталь", "Inverser l’axe horizontal"),
    "Invert vertical": ("反转垂直轴", "Vertikal umkehren", "Инвертировать вертикаль", "Inverser l’axe vertical"),
    "I have reviewed and confirmed the activation binding above": ("我已检查并确认上述激活绑定", "Ich habe die Aktivierungsbelegung geprüft und bestätigt", "Я проверил и подтвердил привязку активации", "J’ai vérifié et confirmé la commande d’activation ci-dessus"),
    "This one-time setup prepares a usable starting profile. Nothing here starts capture, sends movement, or downloads a component without your choice.": ("首次设置准备可用的初始配置。未经您的选择，不会开始捕获、发送移动或下载组件。", "Diese Ersteinrichtung erstellt ein nutzbares Startprofil. Ohne Ihre Auswahl werden weder Aufnahme noch Bewegung oder Komponentendownload gestartet.", "Начальная настройка создаёт рабочий профиль. Без вашего выбора захват, движение и загрузка компонентов не запускаются.", "Cette configuration initiale prépare un profil utilisable. Rien ne démarre la capture, n’envoie de mouvement ou ne télécharge de composant sans votre choix."),
    "You will choose the inference path, capture area, activation binding, and a movement calibration preset. The wizard is included in both Free and Premium and can be run again from Overview or its shortcut.": ("您将选择推理路径、捕获区域、激活绑定和移动校准预设。Free 与 Premium 均包含向导，可在概览或快捷键中再次运行。", "Sie wählen Verarbeitung, Aufnahmebereich, Aktivierungsbelegung und Bewegungspreset. Der Assistent ist in Free und Premium enthalten und über Übersicht oder Shortcut erneut verfügbar.", "Вы выберете обработку, область захвата, привязку активации и пресет движения. Мастер доступен в Free и Premium из обзора или по сочетанию клавиш.", "Vous choisirez le traitement, la zone de capture, la commande d’activation et un préréglage de mouvement. L’assistant est inclus dans Free et Premium et reste accessible depuis la vue d’ensemble ou son raccourci."),
    "Choose the model and processing path used when a session starts.": ("选择会话开始时使用的模型与处理路径。", "Modell und Verarbeitung beim Sitzungsstart wählen.", "Выберите модель и обработку для начала сеанса.", "Choisissez le modèle et le traitement utilisés au démarrage de la session."),
    "Models already in the local models folder are available here.": ("此处可选本地模型文件夹中的模型。", "Modelle aus dem lokalen Modellordner sind hier verfügbar.", "Здесь доступны модели из локальной папки.", "Les modèles du dossier local sont disponibles ici."),
    "A missing optional runtime is offered after setup; the wizard itself stays responsive.": ("设置后可安装缺失的可选运行时；向导保持响应。", "Fehlende optionale Laufzeiten werden nach der Einrichtung angeboten; der Assistent bleibt bedienbar.", "Недостающая среда предлагается после настройки; мастер остаётся отзывчивым.", "Une dépendance facultative manquante est proposée après la configuration ; l’assistant reste réactif."),
    "Set the initial display, centered capture size, and rate limits.": ("设置初始显示器、居中捕获尺寸与速率限制。", "Anzeige, mittige Aufnahmegröße und Ratenlimits festlegen.", "Задайте экран, размер центрального захвата и частоты.", "Réglez l’écran, la taille de capture centrée et les limites de fréquence."),
    "Centered region": ("居中区域", "Mittiger Bereich", "Центральная область", "Zone centrée"),
    "Rate limits": ("速率限制", "Ratenlimits", "Ограничения частоты", "Limites de fréquence"),
    "The native engine captures only the selected centered region.": ("原生引擎仅捕获选定的居中区域。", "Die native Engine erfasst nur den gewählten mittigen Bereich.", "Движок захватывает только выбранную центральную область.", "Le moteur natif capture uniquement la zone centrée choisie."),
    "This starts at offset 0, 0 and remains editable in Targeting.": ("初始偏移为 0, 0，可在目标选择中继续修改。", "Beginnt bei Versatz 0, 0 und bleibt unter Zielauswahl bearbeitbar.", "Начальное смещение 0, 0; его можно изменить в выборе цели.", "Le décalage initial est 0, 0 et reste modifiable dans Ciblage."),
    "These are limits, not promised throughput, and can be typed above 120.": ("这些是上限，不保证实际吞吐量，可输入大于 120 的值。", "Dies sind Limits, keine zugesicherte Leistung; Werte über 120 können eingegeben werden.", "Это ограничения, а не гарантированная скорость; можно вводить значения выше 120.", "Ce sont des limites, pas un débit garanti ; vous pouvez saisir plus de 120."),
    "Movement and calibration remain inert until you confirm how they are authorized.": ("确认授权方式前，移动与校准保持禁用。", "Bewegung und Kalibrierung bleiben bis zur bestätigten Freigabe inaktiv.", "Движение и калибровка не действуют до подтверждения авторизации.", "Mouvement et calibration restent inactifs tant que leur autorisation n’est pas confirmée."),
    "Choose which input can authorize movement output.": ("选择可授权移动输出的输入。", "Eingabe für die Bewegungsfreigabe wählen.", "Выберите ввод, разрешающий движение.", "Choisissez l’entrée qui autorise le mouvement."),
    "Hold is the safer initial behavior; toggle remains available.": ("按住是更稳妥的初始模式；也可选择切换。", "Halten ist der vorsichtigere Start; Umschalten bleibt verfügbar.", "Для начала безопаснее удержание; переключение также доступно.", "Maintenir est le choix initial plus prudent ; Basculer reste disponible."),
    "Keyboard binding": ("键盘绑定", "Tastaturbelegung", "Привязка клавиатуры", "Commande du clavier"),
    "Used by Keyboard and Keyboard-or-controller modes.": ("用于键盘与键盘或控制器模式。", "Für Tastatur und Tastatur-oder-Controller.", "Для режимов клавиатуры и клавиатуры/контроллера.", "Utilisée par les modes Clavier et Clavier ou contrôleur."),
    "Used by Controller and Keyboard-or-controller modes.": ("用于控制器与键盘或控制器模式。", "Für Controller und Tastatur-oder-Controller.", "Для режимов контроллера и клавиатуры/контроллера.", "Utilisée par les modes Contrôleur et Clavier ou contrôleur."),
    "Calibration never begins automatically. The shortcut below opens this wizard; applying a preset only changes profile values.": ("校准不会自动开始。下方快捷键打开向导；应用预设仅更改配置值。", "Kalibrierung startet nie automatisch. Der Shortcut öffnet den Assistenten; ein Preset ändert nur Profilwerte.", "Калибровка не запускается автоматически. Сочетание открывает мастер; пресет меняет только профиль.", "La calibration ne démarre jamais automatiquement. Le raccourci ouvre l’assistant ; un préréglage modifie seulement le profil."),
    "A non-empty binding is required before setup can finish.": ("设置完成前必须设置有效绑定。", "Zum Abschluss ist eine nicht leere Belegung erforderlich.", "Для завершения нужна непустая привязка.", "Une commande non vide est nécessaire pour terminer."),
    "Movement calibration": ("移动校准", "Bewegungskalibrierung", "Калибровка движения", "Calibration du mouvement"),
    "Stable favors settling, Balanced is a general baseline, and Responsive favors quicker correction.": ("稳定侧重平稳停止，均衡提供通用基准，灵敏侧重快速修正。", "Stabil bevorzugt Einregeln, Ausgewogen ist die allgemeine Basis, Reaktionsschnell korrigiert schneller.", "Стабильно предпочитает успокоение, сбалансировано — базовый режим, быстрый отклик — быструю коррекцию.", "Stable privilégie la stabilisation, Équilibré offre une base générale et Réactif une correction rapide."),
    "Axis direction": ("轴方向", "Achsrichtung", "Направление осей", "Direction des axes"),
    "Leave both off unless the destination interprets an axis in reverse.": ("除非目标程序反向解释轴，否则均保持关闭。", "Beide auslassen, sofern das Ziel eine Achse nicht umgekehrt interpretiert.", "Оставьте выключенными, если получатель не инвертирует ось.", "Laissez les deux désactivés sauf si la destination inverse un axe."),
    "Confirm the activation binding before calibration.": ("校准前确认激活绑定。", "Aktivierungsbelegung vor der Kalibrierung bestätigen.", "Подтвердите привязку перед калибровкой.", "Confirmez la commande d’activation avant la calibration."),
    "Choose a shortcut before applying setup.": ("应用设置前选择快捷键。", "Vor dem Anwenden einen Shortcut wählen.", "Выберите сочетание перед применением.", "Choisissez un raccourci avant d’appliquer la configuration."),
    "Show optional update notices": ("显示可选更新通知", "Optionale Updatehinweise anzeigen", "Показывать уведомления об обновлениях", "Afficher les mises à jour facultatives"),
    "Automatically install verified updates": ("自动安装已验证更新", "Verifizierte Updates automatisch installieren", "Автоматически устанавливать проверенные обновления", "Installer automatiquement les mises à jour vérifiées"),
    "New box class": ("新框类别", "Klasse der neuen Box", "Класс новой рамки", "Classe du nouveau cadre"),
    "New class name": ("新类别名称", "Neuer Klassenname", "Имя нового класса", "Nom de la nouvelle classe"),
    "Save labels": ("保存标签", "Labels speichern", "Сохранить метки", "Enregistrer les étiquettes"),
    "Approve + next": ("批准并下一张", "Bestätigen + weiter", "Одобрить и далее", "Approuver et suivant"),
    "Assign selected": ("分配所选项", "Auswahl zuweisen", "Назначить выбранное", "Attribuer la sélection"),
    "Delete selected box": ("删除所选框", "Ausgewählte Box löschen", "Удалить выбранную рамку", "Supprimer le cadre sélectionné"),
    "Image zoom": ("图像缩放", "Bildzoom", "Масштаб изображения", "Zoom de l’image"),
    "Fit image": ("适合图像", "Bild einpassen", "Вписать изображение", "Adapter l’image"),
    "Zoom review image in": ("放大审核图像", "Prüfbild vergrößern", "Увеличить изображение проверки", "Agrandir l’image à vérifier"),
    "Zoom review image out": ("缩小审核图像", "Prüfbild verkleinern", "Уменьшить изображение проверки", "Réduire l’image à vérifier"),
    "Export public API certificate": ("导出公共 API 证书", "Öffentliches API-Zertifikat exportieren", "Экспортировать публичный сертификат API", "Exporter le certificat public de l’API"),
    "Optional preferred class IDs, for example: 0, 2": ("可选的首选类别 ID，例如：0, 2", "Optionale bevorzugte Klassen-IDs, z. B. 0, 2", "Предпочтительные ID классов, например 0, 2", "IDs de classes préférées facultatifs, par exemple : 0, 2"),
    "All classes (or: 0, 2, 5)": ("所有类别（或：0, 2, 5）", "Alle Klassen (oder: 0, 2, 5)", "Все классы (или: 0, 2, 5)", "Toutes les classes (ou : 0, 2, 5)"),
    "No saved preset": ("无已保存预设", "Kein gespeichertes Preset", "Нет сохранённого пресета", "Aucun préréglage enregistré"),
    "Available in Vapor Lock Premium": ("Vapor Lock Premium 可用", "In Vapor Lock Premium verfügbar", "Доступно в Vapor Lock Premium", "Disponible dans Vapor Lock Premium"),
    "Click once": ("单次点击", "Einmal klicken", "Один щелчок", "Cliquer une fois"),
    "Click repeatedly while inside": ("位于区域内时重复点击", "Innerhalb wiederholt klicken", "Повторять щелчки внутри", "Cliquer à répétition à l’intérieur"),
    "Hold while inside": ("位于区域内时按住", "Innerhalb halten", "Удерживать внутри", "Maintenir à l’intérieur"),
    "Quickly load a saved profile from any page.": ("在任意页面快速加载已保存配置。", "Gespeichertes Profil von jeder Seite schnell laden.", "Быстро загрузить сохранённый профиль с любой страницы.", "Charger rapidement un profil enregistré depuis n’importe quelle page."),
    "Local session status": ("本地会话状态", "Lokaler Sitzungsstatus", "Состояние локального сеанса", "État de la session locale"),
    "Local session": ("本地会话", "Lokale Sitzung", "Локальный сеанс", "Session locale"),
    "Session mode": ("会话模式", "Sitzungsmodus", "Режим сеанса", "Mode de session"),
    "Quick profile": ("快速配置", "Schnellprofil", "Быстрый профиль", "Profil rapide"),
    "Collection": ("采集", "Sammlung", "Сбор", "Collecte"),
    "Review": ("审核", "Prüfung", "Проверка", "Révision"),
    "Training": ("训练", "Training", "Обучение", "Entraînement"),
    "LOCAL ULTRALYTICS RUNTIME": ("本地 ULTRALYTICS 运行时", "LOKALE ULTRALYTICS-LAUFZEIT", "ЛОКАЛЬНАЯ СРЕДА ULTRALYTICS", "RUNTIME ULTRALYTICS LOCAL"),
    "Training runtime": ("训练运行时", "Trainingslaufzeit", "Среда обучения", "Runtime d'entraînement"),
    "Install training runtime": ("安装训练运行时", "Trainingslaufzeit installieren", "Установить среду обучения", "Installer le runtime d'entraînement"),
    "Check runtime": ("检查运行时", "Laufzeit prüfen", "Проверить среду", "Vérifier le runtime"),
    "RUN SETUP": ("运行设置", "AUSFÜHRUNG EINRICHTEN", "НАСТРОЙКА ЗАПУСКА", "CONFIGURATION DE L'EXÉCUTION"),
    "Base model": ("基础模型", "Basismodell", "Базовая модель", "Modèle de base"),
    "Prepared dataset": ("准备好的数据集", "Vorbereiteter Datensatz", "Подготовленный набор данных", "Jeu de données préparé"),
    "Run name": ("运行名称", "Ausführungsname", "Имя запуска", "Nom de l'exécution"),
    "Platform project": ("平台项目", "Plattformprojekt", "Проект платформы", "Projet de plateforme"),
    "Platform API key": ("平台 API 密钥", "Plattform-API-Schlüssel", "API-ключ платформы", "Clé API de plateforme"),
    "Training preflight": ("训练预检", "Trainingsvorprüfung", "Предпроверка обучения", "Précontrôle d'entraînement"),
    "Run training preflight": ("运行训练预检", "Trainingsvorprüfung ausführen", "Запустить пред-проверку", "Lancer le précontrôle"),
    "TRAINING CONTROLS": ("训练控制", "TRAININGSSTEUERUNG", "НАСТРОЙКИ ОБУЧЕНИЯ", "COMMANDES D'ENTRAÎNEMENT"),
    "Epochs": ("训练轮次", "Epochen", "Эпохи", "Époques"),
    "Image size": ("图像尺寸", "Bildgröße", "Размер изображения", "Taille d'image"),
    "Batch": ("批次", "Stapel", "Пакет", "Lot"),
    "Training device": ("训练设备", "Trainingsgerät", "Устройство обучения", "Périphérique d'entraînement"),
    "Data workers": ("数据工作线程", "Daten-Worker", "Рабочие данных", "Workers de données"),
    "Early-stop patience": ("早停耐心值", "Frühstopp-Geduld", "Раннее завершение", "Patience d'arrêt anticipé"),
    "Dataset cache": ("数据集缓存", "Datensatz-Cache", "Кэш набора данных", "Cache du jeu de données"),
    "Optimizer": ("优化器", "Optimierer", "Оптимизатор", "Optimiseur"),
    "OPTIMIZATION": ("优化", "OPTIMIERUNG", "ОПТИМИЗАЦИЯ", "OPTIMISATION"),
    "Initial learning rate": ("初始学习率", "Anfängliche Lernrate", "Начальная скорость обучения", "Taux d'apprentissage initial"),
    "Final learning-rate factor": ("最终学习率系数", "End-Lernratenfaktor", "Конечный множитель скорости", "Facteur du taux final"),
    "Weight decay": ("权重衰减", "Gewichtsabnahme", "Затухание весов", "Décroissance des poids"),
    "Warmup epochs": ("预热轮次", "Aufwärmepochen", "Эпохи прогрева", "Époques de préchauffage"),
    "Close mosaic": ("关闭拼贴增强", "Mosaik schließen", "Отключить мозаику", "Désactiver la mosaïque"),
    "Dataset fraction": ("数据集比例", "Datensatzanteil", "Доля набора данных", "Fraction du jeu de données"),
    "Mixed precision": ("混合精度", "Gemischte Präzision", "Смешанная точность", "Précision mixte"),
    "Pretrained weights": ("预训练权重", "Vortrainierte Gewichte", "Предобученные веса", "Poids préentraînés"),
    "Cosine schedule": ("余弦计划", "Kosinusplan", "Косинусный график", "Planification cosinus"),
    "Deterministic run": ("确定性运行", "Deterministische Ausführung", "Детерминированный запуск", "Exécution déterministe"),
    "Export ONNX": ("导出 ONNX", "ONNX exportieren", "Экспортировать ONNX", "Exporter ONNX"),
    "Dynamic ONNX": ("动态 ONNX", "Dynamisches ONNX", "Динамический ONNX", "ONNX dynamique"),
    "Random seed": ("随机种子", "Zufallsstartwert", "Случайное начальное значение", "Graine aléatoire"),
    "Additional settings": ("附加设置", "Zusätzliche Einstellungen", "Дополнительные настройки", "Paramètres supplémentaires"),
    "Start training": ("开始训练", "Training starten", "Начать обучение", "Démarrer l'entraînement"),
    "Start local training": ("开始本地训练", "Lokales Training starten", "Начать локальное обучение", "Démarrer l'entraînement local"),
    "Stop training": ("停止训练", "Training stoppen", "Остановить обучение", "Arrêter l'entraînement"),
    "DATASET REVIEW QUEUE": ("数据集审核队列", "DATENSATZ-PRÜFWARTESCHLANGE", "ОЧЕРЕДЬ ПРОВЕРКИ НАБОРА ДАННЫХ", "FILE DE RÉVISION DU JEU DE DONNÉES"),
    "Review actions": ("审核操作", "Prüfaktionen", "Действия проверки", "Actions de révision"),
    "Refresh queue": ("刷新队列", "Warteschlange aktualisieren", "Обновить очередь", "Actualiser la file"),
    "Mark accepted": ("标记为接受", "Als akzeptiert markieren", "Пометить как принятое", "Marquer comme accepté"),
    "Exclude from training": ("从训练中排除", "Vom Training ausschließen", "Исключить из обучения", "Exclure de l'entraînement"),
    "Edit labels": ("编辑标签", "Beschriftungen bearbeiten", "Редактировать метки", "Modifier les étiquettes"),
    "Clear decision": ("清除决定", "Entscheidung löschen", "Очистить решение", "Effacer la décision"),
    "Source capture": ("源采集", "Quellerfassung", "Сбор исходных кадров", "Capture source"),
    "Capture gate": ("采集门控", "Erfassungsgate", "Условие захвата", "Condition de capture"),
    "Only while transport activation is active": ("仅在传输激活时", "Nur bei aktiver Ausgabeaktivierung", "Только при активном выводе", "Uniquement lorsque l'activation de sortie est active"),
    "BACKGROUND ANNOTATION": ("后台标注", "HINTERGRUNDBESCHRIFTUNG", "ФОНОВАЯ РАЗМЕТКА", "ANNOTATION EN ARRIÈRE-PLAN"),
    "Annotation model": ("标注模型", "Beschriftungsmodell", "Модель разметки", "Modèle d'annotation"),
    "Agreement passes": ("一致性次数", "Übereinstimmungsdurchläufe", "Проходы согласования", "Passages de concordance"),
    "Annotation confidence": ("标注置信度", "Beschriftungskonfidenz", "Уверенность разметки", "Confiance d'annotation"),
    "Agreement overlap": ("一致性重叠", "Übereinstimmungsüberlappung", "Перекрытие согласования", "Chevauchement de concordance"),
    "Existing labels": ("现有标签", "Vorhandene Beschriftungen", "Существующие метки", "Étiquettes existantes"),
    "Replace existing labels": ("替换现有标签", "Vorhandene Beschriftungen ersetzen", "Заменить существующие метки", "Remplacer les étiquettes existantes"),
    "Run annotation": ("运行标注", "Beschriftung ausführen", "Запустить разметку", "Lancer l'annotation"),
    "Annotate captured images": ("标注已采集图像", "Erfasste Bilder beschriften", "Разметить собранные изображения", "Annoter les images capturées"),
    "ULTRALYTICS ARCHIVE": ("ULTRALYTICS 归档", "ULTRALYTICS-ARCHIV", "АРХИВ ULTRALYTICS", "ARCHIVE ULTRALYTICS"),
    "Validation split": ("验证集划分", "Validierungsaufteilung", "Разделение валидации", "Répartition de validation"),
    "Dataset archive": ("数据集归档", "Datensatzarchiv", "Архив набора данных", "Archive du jeu de données"),
    "Build Ultralytics ZIP": ("构建 Ultralytics ZIP", "Ultralytics-ZIP erstellen", "Создать ZIP Ultralytics", "Créer le ZIP Ultralytics"),
    "Background task": ("后台任务", "Hintergrundaufgabe", "Фоновая задача", "Tâche en arrière-plan"),
    "Stop background task": ("停止后台任务", "Hintergrundaufgabe stoppen", "Остановить фоновую задачу", "Arrêter la tâche en arrière-plan"),
    "ON-SCREEN OVERLAY · PREMIUM": ("屏幕叠加层 · 高级版", "BILDSCHIRM-OVERLAY · PREMIUM", "ЭКРАННЫЙ ОВЕРЛЕЙ · ПРЕМИУМ", "SUPERPOSITION À L'ÉCRAN · PREMIUM"),
    "Click-through desktop overlay": ("点击穿透桌面叠加层", "Klickdurchlässiges Desktop-Overlay", "Кликабельный сквозной оверлей", "Superposition de bureau traversante"),
    "Segmentation masks": ("分割掩码", "Segmentierungsmasken", "Маски сегментации", "Masques de segmentation"),
    "Show instance masks": ("显示实例掩码", "Instanzmasken anzeigen", "Показывать маски экземпляров", "Afficher les masques d'instance"),
    "Mask limit": ("掩码上限", "Maskenlimit", "Лимит масок", "Limite de masques"),
    "Mask threshold": ("掩码阈值", "Maskenschwelle", "Порог маски", "Seuil de masque"),
    "LIVE OUTPUT PATH": ("实时输出路径", "LIVE-AUSGABEPFAD", "ПУТЬ ВЫВОДА В РЕАЛЬНОМ ВРЕМЕНИ", "CHEMIN DE SORTIE EN DIRECT"),
    "Output decision": ("输出决策", "Ausgabeentscheidung", "Решение вывода", "Décision de sortie"),
    "SNAP-RADIUS EVENT ACTION · PREMIUM": ("吸附半径事件动作 · 高级版", "EINRAST-RADIUS-EREIGNIS · PREMIUM", "ДЕЙСТВИЕ В РАДИУСЕ ПРИВЯЗКИ · ПРЕМИУМ", "ACTION D'ÉVÉNEMENT DE RAYON D'ACCROCHE · PREMIUM"),
    "Mouse event": ("鼠标事件", "Mausereignis", "Событие мыши", "Événement souris"),
    "Activation gate": ("激活门控", "Aktivierungsgate", "Условие активации", "Condition d'activation"),
    "Require aim activation": ("需要瞄准激活", "Zielaktivierung erforderlich", "Требовать активацию наведения", "Exiger l'activation de visée"),
    "Action mode": ("动作模式", "Aktionsmodus", "Режим действия", "Mode d'action"),
    "Mouse button": ("鼠标按钮", "Maustaste", "Кнопка мыши", "Bouton de souris"),
    "Click cooldown": ("点击冷却", "Klickabklingzeit", "Задержка клика", "Délai entre clics"),
    "OUTPUT TRANSPORT": ("输出传输", "AUSGABEÜBERTRAGUNG", "ПЕРЕДАЧА ВЫВОДА", "TRANSPORT DE SORTIE"),
    "Controller output opt-in": ("控制器输出选择加入", "Controller-Ausgabe aktivieren", "Включение вывода контроллера", "Activation de sortie manette"),
    "Enable Xbox-compatible controller output": ("启用兼容 Xbox 的控制器输出", "Xbox-kompatible Controller-Ausgabe aktivieren", "Включить вывод совместимого Xbox-контроллера", "Activer la sortie manette compatible Xbox"),
    "Controller route": ("控制器路径", "Controller-Route", "Маршрут контроллера", "Itinéraire de la manette"),
    "Controller output stick": ("控制器输出摇杆", "Controller-Ausgabe-Stick", "Стик вывода контроллера", "Stick de sortie de la manette"),
    "Controller output strength": ("控制器输出强度", "Controller-Ausgabestärke", "Сила вывода контроллера", "Force de sortie de la manette"),
    "Manual stick priority": ("手动摇杆优先", "Manuelle Stick-Priorität", "Приоритет ручного стика", "Priorité au stick manuel"),
    "Physical stick takes priority": ("物理摇杆优先", "Physischer Stick hat Vorrang", "Физический стик имеет приоритет", "Le stick physique est prioritaire"),
    "Controller component": ("控制器组件", "Controller-Komponente", "Компонент контроллера", "Composant de manette"),
    "Install controller component": ("安装控制器组件", "Controller-Komponente installieren", "Установить компонент контроллера", "Installer le composant de manette"),
    "Controller component removal": ("控制器组件移除", "Controller-Komponente entfernen", "Удаление компонента контроллера", "Suppression du composant de manette"),
    "Remove controller component": ("移除控制器组件", "Controller-Komponente entfernen", "Удалить компонент контроллера", "Retirer le composant de manette"),
    "Controller test": ("控制器测试", "Controller-Test", "Тест контроллера", "Test de manette"),
    "Open Windows controller test": ("打开 Windows 控制器测试", "Windows-Controller-Test öffnen", "Открыть тест контроллера Windows", "Ouvrir le test de manette Windows"),
    "HARDWARE MOVEMENT ADAPTERS": ("硬件移动适配器", "HARDWARE-BEWEGUNGSADAPTER", "АППАРАТНЫЕ АДАПТЕРЫ ДВИЖЕНИЯ", "ADAPTATEURS MATÉRIELS DE MOUVEMENT"),
    "Serial port": ("串口", "Serieller Anschluss", "Последовательный порт", "Port série"),
    "Serial baud rate": ("串口波特率", "Serielle Baudrate", "Скорость последовательного порта", "Débit en bauds série"),
    "KMBox Net address": ("KMBox Net 地址", "KMBox-Netzwerkadresse", "Адрес KMBox Net", "Adresse KMBox Net"),
    "KMBox Net port": ("KMBox Net 端口", "KMBox-Netzwerkport", "Порт KMBox Net", "Port KMBox Net"),
    "KMBox Net device code": ("KMBox Net 设备代码", "KMBox-Netzwerk-Gerätecode", "Код устройства KMBox Net", "Code d'appareil KMBox Net"),
    "KMBox A library": ("KMBox A 库", "KMBox-A-Bibliothek", "Библиотека KMBox A", "Bibliothèque KMBox A"),
    "KMBox A vendor ID": ("KMBox A 厂商 ID", "KMBox-A-Anbieter-ID", "ID производителя KMBox A", "ID fournisseur KMBox A"),
    "KMBox A product ID": ("KMBox A 产品 ID", "KMBox-A-Produkt-ID", "ID продукта KMBox A", "ID produit KMBox A"),
    "MOTION COMPENSATION · PREMIUM": ("运动补偿 · 高级版", "BEWEGUNGSKOMPENSATION · PREMIUM", "КОМПЕНСАЦИЯ ДВИЖЕНИЯ · ПРЕМИУМ", "COMPENSATION DE MOUVEMENT · PREMIUM"),
    "Correction source": ("校正来源", "Korrekturquelle", "Источник коррекции", "Source de correction"),
    "SAVED COMPENSATION LIBRARY": ("已保存补偿库", "GESPEICHERTE KOMPENSATIONSBIBLIOTHEK", "СОХРАНЕННАЯ БИБЛИОТЕКА КОМПЕНСАЦИИ", "BIBLIOTHÈQUE DE COMPENSATION ENREGISTRÉE"),
    "Library file": ("库文件", "Bibliotheksdatei", "Файл библиотеки", "Fichier de bibliothèque"),
    "Reload": ("重新加载", "Neu laden", "Перезагрузить", "Recharger"),
    "Saved preset": ("已保存预设", "Gespeicherte Vorgabe", "Сохраненный пресет", "Préréglage enregistré"),
    "Preset identifier": ("预设标识", "Vorgabenkennung", "Идентификатор пресета", "Identifiant de préréglage"),
    "Observed vertical drift": ("观测到的垂直漂移", "Beobachtete vertikale Drift", "Наблюдаемый вертикальный дрейф", "Dérive verticale observée"),
    "Observed horizontal drift": ("观测到的水平漂移", "Beobachtete horizontale Drift", "Наблюдаемый горизонтальный дрейф", "Dérive horizontale observée"),
    "Library editing": ("库编辑", "Bibliotheksbearbeitung", "Редактирование библиотеки", "Édition de bibliothèque"),
    "Add or update": ("添加或更新", "Hinzufügen oder aktualisieren", "Добавить или обновить", "Ajouter ou mettre à jour"),
    "Remove": ("移除", "Entfernen", "Удалить", "Retirer"),
    "AUTOMATIC ESTIMATE": ("自动估计", "AUTOMATISCHE SCHÄTZUNG", "АВТОМАТИЧЕСКАЯ ОЦЕНКА", "ESTIMATION AUTOMATIQUE"),
    "Minimum agreeing tracks": ("最少一致轨迹", "Minimale übereinstimmende Tracks", "Минимум согласованных треков", "Minimum de pistes concordantes"),
    "Agreement tolerance": ("一致性容差", "Übereinstimmungstoleranz", "Допуск согласования", "Tolérance de concordance"),
    "Automatic smoothing": ("自动平滑", "Automatische Glättung", "Автоматическое сглаживание", "Lissage automatique"),
    "Maximum automatic rate": ("最大自动速率", "Maximale automatische Rate", "Максимальная автоматическая частота", "Fréquence automatique maximale"),
    "Compensation strength": ("补偿强度", "Kompensationsstärke", "Сила компенсации", "Force de compensation"),
    "Waiting for timing samples": ("正在等待计时样本", "Warte auf Zeitmessproben", "Ожидание временных выборок", "En attente d'échantillons de durée"),
    "Allow other devices on this network": ("允许此网络上的其他设备", "Andere Geräte in diesem Netzwerk zulassen", "Разрешить другие устройства в этой сети", "Autoriser d'autres appareils sur ce réseau"),
    "GLOBAL CONTROLS · PREMIUM": ("全局控制 · 高级版", "GLOBALE STEUERUNG · PREMIUM", "ГЛОБАЛЬНОЕ УПРАВЛЕНИЕ · ПРЕМИУМ", "COMMANDES GLOBALES · PREMIUM"),
    "Pin or release current target": ("固定或释放当前目标", "Aktuelles Ziel anheften oder lösen", "Закрепить или отпустить текущую цель", "Épingler ou libérer la cible actuelle"),
    "Save annotated snapshot": ("保存标注快照", "Kommentierte Momentaufnahme speichern", "Сохранить размеченный снимок", "Enregistrer l'instantané annoté"),
    "Profile slot 1": ("配置文件槽位 1", "Profilplatz 1", "Слот профиля 1", "Emplacement de profil 1"),
    "Profile slot 2": ("配置文件槽位 2", "Profilplatz 2", "Слот профиля 2", "Emplacement de profil 2"),
    "Profile slot 3": ("配置文件槽位 3", "Profilplatz 3", "Слот профиля 3", "Emplacement de profil 3"),
    "Training runtime has not been checked.": ("尚未检查训练运行时。", "Die Trainingslaufzeit wurde noch nicht geprüft.", "Среда обучения еще не проверена.", "Le runtime d'entraînement n'a pas encore été vérifié."),
    "Local training is idle.": ("本地训练处于空闲状态。", "Lokales Training ist bereit.", "Локальное обучение простаивает.", "L'entraînement local est inactif."),
    "No completed local training runs yet.": ("尚无已完成的本地训练运行。", "Noch keine lokalen Trainingsläufe abgeschlossen.", "Пока нет завершенных локальных запусков обучения.", "Aucun entraînement local terminé pour l'instant."),
    "No image selected": ("未选择图像", "Kein Bild ausgewählt", "Изображение не выбрано", "Aucune image sélectionnée"),
    "No samples saved in this session.": ("此会话中未保存样本。", "In dieser Sitzung wurden keine Proben gespeichert.", "В этом сеансе не сохранено образцов.", "Aucun échantillon enregistré dans cette session."),
    "Background annotation is idle.": ("后台标注处于空闲状态。", "Hintergrundbeschriftung ist bereit.", "Фоновая разметка простаивает.", "L'annotation en arrière-plan est inactive."),
    "Start capture to receive the live annotated preview": ("开始采集以接收实时标注预览", "Erfassung starten, um die Live-Vorschau zu erhalten", "Запустите захват для живого аннотированного просмотра", "Démarrez la capture pour recevoir l'aperçu annoté en direct"),
    "Desktop overlay visibility": ("桌面叠加层可见性", "Sichtbarkeit des Desktop-Overlays", "Видимость оверлея рабочего стола", "Visibilité de la superposition de bureau"),
    "Preview snapshot · Premium": ("预览快照 · 高级版", "Vorschaumomentaufnahme · Premium", "Снимок просмотра · Премиум", "Instantané d'aperçu · Premium"),
    "No active observation session": ("没有活动观察会话", "Keine aktive Beobachtungssitzung", "Нет активного сеанса наблюдения", "Aucune session d'observation active"),
    "Browse": ("浏览", "Durchsuchen", "Обзор", "Parcourir"),
    "LIVE CONTROLLER INPUT": ("实时控制器输入", "LIVE-CONTROLLER-EINGABE", "ВВОД КОНТРОЛЛЕРА В РЕАЛЬНОМ ВРЕМЕНИ", "ENTRÉE DE MANETTE EN DIRECT"),
    "Waiting for controller input": ("正在等待控制器输入", "Warte auf Controller-Eingabe", "Ожидание ввода контроллера", "En attente d'une entrée de manette"),
    "CONTROLLER INPUT": ("控制器输入", "CONTROLLER-EINGABE", "ВВОД КОНТРОЛЛЕРА", "ENTRÉE DE MANETTE"),
    "Controller selection": ("控制器选择", "Controller-Auswahl", "Выбор контроллера", "Sélection de manette"),
    "Controller activation": ("控制器激活", "Controller-Aktivierung", "Активация контроллера", "Activation de manette"),
    "Anchor stabilization": ("锚点稳定", "Ankerstabilisierung", "Стабилизация якоря", "Stabilisation de l'ancre"),
    "Center settle radius": ("中心稳定半径", "Zentrierungsradius", "Радиус стабилизации центра", "Rayon de stabilisation au centre"),
    "PREDICTION": ("预测", "VORHERSAGE", "ПРОГНОЗ", "PRÉDICTION"),
    "MANUAL TRACK LOCK · PREMIUM": ("手动轨迹锁定 · 高级版", "MANUELLE TRACKSPERRE · PREMIUM", "РУЧНАЯ ФИКСАЦИЯ ТРЕКА · ПРЕМИУМ", "VERROUILLAGE MANUEL DE PISTE · PREMIUM"),
    "Manual target lock": ("手动目标锁定", "Manuelle Zielsperre", "Ручная фиксация цели", "Verrouillage manuel de cible"),
    "Enable manual target pinning": ("启用手动目标固定", "Manuelles Anheften des Ziels aktivieren", "Включить ручное закрепление цели", "Activer l'épinglage manuel de cible"),
    "Lock input source": ("锁定输入来源", "Sperr-Eingabequelle", "Источник ввода фиксации", "Source d'entrée du verrouillage"),
    "Keyboard lock input": ("键盘锁定输入", "Tastatur-Sperreingabe", "Ввод фиксации с клавиатуры", "Entrée de verrouillage clavier"),
    "Controller lock input": ("控制器锁定输入", "Controller-Sperreingabe", "Ввод фиксации контроллера", "Entrée de verrouillage manette"),
    "Current target action": ("当前目标操作", "Aktuelle Zielaktion", "Действие текущей цели", "Action de la cible actuelle"),
    "CLASS PRIORITIES AND ANCHORS - PREMIUM": ("类别优先级和锚点 - 高级版", "KLASSENPRIORITÄTEN UND ANKER - PREMIUM", "ПРИОРИТЕТЫ КЛАССОВ И ЯКОРЯ - ПРЕМИУМ", "PRIORITÉS DE CLASSE ET ANCRES - PREMIUM"),
    "Add class": ("添加类别", "Klasse hinzufügen", "Добавить класс", "Ajouter une classe"),
    "Remove selected": ("移除选中项", "Auswahl entfernen", "Удалить выбранное", "Supprimer la sélection"),
    "Apply rules": ("应用规则", "Regeln anwenden", "Применить правила", "Appliquer les règles"),
    "Minimum track age": ("最小轨迹年龄", "Mindestalter des Tracks", "Минимальный возраст трека", "Âge minimal de piste"),
    "Suppress overlaps across different classes": ("抑制不同类别之间的重叠", "Überlappungen zwischen verschiedenen Klassen unterdrücken", "Подавлять перекрытия между разными классами", "Supprimer les chevauchements entre classes différentes"),
    "RATE": ("速率", "RATE", "ЧАСТОТА", "DÉBIT"),
    "ONNX RUNTIME": ("ONNX 运行时", "ONNX-LAUFZEIT", "СРЕДА ONNX", "RUNTIME ONNX"),
    "AUTOMATIC": ("自动", "AUTOMATISCH", "АВТОМАТИЧЕСКИ", "AUTOMATIQUE"),
    "TRACKS": ("轨迹", "TRACKS", "ТРЕКИ", "PISTES"),
    "UNAVAILABLE": ("不可用", "NICHT VERFÜGBAR", "НЕДОСТУПНО", "INDISPONIBLE"),
    "Enabled. Reporting begins only after the native observation session is running.": ("已启用。仅在本地观察会话运行后开始报告。", "Aktiviert. Die Meldung beginnt erst, wenn die native Beobachtungssitzung läuft.", "Включено. Отчетность начинается только после запуска сеанса наблюдения.", "Activé. Le rapport commence uniquement lorsque la session d'observation native est active."),
    "Disabled. Enable Local API to start an authenticated endpoint.": ("已禁用。启用本地 API 以启动经过身份验证的端点。", "Deaktiviert. Aktivieren Sie die lokale API, um einen authentifizierten Endpunkt zu starten.", "Отключено. Включите локальный API, чтобы запустить аутентифицированную конечную точку.", "Désactivé. Activez l'API locale pour démarrer un point de terminaison authentifié."),
    "Run a preflight before training to validate labels, image pairing, space, and available NVIDIA memory.": ("训练前运行预检以验证标签、图像配对、磁盘空间和可用 NVIDIA 内存。", "Führen Sie vor dem Training eine Vorprüfung für Beschriftungen, Bildpaare, Speicherplatz und verfügbaren NVIDIA-Speicher aus.", "Перед обучением выполните проверку меток, пар изображений, места и доступной памяти NVIDIA.", "Lancez un précontrôle avant l'entraînement pour valider les étiquettes, les paires d'images, l'espace et la mémoire NVIDIA disponible."),
    "Refresh to inspect captured images and generated labels before training.": ("训练前刷新以检查采集的图像和生成的标签。", "Aktualisieren Sie vor dem Training die erfassten Bilder und erzeugten Beschriftungen.", "Обновите список, чтобы проверить собранные изображения и метки перед обучением.", "Actualisez pour examiner les images capturées et les étiquettes générées avant l'entraînement."),
    "Transport": ("传输", "Transport", "Транспорт", "Transport"),
    "Controller status appears during an active capture session.": ("控制器状态会在活动采集会话期间显示。", "Der Controller-Status erscheint während einer aktiven Erfassungssitzung.", "Состояние контроллера отображается во время активного сеанса захвата.", "L'état de la manette apparaît pendant une session de capture active."),
    "No controller binding is active for this profile. Analog sticks are intentionally ignored and never override camera controls.": ("此配置文件没有活动的控制器绑定。模拟摇杆会被有意忽略，绝不会覆盖相机控制。", "Für dieses Profil ist keine Controller-Bindung aktiv. Analoge Sticks werden absichtlich ignoriert und überschreiben niemals die Kamerasteuerung.", "Для этого профиля нет активной привязки контроллера. Аналоговые стики намеренно игнорируются и никогда не переопределяют управление камерой.", "Aucune liaison de manette n'est active pour ce profil. Les sticks analogiques sont volontairement ignorés et ne remplacent jamais les commandes de caméra."),
    "Manual target lock is waiting for a live target.": ("手动目标锁定正在等待实时目标。", "Die manuelle Zielsperre wartet auf ein Live-Ziel.", "Ручная фиксация цели ожидает активную цель.", "Le verrouillage manuel de cible attend une cible en direct."),
    "Checking global shortcut registration…": ("正在检查全局快捷键注册…", "Globale Tastenkürzel-Registrierung wird geprüft…", "Проверка регистрации глобальных сочетаний…", "Vérification de l'enregistrement des raccourcis globaux…"),
    "Controller bindings are distinct. Vapor Lock listens only for the configured button or trigger inputs; analog sticks are intentionally ignored.": ("控制器绑定彼此独立。Vapor Lock 仅监听配置的按钮或扳机输入；模拟摇杆会被有意忽略。", "Controller-Bindungen sind getrennt. Vapor Lock hört nur auf konfigurierte Tasten- oder Trigger-Eingaben; analoge Sticks werden absichtlich ignoriert.", "Привязки контроллера разделены. Vapor Lock слушает только настроенные кнопки или триггеры; аналоговые стики намеренно игнорируются.", "Les liaisons de manette sont distinctes. Vapor Lock écoute uniquement les boutons ou gâchettes configurés ; les sticks analogiques sont volontairement ignorés."),
    "Connected slots: {slots} | Selected: {selected}{suffix}": ("已连接槽位：{slots} | 已选择：{selected}{suffix}", "Verbundene Slots: {slots} | Ausgewählt: {selected}{suffix}", "Подключенные слоты: {slots} | Выбрано: {selected}{suffix}", "Emplacements connectés : {slots} | Sélectionné : {selected}{suffix}"),
    " (disconnected)": ("（未连接）", " (nicht verbunden)", " (не подключено)", " (déconnecté)"),
    "Left stick": ("左摇杆", "Linker Stick", "Левый стик", "Stick gauche"),
    "Right stick": ("右摇杆", "Rechter Stick", "Правый стик", "Stick droit"),
    "Left trigger": ("左扳机", "Linker Trigger", "Левый триггер", "Gâchette gauche"),
    "Right trigger": ("右扳机", "Rechter Trigger", "Правый триггер", "Gâchette droite"),
    "{title}: %v / 255": ("{title}：%v / 255", "{title}: %v / 255", "{title}: %v / 255", "{title} : %v / 255"),
    "Buttons: {buttons}": ("按钮：{buttons}", "Tasten: {buttons}", "Кнопки: {buttons}", "Boutons : {buttons}"),
    "Activation binding: {activation} | Lock binding: {lock}": ("激活绑定：{activation} | 锁定绑定：{lock}", "Aktivierungsbindung: {activation} | Sperrbindung: {lock}", "Привязка активации: {activation} | Привязка фиксации: {lock}", "Liaison d'activation : {activation} | Liaison de verrouillage : {lock}"),
    "none": ("无", "keine", "нет", "aucun"),
    "automatic": ("自动", "automatisch", "автоматически", "automatique"),
    "pressed": ("按下", "gedrückt", "нажато", "enfoncé"),
    "released": ("释放", "losgelassen", "отпущено", "relâché"),
    "Right mouse button": ("鼠标右键", "Rechte Maustaste", "Правая кнопка мыши", "Bouton droit de la souris"),
    "Left Shift": ("左 Shift", "Linke Umschalttaste", "Левый Shift", "Maj gauche"),
    "Left Control": ("左 Control", "Linke Strg-Taste", "Левый Control", "Ctrl gauche"),
    "Space": ("空格", "Leertaste", "Пробел", "Espace"),
    "ACTIVE WORKSPACE": ("活动工作区", "AKTIVER ARBEITSBEREICH", "АКТИВНОЕ РАБОЧЕЕ ПРОСТРАНСТВО", "ESPACE DE TRAVAIL ACTIF"),
    "Selection, retention, and anchor response remain visible beside the live preview.": (
        "选择、保持和锚点响应会在实时预览旁保持可见。", "Auswahl, Halteverhalten und Ankerreaktion bleiben neben der Live-Vorschau sichtbar.",
        "Выбор, удержание и реакция якоря остаются видимыми рядом с активным предварительным просмотром.", "La sélection, le maintien et la réponse de l'ancre restent visibles à côté de l'aperçu en direct."
    ),
    "Active track": ("活动轨迹", "Aktiver Track", "Активный трек", "Suivi actif"),
    "Confidence": ("置信度", "Konfidenz", "Уверенность", "Confiance"),
    "Policy controls": ("策略控制", "Richtliniensteuerung", "Управление политикой", "Commandes de politique"),
    "Reset": ("重置", "Zurücksetzen", "Сбросить", "Réinitialiser"),
    "STRATEGY": ("策略", "STRATEGIE", "СТРАТЕГИЯ", "STRATÉGIE"),
    "LOCK BEHAVIOR": ("锁定行为", "SPERRVERHALTEN", "ПОВЕДЕНИЕ ФИКСАЦИИ", "COMPORTEMENT DE VERROUILLAGE"),
    "Horizontal offset": ("水平偏移", "Horizontaler Versatz", "Горизонтальное смещение", "Décalage horizontal"),
    "Vertical offset": ("垂直偏移", "Vertikaler Versatz", "Вертикальное смещение", "Décalage vertical"),
    "Switch tolerance": ("切换容差", "Wechseltoleranz", "Допуск переключения", "Tolérance de changement"),
    "Waiting for capture": ("等待捕获", "Warten auf Erfassung", "Ожидание захвата", "En attente de capture"),
    "Waiting for fresh frames": ("等待新画面", "Warten auf neue Bilder", "Ожидание новых кадров", "En attente de nouvelles images"),
    "Snapshot — waiting for fresh frames": ("快照 — 等待新画面", "Standbild — Warten auf neue Bilder", "Снимок — ожидание новых кадров", "Instantané — en attente de nouvelles images"),
    "Scanning for a target": ("正在扫描目标", "Suche nach einem Ziel", "Поиск цели", "Recherche d'une cible"),
    "Target pinned": ("目标已固定", "Ziel angeheftet", "Цель закреплена", "Cible épinglée"),
    "Selection stable": ("选择稳定", "Auswahl stabil", "Выбор стабилен", "Sélection stable"),
    "Fine tuning": ("精细调节", "Feinabstimmung", "Точная настройка", "Réglage fin"),
    "Geometry": ("几何", "Geometrie", "Геометрия", "Géométrie"),
    "Continuity": ("连续性", "Kontinuität", "Непрерывность", "Continuité"),
    "Target geometry": ("目标几何", "Zielgeometrie", "Геометрия цели", "Géométrie de cible"),
    "Lock continuity": ("锁定连续性", "Sperrkontinuität", "Непрерывность фиксации", "Continuité du verrouillage"),
    "ACQUISITION": ("获取", "ERFASSUNG", "ЗАХВАТ", "ACQUISITION"),
    "NEAR RADIUS": ("近距半径", "NAHBEREICHSRADIUS", "БЛИЖНИЙ РАДИУС", "RAYON PROCHE"),
    "SNAP RADIUS": ("吸附半径", "EINRASTRADIUS", "РАДИУС ПРИВЯЗКИ", "RAYON D'ACCROCHE"),
    "RETENTION": ("保持", "HALTEN", "УДЕРЖАНИЕ", "MAINTIEN"),
})
_ROWS.update({
    "Official Vapor Lock models": ("Vapor Lock 官方模型", "Offizielle Vapor-Lock-Modelle", "Официальные модели Vapor Lock", "Modèles officiels Vapor Lock"),
    "OFFICIAL MODEL CATALOG": ("官方模型目录", "OFFIZIELLER MODELLKATALOG", "КАТАЛОГ ОФИЦИАЛЬНЫХ МОДЕЛЕЙ", "CATALOGUE DES MODÈLES OFFICIELS"),
    "Search games or models": ("搜索游戏或模型", "Spiele oder Modelle suchen", "Поиск игр или моделей", "Rechercher des jeux ou modèles"),
    "Game / model": ("游戏 / 模型", "Spiel / Modell", "Игра / модель", "Jeu / modèle"),
    "Version": ("版本", "Version", "Версия", "Version"),
    "Task": ("任务", "Aufgabe", "Задача", "Tâche"),
    "Status": ("状态", "Status", "Состояние", "État"),
    "Installed": ("已安装", "Installiert", "Установлено", "Installé"),
    "Available": ("可用", "Verfügbar", "Доступно", "Disponible"),
    "detection": ("检测", "Erkennung", "Обнаружение", "Détection"),
    "instance-segmentation": ("实例分割", "Instanzsegmentierung", "Сегментация экземпляров", "Segmentation d'instances"),
    "Update installed": ("更新已安装模型", "Installierte aktualisieren", "Обновить установленные", "Mettre à jour les modèles installés"),
    "Install selected": ("安装所选模型", "Auswahl installieren", "Установить выбранную", "Installer la sélection"),
    "Reading the signed official-model catalog...": ("正在读取已签名的官方模型目录...", "Signierter Modellkatalog wird geladen...", "Загрузка подписанного каталога моделей...", "Chargement du catalogue signé..."),
    "No official models have been published yet.": ("尚未发布官方模型。", "Noch keine offiziellen Modelle veröffentlicht.", "Официальные модели пока не опубликованы.", "Aucun modèle officiel n'a encore été publié."),
    "No matching games or models.": ("没有匹配的游戏或模型。", "Keine passenden Spiele oder Modelle.", "Подходящие игры или модели не найдены.", "Aucun jeu ou modèle correspondant."),
    "No description provided.": ("未提供描述。", "Keine Beschreibung vorhanden.", "Описание отсутствует.", "Aucune description fournie."),
    "Official models": ("官方模型", "Offizielle Modelle", "Официальные модели", "Modèles officiels"),
    "No official models are installed.": ("尚未安装官方模型。", "Keine offiziellen Modelle installiert.", "Официальные модели не установлены.", "Aucun modèle officiel installé."),
    "Downloading {name}...": ("正在下载 {name}...", "{name} wird heruntergeladen...", "Загрузка {name}...", "Téléchargement de {name}..."),
    "Installed {count} official model(s).": ("已安装 {count} 个官方模型。", "{count} offizielle Modelle installiert.", "Установлено официальных моделей: {count}.", "{count} modèle(s) officiel(s) installé(s)."),
    "The official-model operation did not complete.": ("官方模型操作未完成。", "Der Modellvorgang wurde nicht abgeschlossen.", "Операция с официальной моделью не завершена.", "L'opération sur le modèle officiel n'a pas abouti."),
    "Unavailable in this engine: persistent CUDA I/O buffers are required. Standard CUDA inference remains available.": (
        "此引擎不支持：需要持久化 CUDA 输入/输出缓冲区。标准 CUDA 推理仍可用。", "In dieser Engine nicht verfügbar: Persistente CUDA-E/A-Puffer sind erforderlich. Standard-CUDA-Inferenz bleibt verfügbar.",
        "Недоступно в этом движке: нужны постоянные буферы ввода-вывода CUDA. Обычный вывод CUDA доступен.", "Indisponible dans ce moteur : des tampons d'entrée/sortie CUDA persistants sont requis. L'inférence CUDA standard reste disponible."),
})
_ROWS.update({
    "Start an Observe session before calibration. Sampling must not send movement or clicks.": (
        "请先启动观察会话，再进行校准。采样时不得发送移动或点击。",
        "Starten Sie vor der Kalibrierung eine Beobachtungssitzung. Beim Messen dürfen keine Bewegungen oder Klicks gesendet werden.",
        "Перед калибровкой запустите сеанс наблюдения. Во время измерения нельзя отправлять движения или нажатия.",
        "Démarrez une session d'observation avant l'étalonnage. La mesure ne doit envoyer ni mouvements ni clics.",
    ),
})
_ROWS.update({
    "Requires a compatible CUDA engine and a float NCHW model. Other models use CPU preprocessing.": (
        "需要兼容的 CUDA 引擎和浮点 NCHW 模型。其他模型使用 CPU 预处理。",
        "Erfordert eine kompatible CUDA-Engine und ein Float-NCHW-Modell. Andere Modelle nutzen CPU-Vorverarbeitung.",
        "Нужны совместимый движок CUDA и модель float NCHW. Для других моделей используется предобработка на CPU.",
        "Nécessite un moteur CUDA compatible et un modèle NCHW à virgule flottante. Les autres modèles utilisent le prétraitement CPU.",
    ),
    "Select CUDA to use GPU preprocessing. CPU and DirectML use CPU preprocessing.": (
        "选择 CUDA 以使用 GPU 预处理。CPU 和 DirectML 使用 CPU 预处理。",
        "Wählen Sie CUDA für die GPU-Vorverarbeitung. CPU und DirectML nutzen CPU-Vorverarbeitung.",
        "Выберите CUDA для предобработки на GPU. CPU и DirectML используют предобработку на CPU.",
        "Sélectionnez CUDA pour le prétraitement GPU. CPU et DirectML utilisent le prétraitement CPU.",
    ),
    "Checking this engine's GPU preprocessing support...": (
        "正在检查此引擎的 GPU 预处理支持...", "GPU-Vorverarbeitung dieser Engine wird geprüft...",
        "Проверка поддержки предобработки на GPU...", "Vérification du prétraitement GPU de ce moteur...",
    ),
    "GPU preprocessing is not included in this engine. Standard CUDA inference remains available.": (
        "此引擎未包含 GPU 预处理。标准 CUDA 推理仍可用。",
        "Diese Engine enthält keine GPU-Vorverarbeitung. Standard-CUDA-Inferenz bleibt verfügbar.",
        "В этом движке нет предобработки на GPU. Обычный вывод CUDA остаётся доступным.",
        "Ce moteur n'inclut pas le prétraitement GPU. L'inférence CUDA standard reste disponible.",
    ),
    "GPU preprocessing support could not be verified. CPU preprocessing remains available.": (
        "无法验证 GPU 预处理支持。CPU 预处理仍可用。",
        "GPU-Vorverarbeitung konnte nicht verifiziert werden. CPU-Vorverarbeitung bleibt verfügbar.",
        "Не удалось проверить поддержку предобработки на GPU. Предобработка на CPU остаётся доступной.",
        "Impossible de vérifier le prétraitement GPU. Le prétraitement CPU reste disponible.",
    ),
})
_ROWS.update({
    "0 lets ONNX Runtime choose automatically. A positive value caps parallel model graph work.": ("0 由 ONNX Runtime 自动选择。正值限制模型图的并行工作线程。", "0 überlässt ONNX Runtime die Auswahl. Positive Werte begrenzen die parallele Graphverarbeitung.", "0 включает автоматический выбор ONNX Runtime. Положительное значение ограничивает параллельную обработку графа.", "0 laisse ONNX Runtime choisir. Une valeur positive limite le travail parallèle du graphe."),
    "0 lets ONNX Runtime choose automatically. A positive value caps work inside each model operation.": ("0 由 ONNX Runtime 自动选择。正值限制每个模型算子的工作线程。", "0 überlässt ONNX Runtime die Auswahl. Positive Werte begrenzen die Threads innerhalb einzelner Operationen.", "0 включает автоматический выбор ONNX Runtime. Положительное значение ограничивает потоки внутри каждой операции.", "0 laisse ONNX Runtime choisir. Une valeur positive limite les threads de chaque opération."),
    "A local ONNX file. A model is optional only for the built-in test signal.": ("本地 ONNX 文件。仅内置测试信号不需要模型。", "Lokale ONNX-Datei. Nur das eingebaute Testsignal benötigt kein Modell.", "Локальный файл ONNX. Модель не требуется только для встроенного тестового сигнала.", "Fichier ONNX local. Seul le signal de test intégré peut fonctionner sans modèle."),
    "A physical monitor uses native DXGI Desktop Duplication. Virtual desktop is available as a compatibility path.": ("物理显示器使用原生 DXGI 桌面复制。虚拟桌面提供兼容方式。", "Physische Monitore nutzen DXGI Desktop Duplication. Der virtuelle Desktop bietet einen Kompatibilitätsmodus.", "Физический монитор использует DXGI Desktop Duplication. Виртуальный рабочий стол доступен как совместимый вариант.", "Un écran physique utilise DXGI Desktop Duplication. Le bureau virtuel offre une solution de compatibilité."),
    "A plain text library using: identifier | vertical pixels per second | horizontal pixels per second. Positive values describe observed drift; Vapor Lock applies the opposite correction.": ("纯文本格式：标识符 | 垂直像素/秒 | 水平像素/秒。数值描述观测到的漂移；Vapor Lock 施加反向补偿。", "Textformat: Kennung | vertikale Pixel pro Sekunde | horizontale Pixel pro Sekunde. Die Werte beschreiben die beobachtete Drift; Vapor Lock korrigiert in Gegenrichtung.", "Текстовый формат: имя | пиксели в секунду по вертикали | пиксели в секунду по горизонтали. Значения описывают наблюдаемый дрейф; Vapor Lock применяет обратную коррекцию.", "Format texte : identifiant | pixels verticaux par seconde | pixels horizontaux par seconde. Les valeurs décrivent la dérive observée ; Vapor Lock applique la correction opposée."),
    "A random local secret required by every API request. It is stored outside profiles, never logged, and is replaced immediately when regenerated.": ("每个 API 请求都需要的随机本地密钥。它存储在配置文件之外，不写入日志，重新生成后立即替换。", "Zufälliges lokales Geheimnis für jede API-Anfrage. Es wird außerhalb der Profile gespeichert, nie protokolliert und bei Neugenerierung sofort ersetzt.", "Случайный локальный секрет для каждого запроса API. Хранится отдельно от профилей, не записывается в журналы и сразу заменяется при обновлении.", "Secret local aléatoire requis pour chaque requête API. Conservé hors des profils, jamais journalisé, remplacé immédiatement lors de sa régénération."),
    "A short name used in the compensation library and profiles.": ("用于补偿库和配置文件的简短名称。", "Kurzer Name für die Kompensationsbibliothek und Profile.", "Краткое имя для библиотеки компенсации и профилей.", "Nom court utilisé dans la bibliothèque de compensation et les profils."),
    "Accept the selected image and move forward, exclude it from the next archive, or clear its decision. The label editor writes only that image's paired YOLO label file.": ("接受所选图像并前进、从下一个归档中排除，或清除决定。标签编辑器仅修改该图像对应的 YOLO 标签文件。", "Bild akzeptieren und weitergehen, vom nächsten Archiv ausschließen oder Entscheidung löschen. Der Editor ändert nur die zugehörige YOLO-Labeldatei.", "Примите изображение и перейдите дальше, исключите его из следующего архива или сбросьте решение. Редактор изменяет только соответствующий файл разметки YOLO.", "Acceptez l'image et avancez, excluez-la de la prochaine archive ou effacez la décision. L'éditeur ne modifie que son fichier d'annotations YOLO."),
    "Add every class needed for this dataset, independent of the annotation model. Numpad 1 through 9 instantly selects the class mapped to its slot; the dropdown always exposes the full class library.": ("可添加数据集需要的所有类别，不受标注模型限制。数字小键盘 1 至 9 切换对应类别；下拉菜单始终显示完整类别库。", "Alle benötigten Klassen lassen sich unabhängig vom Modell hinzufügen. Ziffernblock 1 bis 9 wählt die zugewiesene Klasse; die Auswahlliste enthält alle Klassen.", "Добавляйте нужные классы независимо от модели разметки. Цифровая клавиатура 1–9 выбирает назначенный класс; список содержит все классы.", "Ajoutez les classes nécessaires indépendamment du modèle d'annotation. Le pavé numérique 1 à 9 sélectionne les classes associées ; la liste contient toutes les classes."),
    "Add, update, or remove a named preset. The library stays user-readable and is saved atomically.": ("添加、更新或删除命名预设。库保持可读，并以原子方式保存。", "Benannte Vorgaben hinzufügen, ändern oder entfernen. Die lesbare Bibliothek wird atomar gespeichert.", "Добавляйте, обновляйте или удаляйте именованные настройки. Библиотека остаётся читаемой и сохраняется атомарно.", "Ajoutez, modifiez ou supprimez un préréglage nommé. La bibliothèque reste lisible et son enregistrement est atomique."),
    "Additional captured frames skipped before an inference pass.": ("每次推理前额外跳过的捕获帧数。", "Zusätzlich übersprungene Bilder vor jedem Inferenzdurchlauf.", "Количество дополнительных кадров, пропускаемых перед выводом модели.", "Nombre d'images capturées supplémentaires ignorées avant une inférence."),
    "Applies only in click mode. It limits repeated clicks while the target remains inside snap radius; hold mode has no cooldown.": ("仅适用于点击模式。目标在吸附半径内时限制重复点击；按住模式没有冷却时间。", "Nur im Klickmodus: begrenzt wiederholte Klicks innerhalb des Einrastradius. Der Haltemodus hat keine Abklingzeit.", "Только для режима щелчка: ограничивает повторные щелчки внутри радиуса захвата. В режиме удержания задержки нет.", "Uniquement en mode clic : limite les clics répétés dans le rayon d'accrochage. Aucun délai en mode maintien."),
    "Automatic compensation waits for this many visible, mature tracks before estimating shared image drift.": ("自动补偿等待达到此数量的可见稳定轨迹，再估算共同图像漂移。", "Die automatische Kompensation wartet auf diese Anzahl sichtbarer, etablierter Spuren, bevor sie die gemeinsame Bilddrift schätzt.", "Автокомпенсация ожидает это число видимых устойчивых траекторий перед оценкой общего дрейфа изображения.", "La compensation automatique attend ce nombre de trajectoires visibles et établies avant d'estimer la dérive commune."),
    "Automatic reads ONNX metadata and validates ambiguous output rows. Choose an explicit layout only for a custom export that needs a manual override.": ("自动读取 ONNX 元数据并验证有歧义的输出。仅在自定义导出需要时手动选择格式。", "Automatik liest ONNX-Metadaten und prüft mehrdeutige Ausgaben. Ein Format nur für spezielle Exporte manuell vorgeben.", "Автоматический режим читает метаданные ONNX и проверяет неоднозначные выходы. Указывайте формат вручную только для нестандартного экспорта.", "Le mode automatique lit les métadonnées ONNX et vérifie les sorties ambiguës. Ne forcez le format que pour un export personnalisé."),
    "Automatic updates": ("自动更新", "Automatische Updates", "Автоматические обновления", "Mises à jour automatiques"),
    "Automatic uses DirectML first, then CPU if DirectML cannot run the selected model. CUDA and TensorRT RTX remain explicit choices. Provider files are downloaded on demand into this Vapor Lock folder.": ("自动模式优先使用 DirectML，失败时使用 CPU。CUDA 和 TensorRT RTX 需手动选择。运行库按需下载到此 Vapor Lock 文件夹。", "Automatik nutzt zuerst DirectML, bei Inkompatibilität die CPU. CUDA und TensorRT RTX bleiben manuelle Optionen. Laufzeitdateien werden bei Bedarf in diesen Vapor-Lock-Ordner geladen.", "Автоматический режим сначала использует DirectML, при несовместимости — CPU. CUDA и TensorRT RTX выбираются вручную. Компоненты загружаются по необходимости в папку Vapor Lock.", "Le mode automatique privilégie DirectML, puis le CPU si nécessaire. CUDA et TensorRT RTX restent des choix explicites. Les composants sont téléchargés à la demande dans ce dossier Vapor Lock."),
    "Available in both editions. This opens the inert setup wizard and never starts capture by itself.": ("两个版本均可使用。仅打开设置向导，不会自动启动捕获。", "In beiden Editionen verfügbar. Öffnet den Einrichtungsassistenten, ohne die Erfassung automatisch zu starten.", "Доступно в обеих версиях. Открывает мастер настройки, не запуская захват автоматически.", "Disponible dans les deux éditions. Ouvre l'assistant sans démarrer automatiquement la capture."),
    "Bind one global shortcut directly to a saved local profile. An active session stops before the selected profile is loaded, keeping the native engine and control center synchronized.": ("将全局快捷键绑定到本地配置文件。加载前停止活动会话，使原生引擎与控制中心保持同步。", "Ein globales Kürzel direkt mit einem lokalen Profil verknüpfen. Eine aktive Sitzung wird vor dem Laden beendet, damit Engine und Oberfläche synchron bleiben.", "Привяжите глобальное сочетание клавиш к локальному профилю. Активный сеанс останавливается перед загрузкой, сохраняя синхронизацию движка и интерфейса.", "Associez un raccourci global à un profil local. La session active s'arrête avant son chargement pour synchroniser le moteur et l'interface."),
    "Build a ready-to-upload ZIP in this dataset folder with paired images, labels, data.yaml, and classes.txt.": ("在数据集文件夹中生成 ZIP，包含配对图像、标签、data.yaml 和 classes.txt。", "ZIP im Datensatzordner erstellen: Bild-Label-Paare, data.yaml und classes.txt.", "Создать ZIP в папке набора данных с парами изображений и меток, data.yaml и classes.txt.", "Créez un ZIP dans ce dossier avec les images, leurs annotations, data.yaml et classes.txt."),
    "Button sent to the active camera-control application.": ("发送到当前应用程序的鼠标按钮。", "Maustaste, die an die aktive Anwendung gesendet wird.", "Кнопка мыши, отправляемая активному приложению.", "Bouton de souris envoyé à l'application active."),
    "Calm small box-anchor shifts before target policy and movement use them. This leaves the detection outline responsive while the lock settles cleanly.": ("在目标策略和移动使用锚点前平滑微小抖动。保持检测轮廓灵敏，同时使锁定稳定。", "Kleine Ankerverschiebungen vor Zielauswahl und Bewegung glätten. Die Erkennungsumrandung bleibt reaktionsschnell, während sich die Fixierung stabilisiert.", "Сглаживает малые смещения опорной точки перед выбором цели и движением. Контур остаётся отзывчивым, а фиксация стабилизируется.", "Lisse les petits déplacements de l'ancrage avant la sélection et le mouvement. Le contour reste réactif tandis que le verrouillage se stabilise."),
    "Caps automatic correction before it reaches the selected movement transport.": ("限制发送到所选移动方式的自动补偿幅度。", "Begrenzt die automatische Korrektur vor der Bewegungsausgabe.", "Ограничивает автокоррекцию перед передачей выбранному способу вывода движения.", "Limite la correction automatique avant son envoi au mode de sortie choisi."),
    "Check quietly at launch and offer newer releases without changing license validity or requiring an upgrade.": ("启动时检查新版本，不改变许可证有效性，也不强制升级。", "Beim Start unauffällig nach Updates suchen, ohne Lizenzänderung oder Aktualisierungszwang.", "Проверять обновления при запуске без изменения лицензии и обязательного обновления.", "Vérifie discrètement les mises à jour au démarrage, sans modifier la licence ni imposer de mise à niveau."),
    "Choose a dataset build created in the Collection tab. Each choice points to a local data.yaml file.": ("选择在采集选项卡中创建的数据集。每项对应一个本地 data.yaml 文件。", "Einen im Erfassungsreiter erstellten Datensatz wählen. Jeder Eintrag verweist auf eine lokale data.yaml.", "Выберите набор, созданный на вкладке сбора. Каждый пункт соответствует локальному файлу data.yaml.", "Choisissez un jeu de données créé dans l'onglet Collecte. Chaque entrée correspond à un fichier data.yaml local."),
    "Choose a discovered local ONNX model for later label creation. Use the current detection model when no separate model is selected.": ("选择本地 ONNX 模型用于标注。未单独选择时使用当前检测模型。", "Ein lokales ONNX-Modell zur späteren Beschriftung wählen. Ohne separate Auswahl wird das aktuelle Erkennungsmodell genutzt.", "Выберите локальную ONNX-модель для разметки. Без отдельного выбора используется текущая модель обнаружения.", "Choisissez un modèle ONNX local pour l'annotation. Sans choix distinct, le modèle de détection actuel est utilisé."),
    "Choose a measured compensation record. The stored vertical and horizontal values remain editable below.": ("选择测量得到的补偿记录。下方仍可编辑垂直和水平值。", "Eine gemessene Kompensation wählen. Vertikale und horizontale Werte bleiben unten bearbeitbar.", "Выберите измеренную компенсацию. Вертикальное и горизонтальное значения можно изменить ниже.", "Choisissez une compensation mesurée. Les valeurs verticales et horizontales restent modifiables ci-dessous."),
    "Choose disk cache, memory cache, or no cache. Memory cache can substantially reduce training time but uses available RAM.": ("选择磁盘缓存、内存缓存或不缓存。内存缓存可缩短训练时间，但会占用 RAM。", "Festplatten-, RAM- oder keinen Cache wählen. RAM-Cache kann das Training beschleunigen, benötigt aber Arbeitsspeicher.", "Выберите кэш на диске, в памяти или без кэша. Кэш в памяти ускоряет обучение, но занимает ОЗУ.", "Choisissez le cache disque, mémoire ou aucun. Le cache mémoire peut accélérer l'entraînement, mais utilise la RAM."),
    "Choose the Xbox controller used for activation and manual target pinning. Automatic selects the first connected controller.": ("选择用于激活和手动锁定的 Xbox 控制器。自动模式选择第一个已连接控制器。", "Xbox-Controller für Aktivierung und manuelle Zielfixierung wählen. Automatik nimmt den ersten verbundenen Controller.", "Выберите Xbox-контроллер для активации и ручной фиксации цели. Автоматически выбирается первый подключённый.", "Choisissez la manette Xbox pour l'activation et la fixation manuelle. Le mode automatique prend la première manette connectée."),
    "Choose the class for new boxes. Click two opposite corners, or click-drag, directly on the image. Up and Down always move through the queue; the selected approval key accepts and advances.": ("选择新框类别。在图像上点击两个对角或拖动绘制。上下键切换图像；确认键接受并前进。", "Klasse für neue Rahmen wählen. Zwei gegenüberliegende Ecken anklicken oder direkt im Bild ziehen. Auf/Ab wechselt Bilder; die Bestätigungstaste akzeptiert und geht weiter.", "Выберите класс новых рамок. Укажите противоположные углы или протяните мышью по изображению. Вверх/вниз переключают изображения, клавиша подтверждения принимает и переходит дальше.", "Choisissez la classe des nouvelles boîtes. Cliquez deux coins opposés ou faites glisser sur l'image. Haut/Bas change d'image ; la touche de validation accepte et avance."),
    "Choose the device that authorizes movement output. Controller input uses Windows' built-in XInput support and needs no separate driver bundle.": ("选择授权移动输出的设备。控制器输入使用 Windows 内置 XInput，无需额外驱动包。", "Gerät zur Bewegungsfreigabe wählen. Controller-Eingaben nutzen Windows-XInput und benötigen kein zusätzliches Treiberpaket.", "Выберите устройство активации движения. Ввод контроллера использует встроенный Windows XInput без отдельного драйвера.", "Choisissez le périphérique autorisant le mouvement. L'entrée manette utilise XInput intégré à Windows sans pilote supplémentaire."),
    "Choose the device used to pin or release the current target. Controller input uses the same controller selection configured under Output.": ("选择固定或释放当前目标的设备。控制器使用输出设置中的同一控制器。", "Gerät zum Fixieren oder Freigeben des Ziels wählen. Verwendet den unter Ausgabe gewählten Controller.", "Выберите устройство фиксации или освобождения цели. Используется тот же контроллер, что выбран в разделе вывода.", "Choisissez le périphérique pour fixer ou libérer la cible. La manette est celle sélectionnée dans les paramètres de sortie."),
    "Choose the local compute device used by Ultralytics. GPU training requires a compatible PyTorch runtime and driver.": ("选择 Ultralytics 使用的本地计算设备。GPU 训练需要兼容的 PyTorch 和驱动。", "Lokales Rechengerät für Ultralytics wählen. GPU-Training benötigt kompatible PyTorch-Laufzeit und Treiber.", "Выберите локальное устройство вычислений Ultralytics. Обучение на GPU требует совместимых PyTorch и драйвера.", "Choisissez le périphérique local pour Ultralytics. L'entraînement GPU nécessite PyTorch et un pilote compatibles."),
    "Choose the local path used for relative movement output. Hardware routes send movement only; no remapping, masking, device-identity, or keyboard operations are used.": ("选择相对移动输出方式。硬件方式仅发送移动，不进行重映射、屏蔽、设备身份或键盘操作。", "Ausgabeweg für relative Bewegung wählen. Hardwarewege senden nur Bewegung, keine Umbelegung, Maskierung, Geräteidentität oder Tastaturaktionen.", "Выберите способ вывода относительного движения. Аппаратные варианты передают только движение, без переназначения, скрытия, изменения идентичности или клавиатурных действий.", "Choisissez le mode de sortie des mouvements relatifs. Les modes matériels n'envoient que le mouvement, sans remappage, masquage, modification d'identité ni actions clavier."),
    "Choose the standard Vapor palette or a high-contrast palette for bright displays and projection surfaces.": ("选择标准 Vapor 配色，或适合明亮屏幕和投影的高对比配色。", "Standard-Vapor-Palette oder hohen Kontrast für helle Displays und Projektionsflächen wählen.", "Выберите стандартную палитру Vapor или высококонтрастную для ярких экранов и проекций.", "Choisissez la palette Vapor standard ou une palette contrastée pour les écrans lumineux et les projections."),
    "Circular uses true radial distance. Rectangular uses equal horizontal and vertical bounds.": ("圆形使用径向距离；矩形使用相同的水平和垂直边界。", "Kreisförmig nutzt den radialen Abstand. Rechteckig nutzt gleiche horizontale und vertikale Grenzen.", "Круг использует радиальное расстояние. Прямоугольник — равные горизонтальные и вертикальные границы.", "Le cercle utilise la distance radiale. Le rectangle utilise des limites horizontales et verticales égales."),
    "Class library and hot swap": ("类别库与快捷切换", "Klassenbibliothek und Schnellwechsel", "Библиотека классов и быстрое переключение", "Bibliothèque de classes et changement rapide"),
    "Click sends a full press and release on its cooldown. Hold presses once on entry and releases when safe after the target leaves snap radius, becomes invalid, or capture stops. Vapor Lock never sends a release while you physically hold that same button.": ("点击模式按冷却时间发送按下和释放。按住模式在进入时按下，在目标离开吸附半径、失效或捕获停止后安全释放。实际按住同一按钮时，Vapor Lock 不会发送释放。", "Klick sendet Drücken und Loslassen mit Abklingzeit. Halten drückt beim Eintritt und lässt nach Verlassen des Einrastradius, ungültigem Ziel oder Erfassungsstopp sicher los. Eine physisch gehaltene Taste wird nie losgelassen.", "Щелчок отправляет нажатие и отпускание с задержкой. Удержание нажимает при входе и безопасно отпускает при выходе из радиуса, потере цели или остановке захвата. Физически удерживаемая кнопка не отпускается.", "Clic envoie pression et relâchement avec délai. Maintien presse à l'entrée puis relâche sans conflit après sortie du rayon, perte de cible ou arrêt. Un bouton physiquement maintenu n'est jamais relâché par Vapor Lock."),
    "Collects several valid automatic drift estimates from the active native session, calculates a robust measured preset, and restores your current compensation mode afterward.": ("从活动原生会话采集多个有效漂移估计，计算稳健预设，随后恢复当前补偿模式。", "Sammelt gültige Driftschätzungen aus der nativen Sitzung, berechnet eine robuste Vorgabe und stellt danach den bisherigen Kompensationsmodus wieder her.", "Собирает несколько оценок дрейфа активного сеанса, вычисляет устойчивую настройку и затем восстанавливает текущий режим компенсации.", "Recueille plusieurs estimations de dérive, calcule un préréglage robuste puis rétablit le mode de compensation actuel."),
    "Color used for non-selected track boxes and labels. This custom value is stored with the current profile.": ("未选中轨迹的框和标签颜色。随当前配置文件保存。", "Farbe nicht ausgewählter Rahmen und Beschriftungen. Wird im aktuellen Profil gespeichert.", "Цвет рамок и подписей невыбранных траекторий. Сохраняется в текущем профиле.", "Couleur des boîtes et étiquettes non sélectionnées. Enregistrée dans le profil actuel."),
    "Color used for the active selected target and its prediction guide.": ("当前目标及其预测指示的颜色。", "Farbe des ausgewählten Ziels und seiner Vorhersageanzeige.", "Цвет выбранной цели и её прогнозной направляющей.", "Couleur de la cible sélectionnée et de son guide de prédiction."),
})
_ROWS.update({
    "Color used for the compact desktop-overlay status strip.": ("桌面叠加状态栏颜色。", "Farbe der kompakten Overlay-Statusleiste.", "Цвет компактной строки состояния оверлея.", "Couleur de la barre d'état compacte de la surimpression."),
    "Color used for the outer targeting guide and capture-region boundary.": ("外围目标辅助线和捕获边界颜色。", "Farbe der äußeren Zielhilfe und Aufnahmegrenze.", "Цвет внешней направляющей и границы захвата.", "Couleur du guide extérieur et de la limite de capture."),
    "Compare original, mirrored, and exposure-adjusted views. Keep only same-class boxes that agree across every view. One pass uses the original image.": ("比较原图、镜像和曝光调整图，仅保留所有视图一致的同类框。一次使用原图。", "Vergleicht Original, Spiegelung und Belichtungsvarianten. Nur in allen Ansichten übereinstimmende Boxen derselben Klasse bleiben. Ein Durchlauf nutzt das Original.", "Сравнивает оригинал, отражение и варианты экспозиции. Сохраняет совпадающие во всех видах рамки одного класса. Один проход использует оригинал.", "Compare l'original, son miroir et des variantes d'exposition. Conserve les cadres de même classe concordant dans chaque vue. Un passage utilise l'original."),
    "Compare the current workspace or any two saved profiles field by field.": ("逐项比较当前工作区或两个已保存配置。", "Aktuellen Arbeitsbereich oder zwei Profile feldweise vergleichen.", "Сравнение текущих настроек или двух профилей по полям.", "Compare les réglages actuels ou deux profils, champ par champ."),
    "Compare tuning": ("比较调校", "Abstimmung vergleichen", "Сравнить настройку", "Comparer les réglages"),
    "Compression quality for debug frames. Higher values retain finer detail and use more interprocess bandwidth.": ("调试帧压缩质量。越高保留更多细节，但进程间带宽用量更大。", "Bildkompressionsqualität. Höhere Werte erhalten Details und benötigen mehr Prozessbandbreite.", "Качество сжатия отладочных кадров. Высокие значения сохраняют детали, но увеличивают межпроцессный трафик.", "Qualité des images de diagnostic. Une valeur élevée préserve les détails mais augmente les transferts entre processus."),
    "Compression quality for source training images.": ("训练源图像的压缩质量。", "Kompressionsqualität der Trainingsbilder.", "Качество сжатия обучающих изображений.", "Qualité de compression des images d'entraînement."),
    "Confidence-weighted prediction for fresh observations. Missing targets retain identity only.": ("对新观测按置信度加权预测；丢失目标仅保留身份。", "Konfidenzgewichtete Vorhersage neuer Beobachtungen. Fehlende Ziele behalten nur ihre Identität.", "Прогноз новых наблюдений с учётом уверенности. Для пропавших целей сохраняется только идентичность.", "Prédiction pondérée par la confiance des observations récentes. Les cibles absentes conservent uniquement leur identité."),
    "Contains an images folder for captures and a labels folder created by the background annotator. Leave blank to use the packaged datasets folder.": ("包含捕获图像的 images 和后台标注器创建的 labels 文件夹。留空使用程序的 datasets 文件夹。", "Enthält images für Aufnahmen und labels vom Annotator. Leer nutzt den mitgelieferten datasets-Ordner.", "Содержит images для снимков и labels для разметки. Пустое поле использует папку datasets программы.", "Contient images pour les captures et labels pour les annotations. Vide utilise le dossier datasets fourni."),
    "Contender holding zone. A new target here gets time to reach the minimum track age before an outer-FOV candidate can take the vacant anchor.": ("候选等待区。此处新目标达到最短轨迹年龄前，外圈候选不会占用空锚点。", "Wartezone: Neue Ziele können das Mindestalter erreichen, bevor äußere Kandidaten den freien Anker übernehmen.", "Зона ожидания: новая цель получает время достичь минимального возраста трека до захвата свободного якоря внешним кандидатом.", "Zone d'attente : une nouvelle cible peut atteindre l'âge minimal avant qu'un candidat extérieur prenne l'ancrage libre."),
    "Create YOLO labels in the background. The control center and active capture session remain usable while it runs.": ("后台创建 YOLO 标签，控制中心和捕获仍可使用。", "YOLO-Labels im Hintergrund erstellen. Oberfläche und Aufnahme bleiben nutzbar.", "Создаёт разметку YOLO в фоне. Панель и захват остаются доступными.", "Crée les annotations YOLO en arrière-plan. L'interface et la capture restent utilisables."),
    "Disable mosaic augmentation this many epochs before the end of training.": ("训练结束前这么多轮禁用马赛克增强。", "Mosaik-Augmentierung so viele Epochen vor Trainingsende abschalten.", "Отключить мозаику за указанное число эпох до конца.", "Désactive la mosaïque ce nombre d'époques avant la fin."),
    "Disabled by default. Enabling it allows Vapor Lock to request administrator approval only when you explicitly install or start the local Xbox-compatible component.": ("默认禁用。仅在明确安装或启动本地 Xbox 兼容组件时请求管理员授权。", "Standardmäßig aus. Administratorfreigabe nur bei ausdrücklich gewählter Installation oder Start der Xbox-Komponente.", "По умолчанию выключено. Права администратора запрашиваются только при явной установке или запуске Xbox-компонента.", "Désactivé par défaut. Autorisation administrateur uniquement lors de l'installation ou du démarrage explicite du composant Xbox local."),
    "Disabled by default. The native process only sends movement while activated and a target is selected.": ("默认禁用。仅激活且选中目标时发送移动。", "Standardmäßig aus. Bewegung nur bei Aktivierung und ausgewähltem Ziel.", "По умолчанию выключено. Движение только при активации и выбранной цели.", "Désactivé par défaut. Mouvement uniquement après activation et avec une cible choisie."),
    "Disabled by default. This route uses an administrator-installed local input driver so compatible software receives driver-level relative movement instead of Windows synthetic input.": ("默认禁用。使用管理员安装的本地输入驱动，向兼容软件发送驱动级相对移动，而非 Windows 合成输入。", "Standardmäßig aus. Ein als Administrator installierter Treiber sendet relative Bewegung statt synthetischer Windows-Eingaben an kompatible Software.", "По умолчанию выключено. Установленный администратором драйвер передаёт совместимому ПО относительное движение вместо синтетического ввода Windows.", "Désactivé par défaut. Un pilote installé par un administrateur transmet un mouvement relatif aux logiciels compatibles au lieu d'entrées synthétiques Windows."),
    "Discard detections below this confidence.": ("丢弃低于此置信度的检测。", "Erkennungen unter dieser Konfidenz verwerfen.", "Отбрасывать обнаружения ниже этой уверенности.", "Écarte les détections sous ce seuil de confiance."),
    "Discard model outputs below this confidence while creating reviewable YOLO labels.": ("创建待审核 YOLO 标签时丢弃低于此置信度的输出。", "Beim Erstellen prüfbarer YOLO-Labels Ausgaben unter dieser Konfidenz verwerfen.", "При создании разметки YOLO для проверки отбрасывать результаты ниже этой уверенности.", "Écarte les résultats sous ce seuil lors de la création d'annotations YOLO à réviser."),
    "Downloads the optional local Ultralytics runtime into this Vapor Lock copy only when requested. NVIDIA systems receive the matching CUDA runtime; other systems receive CPU training. Stop ends an install before it becomes active.": ("按需为此副本下载 Ultralytics。NVIDIA 使用匹配的 CUDA，其他系统使用 CPU。停止会在启用前中止安装。", "Lädt Ultralytics nur auf Anfrage in diese Kopie. NVIDIA erhält passende CUDA-Laufzeit, andere Systeme CPU-Training. Stopp bricht vor Aktivierung ab.", "Загружает Ultralytics в эту копию по запросу. NVIDIA получает совместимую CUDA, остальные обучают на CPU. Остановка прерывает установку до активации.", "Télécharge Ultralytics dans cette copie sur demande. NVIDIA reçoit CUDA compatible, les autres utilisent le CPU. Arrêter interrompt l'installation avant activation."),
    "Draw a thin guide around the exact selected capture region. This is useful when checking centered offsets and monitor alignment.": ("沿捕获区域绘制细线，便于检查居中偏移和显示器对齐。", "Dünne Kontur um den Aufnahmebereich zur Prüfung von Versatz und Monitorausrichtung.", "Тонкая рамка области захвата для проверки смещения и выравнивания монитора.", "Contour fin de la zone capturée pour vérifier les décalages et l'alignement de l'écran."),
    "Draw active tracking information over the debug preview. Source frames written to the training dataset are always unannotated.": ("在调试预览绘制跟踪信息。训练源帧始终不含标注图形。", "Trackinginformationen in der Vorschau. Trainingsbilder enthalten keine eingeblendeten Markierungen.", "Показывает трекинг в предпросмотре. Обучающие кадры сохраняются без наложенной разметки.", "Affiche le suivi dans l'aperçu. Les images d'entraînement sont enregistrées sans surimpression."),
    "Draw an outline for each retained track and highlight the selected lock.": ("绘制保留轨迹轮廓并突出选中目标。", "Kontur für jeden behaltenen Track und Hervorhebung der Auswahl.", "Контуры сохранённых треков и выделение выбранной цели.", "Contours des pistes conservées et mise en évidence de la cible choisie."),
    "Draw decoded instance masks when the selected ONNX model provides prototype-mask output. Standard detection models continue to use boxes.": ("ONNX 提供原型掩码时绘制实例掩码；普通检测模型仍使用框。", "Instanzmasken bei ONNX-Prototypmaskenausgabe anzeigen. Erkennungsmodelle nutzen weiterhin Boxen.", "Маски экземпляров при наличии прототипных масок ONNX. Обычные детекторы используют рамки.", "Masques d'instances si ONNX fournit des prototypes de masques. Les détecteurs classiques utilisent des cadres."),
    "Draw live native track metadata directly over the captured region without opening Control Center. The overlay contains no captured pixels and is excluded from capture when Windows supports it.": ("无需打开控制中心即可叠加实时轨迹信息。叠加层无捕获像素，Windows 支持时排除捕获。", "Live-Trackdaten direkt über der Aufnahme ohne Kontrollzentrum. Das Overlay enthält keine Aufnahmebilder und wird bei Windows-Unterstützung nicht mitaufgenommen.", "Данные треков поверх захвата без панели управления. Оверлей не содержит захваченных пикселей и исключается из захвата при поддержке Windows.", "Pistes en direct sur la zone capturée sans centre de contrôle. La surimpression ne contient pas de pixels capturés et est exclue si Windows le permet."),
    "Draw the active circular or rectangular target zones in the annotated preview.": ("在预览绘制启用的圆形或矩形目标区域。", "Aktive runde oder rechteckige Zielzonen in der Vorschau anzeigen.", "Активные круглые или прямоугольные зоны в предпросмотре.", "Zones actives circulaires ou rectangulaires dans l'aperçu annoté."),
    "Draw the current selected anchor and its configured prediction lead in the debug preview.": ("在预览绘制当前锚点及预测提前量。", "Gewählten Anker und Vorhersagevorlauf in der Vorschau anzeigen.", "Выбранный якорь и упреждение в предпросмотре.", "Ancrage choisi et son avance prédictive dans l'aperçu."),
    "Elapsed retention time, independent of inference rate.": ("已过保留时间，与推理速率无关。", "Verstrichene Haltezeit, unabhängig von der Inferenzrate.", "Время удержания, независимое от частоты инференса.", "Durée de maintien, indépendante du débit d'inférence."),
    "Emit compressed annotated frames from the native engine only while a session is running. Disabling this removes preview encoding work from the capture pipeline.": ("仅会话运行时发送压缩标注帧。关闭可省去预览编码。", "Komprimierte Vorschaubilder nur während einer Sitzung senden. Abschalten vermeidet Vorschaukodierung in der Aufnahmepipeline.", "Сжатые кадры с разметкой только во время сеанса. Отключение исключает кодирование предпросмотра из захвата.", "Images annotées compressées pendant la session uniquement. Désactiver évite l'encodage de l'aperçu dans la capture."),
    "Enable inverse drift correction from the selected saved profile or automatic estimate. Compensation has its own activation and does not require target movement output to be enabled.": ("启用配置或自动估计的反向漂移修正。补偿独立激活，不要求目标移动输出。", "Inverse Driftkorrektur aus Profil oder Schätzung aktivieren. Kompensation hat eine eigene Aktivierung, unabhängig von der Zielbewegung.", "Обратная коррекция дрейфа по профилю или оценке. Компенсация активируется отдельно и не требует вывода движения цели.", "Correction inverse selon le profil ou l'estimation. La compensation s'active indépendamment du mouvement de cible."),
    "Enable only when the chosen annotation model should replace labels already in the dataset folder.": ("仅需用所选模型替换现有标签时启用。", "Nur aktivieren, wenn das Modell vorhandene Labels ersetzen soll.", "Включайте только для замены существующей разметки выбранной моделью.", "Activez uniquement pour remplacer les annotations existantes par celles du modèle choisi."),
    "Enabled by default. The event action runs only while aim activation is authorized: held in Hold mode or armed in Toggle mode.": ("默认启用。仅瞄准激活时运行：按住模式需保持按下，切换模式需启用。", "Standardmäßig an. Aktion nur bei aktiver Zielsteuerung: gedrückt im Haltemodus oder eingeschaltet im Umschaltmodus.", "По умолчанию включено. Действие только при активации: удержании клавиши или включённом переключателе.", "Activé par défaut. L'action exige la visée activée : touche maintenue ou bascule enclenchée."),
    "Enabled by default. Turn off here at any time. Sends only edition, version, a random installation token once, and a short-lived random session token every 30 minutes during capture. No vision, model, profile, license, account, or hardware data is included. Your choice is saved outside profiles.": ("默认启用，可随时关闭。仅发送版本类型、版本、一次随机安装令牌及捕获时每 30 分钟的临时随机会话令牌。不含视觉、模型、配置、许可证、账户或硬件数据。此选择独立保存。", "Standardmäßig an, jederzeit abschaltbar. Sendet nur Edition, Version, einmal ein zufälliges Installationstoken und beim Aufnehmen alle 30 Minuten ein kurzlebiges Sitzungstoken. Keine Bild-, Modell-, Profil-, Lizenz-, Konto- oder Hardwaredaten. Auswahl außerhalb der Profile gespeichert.", "По умолчанию включено; можно отключить. Только редакция, версия, случайный токен установки однократно и временный токен сеанса каждые 30 минут захвата. Без данных изображений, моделей, профилей, лицензий, аккаунта или оборудования. Выбор хранится отдельно от профилей.", "Activé par défaut, désactivable. Envoie seulement édition, version, jeton aléatoire d'installation une fois et jeton temporaire de session toutes les 30 minutes de capture. Aucune donnée visuelle, de modèle, profil, licence, compte ou matériel. Choix enregistré hors des profils."),
    "End-of-schedule multiplier applied to the initial learning rate.": ("训练结束时相对初始学习率的倍数。", "Endfaktor der anfänglichen Lernrate.", "Множитель начальной скорости обучения в конце расписания.", "Multiplicateur final du taux d'apprentissage initial."),
    "Extra lead beyond measured observation age. Stops, reversals and uncertain identity reset the estimate.": ("在观测延迟外增加提前量。停止、反向或身份不确定会重置估计。", "Zusätzlicher Vorlauf über das Beobachtungsalter. Stopps, Richtungswechsel und unsichere Identität setzen die Schätzung zurück.", "Упреждение сверх возраста наблюдения. Остановка, разворот и неопределённая идентичность сбрасывают оценку.", "Avance au-delà de l'âge observé. Arrêts, inversions et identité incertaine réinitialisent l'estimation."),
    "For this time after acquisition, the selected subject is retained even if another candidate briefly scores better.": ("获取后在此时间内保持目标，即使其他候选短暂得分更高。", "Ziel nach Erfassung so lange behalten, auch wenn ein Kandidat kurz besser abschneidet.", "Удерживает цель это время после захвата, даже если другой кандидат кратко лучше.", "Conserve la cible cette durée après acquisition même si un autre candidat obtient brièvement un meilleur score."),
    "Fraction of annotated images placed in the validation split when the archive is built.": ("打包时分配至验证集的标注图像比例。", "Anteil annotierter Bilder für die Validierung beim Archivieren.", "Доля размеченных изображений для проверки при архивации.", "Part des images annotées affectée à la validation lors de l'archivage."),
    "Gradually ramps learning rate at the beginning of training.": ("训练初期逐渐提高学习率。", "Lernrate zu Trainingsbeginn schrittweise erhöhen.", "Постепенно повышает скорость обучения в начале.", "Augmente progressivement le taux d'apprentissage au début."),
    "Hard cap applied after duplicate suppression to keep tracking work bounded.": ("去重后的数量上限，用于限制跟踪负载。", "Obergrenze nach Duplikatunterdrückung zur Begrenzung der Trackinglast.", "Лимит после подавления дублей для ограничения нагрузки трекинга.", "Limite après suppression des doublons pour borner la charge du suivi."),
    "Hold requires the key to remain pressed. Toggle latches on each press.": ("按住模式需保持按下；切换模式每次按下改变状态。", "Halten erfordert eine gedrückte Taste. Umschalten wechselt bei jedem Druck.", "Удержание требует нажатой клавиши. Переключение меняет состояние при каждом нажатии.", "Maintenir exige une touche enfoncée. Basculer change d'état à chaque pression."),
    "Hold runs while the key is held. Click sends exactly one correction each time the key is pressed; it never toggles. Constant ignores the key. Automatic runs only while a valid target or automatic drift source is available.": ("按住时运行；单击每次按下仅修正一次，不切换；持续忽略按键；自动仅在有效目标或漂移源可用时运行。", "Halten läuft bei gedrückter Taste. Klick sendet eine Korrektur je Druck, ohne Umschalten. Konstant ignoriert die Taste. Automatisch benötigt ein gültiges Ziel oder eine Driftquelle.", "Удержание работает при нажатой клавише. Щелчок отправляет одну коррекцию, без переключения. Постоянный режим игнорирует клавишу. Авто требует цель или источник дрейфа.", "Maintenir agit tant que la touche est enfoncée. Clic envoie une correction par pression, sans bascule. Constant ignore la touche. Automatique exige une cible ou source de dérive valide."),
    "Hold the anchor at the aiming reference within this tolerance. Use zero for no extra settling tolerance.": ("在此容差内将锚点保持在参考点。零表示无额外稳定容差。", "Anker innerhalb dieser Toleranz am Zielreferenzpunkt halten. Null deaktiviert zusätzliche Einrasttoleranz.", "Удерживает якорь у точки прицеливания в этом допуске. Ноль отключает дополнительный допуск стабилизации.", "Maintient l'ancrage au repère dans cette tolérance. Zéro supprime la tolérance supplémentaire."),
    "IPv4 address shown by the KMBox Net device.": ("KMBox Net 显示的 IPv4 地址。", "Vom KMBox Net angezeigte IPv4-Adresse.", "IPv4, показанный KMBox Net.", "Adresse IPv4 affichée par le KMBox Net."),
    "Immediate anchor-association zone. A visible target here is selected ahead of near and outer contenders, unless an explicit class priority or watchlist rule wins.": ("立即关联锚点的区域。此处目标优先于近区和外圈候选，除非类别或关注规则优先。", "Sofortige Ankerzuordnung. Sichtbare Ziele hier haben Vorrang vor nahen und äußeren Kandidaten, außer bei expliziter Klassen- oder Listenpriorität.", "Мгновенная привязка якоря. Цель здесь приоритетнее ближних и внешних кандидатов, кроме явных правил класса или списка наблюдения.", "Association immédiate. La cible ici prime sur les candidats proches et extérieurs, sauf priorité explicite de classe ou de liste de suivi."),
    "Immediately stops the native capture engine and disables all output for the current session.": ("立即停止捕获引擎并禁用当前会话所有输出。", "Stoppt sofort die Aufnahme und alle Ausgaben der Sitzung.", "Немедленно останавливает захват и весь вывод сеанса.", "Arrête immédiatement la capture et toutes les sorties de la session."),
    "Inspect the connected controller's buttons and sticks.": ("检查手柄按键和摇杆。", "Tasten und Sticks des Controllers prüfen.", "Проверка кнопок и стиков контроллера.", "Inspecte les boutons et sticks du contrôleur."),
    "Installs or repairs the local Xbox-compatible controller component after a confirmation and a normal Windows administrator prompt. Nothing is installed by selecting the transport alone.": ("确认并通过 Windows 管理员提示后安装或修复 Xbox 兼容组件。仅选择传输方式不会安装。", "Installiert oder repariert die Xbox-Komponente nach Bestätigung und Administratorabfrage. Die Transportauswahl allein installiert nichts.", "Устанавливает или восстанавливает Xbox-компонент после подтверждения и запроса прав. Выбор транспорта сам ничего не устанавливает.", "Installe ou répare le composant Xbox après confirmation et autorisation administrateur Windows. Choisir le transport n'installe rien."),
    "Installs the bundled local driver after a Windows administrator prompt. A Windows restart is required before the virtual mouse transport can start.": ("经管理员授权安装附带驱动。使用虚拟鼠标前需重启 Windows。", "Installiert den Treiber nach Administratorfreigabe. Vor Nutzung der virtuellen Maus muss Windows neu starten.", "Устанавливает драйвер с правами администратора. Перед виртуальной мышью требуется перезапуск Windows.", "Installe le pilote après autorisation administrateur. Redémarrez Windows avant d'utiliser la souris virtuelle."),
    "Key that authorizes movement output.": ("允许移动输出的按键。", "Taste zur Freigabe der Bewegungsausgabe.", "Клавиша разрешения движения.", "Touche autorisant la sortie de mouvement."),
    "Leave empty for every class, or enter comma-separated numeric class IDs.": ("留空代表所有类别，或输入逗号分隔的类别编号。", "Leer für alle Klassen oder numerische IDs durch Kommas getrennt.", "Пусто для всех классов или числовые ID через запятую.", "Vide pour toutes les classes, ou identifiants numériques séparés par des virgules."),
    "Letterbox preserves source geometry. Stretch fills the model input for models trained that way.": ("等比填充保持几何；拉伸适用于以此方式训练的模型。", "Letterbox bewahrt Geometrie. Strecken füllt die Eingabe entsprechend trainierter Modelle.", "Letterbox сохраняет геометрию. Растяжение заполняет вход моделей, обученных так.", "Letterbox préserve la géométrie. Étirer remplit l'entrée des modèles entraînés ainsi."),
    "Loads the selected ONNX model in the selected provider engine and reports the verified session details without starting capture.": ("在所选引擎加载 ONNX 并报告验证信息，不启动捕获。", "Lädt ONNX im gewählten Provider und meldet geprüfte Sitzungsdetails ohne Aufnahme.", "Загружает ONNX выбранным провайдером и показывает проверенные параметры без захвата.", "Charge ONNX avec le fournisseur choisi et affiche les détails vérifiés sans capture."),
    "Local device code used only to establish the connection. It is not sent to Vapor Lock services or written into diagnostics.": ("仅用于连接的本地设备代码，不发送至服务或写入诊断。", "Gerätecode nur zum Verbinden. Keine Übermittlung an Dienste oder Diagnoseprotokolle.", "Код устройства только для подключения. Не передаётся сервисам и не записывается в диагностику.", "Code local réservé à la connexion. Ni envoyé aux services ni consigné dans les diagnostics."),
    "Manual box editor": ("手动框编辑器", "Manueller Boxeditor", "Редактор рамок", "Éditeur manuel de cadres"),
    "Matched observations required for acquisition, unless the explicit snap bypass is enabled.": ("获取目标所需匹配观测数，启用吸附跳过时除外。", "Passende Beobachtungen zur Erfassung, außer bei aktivierter Snap-Ausnahme.", "Совпавшие наблюдения для захвата, кроме включённого обхода для прилипания.", "Observations concordantes requises, sauf contournement explicite par accrochage."),
    "Maximum anchor distance for reconnecting a target after a detector gap.": ("检测中断后重新关联的最大锚点距离。", "Maximaler Ankerabstand zum Wiederverbinden nach Erkennungslücken.", "Максимальное расстояние якоря для восстановления после пропуска детектора.", "Distance maximale de l'ancrage pour retrouver une cible après interruption."),
    "Maximum annotated frames sent to this control center each second.": ("每秒向控制中心发送的最大标注帧数。", "Maximale annotierte Bilder pro Sekunde an die Oberfläche.", "Максимум размеченных кадров в секунду для панели.", "Nombre maximal d'images annotées par seconde vers le centre de contrôle."),
})
_ROWS.update({
    "Maximum full passes over the training set.": ("训练集最大完整遍历次数。", "Maximale vollständige Durchläufe des Trainingssatzes.", "Максимум полных проходов обучающего набора.", "Nombre maximal de passages complets sur l'ensemble d'entraînement."),
    "Maximum observation age plus extra lead. The separate observation-expiry limit still applies.": ("最大观测年龄加额外提前量。独立观测过期限制仍生效。", "Maximales Beobachtungsalter plus Vorlauf. Die separate Ablaufgrenze gilt weiterhin.", "Максимальный возраст наблюдения плюс упреждение. Отдельный срок устаревания остаётся в силе.", "Âge maximal d'observation plus avance. La limite d'expiration distincte reste applicable."),
    "Maximum retained masks decoded for one preview frame. Lower values reduce post-processing work on crowded scenes.": ("每帧预览最多解码的掩码数。降低可减少密集场景后处理。", "Maximal dekodierte Masken je Vorschaubild. Kleinere Werte reduzieren Nachverarbeitung in dichten Szenen.", "Максимум масок на кадр предпросмотра. Меньшие значения снижают постобработку в плотных сценах.", "Masques décodés au maximum par image. Une valeur basse réduit le post-traitement des scènes denses."),
    "Maximum velocity disagreement allowed between detections considered part of the same shared movement.": ("视为共同运动的检测之间允许的最大速度差。", "Maximaler Geschwindigkeitsunterschied bei gemeinsam bewegten Erkennungen.", "Максимальная разница скоростей обнаружений общего движения.", "Écart maximal de vitesse entre détections appartenant au même mouvement partagé."),
    "Maximum wait for immature near contenders. Repeated replacements do not restart this budget.": ("近区未成熟候选的最长等待时间。反复更换不会重置计时。", "Maximale Wartezeit für junge nahe Kandidaten. Wiederholter Austausch startet sie nicht neu.", "Максимум ожидания незрелых ближних кандидатов. Повторная замена не перезапускает отсчёт.", "Attente maximale des candidats proches non confirmés. Les remplacements ne réinitialisent pas ce délai."),
    "Measures the selected model on the current capture region with output, preview, and dataset writing disabled. Capture must be stopped first.": ("在当前捕获区测试模型，禁用输出、预览及数据写入。须先停止捕获。", "Misst das Modell im Aufnahmebereich ohne Ausgabe, Vorschau oder Datensatzschreiben. Aufnahme vorher stoppen.", "Измеряет модель в области захвата без вывода, предпросмотра и записи набора. Сначала остановите захват.", "Mesure le modèle sur la zone de capture sans sortie, aperçu ni écriture du jeu. Arrêtez d'abord la capture."),
    "Minimal limits the screen to the selected subject. Standard shows all tracks. Diagnostics adds lock-recovery and track-count context.": ("精简仅显示所选目标；标准显示所有轨迹；诊断增加恢复及轨迹数量信息。", "Minimal zeigt nur die Auswahl. Standard zeigt alle Tracks. Diagnose ergänzt Wiederherstellung und Trackanzahl.", "Минимальный режим показывает выбранную цель, стандартный — все треки, диагностика добавляет восстановление и количество треков.", "Minimal affiche la cible choisie. Standard affiche toutes les pistes. Diagnostic ajoute récupération et nombre de pistes."),
    "Minimum decoded mask probability used for the visible instance boundary.": ("可见实例边界所需的最低掩码概率。", "Minimale Maskenwahrscheinlichkeit für die sichtbare Instanzgrenze.", "Минимальная вероятность маски для видимой границы экземпляра.", "Probabilité minimale du masque pour la limite visible de l'instance."),
    "Minimum overlap needed to preserve an existing identity.": ("保持现有身份所需的最小重叠度。", "Mindestüberlappung zum Erhalten einer Identität.", "Минимальное пересечение для сохранения идентичности.", "Recouvrement minimal pour préserver une identité existante."),
    "Minimum same-class overlap required for repeated annotation passes to keep an object.": ("多轮标注保留同类对象所需的最小重叠度。", "Mindestüberlappung gleicher Klassen zur Übernahme über mehrere Annotationsdurchläufe.", "Минимальное пересечение одного класса для сохранения объекта после повторных проходов.", "Recouvrement minimal de même classe pour conserver un objet après plusieurs passages."),
    "Minimum time between saved samples. This keeps data collection intentional and avoids near-duplicate frames.": ("样本保存的最短间隔，避免近似重复帧。", "Mindestabstand zwischen Samples zur Vermeidung fast identischer Bilder.", "Минимальный интервал сохранения для предотвращения почти одинаковых кадров.", "Intervalle minimal entre échantillons pour éviter les images presque identiques."),
    "Name stored inside the profile file.": ("配置文件内保存的名称。", "Im Profil gespeicherter Name.", "Имя, сохранённое в профиле.", "Nom enregistré dans le profil."),
    "Name used for local checkpoints and optional Ultralytics Platform synchronization.": ("本地检查点及可选 Ultralytics 平台同步名称。", "Name für lokale Checkpoints und optionale Ultralytics-Synchronisierung.", "Имя локальных контрольных точек и синхронизации с Ultralytics.", "Nom des points de contrôle locaux et de la synchronisation Ultralytics facultative."),
    "Number of background image-loading workers used during training.": ("训练时后台图像加载进程数。", "Anzahl der Hintergrundprozesse zum Bilderladen beim Training.", "Число фоновых загрузчиков изображений при обучении.", "Nombre de processus de chargement des images pendant l'entraînement."),
    "ONNX files in the project's models folder are discovered here. Refresh after adding or removing files.": ("在此发现 models 文件夹中的 ONNX。增删文件后刷新。", "ONNX-Dateien aus models werden hier erkannt. Nach Änderungen aktualisieren.", "Здесь обнаруживаются ONNX из папки models. Обновите после изменения файлов.", "Les fichiers ONNX du dossier models apparaissent ici. Actualisez après ajout ou suppression."),
    "Off by default. When enabled, Vapor Lock accepts authenticated JSON commands only from this computer unless LAN access is explicitly enabled below.": ("默认关闭。启用后仅接受本机认证 JSON 命令，除非明确开启下方局域网访问。", "Standardmäßig aus. Authentifizierte JSON-Befehle nur von diesem PC, sofern LAN nicht ausdrücklich aktiviert wird.", "По умолчанию выключено. Авторизованные JSON-команды только с этого ПК, пока явно не разрешена локальная сеть.", "Désactivé par défaut. Commandes JSON authentifiées de cet ordinateur uniquement, sauf activation explicite du réseau local."),
    "Off is standard class-aware NMS. Enable when your model emits the same object under several competing classes.": ("关闭为按类别 NMS。模型为同一对象输出多个竞争类别时启用。", "Aus nutzt klassenbezogenes NMS. Aktivieren, wenn dasselbe Objekt unter mehreren Klassen erscheint.", "Выключено: NMS с учётом класса. Включите, если объект выдаётся под несколькими классами.", "Désactivé : NMS par classe. Activez si le modèle attribue plusieurs classes concurrentes au même objet."),
    "Official KMBox A DLL used for the documented KM_init and KM_move calls.": ("用于 KM_init 和 KM_move 的官方 KMBox A DLL。", "Offizielle KMBox-A-DLL für KM_init und KM_move.", "Официальная DLL KMBox A для KM_init и KM_move.", "DLL officielle KMBox A pour KM_init et KM_move."),
    "Open setup and calibration": ("打开设置和校准", "Einrichtung und Kalibrierung öffnen", "Открыть настройку и калибровку", "Ouvrir configuration et étalonnage"),
    "Opt in to retrieve and install signed Vapor Lock updates at launch. Models, profiles, datasets, training runs, configuration, and downloaded runtimes are preserved.": ("选择启动时获取并安装签名更新。保留模型、配置、数据集、训练记录及已下载运行时。", "Signierte Updates beim Start abrufen und installieren. Modelle, Profile, Datensätze, Trainingsläufe, Konfiguration und Laufzeiten bleiben erhalten.", "Получать подписанные обновления при запуске. Модели, профили, наборы, обучение, настройки и среды сохраняются.", "Recevoir les mises à jour signées au lancement. Modèles, profils, jeux, entraînements, configuration et environnements sont préservés."),
    "Optional HTTPS access with bearer authentication. Export the public certificate and trust it in your companion client; never disable certificate verification. Use a trusted private network.": ("可选 HTTPS Bearer 认证访问。导出公有证书并在客户端信任，勿禁用证书验证。使用可信私有网络。", "Optionales HTTPS mit Bearer-Authentifizierung. Öffentliches Zertifikat exportieren und im Client vertrauen; Prüfung nie abschalten. Nur vertrauenswürdige private Netze.", "HTTPS с Bearer-авторизацией. Экспортируйте публичный сертификат и доверьте ему клиент; не отключайте проверку. Используйте доверенную частную сеть.", "HTTPS avec authentification Bearer. Exportez le certificat public et approuvez-le dans le client ; ne désactivez jamais sa vérification. Réseau privé de confiance uniquement."),
    "Optional JSON for augmentation and loss hyperparameters, such as hsv_h, mosaic or box. Use the dedicated controls for other training settings.": ("可选 JSON，用于 hsv_h、mosaic 或 box 等增强和损失超参数。其他训练设置请使用专用控件。", "Optionales JSON für Augmentierungs- und Verlustparameter wie hsv_h, mosaic oder box. Verwenden Sie für andere Trainingseinstellungen die vorgesehenen Steuerelemente.", "Необязательный JSON для гиперпараметров аугментации и функции потерь, например hsv_h, mosaic или box. Для остальных настроек используйте соответствующие элементы управления.", "JSON facultatif pour les hyperparamètres d’augmentation et de perte, tels que hsv_h, mosaic ou box. Utilisez les commandes dédiées pour les autres réglages d’entraînement."),
    "Not applied. The previous valid settings have been restored.": ("未应用。已恢复之前的有效设置。", "Nicht angewendet. Die vorherigen gültigen Einstellungen wurden wiederhergestellt.", "Не применено. Предыдущие допустимые настройки восстановлены.", "Non appliqué. Les paramètres valides précédents ont été rétablis."),
    "Optional account project path, such as username/project. Leave blank for fully local training.": ("可选账户项目路径，如 username/project。留空完全本地训练。", "Optionaler Projektpfad wie username/project. Leer für rein lokales Training.", "Необязательный путь проекта, например username/project. Пусто для локального обучения.", "Chemin de projet facultatif, par exemple username/project. Vide pour un entraînement entièrement local."),
    "Optional and never saved. When supplied with a Platform project, Ultralytics receives the run through its own authenticated training integration.": ("可选且不保存。提供平台项目后，通过 Ultralytics 自身认证集成接收训练记录。", "Optional, nie gespeichert. Bei einem Plattformprojekt empfängt Ultralytics den Lauf über seine authentifizierte Integration.", "Необязательно, не сохраняется. С проектом Platform Ultralytics получает запуск через собственную авторизованную интеграцию.", "Facultatif, jamais enregistré. Avec un projet Platform, Ultralytics reçoit l'entraînement via sa propre intégration authentifiée."),
    "Optional selected-target anchor and prediction visualization. It is disabled by default to keep the live overlay calm and readable.": ("可选目标锚点及预测显示，默认关闭以保持叠加清晰。", "Optionale Anker- und Vorhersageanzeige. Standardmäßig aus für ein ruhiges Overlay.", "Отображение якоря и прогноза. По умолчанию выключено для читаемости оверлея.", "Affichage facultatif de l'ancrage et de la prédiction. Désactivé par défaut pour préserver la lisibilité."),
    "Positive means image drift downward. Negative means upward. Vapor Lock sends the inverse correction.": ("正值向下漂移，负值向上。发送反向修正。", "Positiv: Bilddrift nach unten. Negativ: oben. Korrektur erfolgt entgegengesetzt.", "Плюс: дрейф вниз. Минус: вверх. Коррекция направлена обратно.", "Positif : dérive vers le bas. Négatif : vers le haut. Correction inverse."),
    "Positive means image drift right. Negative means left. Vapor Lock sends the inverse correction.": ("正值向右漂移，负值向左。发送反向修正。", "Positiv: Bilddrift nach rechts. Negativ: links. Korrektur erfolgt entgegengesetzt.", "Плюс: дрейф вправо. Минус: влево. Коррекция направлена обратно.", "Positif : dérive à droite. Négatif : à gauche. Correction inverse."),
    "Press the selected input once to pin the active target. Press again to return to automatic selection. A pinned target still releases when it exceeds the configured lost-target safety limits.": ("按一次固定当前目标，再按恢复自动选择。超过丢失安全限制时仍会释放固定目标。", "Einmal drücken fixiert das Ziel, erneut drücken wählt automatisch. Verlust-Sicherheitsgrenzen lösen auch fixierte Ziele.", "Нажмите для закрепления цели, снова для автовыбора. Пределы безопасности потери освобождают и закреплённую цель.", "Appuyez pour fixer la cible, puis pour revenir à la sélection automatique. Les limites de perte libèrent aussi une cible fixée."),
    "Primary bridge mirrors the physical controller through the Xbox-compatible controller so software that reads Controller 1 receives both normal controller input and Vapor Lock movement. Separate creates an independent controller.": ("主桥接通过 Xbox 兼容控制器镜像真实手柄，使读取控制器 1 的软件同时接收正常输入和移动。独立模式创建单独控制器。", "Primäre Brücke spiegelt den echten Controller über Xbox, sodass Controller 1 normale Eingaben und Vapor-Lock-Bewegung erhält. Separat erstellt einen unabhängigen Controller.", "Основной мост зеркалирует реальный контроллер через Xbox: Controller 1 получает обычный ввод и движение Vapor Lock. Отдельный режим создаёт независимый контроллер.", "Le pont principal reproduit le contrôleur physique via Xbox : Controller 1 reçoit l'entrée normale et le mouvement Vapor Lock. Séparé crée un contrôleur indépendant."),
    "Product ID used when initializing the selected KMBox A device.": ("初始化 KMBox A 的产品 ID。", "Produkt-ID zur Initialisierung des KMBox A.", "ID продукта при инициализации KMBox A.", "Identifiant produit pour initialiser le KMBox A."),
    "Reconnect a briefly missing target to a nearby same-class detection before releasing the established lock.": ("释放锁定前，将短暂丢失目标与附近同类检测重新关联。", "Kurz verlorenes Ziel vor Freigabe mit naher Erkennung derselben Klasse verbinden.", "Восстановить пропавшую цель по близкому обнаружению того же класса до сброса захвата.", "Retrouve une cible brièvement absente via une détection proche de même classe avant de libérer le verrouillage."),
    "Recovery window after the last observation. Ambiguous identities and manual pins are not substituted.": ("最后观测后的恢复窗口。不替换身份不明或手动固定目标。", "Wiederherstellungsfenster nach letzter Beobachtung. Unsichere Identitäten und manuelle Fixierungen werden nicht ersetzt.", "Окно восстановления после наблюдения. Не заменяет неоднозначные идентичности и ручные закрепления.", "Fenêtre de récupération après observation. Identités ambiguës et cibles fixées manuellement ne sont pas remplacées."),
    "Regularization strength used to reduce overfitting.": ("减少过拟合的正则化强度。", "Regularisierungsstärke gegen Überanpassung.", "Сила регуляризации против переобучения.", "Intensité de régularisation contre le surapprentissage."),
    "Remove overlapping duplicate raw predictions after confidence filtering. Final end-to-end boxes are recognized automatically and skip this extra pass.": ("置信度过滤后去除重复原始预测。自动识别最终端到端框并跳过此步骤。", "Überlappende rohe Duplikate nach Konfidenzfilter entfernen. Finale End-to-End-Boxen überspringen diesen Schritt automatisch.", "Удаляет пересекающиеся сырые дубли после фильтра уверенности. Конечные end-to-end рамки распознаются и пропускают этот этап.", "Supprime les doublons bruts après filtrage de confiance. Les cadres finaux end-to-end sont reconnus et évitent cette étape."),
    "Removes the bundled Xbox-compatible component and every virtual controller it created. If another app shares that component, it may need repair afterward. Windows will request administrator approval.": ("删除附带 Xbox 组件及其虚拟控制器。共享该组件的其他应用可能需修复。Windows 将请求管理员授权。", "Entfernt Xbox-Komponente und ihre virtuellen Controller. Andere mitnutzende Apps benötigen eventuell Reparatur. Windows fragt Administratorrechte an.", "Удаляет Xbox-компонент и его виртуальные контроллеры. Другим приложениям может понадобиться восстановление. Windows запросит права администратора.", "Supprime le composant Xbox et ses contrôleurs virtuels. Les autres applications qui l'utilisent peuvent nécessiter réparation. Windows demandera l'autorisation administrateur."),
    "Replace current settings with a local JSON profile.": ("用本地 JSON 配置替换当前设置。", "Aktuelle Einstellungen durch lokales JSON-Profil ersetzen.", "Заменить настройки локальным JSON-профилем.", "Remplace les réglages par un profil JSON local."),
    "Required for MAKCU and Vapor Lock HID bridge output. Use the Windows COM port assigned to the connected device.": ("MAKCU 和 HID 桥接必填。使用设备分配的 Windows COM 端口。", "Für MAKCU und HID-Brücke erforderlich. Zugewiesenen Windows-COM-Port verwenden.", "Нужно для MAKCU и HID-моста. Используйте назначенный Windows COM-порт.", "Requis pour MAKCU et le pont HID. Utilisez le port COM Windows attribué à l'appareil."),
    "Reserve identity after the last real observation. Missing targets never authorize movement.": ("最后真实观测后保留身份。丢失目标不会允许移动。", "Identität nach letzter realer Beobachtung reservieren. Fehlende Ziele erlauben keine Bewegung.", "Сохраняет идентичность после последнего реального наблюдения. Пропавшие цели не разрешают движение.", "Réserve l'identité après la dernière observation réelle. Les cibles absentes n'autorisent jamais le mouvement."),
    "Restore the default Ctrl+Alt shortcut set for this profile.": ("恢复此配置的默认 Ctrl+Alt 快捷键。", "Standard-Ctrl+Alt-Tasten dieses Profils wiederherstellen.", "Восстановить сочетания Ctrl+Alt профиля по умолчанию.", "Rétablit les raccourcis Ctrl+Alt par défaut du profil."),
    "Retained fraction at a 60 Hz reference, normalized by observation time. Higher values add delay.": ("以 60 Hz 为基准的保留比例，按观测时间归一化。越高延迟越大。", "Beibehaltener Anteil bei 60 Hz, nach Beobachtungszeit normiert. Höhere Werte verzögern stärker.", "Сохраняемая доля при 60 Гц, нормированная по времени наблюдения. Высокие значения добавляют задержку.", "Fraction conservée à 60 Hz, normalisée par le temps observé. Une valeur élevée ajoute du retard."),
    "Return every editable setting to the bundled defaults.": ("将所有可编辑设置恢复为附带默认值。", "Alle editierbaren Einstellungen auf mitgelieferte Vorgaben zurücksetzen.", "Вернуть все редактируемые настройки к комплектным значениям.", "Rétablit les valeurs fournies pour tous les réglages modifiables."),
    "Review the model, capture source, activation binding, and movement baseline. The wizard never starts capture automatically.": ("检查模型、捕获源、激活键及移动基准。向导不会自动启动捕获。", "Modell, Quelle, Aktivierung und Bewegungsbasis prüfen. Der Assistent startet die Aufnahme nie automatisch.", "Проверка модели, источника, активации и базового движения. Мастер не запускает захват автоматически.", "Vérifie modèle, source, activation et mouvement de base. L'assistant ne lance jamais la capture automatiquement."),
    "Runs entirely on this computer. Training will also refuse malformed labels even if this check was skipped.": ("完全在本机运行。即使跳过此检查，训练也拒绝无效标签。", "Läuft vollständig lokal. Training weist ungültige Labels auch ohne diese Prüfung ab.", "Выполняется локально. Обучение отклонит неверную разметку даже без этой проверки.", "Exécution entièrement locale. L'entraînement refuse aussi les annotations mal formées si ce contrôle est ignoré."),
    "Runs locally in the background and leaves the control center responsive. The result checkpoint and optional ONNX export are added to the model library.": ("本地后台运行，界面保持响应。结果检查点及可选 ONNX 导出加入模型库。", "Läuft lokal im Hintergrund ohne die Oberfläche zu blockieren. Checkpoint und optionaler ONNX-Export erscheinen in der Modellbibliothek.", "Работает локально в фоне без блокировки панели. Контрольная точка и необязательный ONNX добавляются в библиотеку.", "Fonctionne localement en arrière-plan sans bloquer l'interface. Le point de contrôle et l'export ONNX facultatif rejoignent la bibliothèque."),
    "Save the most recent native annotated debug frame as a PNG in this Windows account's Vapor Lock screenshots folder.": ("将最新标注调试帧保存为 PNG 至当前 Windows 账户的截图文件夹。", "Letztes annotiertes Diagnosebild als PNG im Vapor-Lock-Screenshotordner dieses Windows-Kontos speichern.", "Сохраняет последний отладочный кадр как PNG в папку снимков Vapor Lock этой учётной записи Windows.", "Enregistre la dernière image annotée en PNG dans le dossier de captures Vapor Lock de ce compte Windows."),
    "Saved preset applies a named measured drift pattern. Automatic estimates shared drift from mature detections. Combined uses both.": ("预设应用已测漂移模式；自动从成熟检测估计共同漂移；组合同时使用两者。", "Vorgabe nutzt gemessene Drift. Automatisch schätzt gemeinsame Drift reifer Erkennungen. Kombiniert nutzt beides.", "Пресет применяет измеренный дрейф. Авто оценивает общий дрейф зрелых треков. Комбинированный использует оба.", "Le préréglage applique une dérive mesurée. Automatique estime la dérive commune des détections confirmées. Combiné utilise les deux."),
    "Scale label and annotation size. The slider covers the tested range; type a larger or smaller value for expert display tuning.": ("缩放标签和标注。滑条为已测试范围；可输入其他值微调。", "Label- und Markierungsgröße. Regler deckt getesteten Bereich ab; andere Werte können eingegeben werden.", "Масштаб меток и разметки. Ползунок задаёт проверенный диапазон; другие значения вводятся вручную.", "Taille des étiquettes et annotations. Le curseur couvre la plage testée ; saisissez d'autres valeurs pour un réglage expert."),
    "Scale the relative movement signal before it is written to the selected controller stick.": ("写入所选摇杆前缩放相对移动信号。", "Relatives Bewegungssignal vor Ausgabe an den gewählten Stick skalieren.", "Масштабирует относительное движение перед выводом на выбранный стик.", "Multiplie le mouvement relatif avant l'envoi au stick choisi."),
    "Scales the selected preset, automatic estimate, or their combined correction. Type a precise or higher expert value when needed.": ("缩放预设、自动估计或组合修正。可输入精确值或更高值。", "Vorgabe, Schätzung oder gemeinsame Korrektur skalieren. Präzise oder höhere Expertenwerte direkt eingeben.", "Масштаб пресета, оценки или их суммы. При необходимости введите точное или большее значение.", "Multiplie le préréglage, l'estimation ou leur correction combinée. Saisissez une valeur précise ou supérieure si nécessaire."),
})
_ROWS.update({
    "Seed used by the training run. Zero is a valid fixed seed.": ("训练随机种子。零是有效固定种子。", "Zufallsstartwert des Trainings. Null ist ein gültiger fester Wert.", "Зерно обучения. Ноль является допустимым фиксированным значением.", "Graine aléatoire de l'entraînement. Zéro est une valeur fixe valide."),
    "Send an immediate pin or release request to the active native engine. It works only while manual target lock is enabled and a live target is selected.": ("立即发送固定或释放请求。需启用手动锁定且选中实时目标。", "Sofort fixieren oder lösen. Erfordert manuelle Zielsperre und ein ausgewähltes Live-Ziel.", "Немедленно закрепить или отпустить. Требует ручной фиксации и выбранной видимой цели.", "Fixe ou libère immédiatement. Nécessite le verrouillage manuel et une cible active choisie."),
    "Send the selected mouse action only while the current, confidence-qualified target is inside the Target Policy snap radius. Physical use of the same button always takes priority.": ("仅当当前目标满足置信度且位于吸附半径内时发送鼠标动作。真实按键始终优先。", "Mausaktion nur bei ausreichend sicherem Ziel im Snap-Radius. Physische Nutzung derselben Taste hat immer Vorrang.", "Действие мыши только для цели с нужной уверенностью внутри радиуса прилипания. Физическая кнопка всегда приоритетнее.", "Action souris seulement si la cible satisfait la confiance et reste dans le rayon d'accrochage. Le bouton physique a toujours priorité."),
    "Sends an immediate request to the native engine. Manual target lock must be enabled and an active target must exist.": ("立即请求引擎。需启用手动锁定并存在活动目标。", "Sofortige Anfrage an die Engine. Manuelle Sperre und aktives Ziel erforderlich.", "Немедленный запрос движку. Нужны ручная фиксация и активная цель.", "Requête immédiate au moteur. Verrouillage manuel et cible active requis."),
    "Setup wizard": ("设置向导", "Einrichtungsassistent", "Мастер настройки", "Assistant de configuration"),
    "Show a compact Vapor Lock status strip in the upper-left corner of the overlay.": ("在叠加层左上显示紧凑状态栏。", "Kompakte Statusleiste oben links im Overlay anzeigen.", "Компактная строка состояния в левом верхнем углу оверлея.", "Barre d'état compacte en haut à gauche de la surimpression."),
    "Show or hide the active overlay without stopping capture. A global shortcut can toggle this during a session.": ("不停止捕获即可显隐叠加层。会话中可用全局快捷键切换。", "Overlay ohne Aufnahmestopp einblenden oder ausblenden. Globaler Tastenkürzel während der Sitzung verfügbar.", "Показывает или скрывает оверлей без остановки захвата. Доступно глобальное сочетание клавиш.", "Affiche ou masque la surimpression sans arrêter la capture. Un raccourci global permet de basculer pendant la session."),
    "Show the track identity, model label, and confidence beside each live detection.": ("在实时检测旁显示轨迹 ID、模型标签及置信度。", "Track-ID, Modellbezeichnung und Konfidenz neben jeder Erkennung.", "ID трека, метка модели и уверенность возле каждого обнаружения.", "Identité de piste, étiquette et confiance près de chaque détection."),
    "Shows every gate between input activation and a Windows movement event. Counters reset with each observation session.": ("显示激活到 Windows 移动事件间的所有条件。计数每次会话重置。", "Alle Freigabestufen zwischen Aktivierung und Windows-Bewegung. Zähler werden je Sitzung zurückgesetzt.", "Все условия между активацией и событием движения Windows. Счётчики сбрасываются каждый сеанс.", "Affiche chaque condition entre activation et mouvement Windows. Les compteurs sont réinitialisés par session."),
    "Single press to pin the current target; press again to release it.": ("按一次固定当前目标，再按释放。", "Einmal drücken fixiert, erneut drücken löst das Ziel.", "Нажмите для фиксации цели, ещё раз для освобождения.", "Appuyez pour fixer la cible, à nouveau pour la libérer."),
    "Slider values top out at 120 fps; type a higher value when your model can sustain it.": ("滑条最高 120 fps；模型支持时可输入更高值。", "Regler bis 120 fps; höhere Werte direkt eingeben, wenn das Modell sie schafft.", "Ползунок до 120 fps; введите больше, если модель справляется.", "Curseur limité à 120 fps ; saisissez davantage si le modèle le permet."),
    "Slider values top out at 120 fps; type a higher value when your source and hardware support it.": ("滑条最高 120 fps；源和硬件支持时可输入更高值。", "Regler bis 120 fps; höhere Werte bei geeigneter Quelle und Hardware direkt eingeben.", "Ползунок до 120 fps; введите больше при поддержке источника и оборудования.", "Curseur limité à 120 fps ; saisissez davantage si la source et le matériel le permettent."),
    "Square training resolution. Larger values can improve detail at a substantial memory and time cost.": ("方形训练分辨率。越大细节越多，但内存和时间成本显著增加。", "Quadratische Trainingsauflösung. Größer erhält Details, kostet aber viel Speicher und Zeit.", "Квадратное разрешение обучения. Больше деталей ценой памяти и времени.", "Résolution carrée d'entraînement. Plus élevée, elle améliore les détails mais coûte beaucoup de mémoire et de temps."),
    "Stabilize displayed detection boxes. Anchor stabilization independently filters real observations so these filters do not stack their delays.": ("稳定显示框。锚点单独过滤真实观测，因此不会叠加延迟。", "Angezeigte Boxen stabilisieren. Anker filtert reale Beobachtungen separat, ohne Verzögerungen zu addieren.", "Стабилизация рамок. Якорь отдельно фильтрует реальные наблюдения, не суммируя задержки.", "Stabilise les cadres. L'ancrage filtre indépendamment les observations réelles, sans cumuler les retards."),
    "Stabilizes automatic drift estimates between updates. Higher values respond more slowly.": ("稳定更新间的漂移估计。越高响应越慢。", "Driftschätzungen glätten. Höhere Werte reagieren langsamer.", "Стабилизирует оценки дрейфа. Высокие значения реагируют медленнее.", "Stabilise les estimations de dérive. Une valeur élevée réagit plus lentement."),
    "Starting learning rate. This has a large effect on convergence and stability.": ("初始学习率，显著影响收敛和稳定性。", "Anfängliche Lernrate mit starkem Einfluss auf Konvergenz und Stabilität.", "Начальная скорость обучения, сильно влияющая на сходимость и стабильность.", "Taux d'apprentissage initial, déterminant pour la convergence et la stabilité."),
    "Starts a ready capture session or stops the currently running native engine.": ("启动就绪捕获会话或停止正在运行的引擎。", "Bereite Aufnahme starten oder laufende Engine stoppen.", "Запускает готовый захват или останавливает движок.", "Démarre la capture prête ou arrête le moteur actif."),
    "Starts the selected model briefly against the configured capture region and confirms that it loads, accepts frames, and produces stable native timing samples. Capture must be stopped first.": ("短暂运行模型验证加载、帧输入及稳定计时。需先停止捕获。", "Modell kurz im Aufnahmebereich starten, um Laden, Bildeingabe und stabile Laufzeiten zu prüfen. Aufnahme vorher stoppen.", "Кратко запускает модель в области захвата для проверки загрузки, кадров и стабильных замеров. Сначала остановите захват.", "Teste brièvement le modèle sur la zone choisie : chargement, images et mesures stables. Arrêtez d'abord la capture."),
    "Stick that receives relative movement while the selected target is active.": ("目标激活时接收相对移动的摇杆。", "Stick für relative Bewegung bei aktivem Ziel.", "Стик относительного движения при активной цели.", "Stick recevant le mouvement relatif lorsque la cible est active."),
    "Stop after this many non-improving epochs. Use zero to disable early stopping.": ("连续这么多轮无改善后停止。零禁用提前停止。", "Nach so vielen Epochen ohne Verbesserung stoppen. Null deaktiviert vorzeitiges Stoppen.", "Остановиться после этого числа эпох без улучшения. Ноль отключает раннюю остановку.", "Arrête après ce nombre d'époques sans progrès. Zéro désactive l'arrêt anticipé."),
    "Stop annotation or archive creation without closing Vapor Lock. Completed, failed, and cancelled tasks always release the worker for another run.": ("不关闭程序即可停止标注或打包。完成、失败和取消均释放任务进程以再次运行。", "Annotation oder Archivierung ohne Programmende stoppen. Abgeschlossene, fehlgeschlagene und abgebrochene Aufgaben geben den Worker frei.", "Останавливает разметку или архив без закрытия программы. Завершение, ошибка и отмена освобождают процесс для нового запуска.", "Arrête l'annotation ou l'archivage sans fermer l'application. Fin, échec et annulation libèrent le processus pour une nouvelle tâche."),
    "Stop the active local training process without closing Vapor Lock.": ("不关闭程序即可停止本地训练。", "Lokales Training stoppen, ohne Vapor Lock zu schließen.", "Остановить обучение без закрытия Vapor Lock.", "Arrête l'entraînement local sans fermer Vapor Lock."),
    "Stops an active capture session before loading the next local profile, so the control center and native engine never disagree about active settings.": ("加载下一配置前停止捕获，保持界面和引擎设置一致。", "Stoppt Aufnahme vor Profilwechsel, damit Oberfläche und Engine dieselben aktiven Einstellungen haben.", "Останавливает захват до смены профиля для согласованности панели и движка.", "Arrête la capture avant de changer de profil afin de garder l'interface et le moteur cohérents."),
    "Suppress duplicate boxes whose overlap is above this value.": ("抑制重叠度高于此值的重复框。", "Doppelte Boxen über dieser Überlappung unterdrücken.", "Подавляет дубли с пересечением выше этого значения.", "Supprime les cadres doubles dépassant ce recouvrement."),
    "The port for localhost or optional LAN access. Changing it restarts the local API when enabled.": ("本机或局域网端口。更改后重启已启用的本地 API。", "Port für localhost oder LAN. Änderung startet eine aktive lokale API neu.", "Порт localhost или сети. Изменение перезапускает включённый API.", "Port local ou réseau. Le modifier redémarre l'API si elle est active."),
    "The selected Xbox button or trigger acts exactly like the configured hold or toggle activation key. Activation reads the chosen binding. An enabled controller-output route is configured separately.": ("所选 Xbox 按键或扳机等同于按住或切换激活键。激活读取绑定，手柄输出单独配置。", "Xbox-Taste oder Trigger wirkt wie die Halte-/Umschalttaste. Aktivierung liest diese Bindung; Controller-Ausgabe wird separat konfiguriert.", "Кнопка или триггер Xbox действует как клавиша удержания или переключения. Активация читает привязку, вывод контроллера настраивается отдельно.", "Le bouton ou la gâchette Xbox agit comme la touche de maintien ou bascule. L'activation lit cette liaison ; la sortie contrôleur se configure séparément."),
    "Toggles a configured on-screen overlay while capture is active. Enabling it during an existing session starts the lightweight native overlay stream without restarting capture.": ("捕获中切换叠加层。启用时启动轻量原生叠加流，无需重启捕获。", "Overlay während Aufnahme umschalten. Aktivieren startet den leichten Overlay-Datenstrom ohne Aufnahmeneustart.", "Переключает оверлей во время захвата. Включение запускает лёгкий поток без перезапуска захвата.", "Bascule la surimpression pendant la capture. L'activation démarre le flux léger sans redémarrer la capture."),
    "Trainable .pt checkpoints and .yaml model definitions from the local models library. ONNX files are deployment exports and cannot be used as a training base.": ("本地模型库中的可训练 .pt 和 .yaml。ONNX 为部署导出，不可作为训练基础。", "Trainierbare .pt-Checkpoints und .yaml-Definitionen aus models. ONNX-Exporte sind keine Trainingsbasis.", "Обучаемые .pt и определения .yaml из библиотеки. ONNX — экспорт для запуска, не основа обучения.", "Points de contrôle .pt et définitions .yaml de la bibliothèque. Les exports ONNX ne sont pas des bases d'entraînement."),
    "UDP port shown by the KMBox Net device.": ("KMBox Net 显示的 UDP 端口。", "UDP-Port des KMBox Net.", "UDP-порт KMBox Net.", "Port UDP affiché par le KMBox Net."),
    "Update notices": ("更新通知", "Update-Hinweise", "Уведомления об обновлениях", "Notifications de mise à jour"),
    "Use a fraction of the prepared dataset for a faster experimental run.": ("使用部分数据集进行更快实验。", "Datensatzanteil für schnellere Experimente verwenden.", "Часть набора для быстрого эксперимента.", "Utilise une fraction du jeu pour une expérience plus rapide."),
    "Use auto to let Ultralytics choose a memory-safe batch size, or enter a positive integer.": ("auto 让 Ultralytics 选择内存安全批量，或输入正整数。", "auto wählt eine speichersichere Batchgröße, alternativ positive Ganzzahl eingeben.", "auto выбирает безопасный для памяти пакет; иначе введите положительное целое число.", "auto choisit un lot adapté à la mémoire ; sinon saisissez un entier positif."),
    "Use the same hold or lock input selected for movement. Disable only when intentionally collecting an untimed source set.": ("使用移动的按住或锁定键。仅需不受触发限制采集时禁用。", "Dieselbe Halte-/Sperrtaste wie Bewegung nutzen. Nur für bewusst zeitlich ungebundene Sammlung deaktivieren.", "Использует активацию движения. Отключайте только для намеренного сбора без привязки к нажатию.", "Utilise l'activation du mouvement. Désactivez uniquement pour une collecte volontaire sans déclenchement temporel."),
    "Use the slider for a comfortable visible range, or type an exact opacity percentage.": ("使用滑条或输入精确不透明度百分比。", "Regler nutzen oder genaue Deckkraft in Prozent eingeben.", "Используйте ползунок или введите точную непрозрачность в процентах.", "Utilisez le curseur ou saisissez le pourcentage exact d'opacité."),
    "Used by Hold and Click once. The default is left mouse button. It has no effect in Constant or Automatic mode.": ("用于按住及单击，默认鼠标左键。持续和自动模式无效。", "Für Halten und Einzelklick, standardmäßig linke Maustaste. Ohne Wirkung bei Konstant oder Automatisch.", "Для удержания и одного щелчка, по умолчанию левая кнопка. Не влияет на постоянный и автоматический режимы.", "Pour Maintenir et Clic unique, bouton gauche par défaut. Sans effet en Constant ou Automatique."),
    "Used for the selected serial device. MAKCU legacy firmware commonly uses 115200; use the device's configured rate when it differs.": ("串口设备速率。旧 MAKCU 常用 115200；按设备实际配置设置。", "Rate des seriellen Geräts. Ältere MAKCU-Firmware nutzt meist 115200; tatsächliche Geräteeinstellung beachten.", "Скорость последовательного устройства. Старый MAKCU обычно 115200; используйте настроенную на устройстве скорость.", "Débit série. L'ancien micrologiciel MAKCU utilise souvent 115200 ; respectez le réglage réel de l'appareil."),
    "Vendor ID used when initializing the selected KMBox A device.": ("初始化 KMBox A 的厂商 ID。", "Hersteller-ID zur Initialisierung des KMBox A.", "ID производителя при инициализации KMBox A.", "Identifiant fournisseur pour initialiser le KMBox A."),
    "Virtual mouse component": ("虚拟鼠标组件", "Virtuelle Mauskomponente", "Компонент виртуальной мыши", "Composant de souris virtuelle"),
    "Virtual mouse opt-in": ("启用虚拟鼠标", "Virtuelle Maus freigeben", "Разрешить виртуальную мышь", "Activer la souris virtuelle"),
    "Weight-update algorithm used by the training run.": ("训练使用的权重更新算法。", "Algorithmus zur Gewichtsaktualisierung beim Training.", "Алгоритм обновления весов обучения.", "Algorithme de mise à jour des poids pendant l'entraînement."),
    "When enabled during a native session, Vapor Lock writes clean source images only. Annotation is a separate background action, so the active detector never becomes the training authority.": ("启用后仅保存干净源图像。标注为独立后台任务，不由活动检测器决定训练标签。", "Speichert nur unveränderte Quellbilder. Annotation erfolgt separat im Hintergrund; der aktive Detektor bestimmt nicht die Trainingslabels.", "Сохраняет только чистые исходные кадры. Разметка выполняется отдельно в фоне, активный детектор не определяет обучающие метки.", "Enregistre uniquement les images sources propres. L'annotation reste une tâche séparée ; le détecteur actif ne détermine pas les labels d'entraînement."),
    "When enabled, a fresh target inside Snap may bypass minimum observation count. Disable to reject single-observation candidates everywhere.": ("启用后吸附区新目标可跳过最小观测数。禁用则全域拒绝单次观测候选。", "Frische Ziele im Snap dürfen die Mindestanzahl umgehen. Ausschalten verwirft überall Kandidaten mit nur einer Beobachtung.", "Свежая цель внутри Snap может обойти минимум наблюдений. Выключите для отклонения единичных кандидатов везде.", "Une cible récente dans Snap peut éviter le minimum d'observations. Désactivez pour rejeter partout les candidats observés une seule fois."),
    "When populated, listed classes win ties and switching decisions. It does not hide other allowed classes, so an empty result can still recover safely.": ("所列类别在平局和切换时优先。不隐藏其他允许类别，结果为空时仍可恢复。", "Gelistete Klassen gewinnen Gleichstände und Wechselentscheidungen. Andere erlaubte Klassen bleiben für sichere Wiederherstellung sichtbar.", "Указанные классы выигрывают равенства и переключения. Остальные разрешённые классы видимы для восстановления при пустом результате.", "Les classes listées priment lors d'égalités et changements. Les autres classes autorisées restent visibles pour permettre la récupération."),
    "When you deliberately move the selected physical stick, its input wins until you release it.": ("主动移动所选真实摇杆时，其输入优先直到释放。", "Bewusst bewegter physischer Stick hat bis zum Loslassen Vorrang.", "Движение реального стика приоритетно до его отпускания.", "Le stick physique déplacé volontairement a priorité jusqu'à son relâchement."),
    "While a selected track is lost, reserve its last confirmed anchor through the lost-target hold interval. Nearby same-class recovery also respects the Occlusion recovery radius.": ("目标丢失时在保留期内保留最后确认锚点。附近同类恢复也受遮挡恢复半径限制。", "Letzten bestätigten Anker während Verlust-Haltezeit reservieren. Wiederherstellung naher gleicher Klassen beachtet auch den Verdeckungsradius.", "Сохраняет последний якорь на время удержания потерянной цели. Восстановление того же класса учитывает радиус окклюзии.", "Réserve le dernier ancrage pendant le délai de perte. La récupération proche de même classe respecte aussi le rayon d'occlusion."),
    "Wide new-target scan boundary. For a predictable hierarchy, keep this value at or above the Near and Snap radii.": ("新目标宽扫描边界。建议不小于近区及吸附半径。", "Äußere Suchgrenze für neue Ziele. Für klare Hierarchie mindestens so groß wie Nah- und Snap-Radius halten.", "Широкая граница поиска новых целей. Для понятной иерархии не меньше радиусов Near и Snap.", "Limite large de recherche des nouvelles cibles. Gardez-la au moins égale aux rayons Near et Snap."),
    "Write the complete configuration to a new JSON file.": ("将完整配置写入新 JSON 文件。", "Gesamte Konfiguration in neue JSON-Datei schreiben.", "Записать всю конфигурацию в новый JSON.", "Enregistre la configuration complète dans un nouveau fichier JSON."),
    "Writes the newest annotated native debug preview as a PNG. This shortcut never records desktop video or changes capture settings.": ("将最新标注预览保存为 PNG。此快捷键不录制桌面视频或修改捕获设置。", "Neueste annotierte Vorschau als PNG speichern. Zeichnet kein Desktopvideo auf und ändert keine Aufnahmeeinstellungen.", "Сохраняет последний размеченный кадр как PNG. Не записывает видео рабочего стола и не меняет захват.", "Enregistre le dernier aperçu annoté en PNG. Ne filme pas le bureau et ne modifie aucun réglage de capture."),
    "x offset, y offset, width, height. 0, 0, 640, 640 captures a centered 640 x 640 frame. Negative offsets move left/up; positive offsets move right/down.": ("x 偏移、y 偏移、宽、高。0, 0, 640, 640 表示居中 640 x 640。负值向左/上，正值向右/下。", "x-Versatz, y-Versatz, Breite, Höhe. 0, 0, 640, 640 ergibt mittige 640 x 640. Negativ links/oben, positiv rechts/unten.", "Смещение x, y, ширина, высота. 0, 0, 640, 640 — центрированный кадр 640 x 640. Минус влево/вверх, плюс вправо/вниз.", "Décalage x, y, largeur, hauteur. 0, 0, 640, 640 capture 640 x 640 au centre. Négatif vers gauche/haut, positif vers droite/bas."),
    "Working: {current}/{total} · {file}": ("处理中：{current}/{total} · {file}", "In Arbeit: {current}/{total} · {file}", "Обработка: {current}/{total} · {file}", "Traitement : {current}/{total} · {file}"),
    "Annotating {images} source images in the background.": ("后台标注 {images} 张源图像。", "{images} Quellbilder im Hintergrund annotieren.", "Фоновая разметка {images} изображений.", "Annotation de {images} images en arrière-plan."),
    "Annotation complete: {images} images labeled.": ("标注完成：{images} 张图像。", "Annotation abgeschlossen: {images} Bilder.", "Разметка завершена: {images} изображений.", "Annotation terminée : {images} images."),
    "Ultralytics ZIP ready: {archive}": ("Ultralytics ZIP 就绪：{archive}", "Ultralytics-ZIP bereit: {archive}", "Архив Ultralytics готов: {archive}", "ZIP Ultralytics prêt : {archive}"),
    "Dataset task stopped: {message}": ("数据集任务停止：{message}", "Datensatzaufgabe gestoppt: {message}", "Задача набора остановлена: {message}", "Tâche du jeu arrêtée : {message}"),
    "Dataset task stopped. Review the Status & Diagnostics log.": ("数据集任务停止。请查看状态与诊断日志。", "Datensatzaufgabe gestoppt. Status- und Diagnoseprotokoll prüfen.", "Задача остановлена. Проверьте журнал состояния и диагностики.", "Tâche arrêtée. Consultez le journal d'état et de diagnostic."),
    "Stopping background dataset task…": ("正在停止后台数据集任务…", "Hintergrundaufgabe wird gestoppt…", "Остановка фоновой задачи…", "Arrêt de la tâche en arrière-plan…"),
    "Starting background annotation from {dataset}…": ("正在后台标注 {dataset}…", "Hintergrundannotation von {dataset} startet…", "Запуск фоновой разметки {dataset}…", "Démarrage de l'annotation de {dataset}…"),
    "Building Ultralytics ZIP…": ("正在创建 Ultralytics ZIP…", "Ultralytics-ZIP wird erstellt…", "Создание архива Ultralytics…", "Création du ZIP Ultralytics…"),
})
_ROWS.update({
    "Settings guide": ("设置指南", "Einstellungsleitfaden", "Руководство по настройкам", "Guide des réglages"),
    "Slate light": ("石板浅色", "Schiefer hell", "Светлый сланец", "Ardoise claire"),
    "Slate light high contrast": ("石板浅色高对比度", "Schiefer hell, hoher Kontrast", "Светлый сланец, высокий контраст", "Ardoise claire, contraste élevé"),
    "Teal dark": ("青绿深色", "Türkis dunkel", "Тёмный бирюзовый", "Turquoise sombre"),
    "Teal dark high contrast": ("青绿深色高对比度", "Türkis dunkel, hoher Kontrast", "Тёмный бирюзовый, высокий контраст", "Turquoise sombre, contraste élevé"),
    "Violet dark": ("紫罗兰深色", "Violett dunkel", "Тёмный фиолетовый", "Violet sombre"),
    "Violet dark high contrast": ("紫罗兰深色高对比度", "Violett dunkel, hoher Kontrast", "Тёмный фиолетовый, высокий контраст", "Violet sombre, contraste élevé"),
    "Amber dark": ("琥珀深色", "Bernstein dunkel", "Тёмный янтарный", "Ambre sombre"),
    "Amber dark high contrast": ("琥珀深色高对比度", "Bernstein dunkel, hoher Kontrast", "Тёмный янтарный, высокий контраст", "Ambre sombre, contraste élevé"),
    "Rose dark": ("玫瑰深色", "Rosé dunkel", "Тёмный розовый", "Rose sombre"),
    "Rose dark high contrast": ("玫瑰深色高对比度", "Rosé dunkel, hoher Kontrast", "Тёмный розовый, высокий контраст", "Rose sombre, contraste élevé"),
    "Secondary text": ("次要文字", "Sekundärer Text", "Вторичный текст", "Texte secondaire"),
    "Control borders": ("控件边框", "Steuerelementränder", "Границы элементов", "Bordures des commandes"),
    "Primary button text": ("主要按钮文字", "Text der Hauptschaltfläche", "Текст основной кнопки", "Texte du bouton principal"),
    "Control outlines, dividers, and scroll indicators, independent of text color.": ("控件轮廓、分隔线与滚动指示，独立于文字颜色。", "Konturen, Trennlinien und Scrollanzeigen unabhängig von der Textfarbe.", "Контуры, разделители и индикаторы прокрутки независимо от цвета текста.", "Contours, séparateurs et indicateurs de défilement indépendants du texte."),
    "Text on primary action buttons, independent of the panel color.": ("主要操作按钮文字，独立于面板颜色。", "Text auf Hauptaktionsschaltflächen unabhängig von der Panelfarbe.", "Текст основных кнопок независимо от цвета панели.", "Texte des boutons principaux indépendant de la couleur du panneau."),
    "Descriptions and secondary labels, independent of control borders.": ("描述与次要标签，独立于控件边框。", "Beschreibungen und sekundäre Beschriftungen unabhängig von Rändern.", "Описания и вторичные подписи независимо от границ.", "Descriptions et libellés secondaires indépendants des bordures."),
    "Choose one of twelve themes. High contrast themes use opaque surfaces and stronger edges; preview and overlay colors stay separate.": ("从十二个主题中选择。高对比度主题使用不透明表面与清晰边缘；预览和叠加颜色保持独立。", "Wählen Sie eines von zwölf Themes. Hoher Kontrast nutzt undurchsichtige Flächen und klare Kanten; Vorschau- und Overlayfarben bleiben getrennt.", "Выберите одну из двенадцати тем. Высокий контраст использует непрозрачные поверхности и чёткие границы; цвета предпросмотра и оверлея независимы.", "Choisissez parmi douze thèmes. Le contraste élevé utilise des surfaces opaques et des contours renforcés ; aperçu et superposition restent indépendants."),
    "Restore this theme's default colors and opacities. Other themes keep their saved customization.": ("恢复当前主题的默认颜色与不透明度。其他主题保留已保存的自定义设置。", "Standardfarben und Deckkraft dieses Themes wiederherstellen. Andere Themes behalten ihre Anpassungen.", "Восстановить цвета и непрозрачность этой темы. Другие темы сохраняют свои настройки.", "Rétablir les couleurs et opacités de ce thème. Les autres thèmes conservent leur personnalisation."),
    "Read the offline settings and troubleshooting guide.": ("阅读离线设置与故障排查指南。", "Offline-Leitfaden für Einstellungen und Fehlerbehebung lesen.", "Открыть автономное руководство по настройке и устранению проблем.", "Lire le guide hors ligne des réglages et du dépannage."),
    "Find in guide": ("在指南中查找", "Im Leitfaden suchen", "Поиск в руководстве", "Rechercher dans le guide"),
    "Find next": ("查找下一个", "Weitersuchen", "Найти далее", "Suivant"),
    "Topic index": ("主题索引", "Themenübersicht", "Список тем", "Index des sujets"),
    "Increase text size": ("放大文字", "Text vergrößern", "Увеличить текст", "Agrandir le texte"),
    "Decrease text size": ("缩小文字", "Text verkleinern", "Уменьшить текст", "Réduire le texte"),
    "Offline guide · Select text to copy · Ctrl+F to find": ("离线指南 · 选择文字即可复制 · Ctrl+F 查找", "Offline-Leitfaden · Text zum Kopieren markieren · Ctrl+F für Suche", "Автономное руководство · Выделите текст для копирования · Ctrl+F для поиска", "Guide hors ligne · Sélectionnez le texte à copier · Ctrl+F pour rechercher"),
    "Match found": ("找到匹配文字", "Treffer gefunden", "Совпадение найдено", "Résultat trouvé"),
    "Customized palette": ("自定义调色板", "Angepasste Palette", "Настроенная палитра", "Palette personnalisée"),
    "Theme defaults": ("主题默认设置", "Theme-Standardwerte", "Стандартная палитра темы", "Palette du thème"),
    "No matching text": ("未找到匹配文字", "Kein Treffer", "Совпадений нет", "Aucun résultat"),
})
_ROWS.update({
    "Open the bundled guide in your browser.": ("在浏览器中打开随附指南。", "Den mitgelieferten Leitfaden im Browser öffnen.", "Открыть прилагаемое руководство в браузере.", "Ouvrir le guide fourni dans votre navigateur."),
    "The bundled guide could not be opened. Check the guides folder in your installation.": ("无法打开随附指南。请检查安装目录中的 guides 文件夹。", "Der mitgelieferte Leitfaden konnte nicht geöffnet werden. Prüfen Sie den Ordner guides in Ihrer Installation.", "Не удалось открыть руководство. Проверьте папку guides в каталоге установки.", "Impossible d’ouvrir le guide fourni. Vérifiez le dossier guides de votre installation."),
    "Choose one of twelve themes, each with its own background and translucent panels. Preview and overlay colors stay separate.": ("选择十二个主题之一，每个主题都有独特背景和半透明面板。预览和叠加颜色保持独立。", "Wählen Sie eines von zwölf Themes mit eigenem Hintergrund und durchscheinenden Bereichen. Vorschau- und Overlayfarben bleiben unabhängig.", "Выберите одну из двенадцати тем с собственным фоном и полупрозрачными панелями. Цвета предпросмотра и оверлея независимы.", "Choisissez parmi douze thèmes avec leurs propres fonds et panneaux translucides. Les couleurs d’aperçu et de superposition restent indépendantes."),
})
_ROWS.update({
    "All categories": ("所有类别", "Alle Kategorien", "Все категории", "Toutes les catégories"),
    "Category": ("类别", "Kategorie", "Категория", "Catégorie"),
    "Category / game / model": ("类别 / 游戏 / 模型", "Kategorie / Spiel / Modell", "Категория / игра / модель", "Catégorie / jeu / modèle"),
    "Segmentation": ("分割", "Segmentierung", "Сегментация", "Segmentation"),
    "Upgrade": ("升级", "Aktualisieren", "Обновить", "Mettre à jour"),
    "Checking for a verified upgrade…": ("正在检查已验证的更新…", "Ein geprüftes Update wird gesucht…", "Поиск проверенного обновления…", "Recherche d’une mise à jour vérifiée…"),
    "No verified upgrade could be retrieved. Check your connection and try Upgrade again.": ("无法获取已验证的更新。请检查连接，然后再次选择升级。", "Kein geprüftes Update verfügbar. Prüfen Sie die Verbindung und wählen Sie erneut Aktualisieren.", "Не удалось получить проверенное обновление. Проверьте соединение и повторите обновление.", "Impossible d’obtenir une mise à jour vérifiée. Vérifiez la connexion, puis réessayez."),
    "Check again": ("重新检查", "Erneut prüfen", "Проверить снова", "Vérifier à nouveau"),
    "Checking Start policy…": ("正在检查启动策略…", "Startregel wird geprüft…", "Проверка правил запуска…", "Vérification de la règle de démarrage…"),
    "UPDATE REQUIRED": ("需要更新", "UPDATE ERFORDERLICH", "ТРЕБУЕТСЯ ОБНОВЛЕНИЕ", "MISE À JOUR REQUISE"),
    "POLICY UNAVAILABLE": ("策略不可用", "STARTREGEL NICHT VERFÜGBAR", "ПРАВИЛА НЕДОСТУПНЫ", "RÈGLE INDISPONIBLE"),
    "Update required before starting capture. Installed: {current}. Minimum required: {minimum}.\nReason: {reason}": ("启动捕获前需要更新。已安装：{current}。最低要求：{minimum}。\n原因：{reason}", "Vor dem Aufnahmestart ist ein Update erforderlich. Installiert: {current}. Mindestversion: {minimum}.\nGrund: {reason}", "Перед запуском захвата требуется обновление. Установлено: {current}. Минимальная версия: {minimum}.\nПричина: {reason}", "Mise à jour requise avant la capture. Version installée : {current}. Minimum requis : {minimum}.\nMotif : {reason}"),
    "Start policy could not be verified. Connect to the internet and check again. Your license has not been changed.": ("无法验证启动策略。请连接互联网后重试。许可证未被更改。", "Die Startregel konnte nicht geprüft werden. Verbinden Sie sich mit dem Internet und prüfen Sie erneut. Ihre Lizenz wurde nicht geändert.", "Не удалось проверить правила запуска. Подключитесь к интернету и повторите проверку. Лицензия не изменена.", "Impossible de vérifier la règle de démarrage. Connectez-vous à Internet et réessayez. Votre licence n’a pas été modifiée."),
    "Stop capture before updating.": ("更新前请停止捕获。", "Stoppen Sie die Aufnahme vor dem Update.", "Остановите захват перед обновлением.", "Arrêtez la capture avant la mise à jour."),
    "The updater closes Vapor Lock. Save your active profile before continuing. Continue?": ("更新程序将关闭 Vapor Lock。继续前请保存当前配置。是否继续？", "Das Update schließt Vapor Lock. Speichern Sie vorher Ihr aktives Profil. Fortfahren?", "Обновление закроет Vapor Lock. Сохраните текущий профиль. Продолжить?", "La mise à jour ferme Vapor Lock. Enregistrez votre profil actif avant de continuer. Continuer ?"),
    "No verified upgrade currently meets the required version. Check again later.": ("目前没有满足版本要求的已验证更新。请稍后重试。", "Derzeit erfüllt kein geprüftes Update die Mindestversion. Prüfen Sie später erneut.", "Проверенное обновление нужной версии пока недоступно. Проверьте позже.", "Aucune mise à jour vérifiée ne satisfait actuellement la version requise. Réessayez plus tard."),
})
from vlui_live_copy import ROWS as _LIVE_ROWS
_ROWS.update(_LIVE_ROWS)
from vlui_c014_copy import ROWS as _C014_ROWS
_ROWS.update(_C014_ROWS)
from annotation_copy import ROWS as _ANNOTATION_ROWS
_ROWS.update(_ANNOTATION_ROWS)
from annotation_suite_copy import ROWS as _ANNOTATION_SUITE_ROWS
_ROWS.update(_ANNOTATION_SUITE_ROWS)
from vlui_mouse_removal_copy import ROWS as _MOUSE_REMOVAL_ROWS
_ROWS.update(_MOUSE_REMOVAL_ROWS)
from model_tools_copy import ROWS as _MODEL_TOOLS_ROWS
_ROWS.update(_MODEL_TOOLS_ROWS)
_ROWS.update({
    "GPU preprocessing": ("GPU 预处理", "GPU-Vorverarbeitung", "Предобработка на GPU", "Prétraitement GPU"),
    "Requires a compatible CUDA or TensorRT RTX engine and a float NCHW model. Other models use CPU preprocessing.": (
        "需要兼容的 CUDA 或 TensorRT RTX 引擎和浮点 NCHW 模型。其他模型使用 CPU 预处理。",
        "Erfordert eine kompatible CUDA- oder TensorRT-RTX-Engine und ein Float-NCHW-Modell. Andere Modelle nutzen CPU-Vorverarbeitung.",
        "Нужны совместимый движок CUDA или TensorRT RTX и модель float NCHW. Для других моделей используется предобработка на CPU.",
        "Nécessite un moteur CUDA ou TensorRT RTX compatible et un modèle NCHW à virgule flottante. Les autres modèles utilisent le prétraitement CPU.",
    ),
    "Select CUDA or TensorRT RTX to use GPU preprocessing. CPU and DirectML use CPU preprocessing.": (
        "选择 CUDA 或 TensorRT RTX 以使用 GPU 预处理。CPU 和 DirectML 使用 CPU 预处理。",
        "Wählen Sie CUDA oder TensorRT RTX für die GPU-Vorverarbeitung. CPU und DirectML nutzen CPU-Vorverarbeitung.",
        "Выберите CUDA или TensorRT RTX для предобработки на GPU. CPU и DirectML используют предобработку на CPU.",
        "Sélectionnez CUDA ou TensorRT RTX pour le prétraitement GPU. CPU et DirectML utilisent le prétraitement CPU.",
    ),
    "GPU preprocessing is not included in this engine. Inference uses CPU preprocessing.": (
        "此引擎未包含 GPU 预处理。推理使用 CPU 预处理。",
        "Diese Engine enthält keine GPU-Vorverarbeitung. Die Inferenz nutzt CPU-Vorverarbeitung.",
        "В этом движке нет предобработки на GPU. Для вывода используется предобработка на CPU.",
        "Ce moteur n'inclut pas le prétraitement GPU. L'inférence utilise le prétraitement CPU.",
    ),
})
_ROWS.update({
    'Lock tuning': ('锁定调节', 'Einstellungen sperren', 'Заблокировать настройки', 'Verrouiller les réglages'),
    'Applied on next Start.': ('下次启动时应用。', 'Gilt beim nächsten Start.', 'Применяется при следующем запуске.', 'Appliqué au prochain démarrage.'),
    'Automatic first connected controller': ('自动选择首个已连接控制器', 'Automatisch: erster verbundener Controller', 'Автоматически: первый подключённый контроллер', 'Automatique : première manette connectée'),
    'Unlock tuning': ('解锁调节', 'Einstellungen entsperren', 'Разблокировать настройки', 'Déverrouiller les réglages'),
    'Unlock tuning to change settings.': ('请先解锁以更改设置。', 'Zum Ändern die Einstellungen entsperren.', 'Разблокируйте настройки для изменения.', 'Déverrouillez les réglages pour les modifier.'),
    'Open guide: {topic}': ('打开指南：{topic}', 'Anleitung öffnen: {topic}', 'Открыть руководство: {topic}', 'Ouvrir le guide : {topic}'),
    'Trigger thresholds': ('扳机阈值', 'Trigger-Schwellen', 'Пороги триггера', 'Seuils des gâchettes'),
    'Press threshold': ('按下阈值', 'Druckschwelle', 'Порог нажатия', 'Seuil d’appui'),
    'Release threshold': ('释放阈值', 'Loslassschwelle', 'Порог отпускания', 'Seuil de relâchement'),
    'Apply thresholds': ('应用阈值', 'Schwellen übernehmen', 'Применить пороги', 'Appliquer les seuils'),
    'Release must be no higher than press. No changes applied.': ('释放阈值不能高于按下阈值。未应用更改。', 'Loslassschwelle darf Druckschwelle nicht überschreiten. Nichts geändert.', 'Порог отпускания не должен превышать порог нажатия. Изменений нет.', 'Le seuil de relâchement ne doit pas dépasser celui d’appui. Aucun changement appliqué.'),
    'Thresholds updated.': ('阈值已更新。', 'Schwellen aktualisiert.', 'Пороги обновлены.', 'Seuils mis à jour.'),
    'Raw trigger readings: 0–255. Press at or above Press; release below Release. Thresholds use 1–255. Applies to activation triggers on next Start; manual target lock is unchanged.': (
        '扳机读数：0–255。达到按下阈值时激活，低于释放阈值时释放。阈值范围为1–255。下次启动时用于激活扳机；手动目标锁定不变。',
        'Triggerwerte: 0–255. Aktiv ab Druckschwelle; frei unter Loslassschwelle. Schwellen: 1–255. Gilt ab nächstem Start für Aktivierungstrigger; manuelle Zielsperre bleibt unverändert.',
        'Значения триггера: 0–255. Активация от порога нажатия, отпускание ниже порога отпускания. Пороги: 1–255. Со следующего запуска; ручная фиксация цели не меняется.',
        'Valeurs brutes : 0–255. Activation au seuil d’appui ou au-dessus ; relâchement sous son seuil. Seuils : 1–255. Au prochain démarrage pour les gâchettes d’activation ; verrouillage manuel inchangé.'),
})
from batch3_copy import ROWS as _BATCH3_ROWS
_ROWS.update(_BATCH3_ROWS)
from batch4_copy import ROWS as _BATCH4_ROWS
_ROWS.update(_BATCH4_ROWS)
from batch5_copy import ROWS as _BATCH5_ROWS
_ROWS.update(_BATCH5_ROWS)
from batch6_copy import ROWS as _BATCH6_ROWS
_ROWS.update(_BATCH6_ROWS)
from batch7_copy import ROWS as _BATCH7_ROWS
_ROWS.update(_BATCH7_ROWS)
from crosshair_copy import ROWS as _CROSSHAIR_ROWS
_ROWS.update(_CROSSHAIR_ROWS)
from batch8_copy import ROWS as _BATCH8_ROWS
_ROWS.update(_BATCH8_ROWS)
from cloud_copy import ROWS as _CLOUD_ROWS
_ROWS.update(_CLOUD_ROWS)
from batch2_copy import ROWS as _BATCH2_ROWS
_ROWS.update(_BATCH2_ROWS)
_ROWS.update({
    'Access': ('访问', 'Zugriff', 'Доступ', 'Accès'),
    'On': ('开','Ein','Вкл.','Activé'),
    'Off': ('关','Aus','Выкл.','Désactivé'),
    'Output state': ('输出状态','Ausgabestatus','Состояние вывода','État de sortie'),
    'Policy': ('策略','Strategie','Правила','Stratégie'),
    'Preview': ('预览','Vorschau','Предпросмотр','Aperçu'),
    'Process': ('进程','Prozess','Процесс','Processus'),
    'RAM (MiB)': ('内存 (MiB)','RAM (MiB)','ОЗУ (МиБ)','RAM (Mio)'),
    'Private (MiB)': ('专用内存 (MiB)','Privat (MiB)','Частная (МиБ)','Privée (Mio)'),
    'Actions & status': ('操作与状态', 'Aktionen & Status', 'Действия и состояние', 'Actions et état'),
    'Annotation activity': ('标注活动', 'Annotationsaktivität', 'Процесс разметки', 'Activité d’annotation'),
    'CPU / GPU placement': ('CPU / GPU 分配', 'CPU- / GPU-Zuordnung', 'Распределение CPU / GPU', 'Répartition CPU / GPU'),
    'Class library / editor': ('类别库 / 编辑器', 'Klassenbibliothek / Editor', 'Библиотека / редактор классов', 'Bibliothèque / éditeur de classes'),
    'Controller input': ('控制器输入', 'Controller-Eingabe', 'Ввод контроллера', 'Entrée de la manette'),
    'Current movement state': ('当前移动状态', 'Aktueller Bewegungsstatus', 'Текущее состояние движения', 'État actuel du mouvement'),
    'Data source': ('数据源', 'Datenquelle', 'Источник данных', 'Source des données'),
    'Dataset status': ('数据集状态', 'Datensatzstatus', 'Состояние набора данных', 'État du jeu de données'),
    'Detection filters': ('检测筛选', 'Erkennungsfilter', 'Фильтры обнаружения', 'Filtres de détection'),
    'Event actions · Premium': ('事件操作 · Premium', 'Ereignisaktionen · Premium', 'Действия по событиям · Premium', 'Actions sur événements · Premium'),
    'Execution provider': ('执行后端', 'Ausführungsanbieter', 'Провайдер выполнения', 'Moteur d’exécution'),
    'Global controls · Premium': ('全局控制 · Premium', 'Globale Steuerung · Premium', 'Глобальное управление · Premium', 'Commandes globales · Premium'),
    'Hardware connection': ('硬件连接', 'Hardwareverbindung', 'Подключение оборудования', 'Connexion du matériel'),
    'Help & setup': ('帮助与设置', 'Hilfe & Einrichtung', 'Справка и настройка', 'Aide et configuration'),
    'History & curves': ('历史与曲线', 'Verlauf & Kurven', 'История и графики', 'Historique et courbes'),
    'Interface & Language': ('界面与语言', 'Oberfläche & Sprache', 'Интерфейс и язык', 'Interface et langue'),
    'Live controller tester': ('实时控制器测试', 'Live-Controller-Test', 'Проверка контроллера в реальном времени', 'Test de manette en direct'),
    'Live output path': ('实时输出路径', 'Aktueller Ausgabepfad', 'Текущий путь вывода', 'Chemin de sortie en direct'),
    'Local performance history · Premium': ('本地性能历史 · Premium', 'Lokaler Leistungsverlauf · Premium', 'Локальная история производительности · Premium', 'Historique local des performances · Premium'),
    'Model actions': ('模型操作', 'Modellaktionen', 'Действия с моделью', 'Actions du modèle'),
    'Model details': ('模型详情', 'Modelldetails', 'Сведения о модели', 'Détails du modèle'),
    'Model interpretation': ('模型解析', 'Modellinterpretation', 'Интерпретация модели', 'Interprétation du modèle'),
    'Model output / class handling': ('模型输出 / 类别处理', 'Modellausgabe / Klassenbehandlung', 'Выход модели / обработка классов', 'Sortie du modèle / gestion des classes'),
    'Optimization': ('优化', 'Optimierung', 'Оптимизация', 'Optimisation'),
    'Output transport': ('输出传输', 'Ausgabeübertragung', 'Транспорт вывода', 'Transport de sortie'),
    'Profile library': ('配置库', 'Profilbibliothek', 'Библиотека профилей', 'Bibliothèque de profils'),
    'Review queue': ('审核队列', 'Prüfwarteschlange', 'Очередь проверки', 'File de vérification'),
    'Run setup': ('运行设置', 'Laufeinstellungen', 'Настройка запуска', 'Configuration de l’exécution'),
    'Run training': ('运行训练', 'Training starten', 'Запуск обучения', 'Lancer l’entraînement'),
    'Session overview': ('会话概览', 'Sitzungsübersicht', 'Обзор сеанса', 'Vue d’ensemble de la session'),
    'Theme customization': ('主题自定义', 'Design anpassen', 'Настройка темы', 'Personnalisation du thème'),
    'Validation and benchmark status': ('验证与基准测试状态', 'Validierungs- und Benchmarkstatus', 'Состояние проверки и теста производительности', 'État de validation et de mesure des performances'),
    'Editing a card does not select that profile.': ('编辑卡片不会选用该配置。', 'Das Bearbeiten einer Karte wählt dieses Profil nicht aus.', 'Редактирование карточки не выбирает этот профиль.', 'Modifier une carte ne sélectionne pas ce profil.'),
    'Review capture, model and movement setup before starting a session.': ('启动会话前检查采集、模型和移动设置。', 'Vor dem Start Aufnahme, Modell und Bewegung prüfen.', 'Перед запуском проверьте настройки захвата, модели и движения.', 'Vérifiez la capture, le modèle et le mouvement avant de démarrer une session.'),
    'Configure the existing display language, theme, updates and setup preferences.': ('配置显示语言、主题、更新和设置偏好。', 'Anzeigesprache, Design, Updates und Einrichtung konfigurieren.', 'Настройте язык интерфейса, тему, обновления и параметры установки.', 'Configurez la langue, le thème, les mises à jour et les préférences de configuration.'),
    'Choose an ONNX detection model and the native execution provider used for the next session.': ('选择下一会话使用的 ONNX 检测模型和原生执行后端。', 'ONNX-Erkennungsmodell und nativen Ausführungsanbieter für die nächste Sitzung wählen.', 'Выберите модель обнаружения ONNX и провайдер выполнения для следующего сеанса.', 'Choisissez un modèle de détection ONNX et le moteur natif pour la prochaine session.'),
    'Set a physical display and its centered capture region for the next native session.': ('设置下一原生会话的物理显示器及其居中采集区域。', 'Bildschirm und zentrierten Aufnahmebereich für die nächste native Sitzung festlegen.', 'Выберите физический дисплей и центральную область захвата для следующего сеанса.', 'Définissez l’écran physique et sa zone de capture centrée pour la prochaine session native.'),
    'Tune confidence, overlap suppression, and the class allow-list used by the native detector.': ('调整原生检测器的置信度、重叠抑制和允许类别列表。', 'Konfidenz, Überlappungsunterdrückung und erlaubte Klassen des nativen Detektors einstellen.', 'Настройте уверенность, подавление перекрытий и разрешённые классы детектора.', 'Réglez la confiance, la suppression des chevauchements et les classes autorisées du détecteur natif.'),
    'Configure activation and output paths.': ('配置激活条件和输出路径。', 'Aktivierung und Ausgabepfade konfigurieren.', 'Настройте активацию и пути вывода.', 'Configurez l’activation et les chemins de sortie.'),
    'Collect source frames, create reviewable labels, build a YOLO dataset, and optionally train a local model without leaving Vapor Lock.': ('在 Vapor Lock 内采集源图像、创建可审核标注、构建 YOLO 数据集，并按需训练本地模型。', 'Quellbilder sammeln, prüfbare Labels erstellen, einen YOLO-Datensatz aufbauen und optional ein lokales Modell direkt in Vapor Lock trainieren.', 'Собирайте кадры, создавайте разметку для проверки, формируйте набор YOLO и при необходимости обучайте локальную модель в Vapor Lock.', 'Collectez des images, créez des annotations vérifiables, préparez un jeu YOLO et entraînez éventuellement un modèle local dans Vapor Lock.'),
    'Save and restore complete native-engine configurations as readable JSON files.': ('以可读 JSON 文件保存和恢复完整的原生引擎配置。', 'Vollständige Einstellungen der nativen Engine als lesbare JSON-Dateien speichern und wiederherstellen.', 'Сохраняйте и восстанавливайте полные настройки движка в читаемых файлах JSON.', 'Enregistrez et restaurez les configurations complètes du moteur natif en fichiers JSON lisibles.'),
    'Configure the shared calibration shortcut and Windows-wide Premium controls stored with the current profile.': ('配置通用校准快捷键以及随当前配置保存的 Windows 全局 Premium 控制。', 'Gemeinsames Kalibrierungskürzel und profilgebundene Windows-weite Premium-Steuerung konfigurieren.', 'Настройте общий ярлык калибровки и глобальные команды Premium для Windows, сохраняемые в текущем профиле.', 'Configurez le raccourci de calibration commun et les commandes Premium globales de Windows enregistrées dans le profil actuel.'),
    'Enable an authenticated local control API for dashboards, plugins, companion software, and trusted remote control.': ('启用需身份验证的本地控制 API，用于仪表板、插件、配套软件及可信远程控制。', 'Authentifizierte lokale Steuerungs-API für Dashboards, Plugins, Begleitsoftware und vertrauenswürdige Fernsteuerung aktivieren.', 'Включите локальный API с аутентификацией для панелей, плагинов, вспомогательных программ и доверенного удалённого управления.', 'Activez une API locale authentifiée pour les tableaux de bord, plugins, logiciels compagnons et commandes à distance de confiance.'),
    'Review lightweight telemetry emitted by the native engine.': ('查看原生引擎提供的轻量遥测数据。', 'Kompakte Telemetrie der nativen Engine prüfen.', 'Просматривайте лёгкую телеметрию движка.', 'Consultez la télémétrie légère du moteur natif.'),
    'Inspect the exact selected-monitor crop that the native detector is evaluating, including its active tracks, anchor, and projected motion.': ('查看原生检测器正在分析的显示器裁剪区域，包括活动轨迹、锚点和预测运动。', 'Den vom nativen Detektor ausgewerteten Bildschirmausschnitt mit aktiven Spuren, Anker und vorhergesagter Bewegung prüfen.', 'Просматривайте область экрана, анализируемую детектором, с активными траекториями, точкой привязки и прогнозом движения.', 'Inspectez la zone exacte analysée par le détecteur natif, avec ses pistes actives, son point d’ancrage et son mouvement projeté.'),
})
_ROWS.update({
    'Class priorities and anchors': ('类别优先级和锚点', 'Klassenprioritäten und Anker', 'Приоритеты классов и точки привязки', 'Priorités des classes et points d’ancrage'),
    'Manual track lock': ('手动轨迹锁定', 'Manuelle Spursperre', 'Ручная фиксация траектории', 'Verrouillage manuel de piste'),
    'Motion compensation': ('运动补偿', 'Bewegungskompensation', 'Компенсация движения', 'Compensation de mouvement'),
    'Preview snapshot': ('预览快照', 'Vorschaumomentaufnahme', 'Снимок предпросмотра', 'Instantané d’aperçu'),
    'Global controls': ('全局控制', 'Globale Steuerung', 'Глобальное управление', 'Commandes globales'),
    'Profile hotkeys': ('配置文件快捷键', 'Profil-Tastenkürzel', 'Горячие клавиши профиля', 'Raccourcis de profil'),
    'Local control API': ('本地控制 API', 'Lokale Steuerungs-API', 'Локальный API управления', 'API de contrôle locale'),
    'Session details': ('会话详情', 'Sitzungsdetails', 'Сведения о сеансе', 'Détails de la session'),
    'On-screen overlay': ('屏幕叠加层', 'Bildschirm-Overlay', 'Экранное наложение', 'Superposition à l’écran'),
    'Event actions': ('事件操作', 'Ereignisaktionen', 'Действия событий', 'Actions d’événement'),
    'Snap-radius event action': ('吸附半径事件操作', 'Ereignisaktion im Einrastradius', 'Действие в радиусе захвата', 'Action dans le rayon d’accrochage'),
    'Compensation calibration wizard': ('补偿校准向导', 'Assistent zur Kompensationskalibrierung', 'Мастер калибровки компенсации', 'Assistant d’étalonnage de compensation'),
    'Local performance history': ('本地性能历史', 'Lokaler Leistungsverlauf', 'Локальная история производительности', 'Historique local des performances'),
    'Configure the display language, theme and setup preferences.': ('配置显示语言、主题和设置偏好。', 'Anzeigesprache, Design und Einrichtung konfigurieren.', 'Настройте язык интерфейса, тему и параметры установки.', 'Configurez la langue, le thème et les préférences de configuration.'),
    'Configure the shared calibration shortcut and Windows-wide controls stored with the current profile.': ('配置通用校准快捷键以及随当前配置保存的 Windows 全局控制。', 'Gemeinsames Kalibrierungskürzel und profilgebundene Windows-weite Steuerung konfigurieren.', 'Настройте общий ярлык калибровки и глобальные команды Windows, сохраняемые в текущем профиле.', 'Configurez le raccourci de calibration commun et les commandes globales de Windows enregistrées dans le profil actuel.'),
})
_CASEFOLDED_ROWS = {source.casefold(): row for source, row in _ROWS.items()}


def normalize_language(value: object) -> str:
    candidate = str(value or "en").strip()
    return candidate if candidate in SUPPORTED_LANGUAGE_CODES else "en"


def language_name(code: str) -> str:
    normalized = normalize_language(code)
    return next(name for name, value in LANGUAGE_OPTIONS if value == normalized)


def translate_text(source: str, language: str) -> str:
    """Translate a UI string without ever changing unknown technical data."""
    language = normalize_language(language)
    if not source or language == "en":
        return source
    plain = source.replace("&", "")
    row = _ROWS.get(source) or _CASEFOLDED_ROWS.get(source.casefold()) or _ROWS.get(plain)
    if row is not None:
        translated = row[_LANGUAGE_INDEX[language]]
        return translated.upper() if source.isupper() else translated
    if " / " in source:
        return " / ".join(translate_text(part, language) for part in source.split(" / "))
    return source


def apply_qt_language(application, language):
    previous = getattr(application, "_vaporlock_qt_translator", None)
    if previous is not None: application.removeTranslator(previous)
    application._vaporlock_language = normalize_language(language)
    if language == "en":
        application._vaporlock_qt_translator = None
        return
    translator = QTranslator(application)
    filename = f"qtbase_{language.replace('-', '_')}.qm"
    candidates = (Path(getattr(sys, "_MEIPASS", "")) / "translations" / filename,
        Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / filename)
    for path in candidates:
        if path.is_file() and translator.load(str(path)):
            application.installTranslator(translator)
            application._vaporlock_qt_translator = translator
            return
    application._vaporlock_qt_translator = None


class DialogLocalizer(QObject):
    """Translate dialogs built after startup using captured source copy."""
    def __init__(self, application):
        super().__init__(application)
        self.application = application
        application.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            language = getattr(self.application, "_vaporlock_language", "en")
            parent = watched.parentWidget()
            while parent is not None:
                if hasattr(parent, "ui_language"):
                    language = parent.ui_language; break
                parent = parent.parentWidget()
            translator = getattr(watched, "_vaporlock_dialog_translator", None)
            if translator is None:
                translator = InterfaceTranslator()
                translator.capture(watched)
                watched._vaporlock_dialog_translator = translator
            translator.apply(language)
        return False


def format_text(source: str, language: str, **values: object) -> str:
    return translate_text(source, language).format(**values)


def preference_path() -> Path:
    from vlui_native_injection import native_injection
    injection = native_injection()
    if injection is not None:
        return injection.paths.interface_preferences_file
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "VaporLock" / "interface-preferences.json"


def load_preferred_language() -> str:
    try:
        payload = json.loads(preference_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return "en"
        return normalize_language(payload.get("language"))
    except (OSError, ValueError, TypeError):
        return "en"


def save_preferred_language(language: str) -> bool:
    language = normalize_language(language)
    path = preference_path()
    temporary = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except (OSError, ValueError):
            payload = {}
        payload["language"] = language
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class InterfaceTranslator:
    """Capture English source copy once, then safely reapply it in another UI language."""

    def __init__(self) -> None:
        self._bindings: list[tuple[str, QWidget, object]] = []

    def prune(self) -> None:
        self._bindings = [binding for binding in self._bindings if isValid(binding[1])]

    def capture(self, root: QWidget, *, reset: bool = True) -> None:
        """Remember source copy from a widget tree.

        Rebuilt settings pages are always constructed in English. Appending
        those bindings lets the persistent command bar keep its original
        source copy while the old page widgets are disposed by Qt.
        """
        if reset:
            self._bindings.clear()
        self.prune()
        seen = {(kind, id(widget)) for kind, widget, _ in self._bindings}
        def remember(kind, widget, source):
            if (kind, id(widget)) not in seen:
                self._bindings.append((kind, widget, source))
                seen.add((kind, id(widget)))
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            if bool(widget.property("_vaporlock_i18n_skip")):
                continue
            if widget.toolTip():
                remember("tooltip", widget, widget.toolTip())
            if widget.isWindow() and widget.windowTitle():
                remember("window_title", widget, widget.windowTitle())
            if widget.accessibleName():
                remember("accessible_name", widget, widget.accessibleName())
            if widget.accessibleDescription():
                remember("accessible_description", widget, widget.accessibleDescription())
            if isinstance(widget, QGroupBox) and widget.title():
                remember("group_title", widget, widget.title())
            if isinstance(widget, QTableWidget):
                remember("table_headers", widget, tuple(
                    widget.horizontalHeaderItem(index).text() if widget.horizontalHeaderItem(index) else ""
                    for index in range(widget.columnCount())))
            if isinstance(widget, QTreeWidget):
                remember("tree_headers", widget, tuple(widget.headerItem().text(index) for index in range(widget.columnCount())))
            if isinstance(widget, QLabel) and widget.text():
                remember("text", widget, widget.text())
            elif isinstance(widget, (QPushButton, QCheckBox, QToolButton)) and widget.text():
                remember("text", widget, widget.text())
            elif isinstance(widget, QLineEdit) and widget.placeholderText():
                remember("placeholder", widget, widget.placeholderText())
            elif isinstance(widget, QComboBox):
                remember("combo", widget, tuple(widget.itemText(index) for index in range(widget.count())))
            elif isinstance(widget, QTabWidget):
                remember("tabs", widget, tuple(widget.tabText(index) for index in range(widget.count())))
        for action in root.findChildren(QAction):
            if not action.property("_vaporlock_i18n_skip") and action.text():
                remember("action", action, action.text())

    def apply(self, language: str) -> None:
        language = normalize_language(language)
        self.prune()
        for kind, widget, source in self._bindings:
            try:
                if kind == "text":
                    translated=translate_text(str(source), language)
                    if isinstance(widget,(QPushButton,QCheckBox,QToolButton)):
                        translated=translated.replace(' & ',' && ')
                    widget.setText(translated)
                elif kind == "tooltip":
                    widget.setToolTip(translate_text(str(source), language))
                elif kind == "placeholder":
                    assert isinstance(widget, QLineEdit)
                    widget.setPlaceholderText(translate_text(str(source), language))
                elif kind == "window_title":
                    widget.setWindowTitle(translate_text(str(source), language))
                elif kind == "accessible_name":
                    widget.setAccessibleName(translate_text(str(source), language))
                elif kind == "accessible_description":
                    widget.setAccessibleDescription(translate_text(str(source), language))
                elif kind == "group_title":
                    widget.setTitle(translate_text(str(source), language))
                elif kind == "action":
                    widget.setText(translate_text(str(source), language))
                elif kind in {"table_headers", "tree_headers"}:
                    if widget.columnCount() == len(tuple(source)):
                        for index, text in enumerate(tuple(source)):
                            item = widget.horizontalHeaderItem(index) if kind == "table_headers" else widget.headerItem()
                            if item is not None:
                                if kind == "table_headers": item.setText(translate_text(str(text), language))
                                else: item.setText(index, translate_text(str(text), language))
                elif kind == "combo":
                    assert isinstance(widget, QComboBox)
                    values = tuple(source)
                    if widget.count() == len(values):
                        for index, text in enumerate(values):
                            widget.setItemText(index, translate_text(str(text), language))
                elif kind == "tabs":
                    assert isinstance(widget, QTabWidget)
                    values = tuple(source)
                    if widget.count() == len(values):
                        for index, text in enumerate(values):
                            widget.setTabText(index, translate_text(str(text), language))
            except RuntimeError:
                # A page can be rebuilt while a queued UI event is being processed.
                continue
