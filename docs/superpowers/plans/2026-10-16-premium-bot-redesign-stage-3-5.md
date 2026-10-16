# Переработка бота под Premium — этапы 3–5: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** все экраны и уведомления, которые видит пользователь (не админ), переведены на визуальный язык форка: короткий текст, жирная строка-заголовок с одним анимированным эмодзи, без цитат и служебных строк, анимированные иконки и цвета кнопок; плюс кнопка «📱 Добавить устройство» прямо на экране подписки.

**Architecture:** тексты переписываются в слое форка (`app/localization/fork/{ru,en}.json`) поверх ключей апстрима с теми же `{плейсхолдерами}`. Строки, захардкоженные в коде апстрима, оборачиваются вызовом `fork_text(texts, 'FORK_…', '<старый текст>')`: без темы — прежний текст, с темой — текст форка. Структурные изменения экранов (убрать блок, сменить разметку, добавить кнопку) — только под `settings.PREMIUM_THEME_ENABLED`; без темы поведение апстрима не меняется. Предпросмотр каждого экрана — через `scripts/preview_screens.py`.

**Tech Stack:** Python 3, aiogram 3.31, pytest, ruff (CI: `ruff format --check .`, `ruff check .`, `python -m scripts.generate_structure_reference --check` — `ruff format` форматирует и блоки кода в Markdown).

**Spec:** `docs/superpowers/specs/2026-10-16-premium-bot-redesign-design.md` (§4 визуальный язык, §6 охват). Этапы 1–2 выкачены и включены на проде 2026-10-16.

**Описи экранов (обязательное чтение для исполнителя своей задачи):** `.superpowers/sdd/stage-3-5/inventory-1-subscription.md`, `inventory-2-purchase.md`, `inventory-3-notifications.md`, `inventory-4-other.md` — по каждому экрану: обработчик, способ отправки, ключи и их текущие тексты, захардкоженные строки, экранирование, клавиатуры, мёртвые под прод-конфигом ветки.

## Global Constraints

- Прод-конфиг: `SALES_MODE=tariffs`, `MULTI_TARIFF_ENABLED=false`, `CONNECT_BUTTON_MODE=miniapp_subscription`, `ENABLE_LOGO_MODE=true`, один тариф «Стандартный» (не суточный, 3 устройства, `device_price_kopeks=5500`), платёжка RollyPay, `TELEGRAM_STARS_ENABLED=false`, `SERVER_STATUS_MODE=disabled`, `AUTO_PURCHASE_AFTER_TOPUP_ENABLED=true`, `SUPPORT_TOPUP_ENABLED=true`, рефералка 25 %. Экраны, мёртвые под этим конфигом (по описям), **не переписываем**.
- Языки: только `ru` и `en`, одинаковый набор ключей; `{плейсхолдеры}` ключа форка совпадают с апстримом (тест `tests/localization/test_fork_locale.py`). Новые ключи — только `FORK_*`, читаются атрибутом/`texts.get`/`fork_text`, **никогда** `texts.t('FORK_…')`.
- Админку не трогаем.
- **Визуальный язык (спек §4, уточнения владельца после предпросмотров):**
  1. Первая строка — `<b>…</b>` с максимум одним `{e:имя}` в начале; остальное — короткие строки без эмодзи-маркеров у каждой строки. Всего на сообщение 1–2 анимированных эмодзи.
  2. Без `<blockquote>` (цитат), без `<h1>`–`<h6>`, без полосок-прогрессбаров, без таблиц.
  3. Убрать служебное и дублирующее: «Серверы: Default-Squad», «Тип: Платная», «Выберите действие:» (если кнопки и так очевидны), ссылки/подсказки, которые дублируют кнопку, ID транзакций.
  4. Деньги и ключевые значения — `<b>`; даты — `dd.mm.yyyy`; «осталось N дней» — через `format_days` (склонение).
  5. Текст ≤ 1024 символов после снятия тегов (лимит подписи к фото в режиме логотипа).
  6. Кнопки: подпись с одним эмодзи из каталога в начале (станет анимированной иконкой) или `{e:имя}` в ключе; ровно одна зелёная (главное действие) — через таблицу `app/utils/button_theme.py` или явный `style`; «Назад/В меню» — без цвета и иконки.
- Значения из БД/от пользователя по-прежнему `html.escape`; шаблоны не экранировать. `REFERRAL_INVITE_TEXT` экранируется целиком и уходит другим людям — в нём **не** использовать `{e:…}`.
- Без темы (`PREMIUM_THEME_ENABLED=false`) вывод каждого экрана байт-в-байт как сейчас.
- Каждая задача: свои тесты, полный прогон, `uv run ruff format --check .`, `uv run ruff check .`, `make docs-structure`, коммит.
- Каждая задача добавляет свои ключевые экраны в предпросмотр (`build_screens`), собранные на фиктивных данных из тех же шаблонов/строителей, что и бот.

## Review Focus

1. Ключ форка с плейсхолдером, которого нет в `.format(**kwargs)` вызывающего кода → `KeyError` в рантайме (у вебхук-уведомлений — молча не отправится). Тест на каждый переписанный шаблон: `.format(**<реальные kwargs вызова>)` проходит.
2. Код апстрима вырезает точную подстроку из шаблона (`purchase.py:481-484`, `:3020-3024` — строка «📱 Устройства: …») — после переписывания шаблона вырезание молча перестаёт работать. Под темой эти места должны работать с новым текстом (тест с выключенным выбором устройств).
3. Подпись >1024 символов в режиме логотипа → сообщение уходит без логотипа. Тест длины для экранов с переменной длиной (подписка с 5 устройствами, история баланса, список тарифов).
4. Английский пользователь видит русские единицы («ГБ», «безлимит», «дней», «₽» не страшно) — в переписанных экранах использовать форк-форматтеры (Task 1).
5. Кнопка «Вернуться к оформлению» в уведомлении об оплате после того, как автопокупка уже израсходовала корзину (жмёшь — «Корзина не найдена»). Под темой кнопку не показывать, если корзина уже обработана (Task 6).

---

### Task 1: Инструменты форка: `fork_text`, форматтеры, SUPPORT_INFO

**Files:**
- Create: `app/localization/fork_text.py`
- Modify: `app/localization/texts.py` (`_build_dynamic_values` / `Texts.__init__`: динамический `SUPPORT_INFO` не должен перебивать ключ форка)
- Test: `tests/localization/test_fork_text.py`

**Interfaces — Produces:**
- `fork_text(texts, key: str, default: str) -> str` — значение ключа форка, если тема включена и ключ загружен, иначе `default` (для захардкоженных строк апстрима).
- `fork_traffic(texts, gb: float | None) -> str` — `«12.5 ГБ»` / `«12.5 GB»`; `0`/`None` как лимит → `«∞»`. Ключ `FORK_GB` уже есть (`{n} ГБ`/`{n} GB`).
- `fork_traffic_usage(texts, used_gb, limit_gb) -> str` — через существующий `FORK_SUB_TRAFFIC_VALUE` (`{used} из {limit}` / `{used} of {limit}`).
- `fork_period(texts, days: int) -> str` — «30 дней» / «30 days»: обёртка над существующим `format_days` из `app/utils/fork_onboarding.py`.
- `fork_devices(texts, used: int | str | None, limit) -> str` — `«1 из 3»` / `«1 of 3»` (новый ключ `FORK_DEVICES_OF` `{used} из {limit}` / `{used} of {limit}`; лимит `0/None` → `∞`).
- Перенести `_fork_gb`/`_fork_traffic_value` из `app/utils/rich_menu.py` сюда (как `fork_traffic`/`fork_traffic_usage`) и импортировать в `rich_menu.py` (одна реализация).

- [ ] **Step 1:** Тесты: `fork_text` с темой/без темы/без ключа; `fork_traffic(texts_en, 12.5) == '12.5 GB'`, `fork_traffic(texts_ru, 0)` → `'∞'`; `fork_devices(texts_ru, 1, 3) == '1 из 3'`; при теме `get_texts('ru').SUPPORT_INFO` равен значению из `fork/ru.json` (добавить `SUPPORT_INFO` в форк в Task 10 — здесь тест на механизм с временным файлом форка через `monkeypatch` `FORK_LOCALES_DIR`, как в `test_fork_locale.py`).
- [ ] **Step 2:** Run `uv run pytest tests/localization/test_fork_text.py -q` → FAIL.
- [ ] **Step 3:** Реализация. В `Texts.__init__`: динамические значения применяются, но ключи, пришедшие из слоя форка, не перезаписываются (нужен способ знать, что ключ из форка: `loader` отдаёт набор ключей форка, напр. `loader.fork_keys(language) -> frozenset[str]`).
- [ ] **Step 4:** Run → PASS; полный прогон; ruff; `make docs-structure`.
- [ ] **Step 5:** Commit `feat(premium): fork_text и форк-форматтеры трафика, устройств, срока`.

---

### Task 2: Экран «Подписка» + кнопка «Добавить устройство»

**Files:** `app/handlers/subscription/purchase.py` (`show_subscription_info` и хелперы текста, ~191–600), `app/keyboards/inline.py` (`get_subscription_keyboard` ~1121), `app/localization/fork/{ru,en}.json`, `scripts/preview_screens.py`, tests: `tests/handlers/test_fork_subscription_screen.py`.

**Interfaces — Consumes:** Task 1 helpers; `_fork_status_label(subscription, texts)` и `_fork_days_left(subscription)` из `app/utils/rich_menu.py` (вынести в общий модуль, напр. `app/utils/fork_subscription.py`, и импортировать в обоих местах), `_is_daily_tariff`/`EXPIRING_DAYS` из `app/services/menu_layout/fork_layout.py`; `format_days`.

**Целевой текст под темой (ru; en — по смыслу):**
```
<b>{e:check} Подписка активна</b>            ← та же фраза, что в rich-меню (FORK_SUB_STATUS_*)
Стандартный · до 25.10.2026, осталось 8 дней
Трафик: 0 ГБ из ∞ · Устройства: 1 из 3
Подключено: macOS — Mac, iPhone — iPhone 15  ← только если есть устройства; до 5, через запятую, html.escape
Ссылка для приложения (нажмите, чтобы скопировать):
<code>https://sub.example/…</code>               ← только если ссылка показывается сейчас (trial/paid active, не HIDE_SUBSCRIPTION_LINK)
```
- Убираются: имя, баланс, «Тип», «Серверы», цитата тарифа (`purchase.py:369-440`), цитата устройств, цитата докупленного трафика (если есть активные докупки — одна строка `Докуплено: N ГБ до dd.mm.yyyy`), «Осталось: N дн.» отдельной строкой, предупреждения `SUBSCRIPTION_WARNING_*` заменяются статусом «Подписка скоро истекает» (EXPIRING).
- Нет подписки: `<b>{e:cross} Подписки нет</b>` + «Выберите тариф — подключение займёт пару минут.»
- Реализация: под темой отдельная функция сборки текста `_build_fork_subscription_text(...) -> str` (рядом с текущей сборкой); вне темы — прежний путь без изменений. Строка «Устройства» не зависит от вырезания подстроки (Review Focus 2): если выбор устройств выключен, часть «Устройства» просто не добавляется.

**Клавиатура под темой** (`get_subscription_keyboard`): `🚀 Подключиться` (как сейчас, web_app, зелёная явно), `🔄 Продлить` (`subscription_extend`, синяя явно — на этом экране главное действие «Подключиться»), `📱 Добавить устройство` (`subscription_change_devices`, синяя; показывать, если у тарифа `device_price_kopeks > 0` или для нетарифных `settings.is_devices_selection_enabled()` — те же условия, что в настройках), `⚙️ Настройки` + `📦 Тариф` в одном ряду (как сейчас), `🎁 Подарить подписку` (как сейчас), «⬅️ Назад». Автоплатёж — оставить, если показывается сейчас. Подписи — ключи форка (`FORK_BUTTON_*`), с одним эмодзи каталога.

- [ ] Тесты: текст под темой для active / expiring / trial / expired / нет подписки (ru и en) — строки по порядку, нет `<blockquote`, нет «Серверы», длина ≤1024 при 5 устройствах с длинными именами; без темы — вывод совпадает с апстримом (снимок из текущего кода); клавиатура под темой содержит `subscription_change_devices` при цене устройства > 0 и не содержит при 0; ровно одна зелёная (с учётом таблицы темы).
- [ ] RED → реализация → GREEN; полный прогон; ruff; docs; предпросмотр (экраны «Подписка — активна», «— скоро истекает», «— нет подписки»).
- [ ] Commit `feat(premium): экран подписки — коротко, кнопка «Добавить устройство»`.

---

### Task 3: Настройки подписки, устройства, перевыпуск

**Files:** `app/handlers/subscription/purchase.py` (`handle_subscription_settings` ~2970), `app/handlers/subscription/devices.py`, `app/handlers/subscription/revoke.py`, `app/keyboards/inline.py` (`get_change_devices_keyboard` ~2808 — захардкоженные подписи, `get_confirm_change_devices_keyboard`, `get_devices_management_keyboard` ~3373), fork json, preview, tests `tests/handlers/test_fork_devices_screens.py`.

Экраны (по описи 1, §3, §4, §5, §9): настройки (`SUBSCRIPTION_SETTINGS_OVERVIEW` — убрать «Стран», трафик и устройства через форк-форматтеры; вырезание строки устройств — Review Focus 2), изменение устройств (prompt, кнопки количества, подтверждение, недостаточно средств, успех), управление устройствами (список, сброс одного/всех, переименование), перевыпуск (предупреждение, успех, ошибка). Захардкоженные подписи кнопок количества (`inline.py:2825-2897`) и `🔄 {device}` — через `fork_text`. Кнопка подтверждения изменения — зелёная (явно), «Отмена» — без цвета (навигация, не опасное действие); «Сбросить все устройства» и подтверждение перевыпуска — красные (явно).

- [ ] Тесты: каждый переписанный шаблон форматируется реальными kwargs вызова (ru/en); подписи кнопок количества под темой без захардкоженного русского для en; цвета кнопок; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр (настройки, выбор количества, подтверждение, список устройств, перевыпуск).
- [ ] Commit `feat(premium): настройки подписки, устройства и перевыпуск в стиле форка`.

---

### Task 4: Продление и смена тарифа

**Files:** `app/handlers/subscription/purchase.py` (`handle_extend_subscription` ~1666: ветки E0/E0b/E2 с захардкоженным текстом), `app/handlers/subscription/tariff_purchase.py` (`show_tariff_extend` ~2642, `select_tariff_extend_period` ~2830, `confirm_tariff_extend` ~2978, `show_instant_switch_list` ~4533 — живые ветки «нет тарифов»/«истекла»/«отключено», `return_to_saved_tariff_cart` ~5337 для `cart_mode=extend`), fork json, preview, tests `tests/handlers/test_fork_extend_screens.py`.

Экраны (опись 1, §6, §7): выбор периода продления (`TARIFF_RENEW_TITLE`; подписи кнопок периодов — `_period_button_text` и `🔥−N%` через `fork_text`), подтверждение (`TARIFF_RENEW_CONFIRM` — коротко: тариф, период, к оплате **жирным**, баланс→после оплаты одной строкой), недостаточно средств (`TARIFF_PURCHASE_INSUFFICIENT` + `TARIFF_RENEW_CART_SAVED_HINT` — «Не хватает **X**. Пополните баланс — продлим автоматически.»), успех (`TARIFF_RENEW_SUCCESS` — «{e:party} Подписка продлена до dd.mm.yyyy»: если в kwargs нет даты окончания, добавить её только под темой отдельным ключом форка, не меняя плейсхолдеры апстрима), «Сессия устарела»/ошибки, ветки E0/E2 (захардкоженное → `fork_text`), `TARIFF_SWITCH_NO_TARIFFS`/`_EXPIRED`/`_DISABLED`, `TARIFF_PURCHASE_STILL_INSUFFICIENT`, тост `TARIFF_PURCHASE_CART_RESTORED`.

- [ ] Тесты: шаблоны с реальными kwargs (ru/en); подписи кнопок периодов для en без «дней»; одна зелёная на экранах подтверждения и недостаточно средств; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр (выбор периода, подтверждение, не хватает средств, успех).
- [ ] Commit `feat(premium): продление и смена тарифа в стиле форка`.

---

### Task 5: Покупка подписки

**Files:** `app/handlers/subscription/tariff_purchase.py` (`show_tariffs_list` ~810, `format_tariffs_list_text` ~157, `format_tariff_info_for_user` ~501, `get_tariffs_keyboard` ~228, `get_tariff_periods_keyboard` ~271, `select_tariff_period` ~1635, `confirm_tariff_purchase` ~1794, `return_to_saved_tariff_cart`), `app/handlers/subscription/purchase.py` (`return_to_saved_cart` ~1480: захардкоженный «Корзина не найдена»), fork json, preview, tests `tests/handlers/test_fork_purchase_screens.py`.

Экраны (опись 2, §A1–A8): список тарифов (при одном тарифе сразу выбор периода — так и есть), информация о тарифе + выбор периода (`TARIFF_PURCHASE_INFO`, захардкоженное `📝 {description}`), подтверждение (`TARIFF_PURCHASE_CONFIRM`), недостаточно средств (`TARIFF_PURCHASE_INSUFFICIENT` + `TARIFF_PURCHASE_CART_SAVED_HINT`; клавиатура с прямыми кнопками оплаты на недостающую сумму — оставить, главная зелёная — оплата), успех (`TARIFF_PURCHASE_SUCCESS` — «{e:party} Подписка оформлена до …» + кнопка «🚀 Подключиться» зелёная вместо «📱 Моя подписка», если ссылка есть; иначе «Моя подписка»), «всё ещё не хватает», корзина восстановлена, ошибки списания. Суточные ветки — мёртвые, не трогать.

- [ ] Тесты: шаблоны с реальными kwargs; успех содержит кнопку подключения под темой; длина списка тарифов ≤1024 при 5 тарифах с описаниями; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр (выбор периода, подтверждение, не хватает, успех).
- [ ] Commit `feat(premium): покупка подписки в стиле форка`.

---

### Task 6: Баланс, пополнение, оплата RollyPay

**Files:** `app/handlers/balance/main.py` (`show_balance_menu` ~247, `show_balance_history` ~273 — захардкоженное, `show_payment_methods` ~347 + ограничение пополнения, `request_support_topup` ~486, `process_topup_amount` ~535 — захардкоженные лимиты), `app/handlers/balance/rollypay.py` (`start_rollypay_topup` ~195, `_create_rollypay_payment_and_respond` ~37), `app/utils/payment_utils.py` (`get_payment_methods_text` ~535), `app/keyboards/inline.py` (`get_balance_keyboard` ~1613, `get_payment_methods_keyboard` ~1662), `app/keyboards/topup_amounts.py`, `app/services/payment/rollypay.py` (~466-484 — сообщение об успешной оплате целиком захардкожено), `app/services/payment/common.py` (`build_topup_success_keyboard` ~36), fork json, preview, tests `tests/handlers/test_fork_balance_screens.py`.

Экраны (опись 2, §B1–B8): баланс («{e:money_bag} Баланс: **150 ₽**», кнопки «Пополнить» зелёная, «История»), история (заголовок, строки `+150 ₽ · Пополнение · 16.10.2026`, пустая история), способы оплаты (без двойного «Выберите способ»), ввод суммы RollyPay (`ROLLYPAY_ENTER_AMOUNT` коротко; быстрые суммы), ссылка на оплату (`ROLLYPAY_PAYMENT_CREATED` — сумма без «.00», «Оплатить N ₽» зелёная URL-кнопка — url-кнопкам таблица цвет не даёт, задать явно), успех оплаты (`fork_text` на весь текст: «{e:check} Баланс пополнен на **N ₽**» + способ, без ID транзакции), пополнение через поддержку (захардкоженное → `fork_text`), ограничение пополнения.
**Review Focus 5:** клавиатура успеха строится до автопокупки (`rollypay.py:469`) — под темой строить её после `send_cart_notification_after_topup` (или не показывать «Вернуться к оформлению», если корзины уже нет).

- [ ] Тесты: тексты с реальными kwargs; успех оплаты под темой без «Транзакция»; кнопка «Вернуться к оформлению» не показывается, если корзина израсходована автопокупкой; история ≤1024 при 10 операциях с длинными описаниями; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр (баланс, история, способы, ввод суммы, ссылка на оплату, успех).
- [ ] Commit `feat(premium): баланс и оплата в стиле форка`.

---

### Task 7: Уведомления после пополнения (автопокупка, допы)

**Files:** `app/services/subscription_auto_purchase_service.py` (тексты `AUTO_PURCHASE_*`, `DAILY_SUBSCRIPTION_RESUMED_AFTER_TOPUP` — мёртв, захардкоженный суточный — мёртв), fork json, preview, tests `tests/services/test_fork_auto_purchase_texts.py`.

Опись 2 §B7 + опись 1 §6: `AUTO_PURCHASE_SUBSCRIPTION_SUCCESS`, `_EXTENDED`, `_EXTENDED_DETAILS`, `_HINT`, `AUTO_PURCHASE_DEVICES_SUCCESS`/`ADDON_PURCHASE_DEVICES_SUCCESS`, `AUTO_PURCHASE_TRAFFIC_SUCCESS`; кнопки `MY_SUBSCRIPTION_BUTTON`/`BACK_TO_MAIN_MENU_BUTTON` — «🚀 Подключиться» зелёная там, где ссылка есть; иначе «📱 Моя подписка».

- [ ] Тесты: каждый шаблон с реальными kwargs (ru/en); заголовок-строка `<b>…</b>` первой.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр.
- [ ] Commit `feat(premium): уведомления после пополнения в стиле форка`.

---

### Task 8: Уведомления мониторинга и вебхуков

**Files:** `app/services/monitoring_service.py`, `app/services/remnawave_webhook_service.py`, `app/services/notification_delivery_service.py` (только если нужно для форк-ключей), fork json, preview, tests `tests/services/test_fork_notification_texts.py`.

Опись 3: все ЖИВЫЕ пользовательские уведомления — истекает (платная), истекла, конец пробного, винбэк день 1, волны скидок 2/3, автоплатёж успех/ошибка, отписка от канала (если живо), `WEBHOOK_USER_NOT_CONNECTED` («Вы ещё не подключились» — с кнопкой «🚀 Подключиться» зелёной и «💬 Помощь»), `WEBHOOK_SUB_FIRST_CONNECTED`, `WEBHOOK_SUB_EXPIRED`, `DEVICE_*`, трафик, grace-уведомления, прочие живые из описи. Rich-уведомления: первая строка ≤80 видимых символов становится жирным заголовком автоматически (`build_notification_rich_html`) — не дублировать `<b>`, если строка уходит rich; не использовать `<p>`/`<blockquote>` и т.п. (иначе уходит классикой). Захардкоженный русский в живых уведомлениях — `fork_text`.

- [ ] Тесты: каждый переписанный шаблон с реальными kwargs (Review Focus 1 — у вебхуков неизвестный плейсхолдер молча гасит сообщение); `build_notification_rich_html(текст)` не `None` (уходит rich) для каждого.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр (по одному rich-уведомлению каждого вида — через тот же путь, что `rich_notify`).
- [ ] Commit `feat(premium): уведомления о подписке в стиле форка`.

---

### Task 9: Рефералы

**Files:** `app/handlers/referral.py`, `app/keyboards/inline.py` (`get_referral_keyboard` ~2382), fork json, preview, tests `tests/handlers/test_fork_referral_screens.py`.

Опись 4: главный экран рефералки («{e:people} Приглашайте друзей — **25 %** с их оплат», ссылка, сколько пригласили / заработано), список, аналитика, QR (подпись), уведомление о начислении рефералу (если живо). `REFERRAL_INVITE_TEXT` — без `{e:…}` (экранируется целиком и уходит друзьям). Вывод средств / настройки вознаграждения — мёртвые, не трогать.

- [ ] Тесты: шаблоны с реальными kwargs; `REFERRAL_INVITE_TEXT` в форке не содержит `{e:`; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр.
- [ ] Commit `feat(premium): реферальная программа в стиле форка`.

---

### Task 10: Поддержка и тикеты

**Files:** `app/handlers/support.py`, `app/handlers/tickets.py`, `app/services/support_settings_service.py` (если текст поддержки берётся из БД — не трогать БД-текст, менять только запасной `SUPPORT_INFO`), `app/localization/texts.py` (Task 1 уже не даёт динамике перебить форк), fork json, preview, tests `tests/handlers/test_fork_support_screens.py`.

Опись 4: экран поддержки (`SUPPORT_INFO` — коротко: «{e:speech} Поддержка» + одна строка «Напишите нам — ответим в течение пары часов» + кнопки), тикеты: создание (подсказки ввода), список, просмотр (захардкоженное — `fork_text`), ответ, уведомление пользователю об ответе поддержки.

- [ ] Тесты: под темой `get_texts('ru').SUPPORT_INFO` — текст форка; шаблоны с реальными kwargs; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр.
- [ ] Commit `feat(premium): поддержка и тикеты в стиле форка`.

---

### Task 11: Инфо, промокод, подарки, язык, частые ошибки

**Files:** `app/handlers/menu.py` (`show_info_menu` ~354, FAQ/правила/оферта/конфиденциальность — тексты из БД не трогаем, только заголовки/кнопки), `app/handlers/promocode.py`, `app/handlers/gift_activation.py`, `app/handlers/start.py` (`LANGUAGE_SELECTED`, экран выбора языка — заголовок), общие ключи (`ERROR`, `ACCESS_DENIED`, техработы — по описи 4), fork json, preview, tests `tests/handlers/test_fork_misc_screens.py`.

- [ ] Тесты: шаблоны с реальными kwargs; без темы — без изменений.
- [ ] RED → GREEN; прогон; ruff; docs; предпросмотр.
- [ ] Commit `feat(premium): инфо, промокод, подарки и язык в стиле форка`.

---

### Task 12: Итоговая проверка и выкатка (операции — с «да» владельца)

- [ ] Полный прогон, CI-линт локально (`uv run ruff format --check .`, `uv run ruff check .`, `uv run python -m scripts.generate_structure_reference --check`).
- [ ] Итоговое ревью всей ветки.
- [ ] Предпросмотр на msk3 (ru и en), с логотипом `MAIN_MENU_RICH_LOGO_URL=https://zanityvpn.com/bot-logo.png` — по правилам предыдущих предпросмотров (`/root/preview-premium`, `docker compose run --rm --no-deps … python -m scripts.preview_screens --to 535682505`).
- [ ] После «ок» владельца: мерж в `main` (автодеплой; при сбое сети раннера — `gh run rerun --failed`, при сбое деплоя — `docker compose pull bot && docker compose up -d bot` на msk3), проверка здоровья и логов. Флаги уже включены — изменения видны сразу после деплоя. Откат — `PREMIUM_THEME_ENABLED=false` + перезапуск (вернёт и этапы 1–2).
