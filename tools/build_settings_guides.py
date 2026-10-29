"""Build the owner's complete offline guide in the five supported languages.

Canonical prose lives in docs/settings-guide, not the historical abbreviated
guide_html/write_guides helpers retained in the already-published executable.
Run this before packaging; never regenerate release guides from those helpers.
"""
import argparse
import base64
import hashlib
from html import escape
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/settings-guide'
LANGUAGES = ('en', 'zh-CN', 'de', 'ru', 'fr')
NAMES = ('English', '简体中文', 'Deutsch', 'Русский', 'Français')
SOURCE_HASH = '7278e3e1be52699962b12da70a90c25c81271671b118fae0485c28984b74a200'
UI = {
 'en': ['VaporLock, back to top', 'Guide sections', 'Symptom categories', 'Search settings guide', 'Search a symptom or setting...', 'Languages', 'Browse by symptom or setting.', 'No matching topics', 'Matching topics: {count}', ' across both tabs · ', ' in ', 'Common Symptoms', 'Uncommon Symptoms', 'Start is blocked: POLICY UNAVAILABLE / UPDATE REQUIRED', 'The workspace remains accessible while Start is blocked. For POLICY UNAVAILABLE, check the network connection and Windows date/time, then retry the policy check. A previously verified policy works only until its signed expiry; do not delete policy state or bypass verification. For UPDATE REQUIRED, read the displayed reason and install the required verified version, then check again. These requirements do not revoke your license key.'],
 'zh-CN': ['VaporLock，返回顶部', '指南章节', '问题分类', '搜索设置指南', '搜索现象或设置…', '语言', '按现象或设置浏览。', '没有匹配的主题', '匹配主题：{count}', '（两个选项卡合计）· ', ' 项位于', '常见问题', '不常见问题', '无法启动：POLICY UNAVAILABLE / UPDATE REQUIRED', '启动受阻时仍可使用工作区。出现 POLICY UNAVAILABLE 时，请检查网络连接与 Windows 日期和时间，然后重试策略检查。之前验证的策略仅在签名有效期内可用；不要删除策略状态或绕过验证。出现 UPDATE REQUIRED 时，请阅读显示的原因，安装所要求的已验证版本，再次检查。这些要求不会撤销您的许可密钥。'],
 'de': ['VaporLock, zurück nach oben', 'Abschnitte des Leitfadens', 'Problemkategorien', 'Einstellungsleitfaden durchsuchen', 'Problem oder Einstellung suchen…', 'Sprachen', 'Nach Problem oder Einstellung suchen.', 'Keine passenden Themen', 'Passende Themen: {count}', ' in beiden Registerkarten · ', ' in ', 'Häufige Probleme', 'Seltene Probleme', 'Start blockiert: POLICY UNAVAILABLE / UPDATE REQUIRED', 'Der Arbeitsbereich bleibt bei blockiertem Start zugänglich. Prüfen Sie bei POLICY UNAVAILABLE Netzwerkverbindung sowie Windows-Datum und -Uhrzeit und wiederholen Sie die Richtlinienprüfung. Eine zuvor geprüfte Richtlinie gilt nur bis zu ihrem signierten Ablauf; löschen Sie den Richtlinienzustand nicht und umgehen Sie keine Prüfung. Lesen Sie bei UPDATE REQUIRED den angezeigten Grund, installieren Sie die erforderliche verifizierte Version und prüfen Sie erneut. Diese Anforderungen widerrufen Ihren Lizenzschlüssel nicht.'],
 'ru': ['VaporLock, наверх', 'Разделы руководства', 'Категории проблем', 'Поиск по руководству', 'Найти симптом или настройку…', 'Языки', 'Поиск по симптому или настройке.', 'Подходящих тем нет', 'Найдено тем: {count}', ' в обеих вкладках · ', ' в разделе ', 'Частые проблемы', 'Редкие проблемы', 'Запуск заблокирован: POLICY UNAVAILABLE / UPDATE REQUIRED', 'При блокировке запуска рабочая область остаётся доступной. При POLICY UNAVAILABLE проверьте сеть, дату и время Windows, затем повторите проверку политики. Ранее проверенная политика действует только до подписанного срока истечения; не удаляйте её состояние и не обходите проверку. При UPDATE REQUIRED прочтите причину, установите требуемую проверенную версию и проверьте снова. Эти требования не отзывают лицензионный ключ.'],
 'fr': ['VaporLock, retour en haut', 'Sections du guide', 'Catégories de problèmes', 'Rechercher dans le guide', 'Rechercher un symptôme ou un réglage…', 'Langues', 'Parcourir par symptôme ou réglage.', 'Aucun sujet correspondant', 'Sujets correspondants : {count}', ' dans les deux onglets · ', ' dans ', 'Problèmes courants', 'Problèmes moins courants', 'Démarrage bloqué : POLICY UNAVAILABLE / UPDATE REQUIRED', 'L’espace de travail reste accessible lorsque le démarrage est bloqué. Pour POLICY UNAVAILABLE, vérifiez la connexion réseau ainsi que la date et l’heure Windows, puis relancez la vérification. Une politique déjà vérifiée reste utilisable seulement jusqu’à son expiration signée ; ne supprimez pas son état et ne contournez pas la vérification. Pour UPDATE REQUIRED, lisez la raison affichée, installez la version vérifiée requise et vérifiez à nouveau. Ces exigences ne révoquent pas votre clé de licence.'],
}

TRIGGER_HELP = {
 'en': 'Activation trigger thresholds: readings use a raw 0–255 scale. Press activates at or above the press threshold; release occurs below the release threshold. Set both thresholds from 1 to 255, with release no higher than press. The default 30/30 preserves existing behavior. A lower release threshold prevents repeated activation near the press boundary. Select Apply thresholds; the saved pair takes effect on the next Start and is stored in the profile. This affects activation triggers only, not manual target lock or physical controller passthrough.',
 'zh-CN': '激活扳机阈值：原始读数范围为 0–255。读数达到或超过按下阈值时激活，低于释放阈值时释放。两个阈值均须为 1–255，且释放阈值不得高于按下阈值。默认值 30/30 保持原有行为。较低的释放阈值可避免在按下阈值附近反复激活。选择“应用阈值”后，该组合会保存到配置文件，并在下次启动时生效。这仅影响激活扳机，不影响手动目标锁定或物理控制器透传。',
 'de': 'Schwellen für Aktivierungstrigger: Rohwerte liegen zwischen 0 und 255. Ab der Druckschwelle wird aktiviert; unterhalb der Loslassschwelle wird losgelassen. Beide Schwellen müssen zwischen 1 und 255 liegen; die Loslassschwelle darf die Druckschwelle nicht überschreiten. Der Standard 30/30 erhält das bisherige Verhalten. Eine niedrigere Loslassschwelle verhindert wiederholtes Aktivieren nahe der Druckschwelle. Wählen Sie „Schwellen anwenden“: Das Paar wird im Profil gespeichert und gilt ab dem nächsten Start. Dies betrifft nur Aktivierungstrigger, nicht die manuelle Zielsperre oder die Weiterleitung physischer Controller-Eingaben.',
 'ru': 'Пороги триггера активации: исходные показания имеют диапазон 0–255. Активация происходит при достижении порога нажатия, отпускание — ниже порога отпускания. Оба порога должны быть от 1 до 255; порог отпускания не должен превышать порог нажатия. Значения 30/30 сохраняют прежнее поведение. Более низкий порог отпускания предотвращает повторные срабатывания около порога нажатия. Нажмите «Применить пороги»: пара сохраняется в профиле и действует со следующего запуска. Это влияет только на триггеры активации, а не на ручную фиксацию цели или передачу физических сигналов контроллера.',
 'fr': 'Seuils de gâchette d’activation : les valeurs brutes vont de 0 à 255. L’activation commence au seuil d’appui ou au-dessus ; le relâchement se produit sous le seuil de relâchement. Les deux seuils doivent être compris entre 1 et 255, et le relâchement ne doit pas dépasser l’appui. La valeur par défaut 30/30 conserve le comportement existant. Un seuil de relâchement inférieur évite les activations répétées près du seuil d’appui. Choisissez « Appliquer les seuils » : la paire est enregistrée dans le profil et prend effet au prochain démarrage. Cela concerne uniquement les gâchettes d’activation, sans modifier le verrouillage manuel de cible ni la transmission des entrées physiques du contrôleur.',
}

def translation(language):
    rows = {}
    for line in (SOURCE / (language + '.txt')).read_text(encoding='utf-8').splitlines():
        key, text = line.split('\t', 1)
        index = int(key)
        if index in rows:
            raise ValueError('Duplicate translation block')
        rows[index] = text
    return rows

def render(language):
    if language not in LANGUAGES:
        raise ValueError('Unsupported guide language')
    source = (SOURCE / 'source.html').read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_HASH:
        raise ValueError('Owner source changed; refresh reviewed translation units')
    html = source.decode('utf-8')
    units = json.loads((SOURCE / 'units.json').read_text(encoding='utf-8'))
    text = translation(language)
    if set(text) != set(range(len(units))) or len(units) != 225:
        raise ValueError('Incomplete full-guide translation')
    for unit in reversed(units):
        value = text[unit['id']]
        if sorted(re.findall(r'\{\d+\}', value)) != sorted(re.findall(r'\{\d+\}', unit['text'])):
            raise ValueError('Translation changed an inline or technical token')
        tokens = list(unit['tokens'])
        if unit['id'] == 45:
            tokens[0] = '<code>track_retention_ms</code>'
        value = re.sub(r'\{(\d+)\}', lambda m: tokens[int(m[1])], escape(value, quote=False))
        html = html[:unit['start']] + value + html[unit['end']:]
    labels = UI[language]
    topic_start = html.index('id="controller-activation"')
    topic_end = html.index('</article>', topic_start)
    html = html[:topic_end] + '<p>' + escape(TRIGGER_HELP[language]) + '</p>\n' + html[topic_end:]
    html = html.replace('<html lang="en">', f'<html lang="{language}">', 1)
    for old, new in zip(UI['en'][:5], labels[:5]):
        html = html.replace('="' + old + '"', '="' + escape(new, quote=True) + '"')
    html = html.replace('content="VaporLock settings and troubleshooting guide"', 'content="' + escape(text[6], quote=True) + '"')
    links = ' '.join(f'<a href="VaporLock_Settings_Guide_{code}.html" lang="{code}" hreflang="{code}"'
                     + (' aria-current="page"' if code == language else '') + f'>{name}</a>'
                     for code, name in zip(LANGUAGES, NAMES))
    html = html.replace('<header class="intro">', '<header class="intro">\n<nav class="language-nav" aria-label="'
                        + escape(labels[5], quote=True) + '">' + links + '</nav>', 1)
    css = '\n.language-nav {display:flex;flex-wrap:wrap;gap:8px 18px;margin:0 0 22px;font-size:14px;} .language-nav a {color:var(--baby);text-decoration:underline;} .language-nav [aria-current="page"] {color:var(--yellow);font-weight:700;}\n'
    html = html.replace('</style>', css + '</style>', 1)
    policy = '<article class="topic" id="start-policy" data-accent="yellow"><h3>' + escape(labels[13]) + '</h3><p>' + escape(labels[14]) + '</p></article>\n'
    html = html.replace('<p class="path-note">', policy + '<p class="path-note">', 1)
    values = dict(zip(('browse', 'none', 'count', 'both', 'inTab', 'common', 'uncommon'), labels[6:13]))
    literal = json.dumps(values, ensure_ascii=True).replace('<', '\\u003c')
    html = html.replace('(() => {', '(() => {\n      const guideText = ' + literal + ';', 1)
    start = html.index('        status.textContent = words.length')
    end = html.index("          : 'Browse by symptom or setting.';", start) + len("          : 'Browse by symptom or setting.';")
    html = html[:start] + '''        status.textContent = words.length
          ? (visible === 0 ? guideText.none : guideText.count.replace('{count}', String(visible)) +
             guideText.both + counts[activeTab] + guideText.inTab + guideText[activeTab])
          : guideText.browse;''' + html[end:]
    script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
    script_hash = base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode('ascii')
    csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-" + script_hash + "'; base-uri 'none'; form-action 'none'"
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="' + csp + '">', 1)
    return html

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for language in LANGUAGES:
        path = ROOT / 'guides' / f'VaporLock_Settings_Guide_{language}.html'
        document = render(language)
        if args.check:
            if path.read_text(encoding='utf-8') != document:
                raise RuntimeError('Bundled guide differs from canonical full source: ' + language)
        elif not path.is_file() or path.read_bytes() != document.encode('utf-8'):
            path.write_text(document, encoding='utf-8', newline='\n')
        print(language + ': full guide ' + ('verified' if args.check else 'generated'))

if __name__ == '__main__':
    main()
