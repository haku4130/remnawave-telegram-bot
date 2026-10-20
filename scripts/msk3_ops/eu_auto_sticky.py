# AUTO Европа без смены выхода посреди сеанса (2026-10, план docs/superpowers/plans/2026-10-19-eu-auto-sticky-exit.md).
# Выход выбирает клиент (Happ/INCY по шаблону XRAY_JSON), msk4 только держит по порту на выход:
#   profile — профиль MSK-4: 7 входов vless-auto-*, балансеры AUTO_<X>/AUTO_GEM_<X> на один выход с запасным,
#             правила по портам (зонд первым), Gemini по портам, observatory -> burstObservatory;
#   panel   — новые входы в activeInbounds ноды MSK-4 (+ перезапуск), шесть vless-auto-* во все сквады
#             с vless-to-eu-auto и в Testers (иначе панель выкинет виртуальный хост шаблона из подписки),
#             vless-auto-test — только в Testers;
#   hosts   — скрытые хосты «EU AUTO · <X>» и видимый тестовый «🧪 AUTO Европа (тест)», поля — как у хоста AUTO.
#   restore-profile <backup.json> — откат профиля MSK-4 из бэкапа, который пишет profile --apply.
# Без --apply только печатает; с --apply — JSON-бэкап в /root/fi2-migration/, затем запись.
# После profile/panel --apply ждёт до 90 с, пока нода MSK-4 подключится; если нет — код 1 и команда отката.
# Не печатает ключи Reality, UUID пользователей и профиль целиком.
# Запуск на msk3: REMNAWAVE_API_URL=... REMNAWAVE_API_KEY=... python3 eu_auto_sticky.py profile|panel|hosts [--apply] | restore-profile <backup.json> [--apply]
import copy
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request


PROFILE = 'MSK-4'
NODE = 'MSK-4'
AUTO_TAG = 'vless-to-eu-auto'
AUTO_HOST = '🇪🇺 [AUTO] Европа'
GEMINI_BALANCER = 'GEMINI_BALANCER'
TESTERS = 'Testers'
BACKUP_DIR = '/root/fi2-migration'
HEALTH_TRIES, HEALTH_INTERVAL = 18, 5  # ~90 с после записи

EXITS = ['DE1', 'FI1', 'FI2', 'NL1', 'SE1', 'UK1']
PORTS = {'DE1': 3452, 'FI1': 3453, 'FI2': 3454, 'NL1': 3455, 'SE1': 3456, 'UK1': 3457}
BACKUP = {'DE1': 'NL1', 'NL1': 'DE1', 'FI1': 'FI2', 'FI2': 'FI1', 'SE1': 'FI2', 'UK1': 'NL1'}
# Порт -> выход для Gemini: свой, если выход в наборе GEMINI_BALANCER, иначе FI2.
GEMINI_TARGET = {'DE1': 'DE1', 'FI1': 'FI1', 'FI2': 'FI2', 'UK1': 'UK1', 'NL1': 'FI2', 'SE1': 'FI2'}
# Выход Gemini -> запасной (тоже из набора Gemini).
GEMINI_BACKUP = {'DE1': 'FI1', 'FI1': 'FI2', 'FI2': 'FI1', 'UK1': 'DE1'}
PROBE_DOMAIN = 'cp.cloudflare.com'
BURST = {
    'subjectSelector': ['SS_OUTBOUND_'],
    'pingConfig': {
        'destination': 'https://www.gstatic.com/generate_204',
        'interval': '15s',
        'sampling': 4,
        'timeout': '5s',
    },
}
TEST_TAG = 'vless-auto-test'
TEST_PORT = 3458
TEST_HOST = '🧪 AUTO Европа (тест)'

RB_DOMAIN = 'ext:geosite-ru-blocked.dat:ru-blocked'
RB_IP = 'ext:geoip-ru-blocked.dat:ru-blocked'

# Поля хоста AUTO, которые копируются в новые хосты (null пропускается).
HOST_COPY_FIELDS = [
    'address',
    'path',
    'sni',
    'host',
    'alpn',
    'fingerprint',
    'securityLayer',
    'isDisabled',
    'xhttpExtraParams',
    'muxParams',
    'sockoptParams',
    'finalMask',
    'serverDescription',
    'pinnedPeerCertSha256',
    'verifyPeerCertByName',
    'shuffleHost',
    'mihomoX25519',
    'mihomoIpVersion',
    'overrideSniFromAddress',
    'keepSniBlank',
    'vlessRouteId',
    'nodes',
    'excludedInternalSquads',
    'excludeFromSubscriptionTypes',
]


def inbound_tag(exit_name):
    return 'vless-auto-' + exit_name.lower()


def host_remark(exit_name):
    return 'EU AUTO · ' + exit_name


def _ss(exit_name):
    return 'SS_OUTBOUND_' + exit_name


def _new_inbounds():
    return [(inbound_tag(x), PORTS[x]) for x in EXITS] + [(TEST_TAG, TEST_PORT)]


def _balancer(tag, exit_name, backup):
    return {'tag': tag, 'selector': [_ss(exit_name)], 'strategy': {'type': 'leastPing'}, 'fallbackTag': _ss(backup)}


def _balancers():
    out = [_balancer('AUTO_' + x, x, BACKUP[x]) for x in EXITS]
    out += [_balancer('AUTO_GEM_' + x, x, GEMINI_BACKUP[x]) for x in EXITS if x in GEMINI_BACKUP]
    return out


def _gemini_rules():
    return [
        {
            'domain': ['geosite:google-gemini'],
            'inboundTag': [inbound_tag(x)],
            'balancerTag': 'AUTO_GEM_' + GEMINI_TARGET[x],
        }
        for x in EXITS
    ]


def _port_rules():
    rules = []
    for x in EXITS:
        t, bal = [inbound_tag(x)], 'AUTO_' + x
        rules += [
            {'domain': ['full:' + PROBE_DOMAIN], 'inboundTag': t, 'balancerTag': bal},
            {'domain': ['geosite:youtube'], 'inboundTag': t, 'outboundTag': 'direct'},
            {'domain': [RB_DOMAIN], 'inboundTag': t, 'balancerTag': bal},
            {'ip': [RB_IP], 'inboundTag': t, 'balancerTag': bal},
            {'inboundTag': t, 'outboundTag': 'direct'},
        ]
    rules.append({'inboundTag': [TEST_TAG], 'outboundTag': 'block'})
    return rules


def _missing(rules, block):
    """True — блока нет вовсе, False — есть целиком; частичный блок — ошибка."""
    present = [r in rules for r in block]
    if all(present):
        return False
    if any(present):
        raise ValueError('правила новых портов есть лишь частично — профиль правили руками, разберитесь')
    return True


def add_auto(cfg):
    """Добавляет входы, балансеры и правила AUTO по портам. Мутирует cfg, возвращает описания изменений."""
    outbounds = {o.get('tag') for o in cfg.get('outbounds', [])}
    lost = sorted({_ss(x) for x in EXITS} - outbounds)
    if lost:
        raise ValueError('в профиле нет outbound: ' + ', '.join(lost))
    changes = []

    inbounds = cfg['inbounds']
    template = next(i for i in inbounds if i['tag'] == AUTO_TAG)
    for tag, port in _new_inbounds():
        if any(i['tag'] == tag for i in inbounds):
            continue
        busy = [i['tag'] for i in inbounds if i.get('port') == port]
        if busy:
            raise ValueError(f'порт {port} занят входом {busy[0]}')
        ib = copy.deepcopy(template)
        ib['tag'], ib['port'] = tag, port
        inbounds.append(ib)
        changes.append(f'вход + {tag} :{port}')

    routing = cfg['routing']
    balancers = routing.setdefault('balancers', [])
    have = {b['tag'] for b in balancers}
    for b in _balancers():
        if b['tag'] not in have:
            balancers.append(b)
            changes.append('балансер + {} [{}] запасной {}'.format(b['tag'], b['selector'][0], b['fallbackTag']))

    rules = routing['rules']
    gemini = _gemini_rules()
    if _missing(rules, gemini):
        at = next(i for i, r in enumerate(rules) if r.get('balancerTag') == GEMINI_BALANCER and 'inboundTag' not in r)
        rules[at:at] = gemini
        changes.append(f'правила Gemini по портам + {len(gemini)} (перед общим правилом {GEMINI_BALANCER})')

    ports = _port_rules()
    if _missing(rules, ports):
        at = max(i for i, r in enumerate(rules) if r.get('inboundTag') == [AUTO_TAG]) + 1
        rules[at:at] = ports
        changes.append(f'правила портов + {len(ports)} (после группы {AUTO_TAG})')
    return changes


def use_burst(cfg):
    """observatory -> burstObservatory из BURST. Возвращает, изменился ли профиль."""
    if 'observatory' not in cfg and cfg.get('burstObservatory') == BURST:
        return False
    cfg.pop('observatory', None)
    cfg['burstObservatory'] = copy.deepcopy(BURST)
    return True


def host_body(auto_host, remark, port, profile_uuid, inbound_uuid, hidden):
    """Тело POST /api/hosts: поля хоста AUTO (без null), свой remark/порт/вход/видимость."""
    body = {
        'remark': remark,
        'address': auto_host['address'],
        'port': port,
        'inbound': {'configProfileUuid': profile_uuid, 'configProfileInboundUuid': inbound_uuid},
        'isHidden': hidden,
    }
    for k in HOST_COPY_FIELDS:
        if k not in body and auto_host.get(k) is not None:
            body[k] = copy.deepcopy(auto_host[k])
    return body


def squad_additions(squads, inbound_uuids):
    """Имя сквада -> теги новых входов, которых в нём ещё нет."""
    six = [inbound_tag(x) for x in EXITS]
    plan = {}
    for sq in squads:
        tags = {i['tag'] for i in sq['inbounds']}
        # Панель выкидывает виртуальный хост шаблона из подписки, если инбаунд любого из подмешиваемых хостов
        # не входит в сквады пользователя, поэтому тестеры получают и шесть, и тестовый вход.
        want = list(six) if AUTO_TAG in tags or sq['name'] == TESTERS else []
        if sq['name'] == TESTERS:
            want.append(TEST_TAG)
        add = [t for t in want if t not in tags and inbound_uuids.get(t) not in {i['uuid'] for i in sq['inbounds']}]
        if add:
            plan[sq['name']] = add
    return plan


# ---------- API ----------


def _call(method, path, body=None):
    req = urllib.request.Request(os.environ['REMNAWAVE_API_URL'].rstrip('/') + path, method=method)
    req.add_header('Authorization', 'Bearer ' + os.environ['REMNAWAVE_API_KEY'])
    data = None
    if body is not None:
        req.add_header('Content-Type', 'application/json')
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode('utf-8', 'replace')
        print(f'HTTP {method} {path} {e.code}: {body}', file=sys.stderr)  # заголовки запроса не печатаем
        raise


def restore_body(backup):
    """Тело PATCH /api/config-profiles из бэкапа cmd_profile (полный ответ GET профиля)."""
    if backup.get('name') != PROFILE or not backup.get('uuid') or not isinstance(backup.get('config'), dict):
        raise ValueError(f'это не бэкап профиля {PROFILE} (нужны name, uuid и config)')
    return {'uuid': backup['uuid'], 'config': backup['config']}


def config_stats(cfg):
    obs = [k for k in ('observatory', 'burstObservatory') if k in cfg]
    return {
        'inbounds': len(cfg.get('inbounds', [])),
        'balancers': len(cfg.get('routing', {}).get('balancers', [])),
        'rules': len(cfg.get('routing', {}).get('rules', [])),
        'observer': '+'.join(obs) or '—',
    }


def _backup(kind, obj):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    path = f'{BACKUP_DIR}/{kind}-backup-{stamp}.json'
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print('backup:', path)
    return path


def wait_node_connected(tries=HEALTH_TRIES, interval=HEALTH_INTERVAL, sleep=time.sleep):
    """Опрашивает /api/nodes, пока MSK-4 не окажется подключённой два опроса подряд (один может поймать
    момент до реального отключения). True — подключилась, False — не успела."""
    streak = 0
    for _ in range(tries):
        sleep(interval)
        node = next((n for n in _call('GET', '/api/nodes')['response'] if n.get('name') == NODE), None)
        up = bool(node) and node.get('isConnected') and not node.get('isConnecting') and not node.get('isDisabled')
        streak = streak + 1 if up else 0
        if streak >= 2:
            print(f'нода {NODE}: подключена')
            return True
    print(f'нода {NODE}: не подключена за {tries * interval} с')
    return False


def verify_node(backup_path):
    """0 — нода поднялась; 1 — нет, печатает точную команду отката."""
    if wait_node_connected():
        return 0
    print('!' * 70)
    print(f'!!! НОДА {NODE} НЕ ПОДНЯЛАСЬ. Откат профиля:')
    print(f'!!! python3 eu_auto_sticky.py restore-profile {backup_path} --apply')
    print('!' * 70)
    return 1


def _profile_summary():
    lst = _call('GET', '/api/config-profiles')['response']
    lst = lst.get('configProfiles', lst)
    return next(p for p in lst if p['name'] == PROFILE)


def _inbound_uuids(prof):
    return {i['tag']: i['uuid'] for i in prof['inbounds']}


def _need_inbounds(ib, apply):
    missing = [t for t, _ in _new_inbounds() if t not in ib]
    if missing:
        print('нет в профиле (сначала profile --apply):', ', '.join(missing))
        if apply:
            print('ABORT')
            return False
    return True


def cmd_profile(apply):
    uuid = _profile_summary()['uuid']
    cfg = _call('GET', '/api/config-profiles/' + uuid)['response']['config']
    n_in, n_bal, n_rules = len(cfg['inbounds']), len(cfg['routing']['balancers']), len(cfg['routing']['rules'])
    changes = add_auto(cfg)
    burst = use_burst(cfg)
    for c in changes:
        print('  ', c)
    d_in = len(cfg['inbounds']) - n_in
    d_bal = len(cfg['routing']['balancers']) - n_bal
    d_rules = len(cfg['routing']['rules']) - n_rules
    burst_note = 'observatory -> burstObservatory' if burst else 'burstObservatory уже стоит'
    print(f'{PROFILE}: входов +{d_in}, балансеров +{d_bal}, правил +{d_rules}, {burst_note}')
    if not changes and not burst:
        print('нечего менять')
        return 0
    if not apply:
        print('DRY RUN')
        return 0
    saved = _backup('msk4-profile', _call('GET', '/api/config-profiles/' + uuid)['response'])
    print('PATCH ok:', _call('PATCH', '/api/config-profiles', {'uuid': uuid, 'config': cfg})['response']['name'])
    return verify_node(saved)


def cmd_restore_profile(path, apply):
    with open(path) as f:
        body = restore_body(json.load(f))
    live = _call('GET', '/api/config-profiles/' + body['uuid'])['response']
    old, now = config_stats(body['config']), config_stats(live['config'])
    for k, label in (('inbounds', 'входов'), ('balancers', 'балансеров'), ('rules', 'правил')):
        print(f'{label}: бэкап {old[k]}, сейчас {now[k]}')
    print(f'наблюдатель: бэкап {old["observer"]}, сейчас {now["observer"]}')
    if live['config'] == body['config']:
        print('нечего менять')
        return 0
    if not apply:
        print('DRY RUN')
        return 0
    _backup('msk4-profile', live)
    print('PATCH ok:', _call('PATCH', '/api/config-profiles', body)['response']['name'])
    if wait_node_connected():
        return 0
    print(f'!!! НОДА {NODE} НЕ ПОДНЯЛАСЬ и после отката — разбирайтесь вручную (свежий бэкап выше).')
    return 1


def cmd_panel(apply):
    prof = _profile_summary()
    ib = _inbound_uuids(prof)
    if not _need_inbounds(ib, apply):
        return 1
    nodes = _call('GET', '/api/nodes')['response']
    node = next(n for n in nodes if n['name'] == NODE)
    cp = node['configProfile']
    if cp.get('activeConfigProfileUuid') != prof['uuid']:
        print(f'ABORT: у ноды {NODE} другой профиль')
        return 1
    active = [i['uuid'] for i in cp['activeInbounds']]
    active_tags = {i['tag'] for i in cp['activeInbounds']}
    node_add = [t for t, _ in _new_inbounds() if t not in active_tags]
    print(f'нода {NODE}: activeInbounds {len(active)} -> {len(active) + len(node_add)}, + {node_add or "—"}')

    squads = _call('GET', '/api/internal-squads')['response']['internalSquads']
    plan = squad_additions(squads, ib)
    for sq in squads:
        print(f'сквад {sq["name"]:<15} + {plan.get(sq["name"]) or "—"}')
    if not node_add and not plan:
        print('нечего менять')
        return 0
    if not apply:
        print('DRY RUN')
        if node_add:
            n = len(node_add)
            print(f'  PATCH /api/nodes {{uuid, configProfile: {{activeConfigProfileUuid, activeInbounds: +{n}}}}}')
            print('  POST /api/nodes/<uuid>/actions/restart {uuid, forceRestart: true}')
        return 0

    _backup('nodes', nodes)
    _backup('squads', squads)
    saved = _backup('msk4-profile', _call('GET', '/api/config-profiles/' + prof['uuid'])['response'])
    if node_add:
        body = {
            'uuid': node['uuid'],
            'configProfile': {
                'activeConfigProfileUuid': prof['uuid'],
                'activeInbounds': active + [ib[t] for t in node_add],
            },
        }
        r = _call('PATCH', '/api/nodes', body)['response']
        print('нода', r['name'], '-> activeInbounds:', len(r['configProfile']['activeInbounds']))
        _call(
            'POST', '/api/nodes/{}/actions/restart'.format(node['uuid']), {'uuid': node['uuid'], 'forceRestart': True}
        )
        print('нода', NODE, 'перезапущена (forceRestart)')
    for sq in squads:
        add = plan.get(sq['name'])
        if not add:
            continue
        uuids = [i['uuid'] for i in sq['inbounds']] + [ib[t] for t in add]
        r = _call('PATCH', '/api/internal-squads', {'uuid': sq['uuid'], 'inbounds': uuids})['response']
        print('сквад', r['name'], '-> inbounds:', len(r['inbounds']))
    return verify_node(saved) if node_add else 0


def cmd_hosts(apply):
    prof = _profile_summary()
    ib = _inbound_uuids(prof)
    if not _need_inbounds(ib, apply):
        return 1
    hosts = _call('GET', '/api/hosts')['response']
    auto = next(h for h in hosts if h['remark'] == AUTO_HOST)
    existing = {h['remark'] for h in hosts}
    wanted = [(host_remark(x), PORTS[x], inbound_tag(x), True) for x in EXITS]
    wanted.append((TEST_HOST, TEST_PORT, TEST_TAG, False))
    bodies = []
    for remark, port, tag, hidden in wanted:
        if remark in existing:
            print('SKIP (уже есть):', remark)
            continue
        body = host_body(auto, remark, port, prof['uuid'], ib.get(tag, f'<{tag}>'), hidden)
        bodies.append(body)
        shown = {k: v for k, v in body.items() if k not in ('inbound', 'nodes')}
        shown['inbound'] = tag
        shown['nodes'] = len(body.get('nodes', []))
        print('POST /api/hosts', json.dumps(shown, ensure_ascii=False))
    if not bodies:
        print('нечего менять')
        return 0
    if not apply:
        print(f'DRY RUN: хостов +{len(bodies)}')
        return 0
    _backup('hosts', hosts)
    for body in bodies:
        r = _call('POST', '/api/hosts', body)['response']
        print(
            'создан:', r['remark'], '->', '{}:{}'.format(r['address'], r['port']), 'hidden' if r.get('isHidden') else ''
        )
    return 0


def main(argv):
    cmds = {'profile': cmd_profile, 'panel': cmd_panel, 'hosts': cmd_hosts}
    apply = '--apply' in argv
    args = [a for a in argv[2:] if a != '--apply']
    if len(argv) >= 2 and argv[1] == 'restore-profile' and len(args) == 1:
        return cmd_restore_profile(args[0], apply)
    if len(argv) < 2 or argv[1] not in cmds or args:
        print('usage: eu_auto_sticky.py profile|panel|hosts [--apply] | restore-profile <backup.json> [--apply]')
        return 2
    return cmds[argv[1]](apply)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
