# ruff: noqa: PT009, PT027
# Запуск: cd scripts/msk3_ops && python3 -m unittest test_json_routing -v (только stdlib).
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import json_routing
from json_routing import (
    AUTO_HOST_REMARK,
    AUTO_NAME,
    INJECT_PATTERN,
    TEST_HOST_REMARK,
    auto_template,
    find_host,
    profile_from_header,
    split_template,
    sync_templates,
)


DATA = Path(__file__).parent / 'testdata'
# Живой заголовок `routing` (публичный профиль RoscomVPN) и снимок split_template, снятый с версии до правки.
P = profile_from_header((DATA / 'routing_header.txt').read_text().strip())
SPLIT_SNAPSHOT = json.loads((DATA / 'split_snapshot.json').read_text())


class AutoTemplateTest(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(AUTO_NAME, 'EU AUTO balancer')
        self.assertEqual(AUTO_HOST_REMARK, '🇪🇺 [AUTO] Европа')
        self.assertEqual(TEST_HOST_REMARK, '🧪 AUTO Европа (тест)')
        self.assertEqual(INJECT_PATTERN, '^EU AUTO · ')

    def test_auto_template_has_no_proxy_outbound_tags(self):
        t = auto_template(P)
        self.assertFalse([r for r in t['routing']['rules'] if r.get('outboundTag') == 'proxy'])
        proxied = [r for r in t['routing']['rules'] if r.get('domain') == P['ProxySites']]
        self.assertEqual(proxied[0]['balancerTag'], 'EU_AUTO')

    def test_auto_template_keeps_direct_and_block_rules(self):
        split = split_template(P)['routing']['rules']
        auto = auto_template(P)['routing']['rules'][:-1]
        self.assertEqual(len(auto), len(split))
        for s, a in zip(split, auto, strict=True):
            if s['outboundTag'] == 'proxy':
                self.assertEqual(a, {**{k: v for k, v in s.items() if k != 'outboundTag'}, 'balancerTag': 'EU_AUTO'})
            else:
                self.assertEqual(a, s)

    def test_auto_template_catch_all_is_ip_rule_last(self):
        last = auto_template(P)['routing']['rules'][-1]
        self.assertEqual(last, {'type': 'field', 'ip': ['0.0.0.0/0', '::/0'], 'balancerTag': 'EU_AUTO'})

    def test_auto_template_observatory_and_inject(self):
        t = auto_template(P)
        self.assertEqual(t['observatory']['probeInterval'], '168h')
        self.assertTrue(t['observatory']['enableConcurrency'])
        self.assertEqual(t['observatory']['subjectSelector'], ['eu'])
        self.assertEqual(t['observatory']['probeUrl'], 'https://cp.cloudflare.com/generate_204')
        self.assertEqual(
            t['remnawave'],
            {
                'injectHosts': [
                    {
                        'selector': {'type': 'remarkRegex', 'pattern': '^EU AUTO · '},
                        'selectFrom': 'HIDDEN',
                        'tagPrefix': 'eu',
                    }
                ],
                'addVirtualHostAsOutbound': False,
            },
        )
        self.assertEqual(
            t['routing']['balancers'],
            [{'tag': 'EU_AUTO', 'selector': ['eu'], 'strategy': {'type': 'leastPing'}, 'fallbackTag': 'eu'}],
        )

    def test_auto_template_does_not_mutate_split(self):
        before = split_template(P)
        auto_template(P)
        self.assertEqual(split_template(P), before)

    def test_split_template_unchanged(self):
        self.assertEqual(split_template(P), SPLIT_SNAPSHOT)


class FakePanel:
    """Подмена json_routing.call: записывает запросы, отдаёт заранее заданные ответы на GET."""

    def __init__(self, templates, hosts=()):
        self.templates = list(templates)
        self.hosts = list(hosts)
        self.writes = []

    def __call__(self, method, path, body=None):
        if method == 'GET' and path == '/api/subscription-templates':
            return {'response': {'templates': self.templates}}
        if method == 'GET' and path == '/api/hosts':
            return {'response': self.hosts}
        if method == 'GET' and path.startswith('/api/subscription-templates/'):
            uuid = path.rsplit('/', 1)[1]
            return {'response': next(t for t in self.templates if t['uuid'] == uuid)}
        self.writes.append((method, path, body))
        if method == 'POST':
            return {'response': {'uuid': 'new-uuid', 'name': body['name']}}
        return {}


def tpl(name, uuid, template_json):
    return {'name': name, 'uuid': uuid, 'templateType': 'XRAY_JSON', 'templateJson': template_json}


class SyncTest(unittest.TestCase):
    def run_sync(self, templates):
        panel = FakePanel(templates)
        with mock.patch.object(json_routing, 'call', panel), contextlib.redirect_stdout(io.StringIO()):
            sync_templates(P)
        return panel.writes

    def test_sync_without_auto_template_does_not_create_it(self):
        writes = self.run_sync(
            [
                tpl('RoscomVPN split', 'u1', split_template(P)),
                tpl('Full tunnel RU', 'u2', json_routing.full_template(P)),
            ]
        )
        self.assertEqual(writes, [])

    def test_sync_updates_stale_auto_template(self):
        writes = self.run_sync(
            [
                tpl('RoscomVPN split', 'u1', split_template(P)),
                tpl('Full tunnel RU', 'u2', json_routing.full_template(P)),
                tpl(AUTO_NAME, 'u3', {'stale': True}),
            ]
        )
        self.assertEqual(
            writes, [('PATCH', '/api/subscription-templates', {'uuid': 'u3', 'templateJson': auto_template(P)})]
        )

    def test_sync_leaves_fresh_auto_template(self):
        writes = self.run_sync(
            [
                tpl('RoscomVPN split', 'u1', split_template(P)),
                tpl('Full tunnel RU', 'u2', json_routing.full_template(P)),
                tpl(AUTO_NAME, 'u3', auto_template(P)),
            ]
        )
        self.assertEqual(writes, [])


HOSTS = [
    {'uuid': 'h1', 'remark': AUTO_HOST_REMARK, 'isDisabled': False, 'xrayJsonTemplateUuid': None},
    {'uuid': 'h2', 'remark': TEST_HOST_REMARK, 'isDisabled': False, 'xrayJsonTemplateUuid': None},
    {'uuid': 'h3', 'remark': 'dup', 'isDisabled': False, 'xrayJsonTemplateUuid': None},
    {'uuid': 'h4', 'remark': 'dup', 'isDisabled': False, 'xrayJsonTemplateUuid': None},
]


class HostAssignTest(unittest.TestCase):
    def test_find_host_exact(self):
        self.assertEqual(find_host(HOSTS, TEST_HOST_REMARK)['uuid'], 'h2')

    def test_find_host_refuses_zero_and_many(self):
        with self.assertRaises(SystemExit) as zero:
            find_host(HOSTS, 'нет такого')
        self.assertIn('нет такого', str(zero.exception))
        with self.assertRaises(SystemExit) as many:
            find_host(HOSTS, 'dup')
        self.assertIn('2', str(many.exception))

    def run_assign(self, templates, remark, assign=True):
        """Возвращает (записи в панель, файлы бэкапа, содержимое бэкапа, SystemExit или None)."""
        panel = FakePanel(templates, HOSTS)
        old = Path.cwd()
        refused = None
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with mock.patch.object(json_routing, 'call', panel), contextlib.redirect_stdout(io.StringIO()):
                    try:
                        (json_routing.assign_auto if assign else json_routing.unassign_auto)(remark)
                    except SystemExit as e:
                        refused = e
                backups = sorted(Path(d).glob('hosts-backup-*.json'))
                saved = json.loads(backups[0].read_text()) if backups else None
            finally:
                os.chdir(old)
        return panel.writes, backups, saved, refused

    def test_assign_patches_host_after_backup(self):
        writes, backups, saved, refused = self.run_assign([tpl(AUTO_NAME, 'u3', {})], TEST_HOST_REMARK)
        self.assertIsNone(refused)
        self.assertEqual(
            writes, [('PATCH', '/api/hosts', {'uuid': 'h2', 'isDisabled': False, 'xrayJsonTemplateUuid': 'u3'})]
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(saved, HOSTS)

    def test_unassign_clears_template(self):
        writes, backups, _, refused = self.run_assign([tpl(AUTO_NAME, 'u3', {})], TEST_HOST_REMARK, assign=False)
        self.assertIsNone(refused)
        self.assertEqual(
            writes, [('PATCH', '/api/hosts', {'uuid': 'h2', 'isDisabled': False, 'xrayJsonTemplateUuid': None})]
        )
        self.assertEqual(len(backups), 1)

    def test_assign_refuses_without_template_and_writes_nothing(self):
        writes, backups, _, refused = self.run_assign([], TEST_HOST_REMARK)
        self.assertIn('auto-create', str(refused))
        self.assertEqual((writes, backups), ([], []))

    def test_assign_refuses_ambiguous_host_and_writes_nothing(self):
        writes, backups, _, refused = self.run_assign([tpl(AUTO_NAME, 'u3', {})], 'dup')
        self.assertIsNotNone(refused)
        self.assertEqual((writes, backups), ([], []))


if __name__ == '__main__':
    unittest.main()
