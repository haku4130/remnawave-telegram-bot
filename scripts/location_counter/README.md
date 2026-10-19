# Счётчик устройств по локациям

Скрипт на ноде читает access-лог xray из контейнера `remnanode`, помнит последнее подключение каждого
устройства 300 секунд и раз в минуту отправляет в кабинет обезличенный отчёт
(`POST ${CABINET_URL}/api/cabinet/admin/stats/locations/report`, заголовок `X-Location-Counter-Token`).
В отчёте нет ни ID пользователей, ни IP-адресов: только HMAC-хеши, входящий профиль и выход.

Нужны Python 3.10+ (только стандартная библиотека) и docker на ноде.

## Установка

Для SE-1 вместо `NODE_HOST` используйте `root@191.44.114.69`.

```bash
NODE_HOST=root@msk4.zanity.net   # хост ноды

# 1. Скрипт и unit
ssh $NODE_HOST 'mkdir -p /opt/location-counter'
scp scripts/location_counter/location_counter.py $NODE_HOST:/opt/location-counter/
scp scripts/location_counter/location-counter.service $NODE_HOST:/etc/systemd/system/

# 2. Окружение: скопировать пример, заполнить NODE_NAME и токен, закрыть права
scp scripts/location_counter/location-counter.env.example $NODE_HOST:/etc/location-counter.env
ssh $NODE_HOST 'chmod 600 /etc/location-counter.env && nano /etc/location-counter.env'

# 3. Запуск
ssh $NODE_HOST 'systemctl daemon-reload && systemctl enable --now location-counter'

# 4. Логи
ssh $NODE_HOST 'journalctl -u location-counter -f'
```

Ошибка отправки отчёта пишется в журнал одной строкой, сервис ждёт следующей минуты. Если `docker exec`
завершился, сервис выходит с кодом 1 и systemd поднимает его через 10 секунд.

## Откат

```bash
ssh $NODE_HOST 'systemctl disable --now location-counter'
```
