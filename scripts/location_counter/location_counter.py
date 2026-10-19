"""Счётчик устройств по локациям на VPN-ноде.

Читает access-лог xray из контейнера remnanode, помнит последнее подключение каждого устройства
(пара «пользователь + IP») 300 секунд и раз в минуту отправляет обезличенный отчёт в кабинет.
Только стандартная библиотека, совместим с Python 3.10.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


UTC = timezone.utc  # noqa: UP017 -- datetime.UTC появился только в 3.11, а на части нод стоит 3.10

WINDOW_SECONDS = 300
REPORT_INTERVAL_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 15
REPORT_PATH = '/api/cabinet/admin/stats/locations/report'
TOKEN_HEADER = 'X-Location-Counter-Token'
LOG_COMMAND = ['docker', 'exec', 'remnanode', 'tail', '-F', '-n', '+1', '/var/log/xray/current']
EXIT_PREFIX = 'SS_OUTBOUND_'

_LINE_RE = re.compile(
    r'^(?P<date>\d{4}/\d{2}/\d{2}) (?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))? '
    r'from (?:(?:tcp|udp):)?(?:\[(?P<ip6>[0-9A-Fa-f:.]+)\]|(?P<ip4>\d{1,3}(?:\.\d{1,3}){3})):\d+ '
    r'accepted \S+ \[(?P<inbound>[^\s\]]+) (?:->|>>) (?P<outbound>[^\s\]]+)\] '
    r'email: (?P<user>\S+)\s*$'
)


@dataclass(frozen=True)
class LogEvent:
    ts: datetime
    ip: str
    inbound: str
    outbound: str
    user: str


@dataclass
class DeviceState:
    user: str
    ip: str
    last_seen: datetime
    inbound: str
    exit: str | None


def parse_line(line: str) -> LogEvent | None:
    m = _LINE_RE.match(line.strip())
    if m is None:
        return None
    try:
        ts = datetime.strptime(f'{m["date"]} {m["time"]}', '%Y/%m/%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return None
    if m['frac']:
        ts = ts.replace(microsecond=int(m['frac'][:6].ljust(6, '0')))
    return LogEvent(ts, m['ip6'] or m['ip4'], m['inbound'], m['outbound'], m['user'])


def _exit_of(outbound: str) -> str | None:
    return outbound if outbound.startswith(EXIT_PREFIX) else None


class DeviceTracker:
    """Последнее подключение каждого устройства; устройство = (пользователь, IP)."""

    def __init__(self) -> None:
        self._devices: dict[tuple[str, str], DeviceState] = {}

    def observe(self, event: LogEvent) -> None:
        key = (event.user, event.ip)
        state = self._devices.get(key)
        if state is None:
            self._devices[key] = DeviceState(event.user, event.ip, event.ts, event.inbound, _exit_of(event.outbound))
            return
        if event.ts < state.last_seen:
            return
        if event.inbound != state.inbound:
            state.inbound = event.inbound
            state.exit = _exit_of(event.outbound)
        elif state.exit is None:
            state.exit = _exit_of(event.outbound)
        elif _exit_of(event.outbound) is not None:
            state.exit = event.outbound
        state.last_seen = event.ts

    def snapshot(self, now: datetime) -> list[DeviceState]:
        threshold = now - timedelta(seconds=WINDOW_SECONDS)
        self._devices = {k: v for k, v in self._devices.items() if v.last_seen >= threshold}
        return [DeviceState(s.user, s.ip, s.last_seen, s.inbound, s.exit) for s in self._devices.values()]


def make_key(token: str, value: str) -> str:
    return hmac.new(token.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def build_report(node: str, devices: list[DeviceState], token: str, now: datetime) -> dict:
    return {
        'node': node,
        'generated_at': _iso(now),
        'window_seconds': WINDOW_SECONDS,
        'devices': [
            {
                'u': make_key(token, d.user),
                'd': make_key(token, f'{d.user}|{d.ip}'),
                't': _iso(d.last_seen),
                'inbound': d.inbound,
                'exit': d.exit,
            }
            for d in devices
        ],
    }


def _send(url: str, token: str, report: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(report).encode(),
        headers={'Content-Type': 'application/json', TOKEN_HEADER: token},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response.read()


def _read_log(tracker: DeviceTracker, lock: threading.Lock, stopped: threading.Event) -> None:
    try:
        process = subprocess.Popen(  # noqa: S603
            LOG_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors='replace'
        )
        for line in process.stdout:
            event = parse_line(line)
            if event is not None:
                with lock:
                    tracker.observe(event)
        process.wait()
    except Exception as exc:
        print(f'log reader failed: {exc}', file=sys.stderr, flush=True)
    finally:
        stopped.set()


def main() -> int:
    base_url = os.environ.get('CABINET_URL', '').rstrip('/')
    token = os.environ.get('LOCATION_COUNTER_TOKEN', '')
    node = os.environ.get('NODE_NAME', '')
    missing = [
        name
        for name, value in (('CABINET_URL', base_url), ('LOCATION_COUNTER_TOKEN', token), ('NODE_NAME', node))
        if not value
    ]
    if missing:
        print(f'missing environment variables: {", ".join(missing)}', file=sys.stderr)
        return 2

    url = base_url + REPORT_PATH
    tracker = DeviceTracker()
    lock = threading.Lock()
    stopped = threading.Event()
    threading.Thread(target=_read_log, args=(tracker, lock, stopped), daemon=True).start()

    while not stopped.wait(REPORT_INTERVAL_SECONDS):
        now = datetime.now(UTC)
        with lock:
            devices = tracker.snapshot(now)
        try:
            _send(url, token, build_report(node, devices, token, now))
        except Exception as exc:
            print(f'report failed: {exc}', file=sys.stderr, flush=True)
    print('docker exec finished, exiting', file=sys.stderr, flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
