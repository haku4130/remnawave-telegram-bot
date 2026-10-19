from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.services.location_presence import (
    Device,
    Presence,
    build_location_cards,
    devices_from_report,
    exit_display_name,
    merge_presence,
)
from tests.fixtures.locations import (
    OVERRIDES,
    TAG_AUTO,
    TAG_DE,
    prod_hosts,
    prod_nodes,
)


NOW = datetime(2026, 10, 19, 7, 5, tzinfo=UTC)


def dev(u, d, sec_ago, node, inbound, exit=None):
    return Device(u, d, NOW - timedelta(seconds=sec_ago), node, inbound, exit)


def test_devices_from_report_parses_utc_and_node():
    report = {
        'node': 'MSK-4',
        'generated_at': '2026-10-19T07:40:00Z',
        'window_seconds': 300,
        'devices': [
            {
                'u': 'aaaa',
                'd': 'bbbb',
                't': '2026-10-19T07:39:12Z',
                'inbound': 'vless-to-eu-auto',
                'exit': 'SS_OUTBOUND_FI2',
            },
            {'u': 'cccc', 'd': 'dddd', 't': '2026-10-19T07:39:50Z', 'inbound': 'vless-to-de1-ss', 'exit': None},
        ],
    }
    devices = devices_from_report(report)
    assert devices == [
        Device(
            'aaaa',
            'bbbb',
            datetime(2026, 10, 19, 7, 39, 12, tzinfo=UTC),
            'MSK-4',
            'vless-to-eu-auto',
            'SS_OUTBOUND_FI2',
        ),
        Device('cccc', 'dddd', datetime(2026, 10, 19, 7, 39, 50, tzinfo=UTC), 'MSK-4', 'vless-to-de1-ss', None),
    ]
    assert devices[0].last_seen.utcoffset() == timedelta(0)


def test_latest_device_wins_across_nodes():
    p = merge_presence(
        [dev('u1', 'd1', 100, 'NL-1', 'vless-inbound-sni-github'), dev('u1', 'd1', 10, 'MSK-4', 'vless-to-de1-ss')],
        NOW,
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
    tags = ('SS_OUTBOUND_FI2', 'SS_OUTBOUND_UK1', 'SS_OUTBOUND_TO_FI')
    assert [exit_display_name(t) for t in tags] == ['FI-2', 'UK-1', 'TO_FI']


def test_location_host_node_overrides_parsing():
    parsed = Settings(BOT_TOKEN='t', LOCATION_HOST_NODE_OVERRIDES='msk2.zanity.net=MSK-2, X.ORG = N')
    assert parsed.get_location_host_node_overrides() == {'msk2.zanity.net': 'MSK-2', 'x.org': 'N'}
    assert Settings(BOT_TOKEN='t', LOCATION_HOST_NODE_OVERRIDES='').get_location_host_node_overrides() == {}


def _cards(presence=None, reports_at=None, overrides=OVERRIDES):
    return build_location_cards(prod_hosts(), prod_nodes(), presence or {}, reports_at or {}, overrides)


def _by_title(cards):
    return {c.title: c for c in cards}


def test_cards_order_filters_and_mapping():
    cards = _cards()
    assert [c.title for c in cards] == [
        '🇪🇺 [AUTO] Европа',
        '🇳🇱 Нидерланды',
        '🇩🇪 Германия',
        '🇷🇺 #1 Россия (СПБ)',
        '🇷🇺 #2 Россия (МСК)',
        '🇳🇱 Нидерланды (с рекламой)',
        '🇸🇪 Швеция',
    ]
    by_title = _by_title(cards)
    assert by_title['🇷🇺 #1 Россия (СПБ)'].node_name == 'MSK-1'
    assert by_title['🇷🇺 #2 Россия (МСК)'].node_name == 'MSK-2'
    assert by_title['🇸🇪 Швеция'].node_name == 'SE-1'


def test_counter_source_with_zero():
    reported = datetime(2026, 10, 19, 7, 4, tzinfo=UTC)
    cards = _by_title(
        _cards(
            presence={('MSK-4', TAG_AUTO): Presence(5, {'SS_OUTBOUND_FI2': 3})},
            reports_at={'MSK-4': reported},
        )
    )
    auto = cards['🇪🇺 [AUTO] Европа']
    assert (auto.users, auto.exits, auto.source, auto.reported_at) == (5, [('FI-2', 3)], 'counter', reported)
    assert auto.inbound_tag == TAG_AUTO
    germany = cards['🇩🇪 Германия']
    assert (germany.users, germany.source, germany.inbound_tag) == (0, 'counter', TAG_DE)


def test_exits_sorted_by_count_descending():
    cards = _by_title(
        _cards(
            presence={
                ('MSK-4', TAG_AUTO): Presence(6, {'SS_OUTBOUND_FI1': 1, 'SS_OUTBOUND_UK1': 4, 'SS_OUTBOUND_FI2': 1})
            },
            reports_at={'MSK-4': NOW},
        )
    )
    assert cards['🇪🇺 [AUTO] Европа'].exits[0] == ('UK-1', 4)


def test_panel_fallback_single_location():
    cards = _by_title(_cards())
    assert (cards['🇸🇪 Швеция'].users, cards['🇸🇪 Швеция'].source) == (8, 'panel')
    assert (cards['🇷🇺 #1 Россия (СПБ)'].users, cards['🇷🇺 #1 Россия (СПБ)'].source) == (1, 'panel')


def test_no_data_for_multi_location_node_without_report():
    cards = _by_title(_cards())
    for title in ('🇳🇱 Нидерланды', '🇳🇱 Нидерланды (с рекламой)', '🇪🇺 [AUTO] Европа', '🇩🇪 Германия'):
        assert (cards[title].users, cards[title].source) == (None, None), title


def test_ambiguous_host_without_override():
    card = _by_title(_cards(overrides={}))['🇷🇺 #2 Россия (МСК)']
    assert (card.node_name, card.users) == (None, None)
