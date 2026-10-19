# Счётчик устройств по локациям

Скрипт на ноде читает access-лог xray из контейнера `remnanode`, помнит последнее подключение каждого
устройства 300 секунд и раз в минуту отправляет в кабинет обезличенный отчёт
(`POST ${CABINET_URL}/api/cabinet/admin/stats/locations/report`, заголовок `X-Location-Counter-Token`).
В отчёте нет ни ID пользователей, ни IP-адресов: только HMAC-хеши, входящий профиль и выход.

Нужны Python 3.10+ (только стандартная библиотека) и docker на ноде.

## Установка

```bash
NODE_HOST=root@msk4.zanity.net   # хост ноды; для SE-1 — root@191.44.114.69

# 1. Скрипт и unit
ssh $NODE_HOST 'mkdir -p /opt/location-counter'
scp scripts/location_counter/location_counter.py $NODE_HOST:/opt/location-counter/
scp scripts/location_counter/location-counter.service $NODE_HOST:/etc/systemd/system/

# 2. Окружение: файл сразу создаётся с правами 600 (токен не лежит открытым), затем правим NODE_NAME и токен
ssh $NODE_HOST 'install -m 600 /dev/null /etc/location-counter.env'
scp scripts/location_counter/location-counter.env.example $NODE_HOST:/etc/location-counter.env
ssh -t $NODE_HOST 'nano /etc/location-counter.env'

# 3. Запуск
ssh $NODE_HOST 'systemctl daemon-reload && systemctl enable --now location-counter'

# 4. Логи
ssh $NODE_HOST 'journalctl -u location-counter -f'

# 5. Проверка после запуска и после каждого рестарта: в контейнере ровно один процесс tail
#    (ps в контейнере нет, поэтому через /proc; "^tail" совпадает только с самим tail, а не с sh -c, который его
#    запустил). Должно напечатать 1; 2 и больше — остался хвост от прошлого запуска
ssh $NODE_HOST "docker exec remnanode sh -c 'grep -la \"^tail\" /proc/[0-9]*/cmdline 2>/dev/null | wc -l'"
```

Ошибка отправки отчёта пишется в журнал одной строкой, сервис ждёт следующей минуты. Если `docker exec`
завершился, сервис выходит с кодом 1 и systemd поднимает его через 10 секунд.

## Откат

```bash
ssh $NODE_HOST 'systemctl disable --now location-counter'
```
