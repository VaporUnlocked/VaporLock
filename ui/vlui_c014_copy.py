"""Proposed C014 Collection copy; controls follow accepted direction, status rows await review."""
ROWS = {
    'Saved source images: {saved}.': (
        '已保存源图像：{saved}。', 'Gespeicherte Quellbilder: {saved}.',
        'Сохранено исходных изображений: {saved}.', 'Images sources enregistrées : {saved}.'),
    'Detection condition': ('检测条件', 'Erkennungsbedingung', 'Условие обнаружения', 'Condition de détection'),
    'Minimum capture confidence': ('最低采集置信度', 'Minimale Aufnahme-Konfidenz', 'Минимальная уверенность для сбора', 'Confiance minimale de capture'),
    'Only capture frames with a qualifying detection': (
        '仅在当前帧存在符合条件的检测时采集', 'Nur Bilder mit einer passenden Erkennung aufnehmen',
        'Сохранять кадры только при подходящем обнаружении', 'Capturer seulement les images avec une détection admissible'),
    'Save the full current image. Targeting and later annotation use their own settings.': (
        '保存当前完整图像。目标选择和后续标注使用各自的设置。',
        'Das ganze aktuelle Bild speichern. Zielauswahl und spätere Annotation haben eigene Einstellungen.',
        'Сохраняется весь текущий кадр. Выбор цели и последующая разметка используют свои настройки.',
        'Enregistrer toute l’image actuelle. Le ciblage et l’annotation ultérieure ont leurs propres réglages.'),
    'Saved images {saved} · Skipped frames {dropped} · Queue {pending}/1 · Encoding {encoding}/1': (
        '已保存图像 {saved} · 已跳过帧 {dropped} · 队列 {pending}/1 · 编码中 {encoding}/1',
        'Bilder gespeichert {saved} · Bilder übersprungen {dropped} · Warteschlange {pending}/1 · Kodierung {encoding}/1',
        'Сохранено изображений {saved} · Пропущено кадров {dropped} · Очередь {pending}/1 · Кодируется {encoding}/1',
        'Images enregistrées {saved} · Images ignorées {dropped} · File {pending}/1 · Encodage {encoding}/1'),
    'Collection status is temporarily unavailable.': (
        '采集状态暂时不可用。', 'Der Aufnahmestatus ist vorübergehend nicht verfügbar.',
        'Состояние сбора временно недоступно.', 'L’état de la collecte est temporairement indisponible.'),
    'Collection is still finishing cleanup.': (
        '采集仍在完成清理。', 'Die Aufnahme schließt die Bereinigung noch ab.',
        'Очистка после сбора ещё выполняется.', 'Le nettoyage de la collecte est encore en cours.'),
    'Capture confidence is unavailable for this model or frame.': (
        '此模型或当前帧无法提供采集置信度。', 'Die Aufnahme-Konfidenz ist für dieses Modell oder Bild nicht verfügbar.',
        'Уверенность для сбора недоступна для этой модели или кадра.', 'La confiance de capture est indisponible pour ce modèle ou cette image.'),
    'Collection storage is unavailable. Review the dataset folder.': (
        '采集存储不可用。请检查数据集文件夹。', 'Der Aufnahmespeicher ist nicht verfügbar. Den Datensatzordner prüfen.',
        'Хранилище сбора недоступно. Проверьте папку набора данных.', 'Le stockage de la collecte est indisponible. Vérifiez le dossier du jeu de données.'),
    'Capture settings are invalid. Review confidence, interval and quality.': (
        '采集设置无效。请检查置信度、间隔和画质。',
        'Die Aufnahmeeinstellungen sind ungültig. Konfidenz, Intervall und Qualität prüfen.',
        'Неверные настройки сбора. Проверьте уверенность, интервал и качество.',
        'Les réglages de capture sont invalides. Vérifiez la confiance, l’intervalle et la qualité.'),
    'Collection sample could not be saved.': (
        '未能保存采集样本。', 'Das Aufnahmebild konnte nicht gespeichert werden.',
        'Не удалось сохранить образец сбора.', 'L’image collectée n’a pas pu être enregistrée.'),
}

ROWS.update({
    'Profile could not be saved. Current changes are still unsaved.': (
        '无法保存配置。当前更改尚未保存。', 'Das Profil konnte nicht gespeichert werden. Die aktuellen Änderungen bleiben ungespeichert.',
        'Сохранить профиль не удалось. Текущие изменения остаются несохранёнными.', 'Le profil n’a pas pu être enregistré. Les modifications actuelles ne sont pas enregistrées.'),
    'Delete unannotated images': ('删除未标注图像', 'Unannotierte Bilder löschen', 'Удалять изображения без разметки', 'Supprimer les images sans annotation'),
    'Removes images with no final labels after every annotation pass succeeds. Failed, cancelled, and previously labelled images are preserved.': (
        '所有标注轮次成功完成后，删除没有最终标签的图像。失败、取消和已有标签的图像将被保留。',
        'Entfernt Bilder ohne endgültige Labels, nachdem alle Annotationsdurchgänge erfolgreich waren. Fehlgeschlagene, abgebrochene und bereits beschriftete Bilder bleiben erhalten.',
        'Удаляет изображения без итоговых меток после успешного завершения всех проходов разметки. Изображения с ошибками, отменённой обработкой и прежними метками сохраняются.',
        'Supprime les images sans étiquette finale après la réussite de tous les passages. Les images en échec, annulées ou déjà annotées sont conservées.'),
    'Annotation complete: {processed} processed · {annotated} annotated · {removed} removed · {protected} protected · {failed} failed.': (
        '标注完成：已处理 {processed} · 已标注 {annotated} · 已删除 {removed} · 已保护 {protected} · 失败 {failed}。',
        'Annotation abgeschlossen: {processed} verarbeitet · {annotated} annotiert · {removed} entfernt · {protected} geschützt · {failed} fehlgeschlagen.',
        'Разметка завершена: обработано {processed} · размечено {annotated} · удалено {removed} · защищено {protected} · ошибок {failed}.',
        'Annotation terminée : {processed} traitées · {annotated} annotées · {removed} supprimées · {protected} protégées · {failed} en échec.'),
    'Annotation committed. Quarantine cleanup is pending and will be retried before the next dataset task.': (
        '标注已提交。隔离区清理尚未完成，将在下一次数据集任务前重试。',
        'Annotation gespeichert. Die Quarantänebereinigung wird vor der nächsten Datensatzaufgabe erneut versucht.',
        'Разметка сохранена. Очистка карантина будет повторена перед следующей задачей набора данных.',
        'Annotation enregistrée. Le nettoyage de la quarantaine sera réessayé avant la prochaine tâche du jeu de données.'),
    'The previous profile could not be restored. The default profile is active.': (
        '无法恢复上次的配置。当前使用默认配置。', 'Das vorherige Profil konnte nicht wiederhergestellt werden. Das Standardprofil ist aktiv.',
        'Восстановить предыдущий профиль не удалось. Активен профиль по умолчанию.', 'Le profil précédent n’a pas pu être restauré. Le profil par défaut est actif.'),
    'The previous profile could not be restored. Default settings are active.': (
        '无法恢复上次的配置。当前使用默认设置。', 'Das vorherige Profil konnte nicht wiederhergestellt werden. Die Standardeinstellungen sind aktiv.',
        'Восстановить предыдущий профиль не удалось. Активны настройки по умолчанию.', 'Le profil précédent n’a pas pu être restauré. Les réglages par défaut sont actifs.'),
    'Save the full current image when the highest confidence in the live detection feed meets this threshold. Detections filtered out of the live feed cannot trigger capture. Annotation has its own settings.': (
        '实时检测结果中的最高置信度达到此阈值时，保存当前完整图像。被实时结果过滤掉的检测无法触发采集。标注使用独立设置。',
        'Speichert das ganze aktuelle Bild, wenn die höchste Konfidenz der aktuellen Erkennungen diesen Grenzwert erreicht. Herausgefilterte Erkennungen lösen keine Aufnahme aus. Die Annotation hat eigene Einstellungen.',
        'Сохраняет весь текущий кадр, когда максимальная уверенность в текущем потоке обнаружений достигает порога. Отфильтрованные обнаружения не запускают сохранение. У разметки отдельные настройки.',
        'Enregistre toute l’image actuelle lorsque la confiance maximale des détections en direct atteint ce seuil. Les détections filtrées ne déclenchent pas la capture. L’annotation a ses propres réglages.'),
    'Current dataset folder: {path}': ('当前数据集文件夹：{path}', 'Aktueller Datensatzordner: {path}', 'Текущая папка набора данных: {path}', 'Dossier actuel du jeu de données : {path}'),
    'Last saved image: {path}': ('最近保存的图像：{path}', 'Zuletzt gespeichertes Bild: {path}', 'Последнее сохранённое изображение: {path}', 'Dernière image enregistrée : {path}'),
    'Saved profile: {path}': ('配置已保存：{path}', 'Profil gespeichert: {path}', 'Профиль сохранён: {path}', 'Profil enregistré : {path}'),
    'Profile save is unavailable while authorization or a profile transition is pending.': (
        '授权或配置切换尚未完成时，无法保存配置。', 'Das Profil kann während einer ausstehenden Autorisierung oder eines Profilwechsels nicht gespeichert werden.',
        'Сохранение профиля недоступно во время проверки доступа или смены профиля.', 'L’enregistrement est indisponible pendant l’autorisation ou le changement de profil.'),
    'Profile changed before saving. Save again with the current settings.': (
        '保存前配置已更改。请重新保存当前设置。', 'Das Profil wurde vor dem Speichern geändert. Aktuelle Einstellungen erneut speichern.',
        'Профиль изменился до сохранения. Сохраните текущие настройки ещё раз.', 'Le profil a changé avant l’enregistrement. Enregistrez à nouveau les réglages actuels.'),
    'Profile was applied, but its startup selection could not be remembered.': (
        '配置已应用，但无法记住启动时的配置选择。', 'Das Profil wurde angewendet, aber die Auswahl für den nächsten Start konnte nicht gespeichert werden.',
        'Профиль применён, но выбор для следующего запуска сохранить не удалось.', 'Le profil a été appliqué, mais son choix pour le prochain démarrage n’a pas pu être mémorisé.'),
})
