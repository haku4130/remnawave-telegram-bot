import json
from datetime import UTC, datetime, timedelta

from scripts.location_counter.location_counter import DeviceTracker, LogEvent, build_report, make_key, parse_line


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
