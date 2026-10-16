# Переработка бота под Premium — этапы 1–2: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** фундамент оформления под Telegram Premium (каталог эмодзи, слой текстов форка, тема кнопок на выходе) и новый путь новичка (автоязык, правила по ссылке, приветствие с пробным, экран «пробный включён», главное меню с главной кнопкой по состоянию).

**Architecture:** тексты форка лежат отдельно (`app/localization/fork/{ru,en}.json`) и сливаются поверх апстрима при загрузке; метки `{e:имя}` превращаются в `<tg-emoji>` по каталогу. Request-middleware сессии aiogram на выходе переносит эмодзи из подписей инлайн-кнопок в `icon_custom_emoji_id`, красит кнопки по таблице темы и чистит теги там, где HTML не работает. Всё включается флагом `PREMIUM_THEME_ENABLED` (по умолчанию `false`, на проде — `true`).

**Tech Stack:** Python 3, aiogram 3.31 (Bot API ≥ 9.4: `style`, `icon_custom_emoji_id` у кнопок), pydantic-settings, pytest (+ собственный `pytest_pyfunc_call` в `tests/conftest.py`, тесты помечаются `@pytest.mark.asyncio`), ruff (одинарные кавычки, 120 символов).

**Spec:** `docs/superpowers/specs/2026-10-16-premium-bot-redesign-design.md`. Этот план покрывает этапы 1 и 2 спека (§3, §4, §5, §7, §8 п.1–2). Этапы 3–5 (покупка, уведомления, остальные экраны) — отдельные планы: их содержание — тексты, которые пишутся после предпросмотра этапов 1–2.

## Global Constraints

- Языки: правим только `ru` и `en`; ключи форка существуют в обоих файлах с одинаковыми `{плейсхолдерами}`.
- Файлы апстрима `app/localization/locales/*.json` **не трогаем**.
- Новые ключи (которых нет в апстриме) — только с префиксом `FORK_`; читать их в коде **только через атрибут** (`texts.FORK_WELCOME_TRIAL`), не через `texts.t('FORK_…')` — иначе падает `tests/test_locale_integrity.py::test_t_calls_without_default_exist_in_ru`.
- Эмодзи — только из официальных наборов Telegram (`RestrictedEmoji`, `NewsEmoji`, `FlagsEmoji`, `HandEmoji`).
- Плотность «акценты»: 1–2 анимированных эмодзи на сообщение; заголовок — жирный абзац `<b>…</b>`, не `<h3>/<h4>/<h6>`.
- Цвета: `success` — одно главное действие экрана; `primary` — второстепенное; `danger` — отмена/необратимое; навигация («назад», «в меню») — без цвета и без иконки. Явный `style=` в коде всегда побеждает таблицу.
- `PREMIUM_THEME_ENABLED` и `LANGUAGE_AUTO_DETECT` по умолчанию `false` (так upstream-тесты, например `tests/utils/test_rich_menu.py` с `<h4>`, остаются зелёными); на проде включаются в `.env`. Отличие от спека §3.2 («по умолчанию true») — осознанное.
- Отличие от спека §3.5: таблица цветов не переиспользует `button_styles_cache` (это стили кабинетного режима меню, на проде не используется) — главное меню красится явными `style` в раскладке форка (Task 11), остальное — таблицей `button_theme`.
- Отличие от спека §3.1: `scripts/emoji_lookup.py` не пишем — поиск ID уже делает `/root/fi2-migration/tg_emoji.py` на msk3 (только чтение), каталог собран им.
- Отличие от спека §3.4: **reply-клавиатуры не трогаем**. Текст reply-кнопки — это сообщение, которое бот получает при нажатии; срезав эмодзи, мы сломаем обработчики вида `F.text == '…'`.
- Каждый новый файл в `app/` или `scripts/` → `make docs-structure` и коммит `docs/project_structure_reference.md` в той же задаче (иначе падает `tests/test_structure_reference_is_current.py`).
- Пуш в `main` = автодеплой на msk3. Мержить только в задачах выкатки и только после «да» владельца. Изменения `.env`/БД/файлов на сервере — только с бэкапом и после явного «да».
- Запуск тестов: `uv run pytest <путь> -q`. Линтер: `uv run ruff check <файлы> && uv run ruff format --check <файлы>`.

## Review Focus

1. Подпись кнопки состоит из одного эмодзи (`'⬅️'`, `'🔄'`) — иконку не выносим (иначе пустая подпись → Telegram отвергнет кнопку); подпись остаётся как есть. Тест — задача 4.
2. Сообщение с готовыми `entities`/`caption_entities` (текст без `parse_mode`, но с сущностями) — текст не меняем, иначе сдвигаются смещения сущностей. Тест — задача 4.
3. Неизвестное имя в `{e:имя}` в файле форка — бот не должен упасть при загрузке локали: ключ берётся из апстрима, ошибка в лог (а тест в CI падает). Тест — задача 2.
4. Кнопка, подпись которой — общий для пользователей объект (клавиатура-константа модуля), — middleware не мутирует исходную разметку, а строит копию. Тест — задача 4.
5. Новый пользователь открывает `?start=rules` до регистрации — правила показываются, регистрация не запускается и не падает на отсутствии `db_user`. Тест — задача 8.

---

## Файловая структура

| Файл | Ответственность |
|---|---|
| `app/localization/emoji_catalog.py` (новый) | Каталог `имя → (id, unicode)`, `tag()`, `by_unicode()` |
| `app/localization/fork/ru.json`, `en.json` (новые) | Переписанные и новые (`FORK_`) тексты |
| `app/localization/loader.py` | + слой форка в `load_locale`, разбор `{e:имя}` |
| `app/utils/button_theme.py` (новый) | Таблица цветов: `style_for_button(button) -> str | None` |
| `app/middlewares/premium_theme.py` (новый) | Request-middleware темы |
| `app/bot_factory.py` | Регистрация middleware |
| `app/config.py`, `.env.example` | Флаги `PREMIUM_THEME_ENABLED`, `LANGUAGE_AUTO_DETECT` |
| `app/utils/fork_onboarding.py` (новый) | Автоязык, доступность пробного, склонение дней, экраны приветствия/пробного/правил |
| `app/handlers/start.py` | Точки входа: автоязык, `?start=rules`, приветствие форка |
| `app/handlers/subscription/purchase.py` | Точка входа: экран «пробный включён» |
| `app/services/menu_layout/fork_layout.py` (новый) | `resolve_primary_action()`, раскладка меню форка |
| `app/services/menu_layout/context.py`, `service.py`, `app/keyboards/inline.py` | Поле `primary_action`, условие, `style` у кнопок, выбор раскладки |
| `app/utils/rich_menu.py` | Шапка и заголовки без `<h4>/<h6>` при теме, реферальная подсказка |
| `scripts/preview_screens.py` (новый) | Предпросмотр экранов одному адресату |
| `tests/localization/test_emoji_catalog.py`, `test_fork_locale.py`, `tests/utils/test_button_theme.py`, `tests/middlewares/test_premium_theme.py`, `tests/utils/test_fork_onboarding.py`, `tests/handlers/test_fork_start_flow.py`, `tests/services/test_fork_menu_layout.py`, `tests/utils/test_rich_menu_fork.py`, `tests/scripts/test_preview_screens.py` (новые) | Тесты |

Если каталога `tests/localization/` или `tests/scripts/` нет — создать с пустым `__init__.py` (проверить, как устроены соседние каталоги `tests/utils/`: есть ли там `__init__.py`, и сделать так же).

---

# Этап 1. Фундамент

### Task 1: Флаги и каталог эмодзи

**Files:**
- Modify: `app/config.py` (блок рядом с `SKIP_RULES_ACCEPT`, ~строка 1273)
- Modify: `.env.example` (секция `# ===== ЛОКАЛИЗАЦИЯ =====`, ~строка 1201)
- Create: `app/localization/emoji_catalog.py`
- Test: `tests/localization/test_emoji_catalog.py`

**Interfaces:**
- Produces: `settings.PREMIUM_THEME_ENABLED: bool`, `settings.LANGUAGE_AUTO_DETECT: bool`;
  `emoji_catalog.CatalogEmoji` (frozen dataclass: `name: str`, `emoji_id: str`, `fallback: str`);
  `emoji_catalog.CATALOG: dict[str, CatalogEmoji]`; `emoji_catalog.tag(name: str) -> str` (KeyError → `ValueError` для неизвестного имени);
  `emoji_catalog.by_unicode(symbol: str) -> CatalogEmoji | None` (сравнение без `U+FE0F`).

- [ ] **Step 1: Write the failing test**

```python
# tests/localization/test_emoji_catalog.py
"""Каталог анимированных эмодзи: ID из официальных наборов Telegram, запасной символ — один эмодзи."""

import re

import pytest

from app.localization import emoji_catalog
from app.localization.emoji_catalog import CATALOG, by_unicode, tag


def test_ids_are_numeric_and_names_are_snake_case():
    for name, entry in CATALOG.items():
        assert entry.name == name
        assert re.fullmatch(r'[a-z0-9_]+', name), name
        assert re.fullmatch(r'\d{15,25}', entry.emoji_id), name


def test_fallbacks_are_unique_single_emoji():
    seen = {}
    for name, entry in CATALOG.items():
        normalized = entry.fallback.replace('️', '')
        assert normalized, name
        assert ' ' not in entry.fallback, name
        assert normalized not in seen, f'{name} и {seen.get(normalized)} делят {entry.fallback}'
        seen[normalized] = name


def test_tag_renders_tg_emoji_with_fallback():
    assert tag('rocket') == '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>'


def test_tag_unknown_name_raises_value_error():
    with pytest.raises(ValueError, match='nope'):
        tag('nope')


def test_by_unicode_ignores_variation_selector():
    assert by_unicode('⚙️').name == 'gear'
    assert by_unicode('⚙').name == 'gear'
    assert by_unicode('🚀').name == 'rocket'
    assert by_unicode('🛟') is None
    assert by_unicode('') is None


def test_module_exposes_only_public_api():
    assert {'CATALOG', 'CatalogEmoji', 'tag', 'by_unicode'} <= set(dir(emoji_catalog))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/localization/test_emoji_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.localization.emoji_catalog'` (если нет `tests/localization/__init__.py` и в соседних каталогах он есть — создать).

- [ ] **Step 3: Write minimal implementation**

`app/config.py` — сразу после `SKIP_REFERRAL_CODE: bool = False`:

```python
    # Оформление под Telegram Premium владельца бота (форк): тексты из
    # app/localization/fork/, анимированные иконки и цвета кнопок через
    # PremiumThemeMiddleware, новый путь новичка. Выключение — мгновенный откат.
    PREMIUM_THEME_ENABLED: bool = False
    # Язык нового пользователя — из настроек Telegram (language_code), без экрана
    # выбора. Кнопка «Язык» в меню по-прежнему зависит от LANGUAGE_SELECTION_ENABLED.
    LANGUAGE_AUTO_DETECT: bool = False
```

`.env.example` — после строки `LANGUAGE_SELECTION_ENABLED=true`:

```
# Язык нового пользователя — из настроек Telegram, без экрана выбора языка
# LANGUAGE_AUTO_DETECT=false
# Оформление под Telegram Premium владельца бота: анимированные эмодзи, цветные кнопки,
# новое приветствие и главное меню. Требует Premium у аккаунта-владельца бота.
# PREMIUM_THEME_ENABLED=false
```

`app/localization/emoji_catalog.py`:

```python
"""Каталог анимированных (кастомных) эмодзи для текстов и кнопок бота.

Кастомные эмодзи бот может слать, только пока у аккаунта-владельца есть Telegram
Premium; без него Telegram молча подставляет запасной обычный символ, поэтому
у каждой записи он есть. ID — только из официальных наборов Telegram
(RestrictedEmoji, NewsEmoji, FlagsEmoji, HandEmoji): пользовательский набор
автор может удалить, и эмодзи пропадут. Новые ID искать ботом:
getCustomEmojiStickers / getStickerSet по имени набора (только чтение).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEmoji:
    name: str
    emoji_id: str
    fallback: str


_ENTRIES: tuple[tuple[str, str, str], ...] = (
    # RestrictedEmoji («Animated Emoji»)
    ('rocket', '5445284980978621387', '🚀'),
    ('gift', '5199749070830197566', '🎁'),
    ('gem', '5471952986970267163', '💎'),
    ('wave', '5472055112702629499', '👋'),
    ('party', '5436040291507247633', '🎉'),
    ('confetti', '5435933711893797296', '🎊'),
    ('money_bag', '5375296873982604963', '💰'),
    ('money_wings', '5472030678633684592', '💸'),
    ('coin', '5379600444098093058', '🪙'),
    ('people', '5372926953978341366', '👥'),
    ('user', '5373012449597335010', '👤'),
    ('earth', '5399898266265475100', '🌍'),
    ('check', '5427009714745517609', '✅'),
    ('cross', '5465665476971471368', '❌'),
    ('hourglass', '5451732530048802485', '⏳'),
    ('alarm', '5413704112220949842', '⏰'),
    ('refresh', '5264727218734524899', '🔄'),
    ('phone', '5407025283456835913', '📱'),
    ('laptop', '5431376038628171216', '💻'),
    ('tv', '5373330964372004748', '📺'),
    ('call', '5467539229468793355', '📞'),
    ('link', '5375129357373165375', '🔗'),
    ('chart', '5431577498364158238', '📊'),
    ('chart_up', '5373001317042101552', '📈'),
    ('ticket', '5418010521309815154', '🎫'),
    ('key', '5330115548900501467', '🔑'),
    ('test_tube', '5411512278740640309', '🧪'),
    ('handshake', '5357080225463149588', '🤝'),
    ('fire', '5420315771991497307', '🔥'),
    ('sparkles', '5472164874886846699', '✨'),
    ('blue_heart', '5449759615346548186', '💙'),
    ('heart', '5449505950283078474', '❤️'),
    ('new', '5361979468887893611', '🆕'),
    ('home', '5465226866321268133', '🏠'),
    ('speech', '5465300082628763143', '💬'),
    ('lock_key', '5472308992514464048', '🔐'),
    ('cart', '5431499171045581032', '🛒'),
    ('bell', '5242628160297641831', '🔔'),
    ('question', '5467666648263564704', '❓'),
    ('book', '5226512880362332956', '📖'),
    ('memo', '5334882760735598374', '📝'),
    ('pencil', '5334673106202010226', '✏️'),
    ('bulb', '5472146462362048818', '💡'),
    ('target', '5350460637182993292', '🎯'),
    ('inbox', '5433811242135331842', '📥'),
    ('crown', '5467406098367521267', '👑'),
    ('trophy', '5409008750893734809', '🏆'),
    ('briefcase', '5359785904535774578', '💼'),
    ('compass', '5433825729060018456', '🧭'),
    ('robot', '5372981976804366741', '🤖'),
    ('point_right', '5471978009449731768', '👉'),
    ('point_down', '5470177992950946662', '👇'),
    ('thumbs_up', '5469770542288478598', '👍'),
    ('raised_hands', '5469645992531862101', '🙌'),
    ('money_face', '5373303394976929925', '🤑'),
    ('bangbang', '5467890025217661107', '‼️'),
    # NewsEmoji
    ('info', '5334544901428229844', 'ℹ️'),
    ('globe', '5447410659077661506', '🌐'),
    ('warning', '5447644880824181073', '⚠️'),
    ('gear', '5341715473882955310', '⚙️'),
    ('lock', '5296369303661067030', '🔒'),
    ('no_entry', '5240241223632954241', '🚫'),
    ('download', '5406745015365943482', '⬇️'),
    ('play', '5264919878082509254', '▶️'),
    ('dollar', '5409048419211682843', '💵'),
    ('speaking', '5460795800101594035', '🗣️'),
)

CATALOG: dict[str, CatalogEmoji] = {
    name: CatalogEmoji(name, emoji_id, fallback) for name, emoji_id, fallback in _ENTRIES
}

_VS16 = '️'
_BY_UNICODE: dict[str, CatalogEmoji] = {entry.fallback.replace(_VS16, ''): entry for entry in CATALOG.values()}


def tag(name: str) -> str:
    """HTML-тег кастомного эмодзи с запасным символом внутри."""
    entry = CATALOG.get(name)
    if entry is None:
        raise ValueError(f'Неизвестный эмодзи каталога: {name}')
    return f'<tg-emoji emoji-id="{entry.emoji_id}">{entry.fallback}</tg-emoji>'


def by_unicode(symbol: str) -> CatalogEmoji | None:
    """Запись каталога по обычному эмодзи (вариационный селектор U+FE0F не важен)."""
    if not symbol:
        return None
    return _BY_UNICODE.get(symbol.replace(_VS16, ''))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/localization/test_emoji_catalog.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Regenerate structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/localization/emoji_catalog.py app/config.py tests/localization && uv run ruff format --check app/localization/emoji_catalog.py tests/localization
uv run pytest tests/test_structure_reference_is_current.py tests/test_config_languages.py -q
git add app/config.py .env.example app/localization/emoji_catalog.py tests/localization docs/project_structure_reference.md
git commit -m "feat(premium): флаги PREMIUM_THEME_ENABLED/LANGUAGE_AUTO_DETECT и каталог анимированных эмодзи"
```

---

### Task 2: Слой текстов форка в загрузчике

**Files:**
- Modify: `app/localization/loader.py` (импорты; новая функция рядом с `_load_user_locale`; `load_locale` ~строка 289)
- Create: `app/localization/fork/ru.json`, `app/localization/fork/en.json` (на этом этапе — `{}`)
- Test: `tests/localization/test_fork_locale.py`

**Interfaces:**
- Consumes: `emoji_catalog.tag(name) -> str` (Task 1), `settings.PREMIUM_THEME_ENABLED`.
- Produces: `loader.resolve_emoji_markers(value: str) -> str` (заменяет `{e:имя}` на тег; неизвестное имя → `ValueError`);
  `loader.FORK_LOCALES_DIR: Path`; `load_locale(lang)` = апстрим ⊕ форк (если флаг) ⊕ `LOCALES_PATH`.

- [ ] **Step 1: Write the failing test**

```python
# tests/localization/test_fork_locale.py
"""Тексты форка (app/localization/fork/) поверх апстрима.

Ловят то, что сломает бота после синка с апстримом: ключ форка, который апстрим
переименовал (молча перестанет действовать), разошедшиеся {плейсхолдеры}
(.format() упадёт в рантайме), опечатку в имени эмодзи.
"""

import json
import re
from pathlib import Path

import pytest

from app.config import settings
from app.localization import loader
from app.localization.emoji_catalog import CATALOG


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_DIR = ROOT / 'app' / 'localization' / 'locales'
FORK_DIR = ROOT / 'app' / 'localization' / 'fork'
FORK_LANGS = ('ru', 'en')
MARKER_RE = re.compile(r'\{e:([^}]*)\}')
PLACEHOLDER_RE = re.compile(r'\{(?!e:)[^}]*\}')


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


@pytest.fixture
def theme_off(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def test_fork_files_have_identical_keys():
    ru, en = (_read(FORK_DIR / f'{lang}.json') for lang in FORK_LANGS)
    assert set(ru) == set(en), f'ru-en: {sorted(set(ru) - set(en))}, en-ru: {sorted(set(en) - set(ru))}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_keys_exist_upstream_or_are_prefixed(lang):
    upstream = _read(UPSTREAM_DIR / 'ru.json')
    fork = _read(FORK_DIR / f'{lang}.json')
    stray = sorted(key for key in fork if key not in upstream and not key.startswith('FORK_'))
    assert not stray, f'ключей нет в апстриме (переименованы?), а префикса FORK_ нет: {stray}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_values_are_strings_with_known_emoji(lang):
    for key, value in _read(FORK_DIR / f'{lang}.json').items():
        assert isinstance(value, str), key
        for name in MARKER_RE.findall(value):
            assert name in CATALOG, f'{lang}.{key}: неизвестный эмодзи {{e:{name}}}'


@pytest.mark.parametrize('lang', FORK_LANGS)
def test_fork_placeholders_match_upstream(lang):
    upstream = _read(UPSTREAM_DIR / f'{lang}.json')
    problems = []
    for key, value in _read(FORK_DIR / f'{lang}.json').items():
        if key not in upstream:
            continue
        if sorted(PLACEHOLDER_RE.findall(value)) != sorted(PLACEHOLDER_RE.findall(upstream[key])):
            problems.append(key)
    assert not problems, f'{{плейсхолдеры}} разошлись с апстримом: {problems}'


def test_fork_placeholders_match_between_languages():
    ru, en = (_read(FORK_DIR / f'{lang}.json') for lang in FORK_LANGS)
    mismatched = [k for k in ru if sorted(PLACEHOLDER_RE.findall(ru[k])) != sorted(PLACEHOLDER_RE.findall(en[k]))]
    assert not mismatched, mismatched


def test_resolve_emoji_markers():
    assert loader.resolve_emoji_markers('{e:rocket} Go {name}') == (
        '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> Go {name}'
    )
    with pytest.raises(ValueError):
        loader.resolve_emoji_markers('{e:nope} x')


def test_fork_layer_overrides_upstream_when_enabled(theme_on, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': '{e:home} Домой', 'FORK_X': 'x {n}'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    data = loader.load_locale('ru')
    assert data['BACK'] == '<tg-emoji emoji-id="5465226866321268133">🏠</tg-emoji> Домой'
    assert data['FORK_X'] == 'x {n}'
    assert 'MAIN_MENU' in data  # апстрим на месте


def test_fork_layer_skipped_when_disabled(theme_off, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': 'Домой'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    assert loader.load_locale('ru')['BACK'] == _read(UPSTREAM_DIR / 'ru.json')['BACK']


def test_unknown_emoji_keeps_upstream_value_and_does_not_crash(theme_on, monkeypatch, tmp_path):
    (tmp_path / 'ru.json').write_text(json.dumps({'BACK': '{e:nope} Домой'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', tmp_path)
    assert loader.load_locale('ru')['BACK'] == _read(UPSTREAM_DIR / 'ru.json')['BACK']


def test_server_overrides_still_win_over_fork(theme_on, monkeypatch, tmp_path):
    fork_dir = tmp_path / 'fork'
    user_dir = tmp_path / 'user'
    fork_dir.mkdir()
    user_dir.mkdir()
    (fork_dir / 'ru.json').write_text(json.dumps({'BACK': 'форк'}), encoding='utf-8')
    (user_dir / 'ru.json').write_text(json.dumps({'BACK': 'сервер'}), encoding='utf-8')
    monkeypatch.setattr(loader, 'FORK_LOCALES_DIR', fork_dir)
    monkeypatch.setattr(settings, 'LOCALES_PATH', str(user_dir))
    assert loader.load_locale('ru')['BACK'] == 'сервер'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/localization/test_fork_locale.py -q`
Expected: FAIL — нет `app/localization/fork/ru.json` и нет `loader.resolve_emoji_markers` / `loader.FORK_LOCALES_DIR`.

- [ ] **Step 3: Write minimal implementation**

Создать `app/localization/fork/ru.json` и `app/localization/fork/en.json` с содержимым `{}` (и переводом строки в конце).

`app/localization/loader.py`: добавить `import re` к импортам и после `_DEFAULT_LOCALES_DIR = …`:

```python
# Тексты форка: переписанные ключи апстрима и новые (FORK_*). Слой лежит между
# апстримом и пользовательскими файлами LOCALES_PATH и включается флагом
# PREMIUM_THEME_ENABLED — так файлы апстрима не меняются и синк не конфликтует.
FORK_LOCALES_DIR = _BASE_DIR / 'fork'
_EMOJI_MARKER_RE = re.compile(r'\{e:([^}]*)\}')
```

Рядом с `_load_user_locale` добавить:

```python
def resolve_emoji_markers(value: str) -> str:
    """{e:имя} → <tg-emoji> из каталога. Делается при загрузке, до любого .format():
    иначе .format() прочёл бы {e:rocket} как поле «e»."""
    from app.localization.emoji_catalog import tag

    return _EMOJI_MARKER_RE.sub(lambda match: tag(match.group(1)), value)


def _load_fork_locale(language: str) -> dict[str, Any]:
    if not settings.PREMIUM_THEME_ENABLED:
        return {}
    path = FORK_LOCALES_DIR / f'{language}.json'
    if not path.exists():
        return {}

    resolved: dict[str, Any] = {}
    for key, value in _normalize_locale_dict(_load_locale_file(path)).items():
        if not isinstance(value, str):
            resolved[key] = value
            continue
        try:
            resolved[key] = resolve_emoji_markers(value)
        except ValueError as error:
            # Опечатка в имени эмодзи не должна ронять все тексты бота: ключ
            # остаётся апстримным, а тест fork-локалей в CI падает.
            _logger.error('Ключ форка пропущен: неизвестный эмодзи', key=key, language=language, error=str(error))
    return resolved
```

В `load_locale` заменить две строки

```python
    overrides = _load_user_locale(language)
    merged = _merge_dicts(defaults, overrides)
```

на

```python
    fork = _load_fork_locale(language)
    overrides = _load_user_locale(language)
    merged = _merge_dicts(_merge_dicts(defaults, fork), overrides)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/localization tests/test_locale_integrity.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/localization tests/localization && uv run ruff format --check app/localization/loader.py tests/localization
uv run pytest tests/test_structure_reference_is_current.py -q
git add app/localization tests/localization docs/project_structure_reference.md
git commit -m "feat(premium): слой текстов форка поверх апстрима с метками {e:имя}"
```

---

### Task 3: Таблица цветов кнопок

**Files:**
- Create: `app/utils/button_theme.py`
- Test: `tests/utils/test_button_theme.py`

**Interfaces:**
- Produces: `button_theme.style_for_button(button: InlineKeyboardButton) -> str | None` — `'success' | 'primary' | 'danger' | None`. Смотрит только `callback_data` (url/web_app/прочие → `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/utils/test_button_theme.py
"""Цвет кнопки по смыслу: главное действие — зелёный, второстепенное — синий,
отмена — красный, навигация — без цвета. Явный style в коде сюда не доходит
(его не трогает middleware), тут только правила таблицы."""

import pytest
from aiogram.types import InlineKeyboardButton, WebAppInfo

from app.utils.button_theme import style_for_button


def _cb(data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text='x', callback_data=data)


@pytest.mark.parametrize(
    ('callback_data', 'expected'),
    [
        ('trial_activate', 'success'),
        ('menu_trial', 'success'),
        ('subscription_connect', 'success'),
        ('subscription_extend', 'success'),
        ('subscription_confirm', 'success'),
        ('tariff_confirm:3:30', 'success'),
        ('tariff_ext_confirm:3:30', 'success'),
        ('balance_topup', 'success'),
        ('rules_accept', 'success'),
        ('menu_buy', 'primary'),
        ('menu_subscription', 'primary'),
        ('menu_balance', 'primary'),
        ('rules_decline', 'danger'),
        ('gift_cancel', 'danger'),
        ('cancel_rename_5', 'danger'),
        ('admin_user_delete_5', 'danger'),
        ('back_to_menu', None),
        ('back_to_tariffs', None),
        ('noop', None),
        ('menu_info', None),
        ('something_unknown', None),
    ],
)
def test_callback_rules(callback_data, expected):
    assert style_for_button(_cb(callback_data)) == expected


def test_non_callback_buttons_have_no_theme_color():
    assert style_for_button(InlineKeyboardButton(text='x', url='https://example.com')) is None
    assert style_for_button(InlineKeyboardButton(text='x', web_app=WebAppInfo(url='https://example.com'))) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/utils/test_button_theme.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.button_theme'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/utils/button_theme.py
"""Цвет инлайн-кнопки по её callback_data — для кнопок, которым код не задал style.

Смысл цветов один на весь бот: success — главное действие экрана (пробный,
подключиться, оплатить, продлить), primary — второстепенное, danger — отмена и
необратимое, навигация — без цвета. Первое совпавшее правило побеждает, поэтому
«назад» стоит раньше всего: back_to_tariffs не должен стать синим из-за «tariff».
Применяет PremiumThemeMiddleware; явный style= в коде таблица не перекрывает.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton


# (вид совпадения, значение, стиль). Вид: exact | prefix | contains.
_RULES: tuple[tuple[str, str, str | None], ...] = (
    ('exact', 'back_to_menu', None),
    ('prefix', 'back', None),
    ('exact', 'noop', None),
    ('exact', 'rules_decline', 'danger'),
    ('exact', 'privacy_policy_decline', 'danger'),
    ('exact', 'gift_cancel', 'danger'),
    ('exact', 'gift_activation_cancel', 'danger'),
    ('exact', 'referral_withdrawal_cancel', 'danger'),
    ('prefix', 'cancel', 'danger'),
    ('contains', 'delete', 'danger'),
    ('exact', 'trial_activate', 'success'),
    ('exact', 'menu_trial', 'success'),
    ('exact', 'subscription_connect', 'success'),
    ('exact', 'open_subscription_link', 'success'),
    ('exact', 'subscription_extend', 'success'),
    ('exact', 'subscription_confirm', 'success'),
    ('exact', 'simple_subscription_pay_with_balance', 'success'),
    ('exact', 'return_to_saved_cart', 'success'),
    ('exact', 'subscription_resume_checkout', 'success'),
    ('exact', 'balance_topup', 'success'),
    ('exact', 'sub_channel_check', 'success'),
    ('exact', 'rules_accept', 'success'),
    ('exact', 'privacy_policy_accept', 'success'),
    ('prefix', 'tariff_confirm', 'success'),
    ('prefix', 'tariff_ext_confirm', 'success'),
    ('prefix', 'tariff_sw_confirm', 'success'),
    ('prefix', 'instant_sw_confirm', 'success'),
    ('prefix', 'daily_tariff_confirm', 'success'),
    ('prefix', 'daily_tariff_switch_confirm', 'success'),
    ('prefix', 'custom_confirm', 'success'),
    ('exact', 'menu_buy', 'primary'),
    ('exact', 'menu_subscription', 'primary'),
    ('exact', 'menu_balance', 'primary'),
)


def _matches(kind: str, pattern: str, data: str) -> bool:
    if kind == 'exact':
        return data == pattern
    if kind == 'prefix':
        return data.startswith(pattern)
    return pattern in data


def style_for_button(button: InlineKeyboardButton) -> str | None:
    data = button.callback_data
    if not data:
        return None
    for kind, pattern, style in _RULES:
        if _matches(kind, pattern, data):
            return style
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/utils/test_button_theme.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/utils/button_theme.py tests/utils/test_button_theme.py && uv run ruff format --check app/utils/button_theme.py tests/utils/test_button_theme.py
uv run pytest tests/test_structure_reference_is_current.py -q
git add app/utils/button_theme.py tests/utils/test_button_theme.py docs/project_structure_reference.md
git commit -m "feat(premium): таблица цветов кнопок по callback_data"
```

---

### Task 4: Middleware темы на выходе

**Files:**
- Create: `app/middlewares/premium_theme.py`
- Modify: `app/bot_factory.py` (импорт и строка после `bot.session.middleware(StaleCallbackAnswerMiddleware())`)
- Test: `tests/middlewares/test_premium_theme.py`

**Interfaces:**
- Consumes: `emoji_catalog.by_unicode` (Task 1), `button_theme.style_for_button` (Task 3), `app.utils.miniapp_buttons.strip_leading_emoji(text: str) -> str` (существует), `settings.PREMIUM_THEME_ENABLED`.
- Produces: `PremiumThemeMiddleware` (класс `BaseRequestMiddleware`); чистые функции `theme_button(button) -> InlineKeyboardButton`, `theme_markup(markup) -> InlineKeyboardMarkup`, `to_plain(value: str) -> str`, `restore_escaped(value: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/middlewares/test_premium_theme.py
"""Тема Premium на выходе: иконки и цвета кнопок, теги там, где HTML не работает."""

from unittest.mock import MagicMock

import pytest
from aiogram.client.default import DefaultBotProperties
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MessageEntity,
    ReplyKeyboardMarkup,
)

from app.config import settings
from app.middlewares import premium_theme as mw


ROCKET = '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>'


@pytest.fixture(autouse=True)
def theme_on(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)


def _bot():
    bot = MagicMock()
    bot.default = DefaultBotProperties(parse_mode='HTML')
    return bot


async def _run(method):
    seen = {}

    async def make_request(_bot, sent):
        seen['method'] = sent
        return True

    await mw.PremiumThemeMiddleware()(make_request, _bot(), method)
    return seen['method']


def test_leading_tag_becomes_icon():
    out = mw.theme_button(InlineKeyboardButton(text=f'{ROCKET} Подключиться', callback_data='subscription_connect'))
    assert out.text == 'Подключиться'
    assert out.icon_custom_emoji_id == '5445284980978621387'
    assert out.style == 'success'


def test_leading_unicode_from_catalog_becomes_icon():
    out = mw.theme_button(InlineKeyboardButton(text='⚙️ Админ панель', callback_data='admin_panel'))
    assert out.text == 'Админ панель'
    assert out.icon_custom_emoji_id == '5341715473882955310'


def test_unicode_outside_catalog_is_left_alone():
    out = mw.theme_button(InlineKeyboardButton(text='🛟 Помощь', callback_data='menu_support'))
    assert out.text == '🛟 Помощь'
    assert out.icon_custom_emoji_id is None


def test_emoji_only_label_is_not_emptied():
    out = mw.theme_button(InlineKeyboardButton(text='🔄', callback_data='refresh_x'))
    assert out.text == '🔄'
    assert out.icon_custom_emoji_id is None
    tagged = mw.theme_button(InlineKeyboardButton(text=ROCKET, callback_data='x'))
    assert tagged.text == '🚀'
    assert tagged.icon_custom_emoji_id is None


def test_explicit_style_and_icon_win():
    button = InlineKeyboardButton(
        text=f'{ROCKET} Купить', callback_data='back_to_menu', style='danger', icon_custom_emoji_id='111'
    )
    out = mw.theme_button(button)
    assert out.style == 'danger'
    assert out.icon_custom_emoji_id == '111'
    assert out.text == 'Купить'


def test_inner_tags_fall_back_to_unicode():
    out = mw.theme_button(InlineKeyboardButton(text=f'Купить {ROCKET} сейчас', callback_data='x'))
    assert out.text == 'Купить 🚀 сейчас'


def test_source_markup_is_not_mutated():
    button = InlineKeyboardButton(text=f'{ROCKET} Go', callback_data='trial_activate')
    markup = InlineKeyboardMarkup(inline_keyboard=[[button]])
    themed = mw.theme_markup(markup)
    assert markup.inline_keyboard[0][0].text == f'{ROCKET} Go'
    assert themed.inline_keyboard[0][0].text == 'Go'


@pytest.mark.asyncio
async def test_send_message_markup_themed_and_html_text_kept():
    method = SendMessage(
        chat_id=1,
        text=f'{ROCKET} привет',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='🎁 Попробовать', callback_data='trial_activate')]]
        ),
    )
    sent = await _run(method)
    assert sent.text == f'{ROCKET} привет'
    assert sent.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == '5199749070830197566'
    assert sent.reply_markup.inline_keyboard[0][0].style == 'success'


@pytest.mark.asyncio
async def test_plain_text_message_gets_fallback():
    sent = await _run(SendMessage(chat_id=1, text=f'{ROCKET} привет', parse_mode=None))
    assert sent.text == '🚀 привет'


@pytest.mark.asyncio
async def test_text_with_entities_is_left_untouched():
    text = f'{ROCKET} привет'
    method = SendMessage(
        chat_id=1, text=text, parse_mode=None, entities=[MessageEntity(type='bold', offset=0, length=3)]
    )
    sent = await _run(method)
    assert sent.text == text


@pytest.mark.asyncio
async def test_escaped_tag_is_restored_in_html():
    escaped = '&lt;tg-emoji emoji-id=&quot;5445284980978621387&quot;&gt;🚀&lt;/tg-emoji&gt; привет'
    sent = await _run(EditMessageText(chat_id=1, message_id=2, text=escaped))
    assert sent.text == f'{ROCKET} привет'


@pytest.mark.asyncio
async def test_caption_is_handled_like_text():
    sent = await _run(SendPhoto(chat_id=1, photo='x', caption=f'{ROCKET} фото', parse_mode=None))
    assert sent.caption == '🚀 фото'


@pytest.mark.asyncio
async def test_callback_alert_gets_fallback():
    sent = await _run(AnswerCallbackQuery(callback_query_id='1', text=f'{ROCKET} готово', show_alert=True))
    assert sent.text == '🚀 готово'


@pytest.mark.asyncio
async def test_reply_keyboard_is_not_touched():
    reply = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='🏠 Главное меню')]])
    sent = await _run(SendMessage(chat_id=1, text='x', reply_markup=reply))
    assert sent.reply_markup.keyboard[0][0].text == '🏠 Главное меню'
    assert sent.reply_markup.keyboard[0][0].icon_custom_emoji_id is None


@pytest.mark.asyncio
async def test_disabled_flag_passes_through(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    sent = await _run(SendMessage(chat_id=1, text=f'{ROCKET} x', parse_mode=None))
    assert sent.text == f'{ROCKET} x'


@pytest.mark.asyncio
async def test_theme_error_does_not_block_sending(monkeypatch):
    def boom(_markup):
        raise RuntimeError('boom')

    monkeypatch.setattr(mw, 'theme_markup', boom)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='a', callback_data='b')]])
    sent = await _run(SendMessage(chat_id=1, text='x', reply_markup=markup))
    assert sent.reply_markup is markup


def test_registered_in_bot_factory():
    from app.bot_factory import create_bot

    bot = create_bot(token='123456:' + 'A' * 35)
    names = [type(m).__name__ for m in bot.session.middleware._middlewares]
    assert 'PremiumThemeMiddleware' in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/middlewares/test_premium_theme.py -q`
Expected: FAIL — `ImportError: cannot import name 'premium_theme'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/middlewares/premium_theme.py
"""Оформление исходящих запросов под Telegram Premium владельца бота.

Тексты форка несут <tg-emoji> (см. app/localization/fork/), но одни и те же
строки уходят в сообщения, подписи кнопок и всплывающие алерты, а HTML работает
только в первых. Переписывать сотни мест под каждый контекст — значит воевать
с апстримом на каждом синке, поэтому всё делается на выходе, в middleware сессии:

* инлайн-кнопки: ведущий <tg-emoji> или известный каталогу обычный эмодзи →
  icon_custom_emoji_id (анимированная иконка), прочие теги → обычный символ,
  цвет — по таблице button_theme, если код не задал свой;
* алерты и сообщения без HTML → теги заменяются обычным символом;
* HTML-сообщения → экранированный кем-то тег восстанавливается (с warning в лог).

Reply-клавиатуры не трогаем: текст reply-кнопки бот получает обратно сообщением,
и срезанный эмодзи сломал бы обработчики по тексту. Ошибка оформления не мешает
отправке — запрос уходит как есть.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.localization.emoji_catalog import by_unicode
from app.utils.button_theme import style_for_button
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)

_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.DOTALL)
_LEADING_TG_EMOJI_RE = re.compile(r'^\s*<tg-emoji emoji-id="(\d+)">.*?</tg-emoji>\s*', re.DOTALL)
_ESCAPED_TG_EMOJI_RE = re.compile(
    r'&lt;tg-emoji emoji-id=(?:&quot;|")(\d+)(?:&quot;|")&gt;(.*?)&lt;/tg-emoji&gt;', re.DOTALL
)
# Поле текста и поле его сущностей: при готовых сущностях текст менять нельзя —
# их смещения посчитаны по исходной строке.
_TEXT_FIELDS = (('text', 'entities'), ('caption', 'caption_entities'))


def to_plain(value: str) -> str:
    return _TG_EMOJI_RE.sub(lambda match: match.group(2), value)


def restore_escaped(value: str) -> str:
    return _ESCAPED_TG_EMOJI_RE.sub(
        lambda match: f'<tg-emoji emoji-id="{match.group(1)}">{match.group(2)}</tg-emoji>', value
    )


def theme_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    text = button.text or ''
    update: dict[str, Any] = {}

    leading_tag = _LEADING_TG_EMOJI_RE.match(text)
    if leading_tag and text[leading_tag.end() :].strip():
        if not button.icon_custom_emoji_id:
            update['icon_custom_emoji_id'] = leading_tag.group(1)
        text = text[leading_tag.end() :]
    elif not leading_tag and not button.icon_custom_emoji_id:
        rest = strip_leading_emoji(text)
        leading = text[: len(text) - len(rest)].strip()
        entry = by_unicode(leading) if leading else None
        if entry is not None and rest.strip():
            update['icon_custom_emoji_id'] = entry.emoji_id
            text = rest

    if '<tg-emoji' in text:
        text = to_plain(text)
    if text != button.text:
        update['text'] = text
    if button.style is None:
        style = style_for_button(button)
        if style:
            update['style'] = style
    return button.model_copy(update=update) if update else button


def theme_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[theme_button(button) for button in row] for row in markup.inline_keyboard]
    )


def _is_html(bot: Bot, parse_mode: Any) -> bool:
    if isinstance(parse_mode, Default):
        parse_mode = bot.default[parse_mode.name]
    return isinstance(parse_mode, str) and parse_mode.lower() == 'html'


def _apply(bot: Bot, method: TelegramMethod[Any]) -> None:
    markup = getattr(method, 'reply_markup', None)
    if isinstance(markup, InlineKeyboardMarkup):
        method.reply_markup = theme_markup(markup)

    if isinstance(method, AnswerCallbackQuery):
        if method.text and '<tg-emoji' in method.text:
            method.text = to_plain(method.text)
        return

    fields = type(method).model_fields
    if 'parse_mode' not in fields:
        return  # rich-сообщения и прочие методы без parse_mode: <tg-emoji> там законен
    for text_field, entities_field in _TEXT_FIELDS:
        value = getattr(method, text_field, None) if text_field in fields else None
        if not isinstance(value, str) or 'tg-emoji' not in value:
            continue
        if entities_field in fields and getattr(method, entities_field, None):
            continue
        if _is_html(bot, getattr(method, 'parse_mode', None)):
            restored = restore_escaped(value)
            if restored != value:
                logger.warning(
                    'Восстановлен экранированный <tg-emoji> — место экранирует наш текст',
                    method=type(method).__name__,
                    text=value[:80],
                )
                setattr(method, text_field, restored)
        else:
            setattr(method, text_field, to_plain(value))


class PremiumThemeMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        if settings.PREMIUM_THEME_ENABLED:
            try:
                _apply(bot, method)
            except Exception as error:
                logger.warning('Тема Premium не применена', method=type(method).__name__, error=str(error))
        return await make_request(bot, method)
```

`app/bot_factory.py`: импорт `from app.middlewares.premium_theme import PremiumThemeMiddleware` и после строки `bot.session.middleware(StaleCallbackAnswerMiddleware())`:

```python
    # Анимированные иконки и цвета кнопок, теги <tg-emoji> вне HTML (см. middleware).
    bot.session.middleware(PremiumThemeMiddleware())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/middlewares -q`
Expected: PASS (в том числе старые тесты `test_stale_callback_answer.py`)

- [ ] **Step 5: Full suite, structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/middlewares/premium_theme.py app/bot_factory.py tests/middlewares/test_premium_theme.py && uv run ruff format --check app/middlewares/premium_theme.py tests/middlewares/test_premium_theme.py
uv run pytest -q -x
git add app/middlewares/premium_theme.py app/bot_factory.py tests/middlewares/test_premium_theme.py docs/project_structure_reference.md
git commit -m "feat(premium): middleware темы — анимированные иконки и цвета кнопок, теги вне HTML"
```

---

### Task 5: Скрипт предпросмотра

**Files:**
- Create: `scripts/preview_screens.py`
- Test: `tests/scripts/test_preview_screens.py`

**Interfaces:**
- Consumes: `CATALOG` (Task 1), `MenuLayoutService` + `MenuContext` (существуют), `create_bot` (`app/bot_factory.py`).
- Produces: `preview_screens.build_screens(language: str) -> list[PreviewScreen]`, где `PreviewScreen` — `NamedTuple(title: str, text: str, markup: InlineKeyboardMarkup | None)`; `async def send_screens(chat_id: int, language: str) -> int` (возвращает число отправленных). Этап 2 (Task 13) дописывает экраны в `build_screens`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_preview_screens.py
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from scripts import preview_screens


def test_build_screens_contains_catalog_sheet_and_menus(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    screens = preview_screens.build_screens('ru')
    titles = [screen.title for screen in screens]
    assert 'Каталог эмодзи' in titles[0]
    assert any('Главное меню' in title for title in titles)
    catalog_text = screens[0].text
    assert catalog_text.count('<tg-emoji') >= 60
    for screen in screens:
        assert len(screen.text) <= 4096, screen.title
        assert screen.markup is None or isinstance(screen.markup, InlineKeyboardMarkup)
```

(`build_screens` синхронная: меню берёт раскладку из кода — `MenuLayoutService.get_default_config()` — без БД; см. шаг 3.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scripts/test_preview_screens.py -q`
Expected: FAIL — `ImportError: cannot import name 'preview_screens'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/preview_screens.py
"""Предпросмотр оформления: шлёт ключевые экраны бота ОДНОМУ адресату.

Экраны собираются настоящими текстами, клавиатурами и PremiumThemeMiddleware на
фиктивных данных — пользователи ничего не видят. Запуск на msk3 из копии ветки
в образе бота (токен и прокси — из прод-.env), см. задачи выкатки в плане:

    docker compose run --rm --no-deps \
      -v /root/preview-premium/app:/app/app:ro -v /root/preview-premium/scripts:/app/scripts:ro \
      bot python -m scripts.preview_screens --to <telegram_id> --lang ru
"""

from __future__ import annotations

import argparse
import asyncio
from typing import NamedTuple

from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.localization.emoji_catalog import CATALOG, tag


class PreviewScreen(NamedTuple):
    title: str
    text: str
    markup: InlineKeyboardMarkup | None


def _catalog_sheet() -> PreviewScreen:
    lines = [f'{tag(name)} <code>{name}</code>' for name in CATALOG]
    return PreviewScreen('Каталог эмодзи', '<b>Каталог эмодзи</b>\n' + '\n'.join(lines), None)


def _menu_keyboard(language: str, **context_fields) -> InlineKeyboardMarkup:
    from app.services.menu_layout_service import MenuContext, MenuLayoutService

    config = MenuLayoutService.get_default_config()
    context = MenuContext(language=language, **context_fields)
    # Синхронная сборка без БД: повторяем MenuLayoutService.build_keyboard по готовой раскладке.
    from app.localization.texts import get_texts

    texts = get_texts(language)
    rows = []
    buttons_config = config.get('buttons', {})
    for row_config in config.get('rows', []):
        if not MenuLayoutService._evaluate_conditions(row_config.get('conditions'), context):
            continue
        row = []
        for button_id in row_config.get('buttons', []):
            cfg = buttons_config.get(button_id)
            if not cfg or not cfg.get('enabled', True):
                continue
            if not MenuLayoutService._check_visibility(cfg.get('visibility', 'all'), context):
                continue
            if not MenuLayoutService._evaluate_conditions(cfg.get('conditions'), context):
                continue
            button = MenuLayoutService._build_button(cfg, context, texts, button_id=button_id)
            if button:
                row.append(button)
        max_per_row = row_config.get('max_per_row', 2)
        rows.extend(row[i : i + max_per_row] for i in range(0, len(row), max_per_row))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_screens(language: str) -> list[PreviewScreen]:
    return [
        _catalog_sheet(),
        PreviewScreen(
            'Главное меню — новичок',
            '<b>Главное меню</b> — новичок без подписки',
            _menu_keyboard(language, balance_kopeks=0),
        ),
        PreviewScreen(
            'Главное меню — подписчик',
            '<b>Главное меню</b> — активная подписка',
            _menu_keyboard(language, has_active_subscription=True, subscription_is_active=True, balance_kopeks=15000),
        ),
    ]


async def send_screens(chat_id: int, language: str) -> int:
    from app.bot_factory import create_bot

    settings.PREMIUM_THEME_ENABLED = True
    bot = create_bot()
    sent = 0
    try:
        for screen in build_screens(language):
            await bot.send_message(chat_id, f'<i>{screen.title}</i>')
            await bot.send_message(chat_id, screen.text, reply_markup=screen.markup)
            sent += 1
    finally:
        await bot.session.close()
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description='Предпросмотр экранов бота одному адресату')
    parser.add_argument('--to', type=int, required=True, help='telegram_id получателя (владелец)')
    parser.add_argument('--lang', default='ru', choices=('ru', 'en'))
    args = parser.parse_args()
    count = asyncio.run(send_screens(args.to, args.lang))
    print(f'Отправлено экранов: {count}')


if __name__ == '__main__':
    main()
```

Перед реализацией проверить, что `app.services.menu_layout_service` реэкспортирует `MenuContext` и `MenuLayoutService` (так импортирует `app/keyboards/inline.py:51`) и что `_build_button` при `dynamic_text` не ходит в БД (только `texts.format_price`). Если `texts` не имеет `format_price` — передать контекст без `balance_kopeks` и проверить вывод.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scripts/test_preview_screens.py -q`
Expected: PASS

- [ ] **Step 5: Structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check scripts/preview_screens.py tests/scripts && uv run ruff format --check scripts/preview_screens.py tests/scripts
uv run pytest tests/test_structure_reference_is_current.py -q
git add scripts/preview_screens.py tests/scripts docs/project_structure_reference.md
git commit -m "feat(premium): скрипт предпросмотра экранов одному адресату"
```

---

### Task 6: Выкатка этапа 1 (операции, каждое — с «да» владельца)

**Files:** нет изменений кода. Команды выполняет агент только после явного «да» владельца на каждый пункт; иначе — передать владельцу.

- [ ] **Step 1: Полный прогон перед мержем**

Run: `uv run pytest -q && uv run ruff check app scripts tests`
Expected: всё зелёное.

- [ ] **Step 2: Предпросмотр на msk3 (с «да»)**

```bash
uv run python -c "print('ok')"  # убедиться, что окружение живо
rsync -a --delete app scripts root@msk3.zanity.net:/root/preview-premium/
~/.claude/skills/zanity-ops/bin/botdb --bare "select telegram_id from users where username = 'haku4130'"
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose run --rm --no-deps -e PREMIUM_THEME_ENABLED=true -v /root/preview-premium/app:/app/app:ro -v /root/preview-premium/scripts:/app/scripts:ro bot python -m scripts.preview_screens --to <telegram_id из запроса выше> --lang ru'
```

Expected: `Отправлено экранов: 3`. Владелец смотрит в Telegram: в каталоге все эмодзи анимированы (неживые — удалить из каталога отдельным коммитом), в меню иконки анимированы, «Купить» синяя, «Пробный» зелёная.

- [ ] **Step 3: Мерж в main (с «да»)** — по навыку `superpowers:finishing-a-development-branch`. Пуш в `main` = автодеплой.

  Один флаг `PREMIUM_THEME_ENABLED` включает оба этапа: в ветке с этапом 2 он же включает приветствие форка, экран «пробный включён» и раскладку меню. Для поэтапной выкатки в `main` сначала мержится ветка `premium-stage-1` (`449d924f`, только этап 1), а ветка этапа 2 — в Task 13.

- [ ] **Step 4: Прод (с «да»), сразу после деплоя**

```bash
ssh root@msk3.zanity.net 'set -e; cd /root/remnawave-bot; ts=$(date +%Y%m%d-%H%M%S); mkdir -p /root/backups; tar czf /root/backups/bot-locales-$ts.tgz locales; cp .env /root/backups/bot-env-$ts.bak; for f in locales/*.json; do echo "{}" > "$f"; done; chown 1000:1000 locales/*.json; grep -q "^PREMIUM_THEME_ENABLED=" .env && sed -i "s/^PREMIUM_THEME_ENABLED=.*/PREMIUM_THEME_ENABLED=true/" .env || echo "PREMIUM_THEME_ENABLED=true" >> .env; docker compose up -d --force-recreate bot'
```

- [ ] **Step 5: Проверка**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && sleep 20 && docker compose ps bot && docker compose logs --since 2m bot | grep -iE "error|Тема Premium|tg-emoji" | tail -20'
```

Expected: контейнер `healthy`, нет ошибок темы. Владелец открывает бота: кнопки с анимированными иконками и цветом. Откат: `PREMIUM_THEME_ENABLED=false` в `.env` + `docker compose up -d --force-recreate bot`.

---

# Этап 2. Путь новичка и главное меню

### Task 7: Онбординг-хелперы и автоязык

**Files:**
- Create: `app/utils/fork_onboarding.py` (на этой задаче — только `resolve_auto_language`, `is_trial_available`, `format_days`)
- Modify: `app/handlers/start.py` (блок выбора языка ~1755–1772)
- Test: `tests/utils/test_fork_onboarding.py`, `tests/handlers/test_fork_start_flow.py`

**Interfaces:**
- Consumes: `settings.LANGUAGE_AUTO_DETECT`, `settings.get_available_languages()`, `settings.is_trial_disabled_for_user(auth_type)`, `settings.is_trial_paid_activation_enabled()`, `User.is_trial_already_used()`.
- Produces: `fork_onboarding.resolve_auto_language(language_code: str | None, available: list[str]) -> str | None`;
  `fork_onboarding.is_trial_available(user) -> bool` (бесплатный пробный, который можно активировать сейчас);
  `fork_onboarding.format_days(texts, days: int) -> str` (по ключам `FORK_DAYS_ONE/FEW/MANY`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/utils/test_fork_onboarding.py
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.utils import fork_onboarding as fo


@pytest.mark.parametrize(
    ('code', 'expected'),
    [('ru', 'ru'), ('ru-RU', 'ru'), ('uk', 'ru'), ('be', 'ru'), ('kk', 'ru'), ('en', 'en'), ('de', 'en'), (None, None)],
)
def test_resolve_auto_language(code, expected):
    assert fo.resolve_auto_language(code, ['ru', 'en']) == expected


def test_resolve_auto_language_respects_available():
    assert fo.resolve_auto_language('de', ['ru']) is None
    assert fo.resolve_auto_language('uk', ['en']) is None


def _user(used=False, auth_type='telegram'):
    return SimpleNamespace(auth_type=auth_type, is_trial_already_used=MagicMock(return_value=used))


def test_trial_available(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    monkeypatch.setattr(settings, 'TRIAL_DISABLED_FOR', 'none')
    monkeypatch.setattr(type(settings), 'is_trial_disabled_for_user', lambda self, auth_type: False)
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: False)
    assert fo.is_trial_available(_user()) is True
    assert fo.is_trial_available(_user(used=True)) is False
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: True)
    assert fo.is_trial_available(_user()) is False


def test_trial_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 0)
    assert fo.is_trial_available(_user()) is False


def test_trial_check_error_means_unavailable(monkeypatch):
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    monkeypatch.setattr(settings, 'TRIAL_DISABLED_FOR', 'none')
    monkeypatch.setattr(type(settings), 'is_trial_disabled_for_user', lambda self, auth_type: False)
    monkeypatch.setattr(type(settings), 'is_trial_paid_activation_enabled', lambda self: False)
    broken = SimpleNamespace(auth_type='telegram', is_trial_already_used=MagicMock(side_effect=RuntimeError))
    assert fo.is_trial_available(broken) is False


@pytest.mark.parametrize(
    ('days', 'ru', 'en'),
    [
        (1, '1 день', '1 day'),
        (3, '3 дня', '3 days'),
        (5, '5 дней', '5 days'),
        (11, '11 дней', '11 days'),
        (21, '21 день', '21 days'),
        (22, '22 дня', '22 days'),
    ],
)
def test_format_days(days, ru, en):
    ru_texts = SimpleNamespace(
        language='ru', FORK_DAYS_ONE='{n} день', FORK_DAYS_FEW='{n} дня', FORK_DAYS_MANY='{n} дней'
    )
    en_texts = SimpleNamespace(
        language='en', FORK_DAYS_ONE='{n} day', FORK_DAYS_FEW='{n} days', FORK_DAYS_MANY='{n} days'
    )
    assert fo.format_days(ru_texts, days) == ru
    assert fo.format_days(en_texts, days) == en
```

```python
# tests/handlers/test_fork_start_flow.py
"""Путь новичка форка в start.py: автоязык, ?start=rules, приветствие."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message
from aiogram.types import User as TgUser

from app.config import settings
from app.handlers import start


def _state(user_id=7):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))


def _message(text='/start', language_code='en', user_id=7):
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = TgUser(id=user_id, is_bot=False, first_name='Тест', language_code=language_code)
    msg.chat = Chat(id=user_id, type='private')
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_auto_language_skips_prompt_and_stores_detected(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', True)
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en')
    prompt = AsyncMock()
    monkeypatch.setattr(start, '_prompt_language_selection', prompt)
    cont = AsyncMock()
    monkeypatch.setattr(start, '_continue_registration_after_language', cont)

    state = _state()
    await start._resolve_new_user_language(_message(language_code='uk'), state)

    prompt.assert_not_awaited()
    assert (await state.get_data())['language'] == 'ru'


@pytest.mark.asyncio
async def test_without_auto_language_prompt_is_shown(monkeypatch):
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', False)
    monkeypatch.setattr(settings, 'LANGUAGE_SELECTION_ENABLED', True)
    prompt = AsyncMock()
    monkeypatch.setattr(start, '_prompt_language_selection', prompt)
    state = _state()
    proceed = await start._resolve_new_user_language(_message(), state)
    assert proceed is False
    prompt.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py -q`
Expected: FAIL — нет модуля `app.utils.fork_onboarding` и функции `start._resolve_new_user_language`.

- [ ] **Step 3: Implement**

`app/utils/fork_onboarding.py`:

```python
"""Путь новичка форка: автоязык, доступность пробного, экраны приветствия и пробного.

Экраны используются только при PREMIUM_THEME_ENABLED: их тексты — ключи FORK_*
из app/localization/fork/ (читаются атрибутом, см. test_locale_integrity).
"""

from __future__ import annotations

import structlog

from app.config import settings


logger = structlog.get_logger(__name__)

# Telegram отдаёт язык интерфейса клиента. Русскоязычной считаем и аудиторию с
# украинским, белорусским и казахским интерфейсом — ua-локаль форк не ведёт.
_RUSSIAN_SPEAKING = frozenset({'ru', 'uk', 'be', 'kk'})


def resolve_auto_language(language_code: str | None, available: list[str]) -> str | None:
    if not language_code:
        return None
    base = language_code.split('-')[0].lower()
    wanted = 'ru' if base in _RUSSIAN_SPEAKING else 'en'
    normalized = {code.lower() for code in available}
    return wanted if wanted in normalized else None


def is_trial_available(user) -> bool:
    """Бесплатный пробный, который пользователь может включить прямо сейчас."""
    if settings.TRIAL_DURATION_DAYS <= 0 or settings.TRIAL_DISABLED_FOR == 'all':
        return False
    if settings.is_trial_paid_activation_enabled():
        return False
    try:
        if settings.is_trial_disabled_for_user(getattr(user, 'auth_type', 'telegram')):
            return False
        return not user.is_trial_already_used()
    except Exception as error:
        logger.warning('Не удалось проверить доступность пробного', error=str(error))
        return False


def format_days(texts, days: int) -> str:
    """«3 дня» / «5 дней» / «3 days» по ключам FORK_DAYS_*."""
    if getattr(texts, 'language', 'ru') == 'ru':
        last_two, last = days % 100, days % 10
        if last == 1 and last_two != 11:
            template = texts.FORK_DAYS_ONE
        elif 2 <= last <= 4 and not 12 <= last_two <= 14:
            template = texts.FORK_DAYS_FEW
        else:
            template = texts.FORK_DAYS_MANY
    else:
        template = texts.FORK_DAYS_ONE if days == 1 else texts.FORK_DAYS_MANY
    return template.format(n=days)
```

`app/handlers/start.py` — вынести блок выбора языка (~1755–1772) в функцию и вызвать её на старом месте. Новая функция рядом с `_prompt_language_selection`:

```python
async def _resolve_new_user_language(message: types.Message, state: FSMContext) -> bool:
    """Язык нового пользователя. False — показан экран выбора языка, регистрация ждёт ответа."""
    data = await state.get_data() or {}
    if data.get('language'):
        return True

    auto_language = None
    if settings.LANGUAGE_AUTO_DETECT:
        from app.utils.fork_onboarding import resolve_auto_language

        auto_language = resolve_auto_language(
            getattr(message.from_user, 'language_code', None), settings.get_available_languages()
        )

    if not auto_language and settings.is_language_selection_enabled():
        await _prompt_language_selection(message, state)
        return False

    default_language = (
        (settings.DEFAULT_LANGUAGE or DEFAULT_LANGUAGE)
        if isinstance(settings.DEFAULT_LANGUAGE, str)
        else DEFAULT_LANGUAGE
    )
    data['language'] = auto_language or default_language.split('-')[0].lower()
    await state.set_data(data)
    logger.info('🌐 LANGUAGE: язык нового пользователя', language=data['language'], auto=bool(auto_language))
    return True
```

На старом месте (строки `data = await state.get_data() or {}` … `logger.info('🌐 LANGUAGE: выбор языка отключен…')`) оставить:

```python
    if not await _resolve_new_user_language(message, state):
        return
    data = await state.get_data() or {}
```

(последняя строка нужна, если код ниже по функции читает `data` — проверить и оставить, только если читает).

Тексты `FORK_DAYS_*` — в `app/localization/fork/ru.json`:

```json
{
  "FORK_DAYS_ONE": "{n} день",
  "FORK_DAYS_FEW": "{n} дня",
  "FORK_DAYS_MANY": "{n} дней"
}
```

и `en.json`:

```json
{
  "FORK_DAYS_ONE": "{n} day",
  "FORK_DAYS_FEW": "{n} days",
  "FORK_DAYS_MANY": "{n} days"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py tests/localization tests/handlers -q`
Expected: PASS (включая старые тесты `tests/handlers/test_gift_deeplink_activation.py`, `test_start_invite_only.py`).

- [ ] **Step 5: Structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/utils/fork_onboarding.py app/handlers/start.py tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py
uv run ruff format --check app/utils/fork_onboarding.py tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py
uv run pytest tests/test_structure_reference_is_current.py -q
git add app/utils/fork_onboarding.py app/handlers/start.py app/localization/fork tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py docs/project_structure_reference.md
git commit -m "feat(premium): язык нового пользователя из настроек Telegram (LANGUAGE_AUTO_DETECT)"
```

---

### Task 8: Правила по ссылке `?start=rules`

**Files:**
- Modify: `app/utils/fork_onboarding.py` (+ `rules_url()`, `send_rules_screen()`)
- Modify: `app/handlers/start.py` (`cmd_start`, сразу после блока `if start_parameter == 'contests':`, ~строка 1380)
- Test: `tests/handlers/test_fork_start_flow.py`

**Interfaces:**
- Consumes: `get_current_rules_content(db, language) -> str` (`app/database/crud/rules.py:68`), `get_rules(language) -> str` (`app/localization/texts.py:293`), `stored_html_to_telegram_pages(raw, max_length=3500) -> list[str]` (`app/utils/telegram_html.py:160`), `settings.get_bot_username()`.
- Produces: `fork_onboarding.rules_url() -> str` (`https://t.me/<bot>?start=rules`, пустая строка без username);
  `async def send_rules_screen(message, db, language: str, *, with_menu_button: bool) -> None`.

- [ ] **Step 1: Write the failing tests** (дописать в `tests/handlers/test_fork_start_flow.py`)

```python
@pytest.mark.asyncio
async def test_rules_deeplink_for_new_user_shows_rules_without_registration(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'LANGUAGE_AUTO_DETECT', True)
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en')
    monkeypatch.setattr(start, 'get_user_by_telegram_id', AsyncMock(return_value=None))
    sent = AsyncMock()
    monkeypatch.setattr(fork_onboarding, 'send_rules_screen', sent)
    register = AsyncMock()
    monkeypatch.setattr(start, '_resolve_new_user_language', register)

    await start.cmd_start(_message('/start rules', language_code='ru'), _state(), AsyncMock(), db_user=None)

    sent.assert_awaited_once()
    assert sent.await_args.kwargs['with_menu_button'] is False
    assert sent.await_args.args[2] == 'ru'
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_rules_screen_paginates_and_adds_menu_button(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(
        fork_onboarding, 'get_current_rules_content', AsyncMock(return_value='<p>' + 'а' * 5000 + '</p>')
    )
    msg = _message()
    await fork_onboarding.send_rules_screen(msg, AsyncMock(), 'ru', with_menu_button=True)
    assert msg.answer.await_count >= 2
    last_kwargs = msg.answer.await_args_list[-1].kwargs
    assert last_kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'back_to_menu'
    assert 'reply_markup' not in msg.answer.await_args_list[0].kwargs


def test_rules_url(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'BOT_USERNAME', '@zanity_vpn_bot')
    assert fork_onboarding.rules_url() == 'https://t.me/zanity_vpn_bot?start=rules'
    monkeypatch.setattr(settings, 'BOT_USERNAME', None)
    assert fork_onboarding.rules_url() == ''
```

Перед написанием теста проверить, какие ещё вызовы делает `cmd_start` до блока `contests` (гейт `_evaluate_telegram_registration_access`, Redis-payload) — замокать их в тесте через `monkeypatch.setattr(start, …)` так же, как в `tests/handlers/test_gift_deeplink_activation.py`, чтобы тест доходил до ветки `rules`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/handlers/test_fork_start_flow.py -q`
Expected: FAIL — нет `send_rules_screen` / `rules_url`.

- [ ] **Step 3: Implement**

В `app/utils/fork_onboarding.py`:

```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.crud.rules import get_current_rules_content
from app.localization.texts import get_rules, get_texts
from app.utils.telegram_html import stored_html_to_telegram_pages


def rules_url() -> str:
    username = settings.get_bot_username()
    return f'https://t.me/{username}?start=rules' if username else ''


async def send_rules_screen(message, db, language: str, *, with_menu_button: bool) -> None:
    """Правила сервиса по ссылке из приветствия. Новичку кнопки не нужно — приветствие
    с кнопкой пробного остаётся сообщением выше; зарегистрированному — «В меню»."""
    texts = get_texts(language)
    rules_text = await get_current_rules_content(db, language) or await get_rules(language)
    pages = stored_html_to_telegram_pages(rules_text, max_length=3500) or ['']
    header = texts.t('RULES_HEADER', '📋 <b>Правила сервиса</b>')
    for index, page in enumerate(pages):
        text = f'{header}\n\n{page}' if index == 0 else page
        if index == len(pages) - 1 and with_menu_button:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=texts.BACK_TO_MAIN_MENU_BUTTON, callback_data='back_to_menu')]
                ]
            )
            await message.answer(text, reply_markup=keyboard)
        else:
            await message.answer(text)
```

(Если импорт `app.database.crud.rules` на уровне модуля даёт цикл импорта — перенести импорты внутрь функции.)

В `app/handlers/start.py`, сразу после блока `if start_parameter == 'contests': … start_parameter = None`:

```python
    # Ссылка «правила сервиса» из приветствия форка (PREMIUM_THEME_ENABLED).
    if start_parameter == 'rules' and settings.PREMIUM_THEME_ENABLED:
        from app.utils import fork_onboarding

        user = db_user or await get_user_by_telegram_id(db, message.from_user.id)
        registered = bool(user and user.status != UserStatus.DELETED.value)
        language = (
            (user.language if registered else None)
            or fork_onboarding.resolve_auto_language(message.from_user.language_code, settings.get_available_languages())
            or settings.DEFAULT_LANGUAGE
        )
        await fork_onboarding.send_rules_screen(message, db, language, with_menu_button=registered)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/handlers tests/utils/test_fork_onboarding.py -q`
Expected: PASS

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check app/utils/fork_onboarding.py app/handlers/start.py tests/handlers/test_fork_start_flow.py
git add app/utils/fork_onboarding.py app/handlers/start.py tests/handlers/test_fork_start_flow.py
git commit -m "feat(premium): правила сервиса по ссылке ?start=rules"
```

---

### Task 9: Приветствие форка

**Files:**
- Modify: `app/utils/fork_onboarding.py` (+ `build_welcome_screen()`)
- Modify: `app/handlers/start.py` (`complete_registration_from_callback` ~2517–2541 и `complete_registration` ~2906–2934)
- Modify: `app/localization/fork/ru.json`, `en.json`
- Test: `tests/utils/test_fork_onboarding.py`, `tests/handlers/test_fork_start_flow.py`

**Interfaces:**
- Consumes: `is_trial_available`, `format_days`, `rules_url` (Tasks 7–8).
- Produces: `fork_onboarding.build_welcome_screen(user, tg_user) -> tuple[str, InlineKeyboardMarkup]`.
  Варианты: пробный доступен → `FORK_WELCOME_TRIAL` + [`FORK_BUTTON_TRY_FREE` → `trial_activate`, style `success`] [`FORK_BUTTON_TARIFFS` → `menu_buy`, style `primary`];
  активная подписка → `FORK_WELCOME_HAS_SUBSCRIPTION` + [`FORK_BUTTON_CONNECT` → `subscription_connect`, `success`];
  иначе → `FORK_WELCOME_NO_TRIAL` + [`FORK_BUTTON_CHOOSE_TARIFF` → `menu_buy`, `success`].

- [ ] **Step 1: Write the failing tests**

В `tests/utils/test_fork_onboarding.py`:

```python
@pytest.fixture
def fork_texts(monkeypatch):
    from app.localization import loader

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    monkeypatch.setattr(settings, 'TRIAL_DURATION_DAYS', 3)
    loader.clear_locale_cache()
    yield
    loader.clear_locale_cache()


def _tg(first_name='Андрей <b>'):
    return SimpleNamespace(first_name=first_name, username='andrey')


def _reg_user(language='ru', active=False):
    sub = SimpleNamespace(is_active=active)
    return SimpleNamespace(
        language=language,
        subscriptions=[sub] if active else [],
        auth_type='telegram',
        is_trial_already_used=MagicMock(return_value=active),
    )


def test_welcome_with_trial(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: True)
    text, kb = fo.build_welcome_screen(_reg_user(), _tg())
    assert 'Андрей &lt;b&gt;' in text  # имя экранировано
    assert '3 дня' in text
    assert 'https://t.me/zanity_vpn_bot?start=rules' in text
    assert '<tg-emoji' in text
    first = kb.inline_keyboard[0][0]
    assert first.callback_data == 'trial_activate' and first.style == 'success'
    assert kb.inline_keyboard[1][0].callback_data == 'menu_buy' and kb.inline_keyboard[1][0].style == 'primary'


def test_welcome_with_active_subscription(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: False)
    text, kb = fo.build_welcome_screen(_reg_user(active=True), _tg('Андрей'))
    assert kb.inline_keyboard[0][0].callback_data == 'subscription_connect'
    assert len(kb.inline_keyboard) == 1


def test_welcome_without_trial(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: False)
    text, kb = fo.build_welcome_screen(_reg_user(), _tg('Андрей'))
    assert kb.inline_keyboard[0][0].callback_data == 'menu_buy'
    assert kb.inline_keyboard[0][0].style == 'success'


def test_welcome_english(fork_texts, monkeypatch):
    monkeypatch.setattr(fo, 'is_trial_available', lambda user: True)
    text, _ = fo.build_welcome_screen(_reg_user(language='en'), _tg('Ann'))
    assert '3 days' in text and 'Hi, Ann' in text
```

В `tests/handlers/test_fork_start_flow.py` — тест, что при включённой теме `complete_registration` шлёт приветствие форка вместо текста из `welcome_texts`. Шаблон теста — `tests/handlers/test_gift_deeplink_activation.py` (in-memory SQLite через `tests.fixtures.sqlite_memory`); если подготовка полного `complete_registration` окажется тяжёлой, вынести выбор текста и клавиатуры в функцию `start._welcome_offer(db, user, tg_user) -> tuple[str | None, InlineKeyboardMarkup | None]` и тестировать её:

```python
@pytest.mark.asyncio
async def test_welcome_offer_prefers_fork_screen(monkeypatch):
    from app.utils import fork_onboarding

    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(fork_onboarding, 'build_welcome_screen', lambda user, tg: ('ФОРК', 'KB'))
    db_welcome = AsyncMock(return_value='ИЗ БД')
    monkeypatch.setattr('app.database.crud.welcome_text.get_welcome_text_for_user', db_welcome)
    text, keyboard = await start._welcome_offer(AsyncMock(), SimpleNamespace(language='ru'), SimpleNamespace())
    assert (text, keyboard) == ('ФОРК', 'KB')
    db_welcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_welcome_offer_uses_db_text_when_theme_off(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    monkeypatch.setattr('app.database.crud.welcome_text.get_welcome_text_for_user', AsyncMock(return_value='ИЗ БД'))
    text, keyboard = await start._welcome_offer(AsyncMock(), SimpleNamespace(language='ru'), SimpleNamespace())
    assert text == 'ИЗ БД' and keyboard is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py -q`
Expected: FAIL — нет `build_welcome_screen` / `_welcome_offer` и ключей `FORK_WELCOME_*`.

- [ ] **Step 3: Implement**

Тексты — добавить в `app/localization/fork/ru.json` (к уже существующим `FORK_DAYS_*`):

```json
  "FORK_WELCOME_TRIAL": "<b>Привет, {user_name}!</b> {e:wave}\n\nЭто ZanityVPN — быстрый VPN для телефона, компьютера и ТВ. YouTube без рекламы, звонки в WhatsApp и Telegram.\n\n{e:gift} Дарим <b>{trial_days} бесплатно</b> — без оплаты и без карты.\n\n<i>Нажимая кнопку, вы принимаете <a href=\"{rules_url}\">правила сервиса</a>.</i>",
  "FORK_WELCOME_NO_TRIAL": "<b>Привет, {user_name}!</b> {e:wave}\n\nЭто ZanityVPN — быстрый VPN для телефона, компьютера и ТВ. YouTube без рекламы, звонки в WhatsApp и Telegram.\n\nВыберите тариф — подключение займёт пару минут.\n\n<i>Пользуясь сервисом, вы принимаете <a href=\"{rules_url}\">правила сервиса</a>.</i>",
  "FORK_WELCOME_HAS_SUBSCRIPTION": "<b>Привет, {user_name}!</b> {e:wave}\n\nПодписка уже активна. Нажмите «Подключиться» — откроется инструкция для вашего устройства.",
  "FORK_BUTTON_TRY_FREE": "{e:gift} Попробовать бесплатно",
  "FORK_BUTTON_TARIFFS": "{e:gem} Тарифы",
  "FORK_BUTTON_CHOOSE_TARIFF": "{e:gem} Выбрать тариф",
  "FORK_BUTTON_CONNECT": "{e:rocket} Подключиться"
```

и в `en.json`:

```json
  "FORK_WELCOME_TRIAL": "<b>Hi, {user_name}!</b> {e:wave}\n\nThis is ZanityVPN — a fast VPN for your phone, computer and TV. YouTube without ads, WhatsApp and Telegram calls.\n\n{e:gift} Get <b>{trial_days} free</b> — no payment, no card.\n\n<i>By tapping the button you accept the <a href=\"{rules_url}\">terms of service</a>.</i>",
  "FORK_WELCOME_NO_TRIAL": "<b>Hi, {user_name}!</b> {e:wave}\n\nThis is ZanityVPN — a fast VPN for your phone, computer and TV. YouTube without ads, WhatsApp and Telegram calls.\n\nChoose a plan — setup takes a couple of minutes.\n\n<i>By using the service you accept the <a href=\"{rules_url}\">terms of service</a>.</i>",
  "FORK_WELCOME_HAS_SUBSCRIPTION": "<b>Hi, {user_name}!</b> {e:wave}\n\nYour subscription is active. Tap “Connect” to see instructions for your device.",
  "FORK_BUTTON_TRY_FREE": "{e:gift} Try for free",
  "FORK_BUTTON_TARIFFS": "{e:gem} Plans",
  "FORK_BUTTON_CHOOSE_TARIFF": "{e:gem} Choose a plan",
  "FORK_BUTTON_CONNECT": "{e:rocket} Connect"
```

Если `rules_url()` пустой (нет username бота) — ссылка не должна стать `href=""`: в `build_welcome_screen` в этом случае подставлять `https://t.me/` не надо; вместо этого убрать абзац со ссылкой целиком (последний абзац, отделённый `\n\n`), см. код ниже.

В `app/utils/fork_onboarding.py`:

```python
import html


def _user_name(tg_user) -> str:
    name = getattr(tg_user, 'first_name', None) or getattr(tg_user, 'username', None) or ''
    return html.escape(name)


def _button(text: str, callback_data: str, style: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=callback_data, style=style)]


def build_welcome_screen(user, tg_user) -> tuple[str, InlineKeyboardMarkup]:
    texts = get_texts(getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE)
    user_name = _user_name(tg_user)
    url = rules_url()
    has_active = any(getattr(sub, 'is_active', False) for sub in (getattr(user, 'subscriptions', None) or []))

    if has_active:
        text = texts.FORK_WELCOME_HAS_SUBSCRIPTION.format(user_name=user_name)
        rows = [_button(texts.FORK_BUTTON_CONNECT, 'subscription_connect', 'success')]
    elif is_trial_available(user):
        text = texts.FORK_WELCOME_TRIAL.format(
            user_name=user_name, trial_days=format_days(texts, settings.TRIAL_DURATION_DAYS), rules_url=url
        )
        rows = [
            _button(texts.FORK_BUTTON_TRY_FREE, 'trial_activate', 'success'),
            _button(texts.FORK_BUTTON_TARIFFS, 'menu_buy', 'primary'),
        ]
    else:
        text = texts.FORK_WELCOME_NO_TRIAL.format(user_name=user_name, rules_url=url)
        rows = [_button(texts.FORK_BUTTON_CHOOSE_TARIFF, 'menu_buy', 'success')]

    if not url and 'href=""' in text:
        text = text.rsplit('\n\n', 1)[0]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
```

В `app/handlers/start.py` — новая функция рядом с `complete_registration`:

```python
async def _welcome_offer(db: AsyncSession, user, tg_user) -> tuple[str | None, types.InlineKeyboardMarkup | None]:
    """Текст и клавиатура приветствия. С темой форка — экран форка (тексты в коде),
    иначе — текст из таблицы welcome_texts с клавиатурой по умолчанию (None)."""
    if settings.PREMIUM_THEME_ENABLED:
        from app.utils.fork_onboarding import build_welcome_screen

        return build_welcome_screen(user, tg_user)

    from app.database.crud.welcome_text import get_welcome_text_for_user

    return await get_welcome_text_for_user(db, tg_user), None
```

В `complete_registration_from_callback` заменить

```python
    from app.database.crud.welcome_text import get_welcome_text_for_user

    offer_text = await get_welcome_text_for_user(db, callback.from_user)
```

на

```python
    offer_text, fork_keyboard = await _welcome_offer(db, user, callback.from_user)
```

и в обоих `callback.message.answer(offer_text, reply_markup=get_post_registration_keyboard(user.language), …)` заменить `reply_markup=` на `reply_markup=fork_keyboard or get_post_registration_keyboard(user.language)`.

В `complete_registration` аналогично заменить получение `offer_text` на `offer_text, fork_keyboard = await _welcome_offer(db, user, message.from_user)`, а выбор клавиатуры —

```python
            if fork_keyboard is not None:
                keyboard = fork_keyboard
            elif user_has_subscription:
                keyboard = get_back_keyboard(user.language, callback_data='back_to_menu')
            else:
                keyboard = get_post_registration_keyboard(user.language)
```

Тест-пины: после правки прогнать `uv run pytest tests/test_rich_menu_pins.py tests/test_start_menu_text_consistency.py -q` — количество вызовов `try_answer_rich_main_menu(` в `start.py` не меняется.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/utils/test_fork_onboarding.py tests/handlers tests/localization tests/test_rich_menu_pins.py tests/test_locale_integrity.py -q`
Expected: PASS

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check app/utils/fork_onboarding.py app/handlers/start.py tests && uv run ruff format --check app/utils/fork_onboarding.py tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py
git add app/utils/fork_onboarding.py app/handlers/start.py app/localization/fork tests/utils/test_fork_onboarding.py tests/handlers/test_fork_start_flow.py
git commit -m "feat(premium): приветствие нового пользователя — пробный одной кнопкой, правила строкой"
```

---

### Task 10: Экран «пробный включён»

**Files:**
- Modify: `app/utils/fork_onboarding.py` (+ `build_trial_ready_screen()`)
- Modify: `app/handlers/subscription/purchase.py` (`activate_trial`, ветка `if remnawave_user and subscription_link:` ~строка 1126; импорт)
- Modify: `app/localization/fork/ru.json`, `en.json`
- Test: `tests/utils/test_fork_onboarding.py`, `tests/test_trial_activation_paid.py` (проверить, что не сломан)

**Interfaces:**
- Consumes: `_build_trial_success_keyboard(texts, subscription_link: str, connect_mode: str) -> InlineKeyboardMarkup` (`purchase.py:3583`; первая строка — кнопка подключения, последняя — «В главное меню»), `format_local_datetime` (импортирован в `purchase.py`).
- Produces: `fork_onboarding.build_trial_ready_screen(texts, end_date_text: str, connect_keyboard: InlineKeyboardMarkup) -> tuple[str, InlineKeyboardMarkup]` — текст `FORK_TRIAL_READY`, клавиатура: строки подключения из `connect_keyboard` (у первой кнопки `style='success'`), затем `FORK_BUTTON_HELP` → `menu_support`, затем последняя строка `connect_keyboard` («В меню»).

- [ ] **Step 1: Write the failing test**

```python
def test_trial_ready_screen(fork_texts):
    from app.localization.texts import get_texts

    texts = get_texts('ru')
    connect = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='🔗 Подключиться', web_app=WebAppInfo(url='https://sub.example/x'))],
            [InlineKeyboardButton(text='⬅️ В главное меню', callback_data='back_to_menu')],
        ]
    )
    text, kb = fo.build_trial_ready_screen(texts, '19.10.2026', connect)
    assert 'Доступ до 19.10.2026' in text and 'Шаг 1' in text and 'Шаг 2' in text
    assert kb.inline_keyboard[0][0].web_app.url == 'https://sub.example/x'
    assert kb.inline_keyboard[0][0].style == 'success'
    assert kb.inline_keyboard[1][0].callback_data == 'menu_support'
    assert kb.inline_keyboard[-1][0].callback_data == 'back_to_menu'
    assert connect.inline_keyboard[0][0].style is None  # исходная клавиатура не мутирована
```

(импорты в начале файла теста: `from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/utils/test_fork_onboarding.py::test_trial_ready_screen -q`
Expected: FAIL — нет `build_trial_ready_screen`.

- [ ] **Step 3: Implement**

Тексты — `ru.json`:

```json
  "FORK_TRIAL_READY": "{e:party} <b>Готово! Доступ до {end_date}</b>\n\n<b>Шаг 1.</b> Нажмите «Подключиться» — откроется инструкция для вашего устройства.\n<b>Шаг 2.</b> Установите приложение и нажмите в нём «Добавить подписку».\n\nНе получается? Напишите нам — поможем за пару минут.",
  "FORK_BUTTON_HELP": "{e:speech} Помощь"
```

`en.json`:

```json
  "FORK_TRIAL_READY": "{e:party} <b>Done! Access until {end_date}</b>\n\n<b>Step 1.</b> Tap “Connect” to see instructions for your device.\n<b>Step 2.</b> Install the app and tap “Add subscription” in it.\n\nStuck? Message us — we’ll help in a couple of minutes.",
  "FORK_BUTTON_HELP": "{e:speech} Get help"
```

`app/utils/fork_onboarding.py`:

```python
def build_trial_ready_screen(
    texts, end_date_text: str, connect_keyboard: InlineKeyboardMarkup
) -> tuple[str, InlineKeyboardMarkup]:
    rows = [list(row) for row in connect_keyboard.inline_keyboard]
    connect_rows, menu_row = rows[:-1], rows[-1]
    if connect_rows and connect_rows[0]:
        first = connect_rows[0][0]
        connect_rows[0] = [first.model_copy(update={'style': 'success'}), *connect_rows[0][1:]]
    help_row = [InlineKeyboardButton(text=texts.FORK_BUTTON_HELP, callback_data='menu_support')]
    text = texts.FORK_TRIAL_READY.format(end_date=end_date_text)
    return text, InlineKeyboardMarkup(inline_keyboard=[*connect_rows, help_row, menu_row])
```

`app/handlers/subscription/purchase.py`, в `activate_trial`: перед строкой `await callback.message.edit_text(trial_success_text, reply_markup=connect_keyboard, parse_mode='HTML')` внутри ветки `if remnawave_user and subscription_link:` вставить:

```python
if settings.PREMIUM_THEME_ENABLED and len(connect_keyboard.inline_keyboard) >= 2:
    from app.utils.fork_onboarding import build_trial_ready_screen

    end_date_text = format_local_datetime(subscription.end_date, fmt='%d.%m.%Y')
    trial_success_text, connect_keyboard = build_trial_ready_screen(texts, end_date_text, connect_keyboard)
    trial_success_text += payment_note
```

(Проверить сигнатуру `format_local_datetime` в `purchase.py` — параметр `fmt`; если имя другое — использовать его. Если `connect_keyboard` получился `get_back_keyboard` с одной строкой — экран форка не применяется, условие `>= 2` это и проверяет.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/utils/test_fork_onboarding.py tests/test_trial_activation_paid.py tests/test_trial_disabled_menu_gating.py tests/localization -q`
Expected: PASS

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check app/utils/fork_onboarding.py app/handlers/subscription/purchase.py tests/utils/test_fork_onboarding.py
git add app/utils/fork_onboarding.py app/handlers/subscription/purchase.py app/localization/fork tests/utils/test_fork_onboarding.py
git commit -m "feat(premium): экран «пробный включён» — два шага и кнопка помощи"
```

---

### Task 11: Главное меню — раскладка форка и главная кнопка по состоянию

**Files:**
- Create: `app/services/menu_layout/fork_layout.py`
- Modify: `app/services/menu_layout/context.py` (поле `primary_action`)
- Modify: `app/services/menu_layout/service.py` (`get_default_config`; условие `primary_action` в `_evaluate_conditions`; `style` в `_build_button`)
- Modify: `app/keyboards/inline.py` (`get_main_menu_keyboard_async`, построение `MenuContext` ~строка 116)
- Test: `tests/services/test_fork_menu_layout.py`

**Interfaces:**
- Consumes: `fork_onboarding.is_trial_available(user)` (Task 7), `DEFAULT_MENU_CONFIG` (`constants.py`).
- Produces: `fork_layout.EXPIRING_DAYS = 3`; `fork_layout.resolve_primary_action(user, subscription) -> str` (`'trial' | 'connect' | 'extend' | 'buy'`);
  `fork_layout.build_fork_menu_config() -> dict` (новый объект при каждом вызове);
  `MenuContext.primary_action: str | None = None`; условие строки/кнопки `{'primary_action': str | list[str]}`; ключ кнопки `'style'` (передаётся в `InlineKeyboardButton(style=…)`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_fork_menu_layout.py
"""Главное меню форка: одна зелёная кнопка по состоянию пользователя."""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.menu_layout import fork_layout
from app.services.menu_layout.context import MenuContext
from app.services.menu_layout.service import MenuLayoutService


def _sub(status='active', is_trial=False, days_left=20, pending=False):
    return SimpleNamespace(actual_status=status, is_trial=is_trial, days_left=days_left, is_pending_trial=pending)


@pytest.mark.parametrize(
    ('subscription', 'trial_ok', 'expected'),
    [
        (None, True, 'trial'),
        (None, False, 'buy'),
        (_sub(pending=True), True, 'trial'),
        (_sub('active', days_left=20), False, 'connect'),
        (_sub('trial', is_trial=True, days_left=1), False, 'connect'),
        (_sub('limited', days_left=20), False, 'connect'),
        (_sub('active', days_left=3), False, 'extend'),
        (_sub('expired'), False, 'extend'),
        (_sub('disabled'), False, 'extend'),
        (_sub('expired', is_trial=True), False, 'buy'),
        (_sub('expired', is_trial=True), True, 'trial'),
    ],
)
def test_resolve_primary_action(monkeypatch, subscription, trial_ok, expected):
    monkeypatch.setattr(fork_layout, 'is_trial_available', lambda user: trial_ok)
    assert fork_layout.resolve_primary_action(SimpleNamespace(), subscription) == expected


def _visible(context: MenuContext) -> list[tuple[str, str | None]]:
    config = fork_layout.build_fork_menu_config()
    result = []
    for row in config['rows']:
        if not MenuLayoutService._evaluate_conditions(row.get('conditions'), context):
            continue
        for button_id in row['buttons']:
            cfg = config['buttons'][button_id]
            if not MenuLayoutService._check_visibility(cfg.get('visibility', 'all'), context):
                continue
            if not MenuLayoutService._evaluate_conditions(cfg.get('conditions'), context):
                continue
            result.append((button_id, cfg.get('style')))
    return result


def _greens(context):
    return [button_id for button_id, style in _visible(context) if style == 'success']


def test_exactly_one_green_button_in_every_state(monkeypatch):
    monkeypatch.setattr(settings, 'SUPPORT_MENU_ENABLED', True)
    states = {
        'trial': MenuContext(primary_action='trial'),
        'buy': MenuContext(primary_action='buy'),
        'connect': MenuContext(primary_action='connect', has_active_subscription=True, subscription_is_active=True),
        'extend_active': MenuContext(
            primary_action='extend', has_active_subscription=True, subscription_is_active=True
        ),
        'extend_expired': MenuContext(primary_action='extend'),
    }
    expected = {
        'trial': ['trial'],
        'buy': ['buy_primary'],
        'connect': ['connect'],
        'extend_active': ['extend'],
        'extend_expired': ['extend'],
    }
    for name, context in states.items():
        assert _greens(context) == expected[name], name


def test_extend_while_active_keeps_blue_connect():
    context = MenuContext(primary_action='extend', has_active_subscription=True, subscription_is_active=True)
    assert ('connect_secondary', 'primary') in _visible(context)


def test_tariffs_button_blue_next_to_trial():
    assert ('buy_subscription', 'primary') in _visible(MenuContext(primary_action='trial'))


def test_default_config_switches_with_theme(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    assert 'extend' in MenuLayoutService.get_default_config()['buttons']
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', False)
    assert 'extend' not in MenuLayoutService.get_default_config()['buttons']


def test_build_button_passes_style():
    button = MenuLayoutService._build_button(
        {'type': 'builtin', 'text': {'ru': 'Продлить'}, 'action': 'subscription_extend', 'style': 'success'},
        MenuContext(),
        SimpleNamespace(),
    )
    assert button.style == 'success'


def test_primary_action_condition_accepts_list():
    context = MenuContext(primary_action='trial')
    assert MenuLayoutService._evaluate_conditions({'primary_action': ['trial', 'extend']}, context)
    assert not MenuLayoutService._evaluate_conditions({'primary_action': 'buy'}, context)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/services/test_fork_menu_layout.py -q`
Expected: FAIL — нет модуля `fork_layout` и поля `primary_action`.

- [ ] **Step 3: Implement**

`app/services/menu_layout/context.py` — в конец полей `MenuContext`:

```python
    # Главное (зелёное) действие меню форка: trial | connect | extend | buy.
    primary_action: str | None = None
```

`app/services/menu_layout/service.py`:

1. `get_default_config`:

```python
    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        """Получить дефолтную конфигурацию."""
        if settings.PREMIUM_THEME_ENABLED:
            from app.services.menu_layout.fork_layout import build_fork_menu_config

            return build_fork_menu_config()
        return copy.deepcopy(DEFAULT_MENU_CONFIG)
```

(Проверить, что `settings` импортирован в `service.py` — используется в `_evaluate_conditions`, значит да.)

2. В `_evaluate_conditions`, перед финальным `return True`:

```python
        # primary_action — главное действие меню форка (см. fork_layout)
        expected_action = conditions.get('primary_action')
        if expected_action is not None:
            allowed = expected_action if isinstance(expected_action, (list, tuple, set)) else (expected_action,)
            if context.primary_action not in allowed:
                return False
```

3. В `_build_button`: после `custom_emoji_id = …` добавить `style = button_config.get('style') or None` и в **каждый** из пяти вызовов `InlineKeyboardButton(...)` в этой функции добавить аргумент `style=style`.

`app/services/menu_layout/fork_layout.py`:

```python
"""Раскладка главного меню форка: одна зелёная кнопка по состоянию пользователя.

Пробный доступен → «Попробовать бесплатно»; подписка активна → «Подключиться»;
платная истекает (≤ EXPIRING_DAYS) или истекла → «Продлить»; иначе → «Выбрать
тариф». Раскладка строится от DEFAULT_MENU_CONFIG апстрима, поэтому новые
кнопки апстрима (simple_subscription, contests…) остаются на месте.
"""

from __future__ import annotations

import copy
from typing import Any

from app.services.menu_layout.constants import DEFAULT_MENU_CONFIG
from app.utils.fork_onboarding import is_trial_available


EXPIRING_DAYS = 3
_ACTIVE_STATUSES = frozenset({'active', 'trial', 'limited'})
_ENDED_STATUSES = frozenset({'expired', 'disabled'})


def resolve_primary_action(user, subscription) -> str:
    if subscription is not None and not getattr(subscription, 'is_pending_trial', False):
        status = getattr(subscription, 'actual_status', None)
        is_trial = bool(getattr(subscription, 'is_trial', False))
        if status in _ACTIVE_STATUSES:
            if not is_trial and getattr(subscription, 'days_left', EXPIRING_DAYS + 1) <= EXPIRING_DAYS:
                return 'extend'
            return 'connect'
        if status in _ENDED_STATUSES and not is_trial:
            return 'extend'
    if user is not None and is_trial_available(user):
        return 'trial'
    return 'buy'


def _row(row_id: str, buttons: list[str], conditions: dict[str, Any] | None = None, max_per_row: int = 2):
    return {'id': row_id, 'buttons': buttons, 'conditions': conditions, 'max_per_row': max_per_row}


def build_fork_menu_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_MENU_CONFIG)
    buttons = config['buttons']

    buttons['trial'].update(
        text={'ru': '🎁 Попробовать бесплатно', 'en': '🎁 Try for free'},
        action='trial_activate',
        conditions={'primary_action': 'trial'},
        style='success',
    )
    buttons['connect'].update(
        text={'ru': '🚀 Подключиться', 'en': '🚀 Connect'},
        conditions={'primary_action': 'connect', 'has_active_subscription': True, 'subscription_is_active': True},
        style='success',
    )
    buttons['connect_secondary'] = {
        **copy.deepcopy(buttons['connect']),
        'conditions': {'primary_action': 'extend', 'has_active_subscription': True, 'subscription_is_active': True},
        'style': 'primary',
    }
    buttons['extend'] = {
        'type': 'builtin',
        'builtin_id': 'extend',
        'text': {'ru': '🔄 Продлить', 'en': '🔄 Renew'},
        'action': 'subscription_extend',
        'enabled': True,
        'visibility': 'all',
        'conditions': {'primary_action': 'extend'},
        'dynamic_text': False,
        'style': 'success',
    }
    buttons['buy_primary'] = {
        **copy.deepcopy(buttons['buy_subscription']),
        'builtin_id': 'buy_primary',
        'text': {'ru': '💎 Выбрать тариф', 'en': '💎 Choose a plan'},
        'conditions': {'primary_action': 'buy'},
        'style': 'success',
    }
    buttons['buy_subscription'].update(
        text={'ru': '💎 Тарифы', 'en': '💎 Plans'},
        conditions={'show_buy': True, 'primary_action': ['trial', 'extend']},
        style='primary',
    )
    buttons['subscription']['style'] = 'primary'
    buttons['balance']['style'] = 'primary'
    buttons['referrals']['text'] = {'ru': '👥 Друзья', 'en': '👥 Friends'}

    by_id = {row['id']: row for row in config['rows']}
    config['rows'] = [
        _row('fork_primary_row', ['trial', 'extend', 'buy_primary'], max_per_row=1),
        _row('connect_row', ['connect', 'connect_secondary'], max_per_row=1),
        by_id['happ_row'],
        _row('subscription_balance_row', ['subscription', 'balance']),
        _row('tariffs_row', ['buy_subscription'], max_per_row=1),
        _row('buy_traffic_row', ['buy_traffic'], {'has_active_subscription': True}, max_per_row=1),
        by_id['simple_subscription_row'],
        by_id['resume_row'],
        _row('referral_promo_row', ['referrals', 'promocode']),
        by_id['contests_row'],
        by_id['support_info_row'],
        by_id['language_row'],
        by_id['admin_row'],
        by_id['moderator_row'],
    ]
    return config
```

`subscription_extend` (`handle_extend_subscription`, `purchase.py:1641`) истекшую подписку не отвергает: проверяет только наличие подписки и триальность (триал без тарифа → предложение купить), поэтому кнопка «Продлить» годится и для истекшей платной подписки.

`app/keyboards/inline.py`, в вызове `MenuContext(...)` в `get_main_menu_keyboard_async` добавить последним аргументом:

```python
primary_action = (_fork_primary_action(user, subscription),)
```

и рядом с функцией (на уровне модуля):

```python
def _fork_primary_action(user, subscription) -> str | None:
    if not settings.PREMIUM_THEME_ENABLED:
        return None
    from app.services.menu_layout.fork_layout import resolve_primary_action

    return resolve_primary_action(user, subscription)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/services/test_fork_menu_layout.py tests/services tests/test_menu_subscription_status.py tests/test_trial_disabled_menu_gating.py -q`
Expected: PASS

- [ ] **Step 5: Structure reference, lint, commit**

```bash
make docs-structure
uv run ruff check app/services/menu_layout app/keyboards/inline.py tests/services/test_fork_menu_layout.py
uv run ruff format --check app/services/menu_layout/fork_layout.py tests/services/test_fork_menu_layout.py
uv run pytest tests/test_structure_reference_is_current.py -q
git add app/services/menu_layout app/keyboards/inline.py tests/services/test_fork_menu_layout.py docs/project_structure_reference.md
git commit -m "feat(premium): главное меню — одна зелёная кнопка по состоянию (пробный/подключиться/продлить/тариф)"
```

---

### Task 12: Rich-меню — шапка без засечек и тексты

**Files:**
- Modify: `app/utils/rich_menu.py` (`build_main_menu_rich_html`, строки 514–533; блок баланса ~540)
- Modify: `app/localization/fork/ru.json`, `en.json`
- Test: `tests/utils/test_rich_menu_fork.py`

**Interfaces:**
- Consumes: `settings.PREMIUM_THEME_ENABLED`, `settings.is_referral_program_enabled()`, `settings.REFERRAL_COMMISSION_PERCENT`.
- Produces: при теме — шапка `<p><b>{имя}</b></p>` без `<hr/>` после неё; заголовки разделов `<p><b>…</b></p>` вместо `<h6>`; абзац `FORK_MENU_REFERRAL_HINT` после баланса (если реферальная программа включена и процент > 0). Без темы — HTML как раньше (старые тесты `tests/utils/test_rich_menu.py` не меняются).

- [ ] **Step 1: Write the failing test**

Подготовка — помощники из `tests/utils/test_rich_menu.py` (`DummyTexts`, `_make_subscription`, `_make_user`, `_patch_content_sources`):

```python
# tests/utils/test_rich_menu_fork.py
"""Rich-меню с темой форка: без <h4>/<h6> (засечки на мобильном), реферальная подсказка."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.utils import rich_menu
from tests.utils.test_rich_menu import DummyTexts, _make_subscription, _make_user, _patch_content_sources


HINT = '<tg-emoji emoji-id="5372926953978341366">👥</tg-emoji> Пригласите друга — {percent}% с его оплат.'


class ForkTexts(DummyTexts):
    def get(self, key, default=None):
        return HINT if key == 'FORK_MENU_REFERRAL_HINT' else default


@pytest.fixture
def theme(monkeypatch):
    rich_menu._reset_rich_menu_availability()
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'MAIN_MENU_RICH_LOGO_URL', '', raising=False)
    monkeypatch.setattr(settings, 'WEBHOOK_URL', None, raising=False)
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 20)
    monkeypatch.setattr(type(settings), 'is_referral_program_enabled', lambda self: True)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)
    monkeypatch.setattr(type(settings), 'is_tariffs_mode', lambda self: False)
    _patch_content_sources(monkeypatch)
    yield
    rich_menu._reset_rich_menu_availability()


@pytest.mark.asyncio
async def test_rich_menu_has_no_serif_headings(theme):
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, ForkTexts(), AsyncMock())
    assert '<h4>' not in html_out and '<h6>' not in html_out
    assert html_out.startswith('<p><b>Егор &lt;script&gt;</b></p>')
    assert '20%' in html_out
    assert '<tg-emoji emoji-id="5372926953978341366">' in html_out


@pytest.mark.asyncio
async def test_no_referral_hint_when_commission_zero(theme, monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 0)
    user = _make_user(_make_subscription(datetime.now(UTC)))
    html_out = await rich_menu.build_main_menu_rich_html(user, ForkTexts(), AsyncMock())
    assert 'Пригласите друга' not in html_out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/utils/test_rich_menu_fork.py -q`
Expected: FAIL — в выводе есть `<h4>`.

- [ ] **Step 3: Implement**

`app/utils/rich_menu.py`, рядом с `_rich_text`:

```python
def _section_heading(inner_html: str) -> str:
    # С темой форка — жирный абзац: <h3>-<h6> мобильный Telegram набирает с засечками.
    if settings.PREMIUM_THEME_ENABLED:
        return f'<p><b>{inner_html}</b></p>'
    return f'<h6>{inner_html}</h6>'
```

В `build_main_menu_rich_html`:

```python
    if settings.PREMIUM_THEME_ENABLED:
        blocks.append(f'<p><b>{user_name}</b></p>')
    else:
        blocks.append(f'<h4>👤 {user_name}</h4>')
        blocks.append('<hr/>')
```

обе строки `blocks.append(f'<h6>{_rich_text(heading)}</h6>')` → `blocks.append(_section_heading(_rich_text(heading)))`.

После добавления блока баланса:

```python
if (
    settings.PREMIUM_THEME_ENABLED
    and settings.is_referral_program_enabled()
    and settings.REFERRAL_COMMISSION_PERCENT > 0
):
    hint = texts.get('FORK_MENU_REFERRAL_HINT')
    if hint:
        percent = html.escape(str(settings.REFERRAL_COMMISSION_PERCENT))
        blocks.append(f'<p>{_rich_text(hint).replace("{percent}", percent)}</p>')
```

(`texts.get` — не `.t`, см. Global Constraints; проверить, что `settings.is_referral_program_enabled` существует — используется в `service.py` `_evaluate_conditions`.)

Тексты — `ru.json`:

```json
  "MAIN_MENU_RICH_SUBSCRIPTION_HEADING": "{e:phone} Подписка",
  "MAIN_MENU_RICH_SUBSCRIPTIONS_HEADING": "{e:phone} Подписки",
  "MAIN_MENU_RICH_BALANCE": "Баланс: {balance}",
  "MAIN_MENU_RICH_TRIAL_BUTTON": "{e:gift} Попробовать бесплатно",
  "MAIN_MENU_RICH_CONNECT": "Подключиться",
  "MAIN_MENU_RICH_RENEW": "Продлить",
  "MAIN_MENU_RICH_TRIAL_ACTIVATED": "{e:party} <b>Пробный доступ включён</b>",
  "MAIN_MENU_ACTION_PROMPT": "Выберите действие",
  "MAIN_MENU": "<b>{user_name}</b>\n\n{e:phone} <b>Подписка:</b> {subscription_status}\n\nВыберите действие\n",
  "FORK_MENU_REFERRAL_HINT": "{e:people} Пригласите друга — {percent}% с его оплат."
```

`en.json`:

```json
  "MAIN_MENU_RICH_SUBSCRIPTION_HEADING": "{e:phone} Subscription",
  "MAIN_MENU_RICH_SUBSCRIPTIONS_HEADING": "{e:phone} Subscriptions",
  "MAIN_MENU_RICH_BALANCE": "Balance: {balance}",
  "MAIN_MENU_RICH_TRIAL_BUTTON": "{e:gift} Try for free",
  "MAIN_MENU_RICH_CONNECT": "Connect",
  "MAIN_MENU_RICH_RENEW": "Renew",
  "MAIN_MENU_RICH_TRIAL_ACTIVATED": "{e:party} <b>Trial access is on</b>",
  "MAIN_MENU_ACTION_PROMPT": "Choose an option",
  "MAIN_MENU": "<b>{user_name}</b>\n\n{e:phone} <b>Subscription:</b> {subscription_status}\n\nChoose an option\n",
  "FORK_MENU_REFERRAL_HINT": "{e:people} Invite a friend — get {percent}% of their payments."
```

`MAIN_MENU` — текст классического меню (запасной путь, когда rich недоступен), плейсхолдеры те же, что у апстрима (`{user_name}`, `{subscription_status}`) — это проверяет `test_fork_placeholders_match_upstream`. Перед правкой убедиться, что `get_main_menu_text` (`app/handlers/menu.py:1436`) не экранирует шаблон (`html.escape` на результате `texts.MAIN_MENU`); если экранирует — `MAIN_MENU` в форк не добавлять (иначе middleware будет восстанавливать тег с warning на каждом показе меню).

(`{percent}` в `FORK_MENU_REFERRAL_HINT` подставляется `.replace`, а не `.format`, — так же, как баланс в соседнем коде.) Перед этим проверить тест-пин `test_trial_deeplink_wired_in_start` для `MAIN_MENU_RICH_TRIAL_ACTIVATED` — он проверяет код в `start.py`, а не текст ключа, поэтому правка текста его не задевает.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/utils/test_rich_menu_fork.py tests/utils/test_rich_menu.py tests/test_rich_menu_pins.py tests/localization tests/test_locale_integrity.py -q`
Expected: PASS

- [ ] **Step 5: Lint, commit**

```bash
uv run ruff check app/utils/rich_menu.py tests/utils/test_rich_menu_fork.py
git add app/utils/rich_menu.py app/localization/fork tests/utils/test_rich_menu_fork.py
git commit -m "feat(premium): rich-меню без засечек, тексты с эмодзи и реферальная подсказка"
```

---

### Task 13: Предпросмотр и выкатка этапа 2

**Files:**
- Modify: `scripts/preview_screens.py` (экраны этапа 2 в `build_screens`)
- Test: `tests/scripts/test_preview_screens.py`

**Interfaces:**
- Consumes: `build_welcome_screen`, `build_trial_ready_screen` (Tasks 9–10), `_menu_keyboard` (Task 5), `MenuContext.primary_action` (Task 11).

- [ ] **Step 1: Write the failing test** (дописать)

```python
def test_stage_two_screens_present(monkeypatch):
    monkeypatch.setattr(settings, 'PREMIUM_THEME_ENABLED', True)
    monkeypatch.setattr(settings, 'BOT_USERNAME', 'zanity_vpn_bot')
    titles = [screen.title for screen in preview_screens.build_screens('ru')]
    for expected in (
        'Приветствие — пробный',
        'Пробный включён',
        'Меню — пробный',
        'Меню — подключиться',
        'Меню — продлить',
        'Меню — тариф',
    ):
        assert expected in titles, expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scripts/test_preview_screens.py -q`
Expected: FAIL — нет экранов этапа 2.

- [ ] **Step 3: Implement** — в `scripts/preview_screens.py` в конец списка `build_screens`:

```python
def _stage_two_screens(language: str) -> list[PreviewScreen]:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from aiogram.types import InlineKeyboardButton, WebAppInfo

    from app.localization.texts import get_texts
    from app.utils import fork_onboarding

    texts = get_texts(language)
    newcomer = SimpleNamespace(
        language=language, subscriptions=[], auth_type='telegram', is_trial_already_used=MagicMock(return_value=False)
    )
    tg_user = SimpleNamespace(first_name='Андрей' if language == 'ru' else 'Andrew', username=None)
    welcome_text, welcome_kb = fork_onboarding.build_welcome_screen(newcomer, tg_user)
    connect_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.CONNECT_BUTTON, web_app=WebAppInfo(url='https://example.com/sub'))],
            [InlineKeyboardButton(text=texts.BACK_TO_MAIN_MENU_BUTTON, callback_data='back_to_menu')],
        ]
    )
    ready_text, ready_kb = fork_onboarding.build_trial_ready_screen(texts, '19.10.2026', connect_kb)
    active = {'has_active_subscription': True, 'subscription_is_active': True}
    return [
        PreviewScreen('Приветствие — пробный', welcome_text, welcome_kb),
        PreviewScreen('Пробный включён', ready_text, ready_kb),
        PreviewScreen('Меню — пробный', 'Меню: новичок', _menu_keyboard(language, primary_action='trial')),
        PreviewScreen(
            'Меню — подключиться',
            'Меню: подписка активна',
            _menu_keyboard(language, primary_action='connect', **active),
        ),
        PreviewScreen(
            'Меню — продлить', 'Меню: истекает через 2 дня', _menu_keyboard(language, primary_action='extend', **active)
        ),
        PreviewScreen('Меню — тариф', 'Меню: пробный уже был', _menu_keyboard(language, primary_action='buy')),
    ]
```

и в `build_screens` вернуть `[...существующие..., *(_stage_two_screens(language) if settings.PREMIUM_THEME_ENABLED else [])]`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q && uv run ruff check app scripts tests`
Expected: всё зелёное.

- [ ] **Step 5: Commit**

```bash
git add scripts/preview_screens.py tests/scripts/test_preview_screens.py
git commit -m "feat(premium): предпросмотр экранов пути новичка"
```

- [ ] **Step 6: Предпросмотр на msk3 (с «да»)** — та же команда, что в Task 6 Step 2 (с `-e PREMIUM_THEME_ENABLED=true -e BOT_USERNAME=zanity_vpn_bot`), для `--lang ru` и `--lang en`. Владелец проверяет: одна зелёная кнопка на каждом экране меню, приветствие и «пробный включён» читаются, ссылка «правила» ведёт в бота.

- [ ] **Step 7: Мерж в main (с «да»)** — по `superpowers:finishing-a-development-branch`.

- [ ] **Step 8: Прод (с «да»), сразу после деплоя**

```bash
ssh root@msk3.zanity.net 'set -e; cd /root/remnawave-bot; ts=$(date +%Y%m%d-%H%M%S); cp .env /root/backups/bot-env-$ts.bak; for kv in SKIP_RULES_ACCEPT=true LANGUAGE_AUTO_DETECT=true; do k=${kv%%=*}; grep -q "^$k=" .env && sed -i "s/^$k=.*/$kv/" .env || echo "$kv" >> .env; done; docker compose up -d --force-recreate bot'
```

Запись `welcome_texts` id=1 выключить (с «да»; бэкап — сама строка, она не удаляется):

```bash
ssh root@msk3.zanity.net "docker exec remnawave_bot_db psql -U \$(grep ^POSTGRES_USER= /root/remnawave-bot/.env | cut -d= -f2) -d remnawave_bot -c 'update welcome_texts set is_enabled = false where id = 1 returning id, is_enabled'"
```

(Если имя пользователя/БД в `.env` называются иначе — посмотреть `docker-compose.yml` сервиса `postgres`.)

- [ ] **Step 9: Проверка** — логи без ошибок (команда из Task 6 Step 5). Владелец проходит путь новичка сам: удалить себя нельзя, поэтому проверить вторым аккаунтом или попросить знакомого; минимум — `/start rules` и главное меню у себя. Записать в память проекта дату выкатки этапа 2 и метрики «до» (27 % без подписки, 32 % пробных с подключением) для сравнения через 2–4 недели.

- [ ] **Step 10: Откат этапа 2 (с «да»; в обратном порядке к Step 8)** — сначала включить запись `welcome_texts` (пока тема включена, она не читается, поэтому порядок безопасен), затем выключить флаги в `.env` (свежий бэкап перед правкой; эталон «до этапа 2» — бэкап из Step 8) и пересоздать контейнер:

```bash
ssh root@msk3.zanity.net "docker exec remnawave_bot_db psql -U \$(grep ^POSTGRES_USER= /root/remnawave-bot/.env | cut -d= -f2) -d remnawave_bot -c 'update welcome_texts set is_enabled = true where id = 1 returning id, is_enabled'"
ssh root@msk3.zanity.net 'set -e; cd /root/remnawave-bot; ts=$(date +%Y%m%d-%H%M%S); cp .env /root/backups/bot-env-$ts.bak; for kv in PREMIUM_THEME_ENABLED=false SKIP_RULES_ACCEPT=false LANGUAGE_AUTO_DETECT=false; do k=${kv%%=*}; grep -q "^$k=" .env && sed -i "s/^$k=.*/$kv/" .env || echo "$kv" >> .env; done; docker compose up -d --force-recreate bot'
```

`PREMIUM_THEME_ENABLED=false` выключает и этап 1 (флаг общий); чтобы оставить этап 1, верните в `main` код этапа 1 (`premium-stage-1`) и держите флаг `true`. Проверка — команда из Task 6 Step 5.
