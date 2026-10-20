import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta

from scripts.location_counter import location_counter
from scripts.location_counter.location_counter import (
    DeviceTracker,
    LogEvent,
    build_report,
    is_probe,
    make_key,
    parse_line,
)


LINE = (
    '2026/10/18 17:34:07.590389 from 5.18.1.2:51234 accepted tcp:57.144.248.145:443 '
    '[vless-to-eu-auto -> SS_OUTBOUND_FI2] email: 123'
)


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


def test_parse_line_captures_destination_host():
    e = parse_line(
        '2026/10/19 15:17:39.217281 from 109.252.162.151:1230 accepted tcp:cp.cloudflare.com:443 '
        '[vless-auto-fi1 -> SS_OUTBOUND_FI1] email: 141'
    )
    assert e.host == 'cp.cloudflare.com'
    e6 = parse_line('2026/10/19 15:17:39 from 1.2.3.4:5 accepted udp:[2a00:1450::1]:443 [a -> direct] email: 7')
    assert e6.host == '2a00:1450::1'


def test_probe_connections_are_ignored():
    probe = parse_line(
        '2026/10/19 15:17:39 from 1.2.3.4:5 accepted tcp:cp.cloudflare.com:443 '
        '[vless-auto-de1 -> SS_OUTBOUND_DE1] email: 141'
    )
    real = parse_line(
        '2026/10/19 15:17:40 from 1.2.3.4:5 accepted tcp:instagram.com:443 '
        '[vless-auto-fi1 -> SS_OUTBOUND_FI1] email: 141'
    )
    assert is_probe(probe) and not is_probe(real)


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


def test_stale_exit_dropped_but_device_stays():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(100, 'vless-to-eu-auto', 'direct'))
    t.observe(ev(200, 'vless-to-eu-auto', 'direct'))
    # последний мост был 305 с назад (вне окна), а само устройство подключалось 105 с назад
    [d] = t.snapshot(ev(305, 'x', 'y').ts)
    assert (d.last_seen, d.exit) == (ev(200, 'x', 'y').ts, None)


def test_fresh_exit_kept_in_window():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(250, 'vless-to-eu-auto', 'direct'))
    [d] = t.snapshot(ev(299, 'x', 'y').ts)
    assert d.exit == 'SS_OUTBOUND_FI1'


def test_exit_refreshed_by_new_bridge_connection():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(200, 'vless-to-eu-auto', 'SS_OUTBOUND_DE1'))
    [d] = t.snapshot(ev(400, 'x', 'y').ts)
    assert d.exit == 'SS_OUTBOUND_DE1'


def test_exit_not_leaked_across_inbound_switch_via_exit_seen():
    t = DeviceTracker()
    t.observe(ev(0, 'vless-to-eu-auto', 'SS_OUTBOUND_FI1'))
    t.observe(ev(5, 'vless-to-de1-ss', 'direct'))
    [d] = t.snapshot(ev(10, 'x', 'y').ts)
    assert (d.inbound, d.exit) == ('vless-to-de1-ss', None)


def test_log_command_wraps_tail_with_stdin_watchdog():
    cmd = location_counter.LOG_COMMAND
    assert cmd[:4] == ['docker', 'exec', '-i', 'remnanode']
    assert cmd[4:6] == ['sh', '-c']
    script = cmd[6]
    assert 'tail -F -n +1 /var/log/xray/current' in script
    assert 'cat >/dev/null' in script and 'kill $p' in script


def test_log_reader_keeps_stdin_pipe_open(monkeypatch):
    captured = {}

    class FakeProcess:
        stdout = iter([])

        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs
        return FakeProcess()

    monkeypatch.setattr(location_counter.subprocess, 'Popen', fake_popen)
    stopped = threading.Event()
    location_counter._read_log(DeviceTracker(), threading.Lock(), stopped)
    assert captured['cmd'] == location_counter.LOG_COMMAND
    assert captured['kwargs']['stdin'] == subprocess.PIPE
    assert stopped.is_set()


def test_log_reader_skips_probe_connections(monkeypatch):
    lines = [
        '2026/10/19 15:17:39 from 1.2.3.4:5 accepted tcp:cp.cloudflare.com:443 [vless-auto-de1 -> SS_OUTBOUND_DE1] email: 141\n',
        '2026/10/19 15:17:40 from 1.2.3.4:5 accepted tcp:instagram.com:443 [vless-auto-fi1 -> SS_OUTBOUND_FI1] email: 141\n',
    ]

    class FakeProcess:
        stdout = iter(lines)

        def wait(self):
            return 0

    monkeypatch.setattr(location_counter.subprocess, 'Popen', lambda cmd, **kwargs: FakeProcess())
    tracker = DeviceTracker()
    location_counter._read_log(tracker, threading.Lock(), threading.Event())
    assert tracker.events_seen == 1
    assert tracker.newest_ts == datetime(2026, 10, 19, 15, 17, 40, tzinfo=UTC)


def test_clock_skew_flags_both_directions_beyond_limit():
    now = ev(1000, 'x', 'y').ts
    assert location_counter.clock_skew(None, now) is None
    assert location_counter.clock_skew(now - timedelta(seconds=299), now) is None
    assert location_counter.clock_skew(now - timedelta(seconds=3600), now) == 3600
    assert location_counter.clock_skew(now + timedelta(seconds=301), now) == -301


def test_tracker_remembers_newest_event_and_count():
    t = DeviceTracker()
    t.observe(ev(10, 'a', 'direct'))
    t.observe(ev(5, 'a', 'direct', ip='2.2.2.2'))
    assert (t.events_seen, t.newest_ts) == (2, ev(10, 'x', 'y').ts)
