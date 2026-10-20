# ruff: noqa: PT009, PT027
# Запуск: cd scripts/msk3_ops && python3 -m unittest test_eu_auto_sticky -v (только stdlib).
import copy
import unittest

from eu_auto_sticky import (
    BACKUP,
    EXITS,
    GEMINI_BACKUP,
    GEMINI_TARGET,
    PORTS,
    TEST_PORT,
    TEST_TAG,
    add_auto,
    host_body,
    inbound_tag,
    squad_additions,
    use_burst,
)


RB_DOMAIN = 'ext:geosite-ru-blocked.dat:ru-blocked'
RB_IP = 'ext:geoip-ru-blocked.dat:ru-blocked'


def _group(tag, dest):
    return [
        {'domain': ['geosite:youtube'], 'inboundTag': [tag], 'outboundTag': 'direct'},
        {'domain': [RB_DOMAIN], 'inboundTag': [tag], **dest},
        {'ip': [RB_IP], 'inboundTag': [tag], **dest},
        {'inboundTag': [tag], 'outboundTag': 'direct'},
    ]


def profile():
    """Урезанный профиль MSK-4 в форме прода (2026-10-20), Reality-ключи фиктивные."""
    reality = {
        'target': '127.0.0.1:8443',
        'shortIds': ['K'],
        'privateKey': 'K',
        'serverNames': ['zayavka.fenix-soft.ru', 'max.ru', 'vk.com'],
        'minClientVer': '25.9.11',
    }

    def inbound(tag, port):
        return {
            'tag': tag,
            'port': port,
            'listen': '0.0.0.0',
            'protocol': 'vless',
            'settings': {'clients': [], 'decryption': 'none'},
            'sniffing': {'enabled': True, 'routeOnly': True, 'destOverride': ['http', 'tls', 'quic']},
            'streamSettings': {'network': 'raw', 'security': 'reality', 'realitySettings': copy.deepcopy(reality)},
        }

    ss = ['SS_OUTBOUND_' + x for x in ['DE1', 'FI1', 'FI2', 'NL1', 'SE1', 'UK1']]
    return {
        'dns': {},
        'log': {'loglevel': 'warning'},
        'routing': {
            'domainStrategy': 'IPIfNonMatch',
            'balancers': [
                {
                    'tag': 'SS_EU_BALANCER',
                    'selector': list(ss),
                    'strategy': {'type': 'leastPing'},
                    'fallbackTag': 'SS_OUTBOUND_NL1',
                },
                {
                    'tag': 'GEMINI_BALANCER',
                    'selector': ['SS_OUTBOUND_DE1', 'SS_OUTBOUND_FI1', 'SS_OUTBOUND_FI2', 'SS_OUTBOUND_UK1'],
                    'strategy': {'type': 'leastPing'},
                    'fallbackTag': 'SS_OUTBOUND_DE1',
                },
            ],
            'rules': [
                {'domain': ['geosite:private'], 'outboundTag': 'block'},
                {'domain': ['geosite:category-ads-all'], 'outboundTag': 'block'},
                {'protocol': ['bittorrent'], 'outboundTag': 'block'},
                {'ip': ['geoip:private'], 'outboundTag': 'block'},
                {'domain': ['geosite:google-gemini'], 'balancerTag': 'GEMINI_BALANCER'},
                {'inboundTag': ['vless-simple'], 'outboundTag': 'direct'},
                *_group('vless-to-de1-ss', {'outboundTag': 'SS_OUTBOUND_DE1'}),
                *_group('vless-to-eu-auto', {'balancerTag': 'SS_EU_BALANCER'}),
                *_group('vless-ru-to-fi2', {'outboundTag': 'SS_OUTBOUND_FI2'}),
            ],
        },
        'inbounds': [
            inbound('vless-simple', 3444),
            inbound('vless-to-de1-ss', 3442),
            inbound('vless-to-eu-auto', 3445),
            inbound('vless-ru-to-fi2', 3446),
        ],
        'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}, {'tag': 'block', 'protocol': 'blackhole'}]
        + [{'tag': t, 'protocol': 'shadowsocks'} for t in ss],
        'observatory': {
            'probeUrl': 'https://www.gstatic.com/generate_204',
            'probeInterval': '30s',
            'subjectSelector': ['SS_OUTBOUND_'],
            'enableConcurrency': True,
        },
    }


def add_and_get_rules():
    cfg = profile()
    add_auto(cfg)
    return cfg['routing']['rules']


def index_of(rules, inbound=None, domain=None, balancer=None):
    for i, r in enumerate(rules):
        if inbound is not None and r.get('inboundTag') != [inbound]:
            continue
        if inbound is None and 'inboundTag' in r:
            continue
        if domain is not None and r.get('domain') != domain:
            continue
        if balancer is not None and r.get('balancerTag') != balancer:
            continue
        return i
    raise AssertionError(f'rule not found: {inbound} {domain} {balancer}')


class ConstantsTest(unittest.TestCase):
    def test_constants_match_plan(self):
        self.assertEqual(EXITS, ['DE1', 'FI1', 'FI2', 'NL1', 'SE1', 'UK1'])
        self.assertEqual(PORTS, {'DE1': 3452, 'FI1': 3453, 'FI2': 3454, 'NL1': 3455, 'SE1': 3456, 'UK1': 3457})
        self.assertEqual(BACKUP, {'DE1': 'NL1', 'NL1': 'DE1', 'FI1': 'FI2', 'FI2': 'FI1', 'SE1': 'FI2', 'UK1': 'NL1'})
        self.assertEqual(GEMINI_BACKUP, {'DE1': 'FI1', 'FI1': 'FI2', 'FI2': 'FI1', 'UK1': 'DE1'})
        self.assertEqual(
            GEMINI_TARGET, {'DE1': 'DE1', 'FI1': 'FI1', 'FI2': 'FI2', 'UK1': 'UK1', 'NL1': 'FI2', 'SE1': 'FI2'}
        )
        self.assertEqual((TEST_TAG, TEST_PORT), ('vless-auto-test', 3458))
        self.assertEqual(inbound_tag('FI1'), 'vless-auto-fi1')


class AddAutoTest(unittest.TestCase):
    def test_adds_seven_inbounds_copied_from_auto(self):
        cfg = profile()
        add_auto(cfg)
        by_tag = {i['tag']: i for i in cfg['inbounds']}
        self.assertEqual(len(cfg['inbounds']), 4 + 7)
        self.assertEqual(by_tag['vless-auto-fi1']['port'], 3453)
        self.assertEqual(by_tag['vless-auto-test']['port'], 3458)
        self.assertEqual(by_tag['vless-auto-uk1']['streamSettings'], by_tag['vless-to-eu-auto']['streamSettings'])
        self.assertEqual(by_tag['vless-auto-uk1']['sniffing'], by_tag['vless-to-eu-auto']['sniffing'])
        self.assertIsNot(by_tag['vless-auto-uk1']['streamSettings'], by_tag['vless-to-eu-auto']['streamSettings'])
        self.assertEqual(by_tag['vless-to-eu-auto']['port'], 3445)

    def test_balancer_has_one_candidate_and_backup(self):
        cfg = profile()
        add_auto(cfg)
        b = {x['tag']: x for x in cfg['routing']['balancers']}
        self.assertEqual(len(b), 2 + 10)
        self.assertEqual(
            b['AUTO_SE1'],
            {
                'tag': 'AUTO_SE1',
                'selector': ['SS_OUTBOUND_SE1'],
                'strategy': {'type': 'leastPing'},
                'fallbackTag': 'SS_OUTBOUND_FI2',
            },
        )
        self.assertEqual(b['AUTO_GEM_UK1']['selector'], ['SS_OUTBOUND_UK1'])
        self.assertEqual(b['AUTO_GEM_UK1']['fallbackTag'], 'SS_OUTBOUND_DE1')
        self.assertNotIn('AUTO_GEM_NL1', b)
        self.assertNotIn('AUTO_GEM_SE1', b)

    def test_probe_rule_first_in_port_group_and_after_blocks(self):
        rules = add_and_get_rules()
        i = index_of(rules, inbound='vless-auto-de1', domain=['full:cp.cloudflare.com'])
        self.assertEqual(rules[i]['balancerTag'], 'AUTO_DE1')
        self.assertGreater(i, index_of(rules, domain=['geosite:private']))
        self.assertEqual(rules[i + 1]['domain'], ['geosite:youtube'])

    def test_port_group_follows_auto_group_in_exit_order(self):
        rules = add_and_get_rules()
        last_auto = max(i for i, r in enumerate(rules) if r.get('inboundTag') == ['vless-to-eu-auto'])
        start = last_auto + 1
        for x in EXITS:
            t, bal = inbound_tag(x), 'AUTO_' + x
            self.assertEqual(
                rules[start : start + 5],
                [
                    {'domain': ['full:cp.cloudflare.com'], 'inboundTag': [t], 'balancerTag': bal},
                    {'domain': ['geosite:youtube'], 'inboundTag': [t], 'outboundTag': 'direct'},
                    {'domain': [RB_DOMAIN], 'inboundTag': [t], 'balancerTag': bal},
                    {'ip': [RB_IP], 'inboundTag': [t], 'balancerTag': bal},
                    {'inboundTag': [t], 'outboundTag': 'direct'},
                ],
            )
            start += 5
        self.assertEqual(rules[start], {'inboundTag': ['vless-auto-test'], 'outboundTag': 'block'})
        self.assertEqual(rules[start + 1]['inboundTag'], ['vless-ru-to-fi2'])

    def test_gemini_on_ports_precedes_general_gemini_rule(self):
        rules = add_and_get_rules()
        general = index_of(rules, balancer='GEMINI_BALANCER')
        se1 = index_of(rules, inbound='vless-auto-se1', domain=['geosite:google-gemini'])
        self.assertLess(se1, general)
        self.assertGreater(se1, index_of(rules, domain=['geosite:private']))
        self.assertEqual(rules[se1]['balancerTag'], 'AUTO_GEM_FI2')
        de1 = index_of(rules, inbound='vless-auto-de1', domain=['geosite:google-gemini'])
        self.assertEqual(rules[de1]['balancerTag'], 'AUTO_GEM_DE1')
        self.assertEqual(general - de1, 6)

    def test_existing_rules_untouched_and_idempotent(self):
        cfg = profile()
        before = list(cfg['routing']['rules'])
        snapshot = copy.deepcopy(before)
        changes = add_auto(cfg)
        self.assertTrue(changes)
        self.assertEqual([r for r in cfg['routing']['rules'] if r in before], before)
        self.assertEqual(before, snapshot)
        self.assertEqual(len(cfg['routing']['rules']), len(before) + 6 + 31)
        after = copy.deepcopy(cfg)
        self.assertEqual(add_auto(cfg), [])
        self.assertEqual(cfg, after)

    def test_partial_rule_group_is_refused(self):
        cfg = profile()
        add_auto(cfg)
        rules = cfg['routing']['rules']
        del rules[index_of(rules, inbound='vless-auto-nl1', domain=['geosite:youtube'])]
        with self.assertRaises(ValueError):
            add_auto(cfg)

    def test_port_taken_by_other_inbound_is_refused(self):
        cfg = profile()
        cfg['inbounds'][0]['port'] = 3455
        with self.assertRaises(ValueError):
            add_auto(cfg)

    def test_missing_exit_outbound_is_refused(self):
        cfg = profile()
        cfg['outbounds'] = [o for o in cfg['outbounds'] if o['tag'] != 'SS_OUTBOUND_SE1']
        with self.assertRaises(ValueError):
            add_auto(cfg)


class BurstTest(unittest.TestCase):
    def test_burst_replaces_observatory(self):
        cfg = profile()
        self.assertTrue(use_burst(cfg))
        self.assertNotIn('observatory', cfg)
        self.assertEqual(cfg['burstObservatory']['subjectSelector'], ['SS_OUTBOUND_'])
        self.assertEqual(
            cfg['burstObservatory']['pingConfig'],
            {'destination': 'https://www.gstatic.com/generate_204', 'interval': '15s', 'sampling': 4, 'timeout': '5s'},
        )
        self.assertFalse(use_burst(cfg))


class GlueHelpersTest(unittest.TestCase):
    def test_host_body_copies_auto_host_and_skips_nulls(self):
        auto = {
            'uuid': 'u-auto',
            'viewPosition': 0,
            'remark': '🇪🇺 [AUTO] Европа',
            'address': 'msk4.zanity.net',
            'port': 3445,
            'sni': None,
            'alpn': 'h2,http/1.1',
            'fingerprint': 'qq',
            'securityLayer': 'DEFAULT',
            'isHidden': False,
            'overrideSniFromAddress': False,
            'inbound': {'configProfileUuid': 'p', 'configProfileInboundUuid': 'i-auto'},
            'nodes': ['n1', 'n2'],
            'xrayJsonTemplateUuid': 't',
            'tags': [],
            'mapper': {},
        }
        body = host_body(auto, 'EU AUTO · FI1', 3453, 'p', 'i-fi1', hidden=True)
        self.assertEqual(
            body,
            {
                'remark': 'EU AUTO · FI1',
                'address': 'msk4.zanity.net',
                'port': 3453,
                'inbound': {'configProfileUuid': 'p', 'configProfileInboundUuid': 'i-fi1'},
                'isHidden': True,
                'alpn': 'h2,http/1.1',
                'fingerprint': 'qq',
                'securityLayer': 'DEFAULT',
                'overrideSniFromAddress': False,
                'nodes': ['n1', 'n2'],
            },
        )

    def test_squad_additions_six_to_auto_squads_test_only_to_testers(self):
        ib = {inbound_tag(x): 'u-' + x for x in EXITS} | {TEST_TAG: 'u-test'}
        squads = [
            {'name': 'Default-Squad', 'uuid': 'd', 'inbounds': [{'tag': 'vless-to-eu-auto', 'uuid': 'u-auto'}]},
            {'name': 'Bridge Profile', 'uuid': 'b', 'inbounds': [{'tag': 'vless-simple', 'uuid': 'u-s'}]},
            {'name': 'Testers', 'uuid': 't', 'inbounds': [{'tag': 'vless-x', 'uuid': 'u-x'}]},
        ]
        plan = squad_additions(squads, ib)
        self.assertEqual(
            plan,
            {
                'Default-Squad': [inbound_tag(x) for x in EXITS],
                'Testers': [TEST_TAG],
            },
        )
        squads[0]['inbounds'].append({'tag': 'vless-auto-de1', 'uuid': 'u-DE1'})
        self.assertNotIn('vless-auto-de1', squad_additions(squads, ib)['Default-Squad'])


if __name__ == '__main__':
    unittest.main()
