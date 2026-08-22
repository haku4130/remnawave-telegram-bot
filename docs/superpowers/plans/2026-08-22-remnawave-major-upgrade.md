# Мажорное обновление Remnawave 2.7.4 → 3.3.2 — план выполнения

> **Для агентов:** этот план исполняется по одной задаче за раз. Шаги помечены
> чекбоксами (`- [ ]`). Это ops-раннбук, а не код: вместо TDD-цикла каждая
> задача заканчивается **гейтом** — командой проверки и явным ожидаемым
> результатом. Если гейт не сошёлся — СТОП, не переходить к следующей задаче.

**Цель:** перевести панель, 8 нод, бота и кабинет на ветку Remnawave 3.x,
не потеряв данные и уложив простой бота в один час.

**Архитектура:** две фазы. Фаза 0 (день) — мерджи форков, сборка образов,
правка тегов, предгрев, обновление скрипта бэкапа. Фаза 1 (ночное окно) —
только `up -d` и проверки, в жёстком порядке панель → ноды → sub-page → бот →
бэкфил → кабинет → routing-updater.

**Спека:** `docs/superpowers/specs/2026-08-22-remnawave-major-upgrade-design.md`

## Глобальные ограничения

- Целевые версии: панель **3.3.2**, ноды **3.3.2**, subscription-page **8.0.0**,
  бот **v4.1.0**, кабинет **v1.66.0**, скрипт бэкапа **4.0.0**.
- Ноды: `nl1 nl2 de1 de2 fi1 msk1 msk2 msk4`, все в `/opt/remnanode`.
  **de1 использует образ `ghcr.io/remnawave/node`**, остальные — `remnawave/node`.
- Панель: msk2, `/opt/remnawave`, БД `postgres`/`postgres` в контейнере `remnawave-db`.
- subscription-page: msk2, **отдельный** compose-проект `/opt/remnawave/subscription`.
- Бот и кабинет: msk3, `/root/remnawave-bot`, БД `remnawave_bot`/`remnawave_user`
  в контейнере `remnawave_bot_db`. Сервисы: `bot`, `cabinet-frontend`.
- routing-updater: msk3, тот же compose-проект `remnawave-bot`.
- `xray-checker` не трогать — панель он не использует.
- В Фазе 0 команда `up` не выполняется нигде. Только `pull`.
- Дампы Фазы 1 снимаются **строго при остановленном боте** — иначе БД панели и
  бота разъедутся и совместный откат станет невозможен.

---

## ФАЗА 0 — подготовка (день, без простоя)

### Task 1: Мердж форка бота v3.64.0 → v4.1.0

**Файлы:** `~/dev/remnawave-telegram-bot`, ветка `main`.
Ожидаемые конфликты: `docker-compose.yml`, `.env.example`, `Dockerfile`,
`.github/workflows/ci.yml`, места с Telegram send-retry и `PROXY_URL`/`TELEGRAM_API_URL`.

- [ ] **Шаг 1: Убедиться, что дерево чистое и есть свежий upstream**

```bash
cd ~/dev/remnawave-telegram-bot && git status --porcelain && git fetch upstream --tags
```

Ожидается: пустой вывод `status` (кроме уже закоммиченных спеки и плана).

- [ ] **Шаг 2: Слить релизный тег**

```bash
cd ~/dev/remnawave-telegram-bot && git merge v4.1.0 --no-ff -m "Merge upstream v4.1.0 into fork main"
```

- [ ] **Шаг 3: Разрешить конфликты, сохранив кастомизации форка**

Правило: код приложения берём из upstream, deploy glue — свой. Конкретно
сохранить: `TELEGRAM_API_URL` и `PROXY_URL` в `app/config.py` и
`app/bot_factory.py`, Telegram send-retry, кастомный логотип, свои
`docker-compose.yml` / `ci.yml` / `Dockerfile`.

```bash
cd ~/dev/remnawave-telegram-bot && git diff --name-only --diff-filter=U
```

- [ ] **Шаг 4: Переименовать переменную в `.env.example`**

`TRAFFIC_EXCLUDED_USER_UUIDS` → `TRAFFIC_EXCLUDED_USER_IDS`.

```bash
cd ~/dev/remnawave-telegram-bot && grep -rn "TRAFFIC_EXCLUDED_USER" .env.example app/config.py
```

- [ ] **Шаг 5: Гейт — миграция 0104 на месте, бэкфил-скрипт на месте**

```bash
cd ~/dev/remnawave-telegram-bot && ls migrations/alembic/versions/0104_remnawave_numeric_id.py scripts/backfill_remnawave_ids.py
```

Ожидается: оба файла существуют.

- [ ] **Шаг 6: Закоммитить мердж и запушить вместе со спекой и планом**

```bash
cd ~/dev/remnawave-telegram-bot && git commit --no-edit && git push origin main
```

- [ ] **Шаг 7: Гейт — CI собрал и запушил образ**

```bash
gh run list --repo haku4130/remnawave-telegram-bot --limit 3
```

Дождаться завершения: `gh run watch <id> --exit-status`.
Ожидается: статус `success`. При провале — `gh run view <id> --log-failed`.

---

### Task 2: Мердж форка кабинета v1.61.0 → v1.66.0

**Файлы:** `~/dev/bedolaga-cabinet`, ветка `main`.
Традиционный конфликт: `src/pages/AdminDashboard.tsx`.

- [ ] **Шаг 1: Слить релизный тег**

```bash
cd ~/dev/bedolaga-cabinet && git status --porcelain && git fetch upstream --tags && git merge v1.66.0 --no-ff -m "Merge upstream v1.66.0 into fork main"
```

- [ ] **Шаг 2: Разрешить конфликт в AdminDashboard.tsx**

Взять рефакторенный upstream-вариант `StatCard` и заново наложить защитные
`stats?.subscriptions?.` — без них дашборд падает, когда статистика ещё не
пришла. Сохранить свой `.github/workflows/docker.yml`.

```bash
cd ~/dev/bedolaga-cabinet && git diff --name-only --diff-filter=U
```

- [ ] **Шаг 3: Гейт — опциональные цепочки на месте**

```bash
cd ~/dev/bedolaga-cabinet && grep -n "stats?.subscriptions?." src/pages/AdminDashboard.tsx
```

Ожидается: непустой вывод.

- [ ] **Шаг 4: Закоммитить и запушить**

```bash
cd ~/dev/bedolaga-cabinet && git commit --no-edit && git push origin main
```

- [ ] **Шаг 5: Гейт — CI зелёный**

```bash
gh run list --repo haku4130/bedolaga-cabinet --limit 3
```

Ожидается: `success`.

---

### Task 3: Зафиксировать точку отката — digest'ы всех текущих образов

Делается **до** любых `pull`, иначе `:latest` уже перепишется и откатываться
будет некуда.

- [ ] **Шаг 1: Снять digest'ы панели и subscription-page**

```bash
ssh root@msk2.zanity.net 'for c in remnawave remnawave-subscription-page; do echo -n "$c "; docker inspect $c --format "{{.Image}}"; done'
```

- [ ] **Шаг 2: Снять digest'ы всех 8 нод**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h "; ssh root@$h.zanity.net 'docker inspect remnanode --format "{{.Image}}"'; done
```

- [ ] **Шаг 3: Снять digest'ы бота, кабинета и routing-updater**

```bash
ssh root@msk3.zanity.net 'for c in remnawave_bot cabinet_frontend remna-routing-updater; do echo -n "$c "; docker inspect $c --format "{{.Image}}"; done'
```

- [ ] **Шаг 4: Гейт — сохранить вывод в файл отката**

Записать все три вывода в `~/remnawave-upgrade-rollback-digests.txt` на рабочей
машине. Ожидается: 11 строк вида `<имя> sha256:...`.

---

### Task 4: Правка тегов образов в compose (без `up`)

- [ ] **Шаг 1: Панель `backend:2` → `backend:3`**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave && cp docker-compose.yml docker-compose.yml.bak-pre3 && sed -i "s|remnawave/backend:2|remnawave/backend:3|" docker-compose.yml && grep -n "remnawave/backend" docker-compose.yml'
```

Ожидается: `image: remnawave/backend:3`.

- [ ] **Шаг 2: subscription-page `:latest` → `:8`**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave/subscription && cp docker-compose.yml docker-compose.yml.bak-pre3 && sed -i "s|remnawave/subscription-page:latest|remnawave/subscription-page:8|" docker-compose.yml && grep -n "subscription-page" docker-compose.yml'
```

Ожидается: `image: remnawave/subscription-page:8`.

- [ ] **Шаг 3: Семь нод на `remnawave/node:3`**

de1 здесь намеренно пропущен — у него другой реестр.

```bash
for h in nl1 nl2 de2 fi1 msk1 msk2 msk4; do echo "--- $h ---"; ssh root@$h.zanity.net 'cd /opt/remnanode && cp docker-compose.yml docker-compose.yml.bak-pre3 && sed -i "s|image: remnawave/node:latest|image: remnawave/node:3|" docker-compose.yml && grep -n "image:" docker-compose.yml'; done
```

- [ ] **Шаг 4: de1 на `ghcr.io/remnawave/node:3`**

```bash
ssh root@de1.zanity.net 'cd /opt/remnanode && cp docker-compose.yml docker-compose.yml.bak-pre3 && sed -i "s|image: ghcr.io/remnawave/node:latest|image: ghcr.io/remnawave/node:3|" docker-compose.yml && grep -n "image:" docker-compose.yml'
```

- [ ] **Шаг 5: Гейт — ни одной ноды не осталось на `:latest`**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'grep -h "image:" /opt/remnanode/docker-compose.yml'; done
```

Ожидается: 8 строк, все оканчиваются на `node:3`, ни одной `:latest`.

- [ ] **Шаг 6: Гейт — контейнеры всё ещё на старых образах**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'docker exec remnanode sh -c "cat /opt/app/package.json 2>/dev/null || cat /app/package.json" 2>/dev/null | grep -m1 version || echo RUNNING-UNKNOWN'; done
```

Ожидается: везде `2.7.0`. Правка файла не должна была ничего перезапустить.

---

### Task 5: Предгрев образов (`pull` без `up`)

Смысл задачи: к моменту окна образ 3.3.2 уже лежит локально на каждом хосте,
и залп по нодам занимает секунды, а не минуты скачивания.

- [ ] **Шаг 1: Предгрев 8 нод**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo "=== $h ==="; ssh root@$h.zanity.net 'cd /opt/remnanode && docker compose pull'; done
```

- [ ] **Шаг 2: Предгрев панели и subscription-page**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave && docker compose pull && cd /opt/remnawave/subscription && docker compose pull'
```

- [ ] **Шаг 3: Предгрев бота, кабинета и routing-updater**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose pull bot cabinet-frontend routing-updater'
```

- [ ] **Шаг 4: Гейт — образы 3.3.2 лежат локально на всех нодах**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'docker images --format "{{.Repository}}:{{.Tag}}" | grep -c "remnawave/node:3"'; done
```

Ожидается: `1` на каждом из 8 хостов.

- [ ] **Шаг 5: Гейт — работающие ноды всё ещё 2.7.0**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'docker exec remnanode sh -c "cat /opt/app/package.json 2>/dev/null || cat /app/package.json" 2>/dev/null | grep -m1 version'; done
```

Ожидается: везде `2.7.0`. Если где-то уже 3.3.2 — значит проскочил `up`,
разбираться немедленно.

---

### Task 6: Обновление скрипта бэкапа 3.2.4 → 4.0.0

Обновляем **до** апгрейда панели и сразу проверяем на живой 2.7.4, чтобы в окне
не выяснилось, что инструмент сломан.

- [ ] **Шаг 1: Сохранить текущую версию скрипта**

```bash
ssh root@msk2.zanity.net 'cp /opt/rw-backup-restore/backup-restore.sh /opt/rw-backup-restore/backup-restore.sh.pre4 && grep -m1 "^VERSION=" /opt/rw-backup-restore/backup-restore.sh'
```

Ожидается: `VERSION="3.2.4"`.

- [ ] **Шаг 2: Обновить через встроенный self-update**

```bash
ssh -t root@msk2.zanity.net 'rw-backup update'
```

- [ ] **Шаг 3: Гейт — версия сменилась, конфиг на месте**

```bash
ssh root@msk2.zanity.net 'grep -m1 "^VERSION=" /opt/rw-backup-restore/backup-restore.sh && test -s /opt/rw-backup-restore/config.env && echo CONFIG-OK'
```

Ожидается: `VERSION="4.0.0"` и `CONFIG-OK`.

- [ ] **Шаг 4: Тестовый бэкап на ещё не обновлённой панели**

```bash
ssh root@msk2.zanity.net '/opt/rw-backup-restore/backup-restore.sh backup'
```

- [ ] **Шаг 5: Гейт — бэкап снят и мета корректна**

```bash
ssh root@msk2.zanity.net 'ls -lt /opt/rw-backup-restore/backup/ | head -3'
```

Ожидается: свежий архив сегодняшней датой, ненулевого размера, и он же пришёл
в Telegram. Проверить, что внутри `backup_meta.info` стоит `PANEL_VERSION="2.7.4"`
и `BACKUP_VERSION="4.0.0"`.

- [ ] **Шаг 6: Гейт — крон не сломан**

```bash
ssh root@msk2.zanity.net 'crontab -l | grep backup-restore'
```

Ожидается: строка `@daily /opt/rw-backup-restore/backup-restore.sh backup ...`
на месте — self-update не должен был её потерять.

---

**КОНЕЦ ФАЗЫ 0.** Перед началом окна перепроверить: оба CI зелёные, 8 нод
прогреты и всё ещё на 2.7.0, скрипт бэкапа 4.0.0 отработал, файл digest'ов
сохранён.

---

## ФАЗА 1 — окно (ночь, ≤1 час простоя бота)

### Task 7: Рассылка «начинаем работы» (T−30 минут)

**Требуется подтверждение оператора перед отправкой.** Сообщение уйдёт 161
живому человеку.

- [ ] **Шаг 1: Получить admin-токен кабинета**

Открыть `https://cabinet.zanity.net`, войти админом, скопировать JWT из
localStorage (DevTools → Application → Local Storage). Токен короткоживущий —
брать непосредственно перед отправкой.

- [ ] **Шаг 2: Проверить размер аудитории**

```bash
curl -s -H "Authorization: Bearer $CABINET_TOKEN" https://cabinet.zanity.net/api/admin/broadcasts/filters | python3 -m json.tool
```

Ожидается: в фильтре `all` порядка 161 пользователя.

- [ ] **Шаг 3: Предпросмотр**

```bash
curl -s -X POST -H "Authorization: Bearer $CABINET_TOKEN" -H 'Content-Type: application/json' \
  -d '{"target":"all","message_text":"🔧 <b>Технические работы</b>\n\nЧерез 30 минут начнём обновление инфраструктуры. Примерно на час бот и личный кабинет будут недоступны.\n\n<b>VPN продолжит работать</b> — подключение не отключится. Возможны кратковременные разрывы, клиент переподключится сам.\n\nНедоступны будут только покупка и продление подписки. Если срок вашей подписки истекает сегодня — продлите её сейчас.\n\nНапишем, когда закончим."}' \
  https://cabinet.zanity.net/api/admin/broadcasts/preview | python3 -m json.tool
```

- [ ] **Шаг 4: ПОДТВЕРЖДЕНИЕ ОПЕРАТОРА, затем отправка**

Тот же payload на `POST /api/admin/broadcasts` с
`"category":"system"` и `"selected_buttons":["home"]`.

- [ ] **Шаг 5: Гейт — рассылка доставлена**

```bash
curl -s -H "Authorization: Bearer $CABINET_TOKEN" "https://cabinet.zanity.net/api/admin/broadcasts?limit=1" | python3 -m json.tool
```

Ожидается: свежая запись, число отправленных близко к 161, ошибок нет.

- [ ] **Шаг 6: Подождать 30 минут**

Это не формальность: у людей должен быть шанс продлить подписку до простоя.

---

### Task 8: Остановка бота и снятие консистентной пары дампов

Самая ответственная задача плана. Порядок нарушать нельзя.

- [ ] **Шаг 1: Остановить бота и кабинет**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose stop bot cabinet-frontend && docker compose ps'
```

Ожидается: оба сервиса в состоянии `exited`. С этого момента идёт отсчёт часа.

- [ ] **Шаг 2: Гейт — бот действительно молчит**

```bash
ssh root@msk3.zanity.net 'docker ps --filter name=remnawave_bot --filter name=cabinet_frontend --format "{{.Names}} {{.Status}}"'
```

Ожидается: пустой вывод либо только `remnawave_bot_db`/`remnawave_bot_redis`.
Пока бот жив, дампы снимать нельзя.

- [ ] **Шаг 3: Дамп БД панели**

```bash
ssh root@msk2.zanity.net 'mkdir -p /root/upgrade-dumps && docker exec remnawave-db pg_dumpall -U postgres | gzip > /root/upgrade-dumps/panel-pre3.sql.gz && ls -lh /root/upgrade-dumps/'
```

- [ ] **Шаг 4: Дамп БД бота**

```bash
ssh root@msk3.zanity.net 'mkdir -p /root/upgrade-dumps && docker exec remnawave_bot_db pg_dumpall -U remnawave_user | gzip > /root/upgrade-dumps/bot-pre4.sql.gz && ls -lh /root/upgrade-dumps/'
```

- [ ] **Шаг 5: Гейт — оба дампа валидны**

```bash
ssh root@msk2.zanity.net 'gzip -t /root/upgrade-dumps/panel-pre3.sql.gz && zcat /root/upgrade-dumps/panel-pre3.sql.gz | head -5 && echo PANEL-DUMP-OK'
ssh root@msk3.zanity.net 'gzip -t /root/upgrade-dumps/bot-pre4.sql.gz && zcat /root/upgrade-dumps/bot-pre4.sql.gz | head -5 && echo BOT-DUMP-OK'
```

Ожидается: обе проверки целостности проходят, оба размера в мегабайтах, не в
килобайтах. **Если хоть один дамп не снялся — работы отменяются, бот
поднимается обратно.**

- [ ] **Шаг 6: Скопировать дампы с серверов на рабочую машину**

```bash
mkdir -p ~/remnawave-upgrade-dumps && scp root@msk2.zanity.net:/root/upgrade-dumps/panel-pre3.sql.gz ~/remnawave-upgrade-dumps/ && scp root@msk3.zanity.net:/root/upgrade-dumps/bot-pre4.sql.gz ~/remnawave-upgrade-dumps/
```

Держать копию вне серверов — на случай, если проблема окажется не в софте.

---

### Task 9: Панель 2.7.4 → 3.3.2

- [ ] **Шаг 1: Поднять новую панель**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave && docker compose down && docker compose up -d'
```

- [ ] **Шаг 2: Смотреть миграции в логах**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave && docker compose logs -f remnawave'
```

Ожидается: `Migrating database...` → `Migrations deployed successfully!` →
`Entrypoint script completed.` Прервать просмотр по Ctrl+C после старта приложения.

- [ ] **Шаг 3: Гейт — версия и здоровье**

```bash
ssh root@msk2.zanity.net 'docker exec remnawave sh -c "cat /opt/app/package.json" | grep -m1 version; docker ps --filter name=remnawave --format "{{.Names}} {{.Status}}"'
```

Ожидается: `"version": "3.3.2"` и статус `healthy`.

- [ ] **Шаг 4: Гейт — панель отвечает снаружи и пускает в UI**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://remnapanel.zanity.net/
```

Ожидается: `200`. Затем вручную войти в UI под админом и убедиться, что список
пользователей отображается.

**ЕСЛИ МИГРАЦИЯ УПАЛА:** не пытаться чинить на месте. Перейти к Приложению А.

---

### Task 10: Залп по 8 нодам

Ноды 2.7.0 против панели 3.3.2 — это окно деградации. Цель задачи — сделать его
секундным, поэтому команды на все хосты запускаются одновременно.

- [ ] **Шаг 1: Залп**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do
  ssh root@$h.zanity.net 'cd /opt/remnanode && docker compose down && docker compose up -d' &
done; wait
```

- [ ] **Шаг 2: Гейт — все 8 нод на 3.3.2**

```bash
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'docker exec remnanode sh -c "cat /opt/app/package.json 2>/dev/null || cat /app/package.json" 2>/dev/null | grep -m1 version'; done
```

Ожидается: семь строк `"version": "3.3.2"`.

**de1 — исключение:** в его образе из `ghcr.io` нет `package.json`, только
`dist`, поэтому версию так не прочитать. Проверять по смене digest'а образа
относительно записанного в файле отката плюс по статусу ноды в UI панели:

```bash
ssh root@de1.zanity.net 'docker inspect remnanode --format "{{.Image}}"'
```

Ожидается: digest отличается от `sha256:03f14935…` из файла отката.

- [ ] **Шаг 3: Гейт — nl1 не потерял прокси и релей**

```bash
ssh root@nl1.zanity.net 'docker ps --filter name=tg-proxy --filter name=tg-socks --format "{{.Names}} {{.Status}}"'
```

Ожидается: оба контейнера `Up`. Они standalone и compose их трогать не должен —
шаг подтверждает, что так и вышло.

- [ ] **Шаг 4: Гейт — панель видит ноды живыми**

В UI панели открыть раздел нод. Ожидается: все 8 online, счётчики трафика идут.

---

### Task 11: subscription-page 7.1.8 → 8.0.0

- [ ] **Шаг 1: Поднять**

```bash
ssh root@msk2.zanity.net 'cd /opt/remnawave/subscription && docker compose down && docker compose up -d && docker compose logs --tail 30'
```

- [ ] **Шаг 2: Гейт — версия**

```bash
ssh root@msk2.zanity.net 'docker exec remnawave-subscription-page sh -c "cat /opt/app/package.json 2>/dev/null || cat /app/package.json" 2>/dev/null | grep -m1 version'
```

Ожидается: `"version": "8.0.0"`.

- [ ] **Шаг 3: Гейт — подписочная ссылка отдаёт конфиг**

Взять действующую subscription-ссылку любого активного пользователя из панели и
запросить её. Ожидается: `200` и непустое тело конфига, а не страница ошибки.

---

### Task 12: Бот v3.64.0 → v4.1.0 (миграция 0104)

- [ ] **Шаг 1: Переименовать переменную в боевом `.env`**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && cp .env .env.bak-pre4 && sed -i "s/^TRAFFIC_EXCLUDED_USER_UUIDS=/TRAFFIC_EXCLUDED_USER_IDS=/" .env && grep -n "TRAFFIC_EXCLUDED_USER" .env'
```

Ожидается: `TRAFFIC_EXCLUDED_USER_IDS=` (значение пустое, как и было).

- [ ] **Шаг 2: Поднять бота**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose up -d bot && docker compose logs -f bot'
```

- [ ] **Шаг 3: Гейт — миграция 0104 применилась**

```bash
ssh root@msk3.zanity.net 'docker exec remnawave_bot_db psql -U remnawave_user -d remnawave_bot -tAc "select version_num from alembic_version;"'
```

Ожидается: номер ревизии, соответствующий головной миграции v4.1.0 (не ниже 0104).

- [ ] **Шаг 4: Гейт — колонка появилась и пока пустая**

```bash
ssh root@msk3.zanity.net 'docker exec remnawave_bot_db psql -U remnawave_user -d remnawave_bot -tAc "select count(*) filter (where remnawave_id is null) as nulls, count(*) as total from subscriptions;"'
```

Ожидается: `nulls` равно `total` — колонка есть, бэкфил ещё не прогнан. Это
нормальное промежуточное состояние; бот сейчас работать не должен.

---

### Task 13: Бэкфил, холостой прогон — ТОЧКА ПРИНЯТИЯ РЕШЕНИЯ

- [ ] **Шаг 1: Прогнать dry-run**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose run --rm bot python -m scripts.backfill_remnawave_ids'
```

Флаг не нужен: холостой режим — поведение по умолчанию, скрипт ничего не пишет.

- [ ] **Шаг 2: Прочитать отчёт**

```bash
ssh root@msk3.zanity.net 'ls -lt /root/remnawave-bot/logs/remnawave_backfill_*.json | head -3'
ssh root@msk3.zanity.net 'cat $(ls -t /root/remnawave-bot/logs/remnawave_backfill_dryrun_*.json | head -1)' | python3 -m json.tool | head -60
```

- [ ] **Шаг 3: ГЕЙТ РЕШЕНИЯ — оценить `conflicts` и `unresolved`**

- `conflicts` **должно быть 0**. Любой конфликт означает, что две строки бота
  претендуют на один панельный аккаунт. `--apply` в этом случае не запускать —
  он и сам откажется коммитить, но выяснять причину надо до, а не после.
- `unresolved` — ожидаемо ненулевое, если в панели есть аккаунты, заведённые
  мимо бота. Сверить список с известными служебными и туннельными
  пользователями. Каждый неопознанный `unresolved` — повод остановиться.
- Число разрешённых идентичностей должно быть близко к 138 активным подпискам.

**Если картина не сходится — СТОП.** БД бота ещё не тронута записью, откат
дешёвый: Приложение А, вариант «до apply».

---

### Task 14: Бэкфил, боевой прогон

- [ ] **Шаг 1: Применить**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose run --rm bot python -m scripts.backfill_remnawave_ids --apply'
```

- [ ] **Шаг 2: Гейт — отчёт о применении сохранён**

```bash
ssh root@msk3.zanity.net 'cat $(ls -t /root/remnawave-bot/logs/remnawave_backfill_apply_*.json | head -1)' | python3 -m json.tool | head -40
```

Ожидается: в `summary` поле `committed: true`. Если вместо `applied` в отчёте
лежит `applied_but_rolled_back` — коммит откатился, ничего не записано,
переходить к следующей задаче нельзя.

- [ ] **Шаг 3: Гейт — NULL-идентичностей не осталось**

```bash
ssh root@msk3.zanity.net 'docker exec remnawave_bot_db psql -U remnawave_user -d remnawave_bot -tAc "select count(*) filter (where remnawave_id is null) as nulls, count(*) as total from subscriptions;"'
```

Ожидается: `nulls` = 0 либо равно числу заведомо мёртвых/архивных строк,
объяснённому отчётом dry-run.

- [ ] **Шаг 4: Перезапустить бота**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose restart bot && sleep 20 && docker compose ps bot'
```

Ожидается: статус `healthy`.

- [ ] **Шаг 5: Гейт — в логах нет ошибок идентичности**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose logs --tail 100 bot | grep -iE "error|traceback|remnawave_id" | head -20'
```

Ожидается: пусто либо только безобидные информационные строки.

**С ЭТОГО МОМЕНТА откат означает восстановление обеих БД.** Дальше идём только вперёд.

---

### Task 15: Кабинет v1.61.0 → v1.66.0

- [ ] **Шаг 1: Поднять**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose up -d cabinet-frontend && sleep 15 && docker compose ps cabinet-frontend'
```

Ожидается: `healthy`.

- [ ] **Шаг 2: Гейт — кабинет отвечает**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://cabinet.zanity.net/
```

Ожидается: `200`.

- [ ] **Шаг 3: Гейт — логин через Telegram работает**

Вручную: открыть кабинет, войти через Telegram. Это проверяет цепочку
OIDC → `oauth.telegram.org` через socks5 на nl1. Затем открыть карточку любого
пользователя — она должна показать панельную идентичность без ошибок.

---

### Task 16: routing-updater

Неблокирующая задача. Если не заведётся — окно из-за неё не продлеваем.

- [ ] **Шаг 1: Поднять**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose up -d routing-updater && docker compose logs --tail 50 routing-updater'
```

- [ ] **Шаг 2: Гейт — прогон без ошибок авторизации**

Ожидается: в логах успешное обращение к `https://remnapanel.zanity.net/api`,
без `401`/`404`. Если ошибки есть — записать и разбираться после окна;
на работу сервиса для клиентов это не влияет.

---

### Task 17: Смоук-тест

- [ ] **Шаг 1: Клиентское подключение**

Подключиться реальным клиентом по действующей подписке. Ожидается: трафик идёт,
в панели виден рост счётчика на соответствующей ноде.

- [ ] **Шаг 2: Бот отвечает**

Отправить `/start` боту `@zanity_vpn_bot`. Ожидается: главное меню, подписка
отображается с корректным сроком.

- [ ] **Шаг 3: Гейт — webhook панель→бот жив**

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose logs --tail 50 bot | grep -i webhook'
```

Ожидается: входящие вебхуки от панели обрабатываются без ошибок подписи.

- [ ] **Шаг 4: Гейт — inbound webhook Telegram жив**

```bash
ssh root@msk3.zanity.net 'docker exec remnawave_bot python -c "
import os,httpx
u=os.environ[\"BOT_TOKEN\"]
p=os.environ.get(\"PROXY_URL\")
print(httpx.get(f\"https://api.telegram.org/bot{u}/getWebhookInfo\", proxy=p, timeout=20).json())
"'
```

Ожидается: `url` указывает на nl1, `pending_update_count` близок к нулю,
`last_error_date` не растёт. Кратковременная ошибка в момент перезапуска бота — норма.

- [ ] **Шаг 5: Продление подписки**

Провести тестовое продление на минимальный срок. Ожидается: списание прошло,
срок в панели и в боте совпал.

---

### Task 18: Контрольный бэкап на новой панели

- [ ] **Шаг 1: Снять бэкап скриптом 4.0.0 уже на панели 3.3.2**

```bash
ssh root@msk2.zanity.net '/opt/rw-backup-restore/backup-restore.sh backup && ls -lt /opt/rw-backup-restore/backup/ | head -3'
```

- [ ] **Шаг 2: Гейт — мета отражает новую версию**

```bash
ssh root@msk2.zanity.net 'cd /tmp && rm -rf metacheck && mkdir metacheck && tar xzf $(ls -t /opt/rw-backup-restore/backup/*.tar.gz | head -1) -C metacheck backup_meta.info && cat metacheck/backup_meta.info'
```

Ожидается: `PANEL_VERSION="3.3.2"` и `BACKUP_VERSION="4.0.0"`. Пустой
`PANEL_VERSION` означает, что скрипт не научился читать версию с новой панели —
это надо чинить, иначе ежедневный бэкап деградирует незаметно.

---

### Task 19: Рассылка «работы завершены»

**Требуется подтверждение оператора перед отправкой.**

- [ ] **Шаг 1: ПОДТВЕРЖДЕНИЕ, затем отправка**

`POST /api/admin/broadcasts` с `"target":"all"`, `"category":"system"` и текстом:

```
✅ <b>Работы завершены</b>

Бот и личный кабинет снова работают. Спасибо за терпение!

Если что-то ведёт себя странно — напишите в поддержку.
```

- [ ] **Шаг 2: Гейт — доставлено всем**

```bash
curl -s -H "Authorization: Bearer $CABINET_TOKEN" "https://cabinet.zanity.net/api/admin/broadcasts?limit=1" | python3 -m json.tool
```

Ожидается: число отправленных близко к 161, ошибок нет. Это одновременно живая
проверка цепочки бот → socks5 на nl1 → Telegram: если рассылка ушла, отправка
сообщений после апгрейда цела.

---

### Task 20: Закрытие окна

- [ ] **Шаг 1: Убрать мусор образов**

```bash
ssh root@msk2.zanity.net 'docker image prune -f'
ssh root@msk3.zanity.net 'docker image prune -f'
for h in nl1 nl2 de1 de2 fi1 msk1 msk4; do ssh root@$h.zanity.net 'docker image prune -f'; done
```

Бэкапы compose-файлов (`*.bak-pre3`) и дампы **не удалять** — держать минимум
неделю.

- [ ] **Шаг 2: Гейт — итоговая сверка версий**

```bash
ssh root@msk2.zanity.net 'docker exec remnawave sh -c "cat /opt/app/package.json" | grep -m1 version'
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do echo -n "$h: "; ssh root@$h.zanity.net 'docker exec remnanode sh -c "cat /opt/app/package.json 2>/dev/null || cat /app/package.json" 2>/dev/null | grep -m1 version'; done
ssh root@msk3.zanity.net 'docker ps --format "{{.Names}} {{.Status}}" | grep -E "bot|cabinet|routing"'
```

Ожидается: панель 3.3.2, восемь нод 3.3.2, бот и кабинет `healthy`.

- [ ] **Шаг 3: Обновить память проекта**

Записать в `fork-update-deploy-workflow.md`: новые версии форков (бот v4.1.0,
кабинет v1.66.0) и дату. Добавить в память факт про ручной бэкфил
`scripts/backfill_remnawave_ids.py` — он понадобится любому, кто будет
восстанавливать бота из доапгрейдного дампа.

---

## Приложение А — откат

### Вариант 1: до Task 14 (бэкфил не применён)

БД бота записью не тронута. Откатывается только панель и ноды.

```bash
# Панель на прежний digest
ssh root@msk2.zanity.net 'cd /opt/remnawave && docker compose down && cp docker-compose.yml.bak-pre3 docker-compose.yml'
# Восстановить БД панели
ssh root@msk2.zanity.net 'docker compose up -d remnawave-db && sleep 20 && zcat /root/upgrade-dumps/panel-pre3.sql.gz | docker exec -i remnawave-db psql -U postgres'
ssh root@msk2.zanity.net 'cd /opt/remnawave && docker compose up -d'
# Ноды обратно
for h in nl1 nl2 de1 de2 fi1 msk1 msk2 msk4; do ssh root@$h.zanity.net 'cd /opt/remnanode && cp docker-compose.yml.bak-pre3 docker-compose.yml && docker compose down && docker compose up -d' & done; wait
# subscription-page обратно
ssh root@msk2.zanity.net 'cd /opt/remnawave/subscription && cp docker-compose.yml.bak-pre3 docker-compose.yml && docker compose down && docker compose up -d'
# Бот обратно на прежний образ по digest из файла отката
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && cp .env.bak-pre4 .env'
```

Затем поднять бота на прежнем digest'е (взять из
`~/remnawave-upgrade-rollback-digests.txt`, прописать в `docker-compose.yml`
вместо `:latest`) и `docker compose up -d bot cabinet-frontend`.

### Вариант 2: после Task 14 (бэкфил применён)

Дополнительно к варианту 1 восстановить БД бота:

```bash
ssh root@msk3.zanity.net 'cd /root/remnawave-bot && docker compose stop bot cabinet-frontend && zcat /root/upgrade-dumps/bot-pre4.sql.gz | docker exec -i remnawave_bot_db psql -U remnawave_user -d remnawave_bot'
```

Обе БД восстанавливаются из пары дампов Task 8, снятых с одной точки во
времени при остановленном боте. Порядок: сначала панель, потом бот, потом
поднимать бота.

### Что откатывать не нужно

Скрипт бэкапа 4.0.0 обратной совместим с панелью 2.x — версия 3.2.4 уже
работала с ней, а 4.0.0 добавляет режимы восстановления, не ломая старые.
При откате оставить 4.0.0. Если понадобится вернуть — файл
`/opt/rw-backup-restore/backup-restore.sh.pre4`.
