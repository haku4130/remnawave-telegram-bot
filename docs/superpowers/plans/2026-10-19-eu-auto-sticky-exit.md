# AUTO Европа без смены выхода посреди сеанса — план внедрения

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** У Happ/INCY на «🇪🇺 [AUTO] Европа» заблокированный трафик идёт через один выход всё подключение. Выход выбирается на устройстве один раз при подключении, а msk4 подстраховывает его, если выход умер.

**Architecture:** На msk4 шесть новых VLESS-входов `vless-auto-<exit>`. За каждым стоит одно-кандидатный балансер «выход X, при смерти — запасной». Наблюдатель профиля MSK-4 переходит на `burstObservatory`. В панели под каждый вход заводится скрытый хост. Новый XRAY_JSON-шаблон (`injectHosts` + `observatory` с интервалом 168h + балансер) назначается хосту AUTO. Шаблон рендерит и синхронизирует уже работающий `json_routing.py`. Счётчик локаций отбрасывает соединения зонда, а бэкенд сводит новые входы в карточку AUTO.

**Tech Stack:** Python 3.12 stdlib на msk3 (скрипты в `/root/fi2-migration/`, тесты `unittest`), Remnawave 3.3.2 API, Xray 26.7.28 на msk4, FastAPI-бэкенд бота (pytest), счётчик `scripts/location_counter/` (Python ≥3.10, stdlib).

**Spec:** `docs/superpowers/specs/2026-10-19-eu-auto-sticky-exit-design.md`

## Global Constraints

- Любое действие, меняющее прод (профиль, ноды, сквады, хосты, шаблоны, ufw, `.env`, PR/merge), — только после явного «да» владельца на **этот** шаг. Чтение — через скилл `zanity-ops` (`paneldb`/`panelapi`/`botdb`).
- Правка профиля MSK-4 перезапускает xray и рвёт соединения всех на msk4: только ночью, 00:00–03:00 UTC, при онлайне msk4 ≤ 10.
- Перед каждой записью в панель — JSON-бэкап в `/root/fi2-migration/` (`*-backup-YYYYMMDD-HHMMSS.json`).
- API-ключ — из `.env` бота на msk3 (`REMNAWAVE_API_URL`, `REMNAWAVE_API_KEY`). Секреты, ключи Reality и `config_profiles.config` целиком не печатать.
- Выходы и порты: DE1 → 3452, FI1 → 3453, FI2 → 3454, NL1 → 3455, SE1 → 3456, UK1 → 3457; тестовый вход `vless-auto-test` → 3458. Теги входов — `vless-auto-de1` … `vless-auto-uk1`.
- Запасные: DE1→NL1, NL1→DE1, FI1→FI2, FI2→FI1, SE1→FI2, UK1→NL1.
- Gemini на портах AUTO: DE1→FI1, FI1→FI2, FI2→FI1, UK1→DE1 (свой выход порта, запасной из набора Gemini); порты NL1 и SE1 → FI2 с запасным FI1.
- Зонд: `https://cp.cloudflare.com/generate_204`, правило на msk4 — `full:cp.cloudflare.com`.
- `burstObservatory` MSK-4: `subjectSelector: ["SS_OUTBOUND_"]`, `pingConfig: {destination: "https://www.gstatic.com/generate_204", interval: "15s", sampling: 4, timeout: "5s"}`; ключ `observatory` из профиля удаляется.
- Клиентский `observatory`: `probeInterval: "168h"` (строка, **не** `"7d"`), `enableConcurrency: true` обязателен.
- Шаблон XRAY_JSON: имя `EU AUTO balancer`. Скрытые хосты: remark `EU AUTO · <EXIT>` (например `EU AUTO · FI1`), `isHidden: true`. Тестовый виртуальный хост: `🧪 AUTO Европа (тест)`.
- Не трогать: `vless-to-eu-auto` (`:3445`), `SS_EU_BALANCER`, правила и входы фиксированных локаций, ключи Reality.

## Review Focus

1. **Устройство подключается, а в логе msk4 его соединения размазаны по нескольким `vless-auto-*`** — так быть не должно (кроме зонда и первых секунд). Проверка: Task 7, шаг 5 (по логу на тестовом устройстве) и Task 9, шаг 3.
2. **Выход умер** — люди его порта через 1–2 мин уходят на запасной, остальные порты не дёргаются. Одиночный таймаут никого не переключает. Проверка: Task 6, шаг 6 (блок выхода на msk4 через `iptables` на 20 с и на 3 мин, тестовый клиент на msk3).
3. **Клиент получил конфиг, который не понял** — остальные записи подписки у него не должны сломаться. Проверка: Task 7, шаг 4 (все записи подписки на каждом тестовом устройстве).
4. **Трафик, не попавший ни в одно клиентское правило** (например Инстаграм), идёт через балансер, а не в первый injected outbound. Тест: Task 4, `test_auto_template_catch_all_is_ip_rule_last`; на устройстве — Task 7, шаг 5.
5. **Фиксированные локации и `:3445` после перехода на burst** работают как раньше. Проверка: Task 6, шаг 5 (по одному подключению к `:3442`, `:3443`, `:3445`, `:3446`, `:3447` и их выходы в логе).

---

### Task 1: Счётчик локаций не учитывает соединения зонда

**Files:**
- Modify: `scripts/location_counter/location_counter.py` (`_LINE_RE`, `LogEvent`, `parse_line`, `_read_log`)
- Test: `tests/scripts/test_location_counter.py`

**Interfaces:**
- Produces: `PROBE_HOSTS: frozenset[str] = frozenset({'cp.cloudflare.com'})`; `LogEvent.host: str` — хост назначения без `tcp:`/`udp:` и порта (IPv6 — без скобок); `is_probe(event: LogEvent) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_line_captures_destination_host():
    e = parse_line('2026/10/19 15:17:39.217281 from 109.252.162.151:1230 accepted tcp:cp.cloudflare.com:443 '
                   '[vless-auto-fi1 -> SS_OUTBOUND_FI1] email: 141')
    assert e.host == 'cp.cloudflare.com'
    e6 = parse_line('2026/10/19 15:17:39 from 1.2.3.4:5 accepted udp:[2a00:1450::1]:443 [a -> direct] email: 7')
    assert e6.host == '2a00:1450::1'

def test_probe_connections_are_ignored():
    probe = parse_line('2026/10/19 15:17:39 from 1.2.3.4:5 accepted tcp:cp.cloudflare.com:443 '
                       '[vless-auto-de1 -> SS_OUTBOUND_DE1] email: 141')
    real = parse_line('2026/10/19 15:17:40 from 1.2.3.4:5 accepted tcp:instagram.com:443 '
                      '[vless-auto-fi1 -> SS_OUTBOUND_FI1] email: 141')
    assert is_probe(probe) and not is_probe(real)
```

Плюс у `test_log_reader_keeps_stdin_pipe_open` (или нового теста рядом с ним, через тот же monkeypatch `subprocess.Popen`) — строка зонда в потоке не попадает в `tracker.events_seen`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scripts/test_location_counter.py -q`
Expected: FAIL (`LogEvent` has no `host`, `is_probe` not defined).

- [ ] **Step 3: Implement**

В `_LINE_RE` заменить `accepted \S+` на захват назначения: `(?:tcp|udp):` + (`\[ipv6\]` | хост без двоеточий) + `:порт`. В `_read_log` пропускать `is_probe(event)` до `tracker.observe`. Существующие тесты не меняются.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scripts/test_location_counter.py -q && uv run ruff check scripts/location_counter tests/scripts`
Expected: all passed, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/location_counter/location_counter.py tests/scripts/test_location_counter.py
git commit -m "feat(locations): счётчик не учитывает соединения зонда AUTO"
```

---

### Task 2: Бэкенд сводит входы `vless-auto-*` в карточку AUTO

**Files:**
- Modify: `app/config.py` (рядом с `LOCATION_EXIT_BREAKDOWN_INBOUNDS` и `get_location_exit_breakdown_inbounds`)
- Modify: `app/services/location_presence.py` (`merge_presence`)
- Modify: `app/cabinet/routes/admin_locations.py:167` (передать алиасы)
- Modify: `.env.example` (описание новой переменной рядом с `LOCATION_*`)
- Test: `tests/services/test_location_presence.py`

**Interfaces:**
- Produces: `Settings.LOCATION_INBOUND_ALIASES: str = ''` — формат `src=dst,src2=dst` (как у `LOCATION_HOST_NODE_OVERRIDES`, но теги регистрозависимы); `Settings.get_location_inbound_aliases() -> dict[str, str]`; `merge_presence(devices: list[Device], now: datetime, inbound_aliases: Mapping[str, str] | None = None) -> dict[tuple[str, str], Presence]` — inbound устройства заменяется по алиасу **до** группировки, разбивка по выходам сохраняется.

- [ ] **Step 1: Write the failing tests**

```python
def test_location_inbound_aliases_parsing():
    parsed = Settings(BOT_TOKEN='t', LOCATION_INBOUND_ALIASES=' vless-auto-de1 = vless-to-eu-auto ,bad,,x=y')
    assert parsed.get_location_inbound_aliases() == {'vless-auto-de1': 'vless-to-eu-auto', 'x': 'y'}
    assert Settings(BOT_TOKEN='t').get_location_inbound_aliases() == {}

def test_aliased_inbounds_merge_into_one_card_with_exits():
    aliases = {'vless-auto-fi1': TAG_AUTO, 'vless-auto-se1': TAG_AUTO}
    p = merge_presence(
        [
            dev('u1', 'd1', 5, 'MSK-4', 'vless-auto-fi1', 'SS_OUTBOUND_FI1'),
            dev('u1', 'd2', 5, 'MSK-4', 'vless-auto-se1', 'SS_OUTBOUND_SE1'),
            dev('u2', 'd3', 5, 'MSK-4', TAG_AUTO, 'SS_OUTBOUND_FI2'),
        ],
        NOW,
        aliases,
    )
    assert p == {('MSK-4', TAG_AUTO): Presence(2, {'SS_OUTBOUND_FI1': 1, 'SS_OUTBOUND_SE1': 1, 'SS_OUTBOUND_FI2': 1})}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/services/test_location_presence.py -q`
Expected: FAIL (`get_location_inbound_aliases` missing; `merge_presence` takes 2 positional args).

- [ ] **Step 3: Implement** the setting, the parser and the `inbound_aliases` parameter. The route passes `settings.get_location_inbound_aliases()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/services/test_location_presence.py tests/scripts -q && uv run ruff check app tests`
Expected: all passed, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/services/location_presence.py app/cabinet/routes/admin_locations.py .env.example tests/services/test_location_presence.py
git commit -m "feat(locations): алиасы входов — порты AUTO в одной карточке"
```

---

### Task 3: Скрипт правки профиля MSK-4 `eu_auto_sticky.py`

Разработка — в рабочей папке вне репозитория (скретчпад сессии), затем `scp` на msk3 в `/root/fi2-migration/`. Стиль — как у `selfsteal_profile.py`: чистые функции + `unittest`, без `--apply` только печать.

**Files (msk3):**
- Create: `/root/fi2-migration/eu_auto_sticky.py`
- Create: `/root/fi2-migration/test_eu_auto_sticky.py`

**Interfaces:**
- Produces:
  - константы из Global Constraints: `EXITS`, `PORTS`, `BACKUP`, `GEMINI_TARGET` (порт → выход Gemini), `GEMINI_BACKUP`, `PROBE_DOMAIN`, `BURST`, `TEST_TAG = 'vless-auto-test'`, `TEST_PORT = 3458`;
  - `inbound_tag(exit: str) -> str` (`'FI1'` → `'vless-auto-fi1'`);
  - `add_auto(cfg: dict) -> list[str]` — мутирует профиль, возвращает описания изменений, повторный вызов → `[]`;
  - `use_burst(cfg: dict) -> bool` — `observatory` → `burstObservatory` из `BURST`, повторно → `False`;
  - CLI `eu_auto_sticky.py profile [--apply]`: GET профиля `MSK-4`, `add_auto` + `use_burst`, бэкап, PATCH `/api/config-profiles`;
  - CLI `eu_auto_sticky.py panel [--apply]` (только API-склейка, без юнит-тестов): UUID семи новых входов из профиля → `activeInbounds` ноды MSK-4 (PATCH `/api/nodes`), затем `POST /api/nodes/<uuid>/actions/restart` с `{"uuid": …, "forceRestart": true}`; шесть `vless-auto-*` — во все сквады, где есть `vless-to-eu-auto`; `vless-auto-test` — только в `Testers`; бэкап нод и сквадов;
  - CLI `eu_auto_sticky.py hosts [--apply]` (API-склейка): шесть скрытых хостов `EU AUTO · <X>` и видимый `🧪 AUTO Европа (тест)` на `vless-auto-test`, поля безопасности — копия хоста `🇪🇺 [AUTO] Европа`; идемпотентно по remark; бэкап хостов.

Что делает `add_auto` (порядок важен):
- 7 входов: копия `vless-to-eu-auto` (`copy.deepcopy`, Reality и `sniffing` как есть) с новыми `tag`/`port` — шесть `vless-auto-*` и `vless-auto-test`;
- балансеры `AUTO_<X>` (`selector: ["SS_OUTBOUND_<X>"]`, `leastPing`, `fallbackTag: "SS_OUTBOUND_<BACKUP[X]>"`) для шести выходов и `AUTO_GEM_<X>` для DE1/FI1/FI2/UK1 (`fallbackTag` по `GEMINI_BACKUP`);
- перед общим правилом с `balancerTag: GEMINI_BALANCER`: по правилу на порт — `geosite:google-gemini` + `inboundTag: [T]` → `balancerTag: "AUTO_GEM_<GEMINI_TARGET[X]>"`;
- сразу после последнего правила с `inboundTag == ["vless-to-eu-auto"]`, для каждого порта по порядку `EXITS`: `full:cp.cloudflare.com` → `AUTO_X`; `geosite:youtube` → `direct`; `ext:geosite-ru-blocked.dat:ru-blocked` → `AUTO_X`; `ext:geoip-ru-blocked.dat:ru-blocked` (ip) → `AUTO_X`; без условий → `direct`. Для `vless-auto-test` — одно правило → `block`.

- [ ] **Step 1: Write the failing tests** (`test_eu_auto_sticky.py`, фикстура — урезанный профиль: вход `vless-to-eu-auto` с фиктивными Reality-ключами `K`, общие правила 1–5 и группа `vless-to-eu-auto` из спеки, `observatory` как на проде)

```python
def test_adds_seven_inbounds_copied_from_auto(self):
    cfg = profile(); add_auto(cfg)
    by_tag = {i['tag']: i for i in cfg['inbounds']}
    self.assertEqual(by_tag['vless-auto-fi1']['port'], 3453)
    self.assertEqual(by_tag['vless-auto-test']['port'], 3458)
    self.assertEqual(by_tag['vless-auto-uk1']['streamSettings'], by_tag['vless-to-eu-auto']['streamSettings'])

def test_balancer_has_one_candidate_and_backup(self):
    cfg = profile(); add_auto(cfg)
    b = {x['tag']: x for x in cfg['routing']['balancers']}
    self.assertEqual(b['AUTO_SE1'], {'tag': 'AUTO_SE1', 'selector': ['SS_OUTBOUND_SE1'],
                                     'strategy': {'type': 'leastPing'}, 'fallbackTag': 'SS_OUTBOUND_FI2'})
    self.assertEqual(b['AUTO_GEM_UK1']['fallbackTag'], 'SS_OUTBOUND_DE1')

def test_probe_rule_first_in_port_group_and_after_blocks(self):
    rules = add_and_get_rules()
    i = index_of(rules, inbound='vless-auto-de1', domain=['full:cp.cloudflare.com'])
    self.assertEqual(rules[i]['balancerTag'], 'AUTO_DE1')
    self.assertGreater(i, index_of(rules, domain=['geosite:private']))
    self.assertEqual(rules[i + 1]['domain'], ['geosite:youtube'])

def test_gemini_on_ports_precedes_general_gemini_rule(self):
    rules = add_and_get_rules()
    general = index_of(rules, balancer='GEMINI_BALANCER')
    se1 = index_of(rules, inbound='vless-auto-se1', domain=['geosite:google-gemini'])
    self.assertLess(se1, general)
    self.assertEqual(rules[se1]['balancerTag'], 'AUTO_GEM_FI2')

def test_existing_rules_untouched_and_idempotent(self):
    cfg = profile(); before = [r for r in cfg['routing']['rules']]
    add_auto(cfg)
    self.assertEqual([r for r in cfg['routing']['rules'] if r in before], before)
    self.assertEqual(add_auto(cfg), [])

def test_burst_replaces_observatory(self):
    cfg = profile()
    self.assertTrue(use_burst(cfg))
    self.assertNotIn('observatory', cfg)
    self.assertEqual(cfg['burstObservatory']['pingConfig'],
                     {'destination': 'https://www.gstatic.com/generate_204', 'interval': '15s',
                      'sampling': 4, 'timeout': '5s'})
    self.assertFalse(use_burst(cfg))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_eu_auto_sticky -v` (в рабочей папке)
Expected: FAIL (`ModuleNotFoundError: eu_auto_sticky`).

- [ ] **Step 3: Implement `eu_auto_sticky.py`** (функции из Interfaces; `_call` и бэкап — как в `selfsteal_profile.py`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_eu_auto_sticky -v`
Expected: OK.

- [ ] **Step 5: Сухой прогон на живом профиле** (только чтение)

Скопировать оба файла на msk3, затем: `ssh root@msk3.zanity.net 'cd /root/fi2-migration && set -a && . /root/remnawave-bedolaga-telegram-bot/.env && set +a && python3 -m unittest test_eu_auto_sticky && python3 eu_auto_sticky.py profile'` (путь к `.env` бота сверить с `json_routing_sync.sh`).
Expected: тесты OK; печать «входов +7, балансеров +10, правил +…, observatory → burst», `DRY RUN`.

---

### Task 4: Шаблон `EU AUTO balancer` в `json_routing.py`

`json_routing.py` крутится по cron каждые 5 минут (`sync`) — правка должна быть обратно совместимой, а `sync` не должен создавать шаблон, которого ещё нет.

**Files (msk3):**
- Modify: `/root/fi2-migration/json_routing.py` (бэкап `json_routing.py.bak-YYYYMMDD` перед заменой)
- Create: `/root/fi2-migration/test_json_routing.py`

**Interfaces:**
- Consumes: `split_template(p)` (существующая).
- Produces:
  - `AUTO_NAME = 'EU AUTO balancer'`, `AUTO_HOST_REMARK = '🇪🇺 [AUTO] Европа'`, `TEST_HOST_REMARK = '🧪 AUTO Европа (тест)'`, `INJECT_PATTERN = '^EU AUTO · '`;
  - `auto_template(p) -> dict` — копия `split_template(p)`, где каждое `outboundTag: 'proxy'` заменено на `balancerTag: 'EU_AUTO'`; последним правилом `{'type': 'field', 'ip': ['0.0.0.0/0', '::/0'], 'balancerTag': 'EU_AUTO'}`; плюс `remnawave: {injectHosts: [{selector: {type: 'remarkRegex', pattern: INJECT_PATTERN}, selectFrom: 'HIDDEN', tagPrefix: 'eu'}], addVirtualHostAsOutbound: False}`, `observatory: {subjectSelector: ['eu'], probeUrl: 'https://cp.cloudflare.com/generate_204', probeInterval: '168h', enableConcurrency: True}`, `routing.balancers: [{tag: 'EU_AUTO', selector: ['eu'], strategy: {type: 'leastPing'}, fallbackTag: 'eu'}]`;
  - `sync` дополнительно синхронизирует `AUTO_NAME`, **только если** такой шаблон уже есть;
  - новые режимы CLI: `auto-create` (создать/обновить шаблон), `auto-assign <remark>` и `auto-unassign <remark>` (PATCH `xrayJsonTemplateUuid` хоста по точному remark, с бэкапом хостов).

- [ ] **Step 1: Write the failing tests** (`test_json_routing.py`, фикстура `p` — минимальный профиль RoscomVPN: `RemoteDNSIP`, `DomesticDNSIP`, `RemoteDNSDomain`, `DomesticDNSDomain`, `ProxySites`, `DirectSites`, `DirectIp`, `BlockSites`, `GlobalProxy: 'true'`)

```python
def test_auto_template_has_no_proxy_outbound_tags(self):
    t = auto_template(P)
    self.assertFalse([r for r in t['routing']['rules'] if r.get('outboundTag') == 'proxy'])
    proxied = [r for r in t['routing']['rules'] if r.get('domain') == P['ProxySites']]
    self.assertEqual(proxied[0]['balancerTag'], 'EU_AUTO')

def test_auto_template_catch_all_is_ip_rule_last(self):
    last = auto_template(P)['routing']['rules'][-1]
    self.assertEqual(last, {'type': 'field', 'ip': ['0.0.0.0/0', '::/0'], 'balancerTag': 'EU_AUTO'})

def test_auto_template_observatory_and_inject(self):
    t = auto_template(P)
    self.assertEqual(t['observatory']['probeInterval'], '168h')
    self.assertTrue(t['observatory']['enableConcurrency'])
    self.assertEqual(t['remnawave']['injectHosts'][0]['selector'], {'type': 'remarkRegex', 'pattern': '^EU AUTO · '})
    self.assertEqual(t['routing']['balancers'][0]['fallbackTag'], 'eu')

def test_split_template_unchanged(self):
    self.assertEqual(split_template(P), SPLIT_SNAPSHOT)  # снимок из текущего render до правки
```

`SPLIT_SNAPSHOT` получить **до** правки: `python3 json_routing.py render <файл с заголовком>` на текущей версии.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_json_routing -v`
Expected: FAIL (`auto_template` not defined); `test_split_template_unchanged` — PASS.

- [ ] **Step 3: Implement** `auto_template`, расширение `sync`, режимы `auto-create`/`auto-assign`/`auto-unassign`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_json_routing -v`
Expected: OK.

- [ ] **Step 5: Выкатить файл на msk3** (с «да» владельца): бэкап старого, `scp`, затем прогнать cron-команду вручную: `bash /root/fi2-migration/json_routing_sync.sh; tail -3 /var/log/json_routing_sync.log`.
Expected: никаких `updated` (шаблона AUTO ещё нет, остальные не изменились), код выхода 0.

---

### Task 5: Счётчик и бэкенд в прод

- [ ] **Step 1: PR** из ветки `claude/europa-auto-exit-node-change-506f39` в `main` с Task 1–2 (и этими документами). Merge — с «да» владельца; бот выкатывается сам при пуше в `main`.
- [ ] **Step 2: `.env` на msk3** (бэкап `.env.bak-eu-auto-YYYYMMDD`): `LOCATION_INBOUND_ALIASES=vless-auto-de1=vless-to-eu-auto,vless-auto-fi1=vless-to-eu-auto,vless-auto-fi2=vless-to-eu-auto,vless-auto-nl1=vless-to-eu-auto,vless-auto-se1=vless-to-eu-auto,vless-auto-uk1=vless-to-eu-auto`, перезапуск контейнера бота тем же способом, что при прошлых правках `.env`.
- [ ] **Step 3: Новый счётчик на msk4** по `scripts/location_counter/README.md` (остальные ноды — по желанию, у них `vless-auto-*` нет).
Run: `ssh root@msk4.zanity.net 'systemctl is-active location-counter; docker exec remnanode sh -c "grep -la ^tail /proc/[0-9]*/cmdline | wc -l"'`
Expected: `active`, `1`.
- [ ] **Step 4: Карточка AUTO** на `/admin/dashboard` по-прежнему показывает людей и разбивку (новых портов ещё нет — регресса быть не должно).

---

### Task 6: Ночная правка msk4

С «да» владельца, 00:00–03:00 UTC, онлайн msk4 ≤ 10 (`panelapi /api/nodes` — `usersOnline` у MSK-4).

- [ ] **Step 1: Базовая линия.** Сохранить в скретчпад: последние 2000 строк `vless-to-eu-auto`/`vless-to-de1-ss`/`vless-ru-to-fi1` из лога msk4 (`docker exec remnanode tail`), `ufw status numbered`.
- [ ] **Step 2: ufw.** `ssh root@msk4.zanity.net 'ufw allow 3452:3458/tcp && ufw status | grep 3452'`
Expected: правило `3452:3458/tcp ALLOW` (v4 и v6).
- [ ] **Step 3: Профиль.** `python3 eu_auto_sticky.py profile --apply` на msk3. Expected: `backup: …`, `PATCH ok: MSK-4`.
- [ ] **Step 4: Ноды и сквады.** `python3 eu_auto_sticky.py panel --apply` на msk3. Сквады с `vless-to-eu-auto` сейчас — только `Default-Squad`.
Run после: `for p in 3452 3453 3454 3455 3456 3457 3458; do timeout 3 bash -c "</dev/tcp/msk4.zanity.net/$p" && echo $p ok; done` — **с msk3, не с msk4**.
Expected: семь `ok`.
- [ ] **Step 5: Регресс фиксированных локаций и `:3445`.** На msk3 есть `xray`/xray-checker с socks-портами `127.0.0.1:10000–10016`: `curl -s -m 10 -x socks5h://127.0.0.1:<порт> https://archive.torproject.org/ -o /dev/null -w "%{http_code}\n"` для портов локаций Германия, Великобритания, AUTO, Финляндия #1/#2, затем по логу msk4 — каждое ушло в свой `SS_OUTBOUND_*`.
Expected: `200` и выходы как в базовой линии.
- [ ] **Step 6: Подстраховка.** Тестовый клиент: временный контейнер `ghcr.io/xtls/xray-core:26.7.28` на msk3 с socks `127.0.0.1:19080` и outbound VLESS Reality на `msk4.zanity.net:3453` от имени владельца (user id 3; UUID и Reality-параметры подставляет скрипт из `panelapi`/профиля, в вывод не печатает; с «да» владельца). Запрос раз в 5 с: `curl -s -m 8 -x socks5h://127.0.0.1:19080 https://archive.torproject.org/ -o /dev/null`. На msk4 отрезать FI1: `iptables -I OUTPUT -d <IP fi1> -p tcp --dport 9998 -j DROP`.
  - Через 20 с снять (`iptables -D …`). В логе msk4 у `vless-auto-fi1` ни одного `SS_OUTBOUND_FI2` (одиночный сбой не дёргает).
  - Повторить и держать 3 мин: в пределах ~2 мин запросы через `vless-auto-fi1` уходят в `SS_OUTBOUND_FI2`. После снятия правила — в пределах ~1 мин обратно в FI1. Другие `vless-auto-*` не меняют выход.
  Expected: оба пункта выполнены; `iptables -S OUTPUT | grep 9998` пусто; контейнер удалён.
- [ ] **Step 7: Зонд через порт.** Запрос на `https://cp.cloudflare.com/generate_204` через `vless-auto-se1`: в логе `[vless-auto-se1 -> SS_OUTBOUND_SE1]`. YouTube через тот же порт → `direct`.

---

### Task 7: Скрытые хосты, шаблон и тест на устройствах владельца

С «да» владельца. Днём можно: правки хостов и шаблонов не перезапускают ноды.

- [ ] **Step 1: Хосты.** `python3 eu_auto_sticky.py hosts --apply` на msk3. Expected: создано 7 хостов; повторный запуск — 0.
- [ ] **Step 2: Шаблон.** `python3 json_routing.py auto-create`, затем `python3 json_routing.py auto-assign '🧪 AUTO Европа (тест)'`.
- [ ] **Step 3: Проверка ответа подписки** для владельца (user id 3, `short_uuid` — через `paneldb`) по способу из заметки о клиентском роутинге: `curl` на msk2 `http://127.0.0.1:3000/api/sub/<short_uuid>` с `User-Agent: Happ/4.7.1`, `X-Forwarded-For: 127.0.0.1`, `X-Forwarded-Proto: https`, **реальным** `x-hwid` одного из устройств владельца (не выдумывать). В ответе — конфиг с `remarks` `🧪 AUTO Европа (тест)`, шестью outbound'ами `eu…eu-6` на порты 3452–3457, `observatory.probeInterval == "168h"`, балансером `EU_AUTO`. Записи `🇪🇺 [AUTO] Европа` — без балансера (как раньше). У пользователя не из Testers (любой из Default-Squad) теста в подписке нет.
- [ ] **Step 4: Устройства владельца** (делает владелец, мы смотрим лог): Happ Android, INCY Android, iPhone 12 INCY 2.4.3; Happ iOS — если владелец добавит Ксюшу (user 5) в `Testers` на время теста. На каждом: обновить подписку, подключиться к «🧪 AUTO Европа (тест)», 10–15 минут рилсы в Инстаграме. Все остальные записи подписки на устройстве открываются как раньше.
- [ ] **Step 5: Лог msk4** за время теста по `email: 3`: на один IP устройства — соединения зонда ко всем шести портам в первые секунды, дальше **один** `vless-auto-*` для всего заблокированного трафика (Инстаграм тоже); раз в 10 с зондов нет (значит, клиент не потерял `probeInterval`). Переподключение — новый выбор (может совпасть).
- [ ] **Step 6: Смена сети.** На Android переключить Wi-Fi ↔ мобильную сеть; по логу — сменился ли `vless-auto-*`. Записать результат в заметку (это не ошибка, а факт о клиенте).

Стоп-условие: если хоть одно устройство не принимает конфиг или ломает другие записи — `auto-unassign` с тестового хоста и вернуться к владельцу.

---

### Task 8: Включить для всех

С «да» владельца по итогам Task 7.

- [ ] **Step 1:** `python3 json_routing.py auto-assign '🇪🇺 [AUTO] Европа'`.
- [ ] **Step 2:** Тестовый хост `🧪 AUTO Европа (тест)` удалить (или `isDisabled`). Вход `vless-auto-test` и его правило `block` остаются, в сквадах — только `Testers`.
- [ ] **Step 3:** Ответ подписки владельца (как в Task 7, шаг 3): у записи `🇪🇺 [AUTO] Европа` — балансер и шесть `eu*`. Ответ с UA `v2rayTun/…` — base64, AUTO ведёт на `:3445`.
- [ ] **Step 4:** `bash json_routing_sync.sh` вручную — `updated` не печатает (шаблон уже совпадает с рендером).

**Откат:** `python3 json_routing.py auto-unassign '🇪🇺 [AUTO] Европа'` — после обновления подписки Happ/INCY снова идут на `:3445`.

---

### Task 9: Проверка через сутки и заметки

- [ ] **Step 1:** Карточка AUTO в кабинете: люди и разбивка по шести выходам, сумма ≈ людям AUTO до изменения.
- [ ] **Step 2:** Лог msk4 за последний час: доля людей, у которых в пределах непрерывной активности пары «email + IP» больше одного `vless-auto-*` (без учёта `cp.cloudflare.com` и первых 10 с после первой строки пары).
Expected: близко к нулю. Отклонения разобрать: смена сети, переподключение, подстраховка.
- [ ] **Step 3:** Сколько людей Happ/INCY ещё ходит на `:3445` (те, кто не обновил подписку), — для понимания хвоста.
- [ ] **Step 4: Память.** Обновить `eu-exit-routing-and-de2-quota.md` (AUTO теперь клиентский, порты 3452–3458, burst на MSK-4, откат), `russia-locations-client-routing.md` (третий шаблон в `json_routing.py`, режимы `auto-*`), `location-counters.md` (алиасы, фильтр зонда). Строку в `MEMORY.md`.
