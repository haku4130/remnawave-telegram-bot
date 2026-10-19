# Блок «Локации» на панели статистики — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На `/admin/dashboard` кабинета под «Нодами» показать, сколько людей сейчас на каждой локации подписки
(и через какие выходы идёт AUTO Европа), без задвоения при переключении — в том числе между нодами.

**Architecture:** На пяти нодах (msk1, msk2, msk4, nl1, SE-1) systemd-процесс читает хвост access-лога xray,
держит последние подключения устройств за 5 минут и раз в минуту шлёт их (с HMAC-ключами вместо id/IP) в бэкенд
кабинета. Бэкенд кладёт отчёт ноды в Redis (TTL 3 мин), а по запросу админки сводит устройства всех нод
(последнее подключение побеждает), считает уникальных пользователей по (нода, inbound) и накладывает на хосты панели.

**Tech Stack:** Python 3.10+ stdlib (счётчик); FastAPI, Pydantic, Redis через `app.utils.cache.cache`, pytest (бэкенд);
React + TypeScript + React Query + i18next, vitest (фронт, репозиторий `~/dev/bedolaga-cabinet`).

**Spec:** `docs/superpowers/specs/2026-10-18-locations-online-design.md`

## Global Constraints

- Окно — 300 секунд; отчёт — раз в 60 секунд; TTL отчёта в Redis — 180 секунд.
- Устройство = (id пользователя, IP клиента); ключи `u = HMAC-SHA256(token, id)`, `d = HMAC-SHA256(token, id + "|" + ip)`,
  hex, первые 16 символов. Токен один на все ноды и бэкенд.
- Счётчик — только stdlib, Python ≥ 3.10; ничего не пишет ни в xray, ни в панель, ни в профили.
- В отчёте нет сырых id пользователей и IP.
- Мостовой выход — outbound с префиксом `SS_OUTBOUND_`; `direct`, `block` и прочие в разбивку не идут.
- Имя выхода: `SS_OUTBOUND_FI2` → `FI-2`, `SS_OUTBOUND_UK1` → `UK-1`.
- Локации = хосты панели `is_disabled = False`, `is_hidden = False`, без «👇» в `remark`; порядок — `view_position`;
  дубль (нода, inbound) — одна карточка, название первого.
- Пустой `LOCATION_COUNTER_TOKEN` — приём отчётов отвечает 404.
- Пути фронта — с префиксом `/cabinet` (traefik срезает `/api`).
- Тексты фронта — i18n `adminDashboard.locations.*` во всех четырёх локалях (ru, en, fa, zh).
- Изменения на серверах (env на msk3, установка счётчиков, деплой фронта) — только после явного «да» владельца.

## Review Focus

1. Строки лога вида `from tcp:IP:port accepted udp:…` (UDP внутри VLESS, ~3 тыс. на msk4) и IPv6-клиенты
   `from [2a00:…]:port` — должны считаться, а `from DNS accepted …` без `email:` — пропускаться. → тест в Task 1.
2. Устройство ушло с AUTO на «Германию» и вернулось — разбивка AUTO не должна унаследовать старый выход;
   смена inbound'а сбрасывает выход. → тест в Task 1.
3. Две ноды прислали одно устройство с разным временем — засчитывается только более позднее; отчёт ноды
   старше окна по `t` не считается. → тест в Task 2.
4. Один inbound активен на двух нодах (MSK-1 и MSK-2 делят `vless-inbound-3443`, у MSK-2 адрес в панели —
   `remnanode`) — карточка «Россия #2 (МСК)» должна попасть на MSK-2 через `LOCATION_HOST_NODE_OVERRIDES`,
   а не на MSK-1 и не на обе. → тест в Task 2.
5. Счётчик ноды молчит: у её единственной локации — число панели с источником `panel`; у ноды с несколькими
   локациями — `null`, а не 0; если отчёт есть, но устройств на локации нет — 0, а не `null`. → тест в Task 2.

---

### Task 1: Счётчик на ноде

**Files:**
- Create: `scripts/location_counter/__init__.py` (пустой)
- Create: `scripts/location_counter/location_counter.py`
- Create: `scripts/location_counter/location-counter.service`
- Create: `scripts/location_counter/location-counter.env.example`
- Create: `scripts/location_counter/README.md`
- Test: `tests/scripts/test_location_counter.py`

**Interfaces:**
- Produces (контракт с Task 3 — тело POST):
  `{"node": str, "generated_at": "YYYY-MM-DDTHH:MM:SSZ", "window_seconds": 300,
    "devices": [{"u": str16, "d": str16, "t": "YYYY-MM-DDTHH:MM:SSZ", "inbound": str, "exit": str | null}]}`,
  заголовок `X-Location-Counter-Token: <token>`, `Content-Type: application/json`,
  URL = `${CABINET_URL}/api/cabinet/admin/stats/locations/report`.
- В модуле:
  - `@dataclass(frozen=True) class LogEvent: ts: datetime; ip: str; inbound: str; outbound: str; user: str`
  - `parse_line(line: str) -> LogEvent | None` — время в UTC (aware).
  - `@dataclass class DeviceState: user: str; ip: str; last_seen: datetime; inbound: str; exit: str | None`
  - `class DeviceTracker: observe(event: LogEvent) -> None; snapshot(now: datetime) -> list[DeviceState]`
    (snapshot выбрасывает устройства с `last_seen < now - 300 с`).
  - `make_key(token: str, value: str) -> str`
  - `build_report(node: str, devices: list[DeviceState], token: str, now: datetime) -> dict`
  - `main() -> int`

- [ ] **Step 1: Тесты разбора строк**

```python
LINE = '2026/10/18 17:34:07.590389 from 5.18.1.2:51234 accepted tcp:57.144.248.145:443 [vless-to-eu-auto -> SS_OUTBOUND_FI2] email: 123'


def test_parse_regular_line():
    e = parse_line(LINE)
    assert e == LogEvent(
        datetime(2026, 10, 18, 17, 34, 7, 590389, tzinfo=UTC), '5.18.1.2', 'vless-to-eu-auto', 'SS_OUTBOUND_FI2', '123'
    )


def test_parse_double_arrow_and_tcp_prefix():
    e = parse_line(
        '2026/10/18 17:34:07.1 from tcp:5.18.1.2:3832 accepted udp:8.8.8.8:53 [vless-to-eu-auto >> direct] email: 7'
    )
    assert (e.ip, e.outbound, e.user) == ('5.18.1.2', 'direct', '7')


def test_parse_ipv6_client():
    e = parse_line(
        '2026/10/18 17:34:07.1 from [2a00:1fa0::1]:443 accepted tcp:x.com:443 [vless-inbound-sni-github -> direct] email: 9'
    )
    assert e.ip == '2a00:1fa0::1'


def test_skip_lines_without_user():
    assert parse_line('2026/10/18 17:34:07.1 from DNS accepted udp:1.1.1.1:53 [xray.system.49d7 >> direct]') is None
    assert parse_line('garbage') is None
```

- [ ] **Step 2: Тесты трекера и отчёта**

```python
def ev(sec, inbound, outbound, user='1', ip='1.1.1.1'):
    return LogEvent(datetime(2026, 10, 19, 7, 0, 0, tzinfo=UTC) + timedelta(seconds=sec), ip, inbound, outbound, user)


def test_last_connection_wins_and_exit_tracked():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(10, 'vless-to-eu-auto', 'direct'))
    [d] = t.snapshot(ev(20, 'x', 'y').ts)
    assert (d.inbound, d.exit, d.last_seen) == ('vless-to-eu-auto', 'SS_OUTBOUND_FI1', ev(10, 'x', 'y').ts)


def test_inbound_switch_resets_exit():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(5, 'vless-to-de1-ss', 'SS_OUTBOUND_DE1'))
    t.observe(ev(9, 'vless-to-eu-auto', 'direct'))
    [d] = t.snapshot(ev(10, 'x', 'y').ts)
    assert (d.inbound, d.exit) == ('vless-to-eu-auto', None)


def test_older_event_ignored():
    t = DeviceTracker()
    t.observe(ev(10, 'a', 'direct'))
    t.observe(ev(5, 'b', 'direct'))
    assert t.snapshot(ev(11, 'x', 'y').ts)[0].inbound == 'a'


def test_snapshot_prunes_after_window():
    t = DeviceTracker()
    t.observe(ev(0, 'a', 'direct'))
    t.observe(ev(0, 'a', 'direct', ip='2.2.2.2'))
    t.observe(ev(250, 'a', 'direct', ip='2.2.2.2'))
    assert [d.ip for d in t.snapshot(ev(301, 'x', 'y').ts)] == ['2.2.2.2']


def test_report_has_no_raw_ids():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI2', user='4242', ip='9.9.9.9'))
    now = ev(30, 'x', 'y').ts
    r = build_report('MSK-4', t.snapshot(now), 'secret', now)
    assert r['node'] == 'MSK-4' and r['window_seconds'] == 300 and r['generated_at'] == '2026-10-19T07:00:30Z'
    [d] = r['devices']
    assert d == {
        'u': make_key('secret', '4242'),
        'd': make_key('secret', '4242|9.9.9.9'),
        't': '2026-10-19T07:00:00Z',
        'inbound': 'vless-to-eu-auto',
        'exit': 'SS_OUTBOUND_FI2',
    }
    assert len(d['u']) == 16 and '4242' not in json.dumps(r) and '9.9.9.9' not in json.dumps(r)
```

- [ ] **Step 3: Запустить — падают**

Run: `uv run pytest tests/scripts/test_location_counter.py -v`
Expected: FAIL (ModuleNotFoundError `scripts.location_counter.location_counter`)

- [ ] **Step 4: Реализовать модуль**

`parse_line` — одно регулярное выражение по образцу из спеки; адрес клиента после `from ` с необязательным
`tcp:`/`udp:`, IPv4 или `[IPv6]` (скобки снять); строка без `email:` → `None`. `DeviceTracker` — словарь
`(user, ip) → DeviceState`; новое событие старше `last_seen` игнорируется; смена inbound'а сбрасывает `exit`;
`SS_OUTBOUND_*` записывается в `exit`. `make_key` — `hmac.new(token.encode(), value.encode(), sha256).hexdigest()[:16]`.

`main()`: читает env `CABINET_URL`, `LOCATION_COUNTER_TOKEN`, `NODE_NAME` (нет любого → сообщение в stderr, код 2);
поток-читатель запускает `docker exec remnanode tail -F -n +1 /var/log/xray/current` и кормит трекер под `threading.Lock`;
главный поток раз в 60 с строит отчёт по `datetime.now(UTC)` и шлёт `urllib.request` POST (таймаут 15 с), ошибку
пишет в stderr одной строкой и ждёт следующей минуты; завершился `docker exec` → `main` возвращает 1 (systemd поднимет).
`if __name__ == '__main__': sys.exit(main())`.

- [ ] **Step 5: Unit, env-пример, README**

`location-counter.service`: `ExecStart=/usr/bin/python3 /opt/location-counter/location_counter.py`,
`EnvironmentFile=/etc/location-counter.env`, `Restart=always`, `RestartSec=10`, `After=docker.service`,
`Nice=10`. `location-counter.env.example`: три переменные с комментариями (`NODE_NAME` — как в панели: `MSK-1`,
`MSK-2`, `MSK-4`, `NL-1`, `SE-1`; `CABINET_URL=https://cabinet.zanity.net`). README — установка в 5 команд
(scp файла и unit'а, env с `chmod 600`, `systemctl daemon-reload`, `enable --now`, `journalctl -u location-counter -f`)
и откат `systemctl disable --now location-counter`; SE-1 — по IP `root@191.44.114.69`.

- [ ] **Step 6: Тесты зелёные, линт**

Run: `uv run pytest tests/scripts/test_location_counter.py -v && uv run ruff check scripts/location_counter tests/scripts`
Expected: все PASS, ruff без замечаний

- [ ] **Step 7: Commit**

```bash
git add scripts/location_counter tests/scripts/test_location_counter.py
git commit -m "feat(locations): счётчик устройств по локациям из access-лога xray"
```

---

### Task 2: Сведение устройств и карточки локаций (бэкенд, чистая логика)

**Files:**
- Modify: `app/external/remnawave_api.py` — `RemnaWaveNode` (≈стр. 188–224) и `_parse_node` (≈стр. 2209–2255)
- Create: `app/services/location_presence.py`
- Modify: `app/config.py` — рядом с прочими настройками Remnawave
- Modify: `.env.example`
- Test: `tests/services/test_location_presence.py`, `tests/external/` — тест разбора тегов в существующем файле
  тестов `_parse_node`, если он есть, иначе новый `tests/external/test_remnawave_node_inbound_tags.py`

**Interfaces:**
- Consumes: `RemnaWaveHost`, `RemnaWaveNode` из `app/external/remnawave_api.py`.
- Produces:
  - `RemnaWaveNode.active_inbound_tags: dict[str, str]` (uuid → tag из `configProfile.activeInbounds[].tag`),
    по умолчанию `{}`.
  - `settings.LOCATION_COUNTER_TOKEN: str = ''`, `settings.LOCATION_HOST_NODE_OVERRIDES: str = ''`
    (формат `msk2.zanity.net=MSK-2,host2=NODE`), `settings.get_location_host_node_overrides() -> dict[str, str]`
    (ключ — адрес в нижнем регистре).
  - В `location_presence.py`:
    - `WINDOW = timedelta(seconds=300)`
    - `@dataclass(frozen=True) class Device: user_key: str; device_key: str; last_seen: datetime; node: str; inbound: str; exit: str | None`
    - `devices_from_report(report: dict) -> list[Device]`
    - `@dataclass class Presence: users: int; exits: dict[str, int]`
    - `merge_presence(devices: list[Device], now: datetime) -> dict[tuple[str, str], Presence]` — ключ (имя ноды, тег inbound'а)
    - `exit_display_name(tag: str) -> str`
    - `@dataclass class LocationCard: title: str; node_name: str | None; inbound_tag: str | None; users: int | None; exits: list[tuple[str, int]]; source: str | None; reported_at: datetime | None`
    - `build_location_cards(hosts: list[RemnaWaveHost], nodes: list[RemnaWaveNode], presence: dict[tuple[str, str], Presence], reports_at: dict[str, datetime], overrides: dict[str, str]) -> list[LocationCard]`
      (`reports_at` — имя ноды → `generated_at` её свежего отчёта; `source` ∈ `'counter'`, `'panel'`, `None`).

- [ ] **Step 1: Тесты**

`_parse_node` с `configProfile.activeInbounds = [{'uuid': 'u1', 'tag': 'vless-simple'}]` →
`active_inbound_tags == {'u1': 'vless-simple'}`; без `configProfile` → `{}`.

`get_location_host_node_overrides`: `'msk2.zanity.net=MSK-2, X.ORG = N'` → `{'msk2.zanity.net': 'MSK-2', 'x.org': 'N'}`; `''` → `{}`.

```python
NOW = datetime(2026, 10, 19, 7, 5, tzinfo=UTC)


def dev(u, d, sec_ago, node, inbound, exit=None):
    return Device(u, d, NOW - timedelta(seconds=sec_ago), node, inbound, exit)


def test_latest_device_wins_across_nodes():
    p = merge_presence(
        [dev('u1', 'd1', 100, 'NL-1', 'vless-inbound-sni-github'), dev('u1', 'd1', 10, 'MSK-4', 'vless-to-de1-ss')], NOW
    )
    assert p == {('MSK-4', 'vless-to-de1-ss'): Presence(1, {})}


def test_stale_devices_dropped():
    assert merge_presence([dev('u1', 'd1', 301, 'MSK-4', 'a')], NOW) == {}


def test_users_unique_and_exits_counted():
    p = merge_presence(
        [
            dev('u1', 'd1', 5, 'MSK-4', 'vless-to-eu-auto', 'SS_OUTBOUND_FI2'),
            dev('u1', 'd2', 5, 'MSK-4', 'vless-to-eu-auto', 'SS_OUTBOUND_FI2'),
            dev('u2', 'd3', 5, 'MSK-4', 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'),
            dev('u3', 'd4', 5, 'MSK-4', 'vless-to-eu-auto', None),
        ],
        NOW,
    )
    assert p[('MSK-4', 'vless-to-eu-auto')] == Presence(3, {'SS_OUTBOUND_FI2': 1, 'SS_OUTBOUND_FI1': 1})


def test_exit_display_name():
    assert [exit_display_name(t) for t in ('SS_OUTBOUND_FI2', 'SS_OUTBOUND_UK1', 'SS_OUTBOUND_TO_FI')] == [
        'FI-2',
        'UK-1',
        'TO_FI',
    ]
```

Для `build_location_cards` — фикстура, повторяющая прод на 2026-10-18 (сокращённо): ноды MSK-1 (`msk1.zanity.net`,
inbound u3443→`vless-inbound-3443`, online 1), MSK-2 (`remnanode`, u3443, online 2), MSK-4 (`msk4.zanity.net`,
uAuto→`vless-to-eu-auto`, uDe→`vless-to-de1-ss`, online 29), NL-1 (`nl1.zanity.net`, uGh, uAds, online 34),
SE-1 (`191.44.114.69`, uSe, online 8); хосты: «🇪🇺 [AUTO] Европа» (msk4, uAuto, pos 1), «🇩🇪 Германия» (msk4, uDe, pos 3),
«🎬 YouTube — Германия» (msk4, uDe, `is_disabled`), «👇 🎮 Для игр 👇» (msk1, u3443, pos 4),
«🇷🇺 #1 Россия (СПБ)» (msk1, u3443, pos 5), «🇷🇺 #2 Россия (МСК)» (msk2.zanity.net, u3443, pos 6),
«🇳🇱 Нидерланды» (nl1, uGh, pos 2), «🇳🇱 Нидерланды (с рекламой)» (nl1, uAds, pos 7), «🇸🇪 Швеция» (se1.zanity.net, uSe, pos 8);
`overrides = {'msk2.zanity.net': 'MSK-2'}`.

- `test_cards_order_filters_and_mapping`: заголовки по порядку `[AUTO] Европа, Нидерланды, Германия, #1 Россия (СПБ),
  #2 Россия (МСК), Нидерланды (с рекламой), Швеция`; у СПБ `node_name == 'MSK-1'`, у МСК — `'MSK-2'`, у Швеции — `'SE-1'`.
- `test_counter_source_with_zero`: `reports_at` есть для MSK-4, presence только для AUTO (`Presence(5, {'SS_OUTBOUND_FI2': 3})`)
  → AUTO `users == 5`, `exits == [('FI-2', 3)]`, `source == 'counter'`; Германия `users == 0`, `source == 'counter'`.
- `test_panel_fallback_single_location`: отчётов нет → Швеция `users == 8, source == 'panel'`; СПБ `users == 1, 'panel'`.
- `test_no_data_for_multi_location_node_without_report`: отчётов нет → обе «Нидерланды» и обе карточки MSK-4 —
  `users is None`, `source is None`.
- `test_ambiguous_host_without_override`: тот же набор, `overrides = {}` → «#2 Россия (МСК)» `node_name is None, users is None`.

- [ ] **Step 2: Запустить — падают**

Run: `uv run pytest tests/services/test_location_presence.py -v`
Expected: FAIL (ModuleNotFoundError `app.services.location_presence`)

- [ ] **Step 3: Реализовать**

`_parse_node`: добавить `active_inbound_tags` из того же `activeInbounds`. `config.py`: две настройки и геттер
(разбор как у `get_traffic_monitored_nodes`). `.env.example`: закомментированные `LOCATION_COUNTER_TOKEN=` и
`LOCATION_HOST_NODE_OVERRIDES=msk2.zanity.net=MSK-2` с комментарием по-русски.

`merge_presence`: по `device_key` оставить запись с максимальным `last_seen`; отбросить `last_seen < now - WINDOW`;
считать множества `user_key` на (node, inbound) и на (node, inbound, exit).

`build_location_cards` — сопоставление хоста с нодой:

```
candidates = [n for n in nodes if host.config_profile_inbound_uuid in n.active_inbound_uuids]
if len(candidates) > 1: by_addr = [n for n in candidates if n.address.lower() == host.address.lower()]
                        if len(by_addr) == 1: candidates = by_addr
                        else: name = overrides.get(host.address.lower()); candidates = [n for n in candidates if n.name == name]
node = candidates[0] if len(candidates) == 1 else None
```

Число: нода в `reports_at` → `presence.get((node.name, tag))` или 0, `source='counter'`, `reported_at` из отчёта;
иначе если у ноды ровно одна карточка → `node.users_online`, `'panel'`; иначе `None`. Разбивка — по убыванию числа,
имена через `exit_display_name`.

- [ ] **Step 4: Тесты зелёные**

Run: `uv run pytest tests/services/test_location_presence.py tests/external -q && uv run ruff check app tests`
Expected: PASS, ruff чистый

- [ ] **Step 5: Commit**

```bash
git add app/external/remnawave_api.py app/services/location_presence.py app/config.py .env.example tests
git commit -m "feat(locations): сведение устройств между нодами и карточки локаций"
```

---

### Task 3: Эндпоинты кабинета

**Files:**
- Create: `app/cabinet/routes/admin_locations.py`
- Modify: `app/cabinet/routes/__init__.py` (импорт и `include_router` рядом с `admin_stats_router`, стр. 44 и 141)
- Modify: `docs/project_structure_reference.md` (через `make docs-structure`)
- Test: `tests/cabinet/test_admin_locations_http.py`

**Interfaces:**
- Consumes: `devices_from_report`, `merge_presence`, `build_location_cards`, `LocationCard` (Task 2);
  `settings.LOCATION_COUNTER_TOKEN`, `settings.get_location_host_node_overrides()`; `app.utils.cache.cache`;
  `RemnaWaveService().get_api_client()` → `api.get_all_hosts()`, `api.get_all_nodes()`; `require_permission('stats:read')`.
- Produces (контракт с Task 4):
  - `POST /cabinet/admin/stats/locations/report` — тело как в Task 1; 404 при пустом токене, 403 при неверном
    заголовке `X-Location-Counter-Token` (сравнение `hmac.compare_digest`), 422 при битом теле; успех `{"ok": true}`.
    Pydantic: `node` 1–64 символа, `devices` ≤ 5000, `exit` — `str | None`. Хранение — `cache.set(f'location_report:{node}', body, expire=180)`.
  - `GET /cabinet/admin/stats/locations` → `LocationsOverview`:
    `{"window_seconds": 300, "generated_at": iso, "locations": [{"title", "node_name", "inbound_tag", "users",
    "exits": [{"name", "users"}], "source", "reported_at"}]}`; сбой панели → 503 `{"detail": "Failed to load locations"}`.

- [ ] **Step 1: HTTP-тесты** — по образцу `tests/cabinet/test_admin_grace_access_http.py` (настоящий роутер через
  `TestClient`, подмена `require_permission`), `cache` подменить словарём в памяти через `monkeypatch`, панель —
  фейковым `get_api_client` с хостами и нодами из фикстуры Task 2 (вынести её в `tests/fixtures/locations.py`).
  - `test_report_rejected_when_token_unset` → 404
  - `test_report_rejected_with_wrong_token` → 403
  - `test_report_stored_with_ttl` → 200, в кэше ключ `location_report:MSK-4`, `expire == 180`
  - `test_get_merges_reports_from_two_nodes`: отчёты NL-1 (устройство d1 60 с назад на `vless-inbound-sni-github`) и
    MSK-4 (то же d1 10 с назад на `vless-to-de1-ss`) → «Германия» `users == 1`, «Нидерланды» `users == 0`
  - `test_get_returns_503_when_panel_fails` → 503

- [ ] **Step 2: Запустить — падают**

Run: `uv run pytest tests/cabinet/test_admin_locations_http.py -v`
Expected: FAIL (ImportError `admin_locations`)

- [ ] **Step 3: Реализовать** роутер `APIRouter(prefix='/admin/stats/locations', tags=['Cabinet Admin Locations'])`,
  зарегистрировать, `GET` собирает отчёты через `cache.get_keys('location_report:*')` + `cache.get`, `reports_at`
  из их `generated_at`, `now = datetime.now(UTC)`.

- [ ] **Step 4: Пересобрать справку структуры и прогнать тесты**

Run: `make docs-structure && uv run pytest tests/cabinet/test_admin_locations_http.py tests/test_structure_reference_is_current.py -v && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, ruff чистый

- [ ] **Step 5: Полный прогон**

Run: `uv run pytest -q -x`
Expected: всё зелёное; если что-то упало вне новых файлов — проверить тот же тест на `main` (отдельный worktree)
и в отчёте указать, падал ли он до изменений.

Обратить внимание: роут отдаёт `exits` из `LocationCard.exits` (список кортежей) как `[{"name", "users"}]`.

- [ ] **Step 6: Commit**

```bash
git add app/cabinet/routes/admin_locations.py app/cabinet/routes/__init__.py docs/project_structure_reference.md tests
git commit -m "feat(locations): приём отчётов счётчиков и выдача локаций в админку кабинета"
```

---

### Task 4: Блок «Локации» во фронте кабинета

Репозиторий `~/dev/bedolaga-cabinet`, ветка `feat/admin-locations` от `main`.

**Files:**
- Modify: `src/api/admin.ts` — типы рядом с `NodesOverview` (стр. ≈187), метод рядом с `getNodesStatus` (стр. ≈348)
- Modify: `src/pages/AdminDashboard.tsx` — запрос рядом с `statsQuery` (стр. ≈213), секция после «Nodes Section» (стр. ≈348–413)
- Modify: `src/locales/{ru,en,fa,zh}.json` — `adminDashboard.locations`
- Test: существующий `src/locales/locales.test.ts` (паритет ключей)

**Interfaces:**
- Consumes: `GET /cabinet/admin/stats/locations` (Task 3).
- Produces: `interface LocationExit { name: string; users: number }`,
  `interface LocationCard { title: string; node_name: string | null; inbound_tag: string | null; users: number | null; exits: LocationExit[]; source: 'counter' | 'panel' | null; reported_at: string | null }`,
  `interface LocationsOverview { window_seconds: number; generated_at: string; locations: LocationCard[] }`,
  `statsApi.getLocations(): Promise<LocationsOverview>`.

- [ ] **Step 1:** Типы и `getLocations` в `src/api/admin.ts` (путь строго `/cabinet/admin/stats/locations`).
- [ ] **Step 2:** `useQuery({ queryKey: ['admin-dashboard-locations'], queryFn: statsApi.getLocations, refetchInterval: 30_000 })`;
  кнопка «Обновить» дополнительно вызывает его `refetch()`.
- [ ] **Step 3:** Секция в стиле блока «Ноды»: заголовок, строка-пояснение, сетка карточек, «показать ещё / скрыть»
  после трёх (своё состояние `showAllLocations`). Карточка: `title`, мелко `node_name`, крупно `users` («—» при `null`),
  строки `exits` («через FI-2 — 3»), у `source === 'panel'` — мелкая пометка. Ошибка запроса — строка `loadError`
  внутри секции, остальной дашборд не трогается.
- [ ] **Step 4:** Ключи `adminDashboard.locations`: `title` «Локации», `hint` «Активные за 5 минут, каждое устройство —
  на последней локации. Человек с устройствами на разных локациях считается в каждой.», `via` «через {{name}} — {{count}}»,
  `noData` «нет данных», `fromPanel` «по данным панели», `showMore`/`hide` (как у `nodes`), `loadError`
  «Не удалось загрузить локации», `empty` «Локаций нет». en — перевод; fa и zh — английский текст, если в файлах так
  принято для новых ключей (сверить с соседними), иначе перевод.
- [ ] **Step 5: Проверки**

Run: `npm run test && npm run lint && npm run build`
Expected: тесты зелёные (включая `locales.test.ts`), линт чистый, сборка проходит

- [ ] **Step 6: Visual check** — `npm run dev`, страница `/admin/dashboard` с ответом бэкенда из фикстуры
  (через мок `statsApi.getLocations` или локальный бэкенд); скриншот блока на ширине 1280 и 375.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin.ts src/pages/AdminDashboard.tsx src/locales
git commit -m "feat(admin): блок «Локации» на панели статистики"
```

---

### Task 5: Выкатка (только после «да» владельца на каждый шаг)

- [ ] **Step 1:** PR бэкенда → main (автодеплой бота на msk3). Сгенерировать токен (`python3 -c 'import secrets;print(secrets.token_hex(32))'`),
  добавить `LOCATION_COUNTER_TOKEN` и `LOCATION_HOST_NODE_OVERRIDES=msk2.zanity.net=MSK-2` в `.env` на msk3, перезапустить `bot`.
  Проверка: `curl -s -o /dev/null -w '%{http_code}' -X POST https://cabinet.zanity.net/api/cabinet/admin/stats/locations/report` → 403.
- [ ] **Step 2:** Счётчик на msk4 по README. Проверка: `journalctl -u location-counter -n 20` без ошибок отправки; в Redis
  на msk3 появился `location_report:MSK-4`; `GET /cabinet/admin/stats/locations` (под админом) отдаёт числа MSK-4,
  сумма по карточкам MSK-4 близка к онлайну ноды в панели.
- [ ] **Step 3:** Так же nl1, SE-1 (`root@191.44.114.69`), msk1, msk2. Проверка: у всех десяти карточек `source == 'counter'`;
  `top`/`systemctl status` — процесс < 1 % CPU, память < 50 МБ.
- [ ] **Step 4:** Фронт: push ветки, PR в `haku4130/bedolaga-cabinet`, после мёржа — ручной деплой
  `ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose pull cabinet-frontend && docker compose up -d cabinet-frontend'`;
  сверить образ (`docker inspect cabinet_frontend --format "{{.Image}}"`) и открыть `/admin/dashboard`.
- [ ] **Step 5:** Обновить память (новая заметка про счётчики локаций: где стоят, токен в `/etc/location-counter.env`, откат).
