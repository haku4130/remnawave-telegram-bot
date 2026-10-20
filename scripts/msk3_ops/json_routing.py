# Российские локации — весь трафик через VPN даже у Happ/INCY с роутингом RoscomVPN.
# Happ и INCY не накладывают свой профиль маршрутизации на полные JSON-конфиги, поэтому:
#   * шаблон XRAY_JSON «RoscomVPN split» — те же правила, что в профиле из заголовка `routing` (block -> proxy -> direct);
#   * шаблон «Full tunnel RU» — всё в proxy, кроме локалки; назначается трём российским хостам;
#   * правило ответа: Happ/INCY -> XRAY_JSON с шаблоном «RoscomVPN split» (на время теста — только HWID из TEST_HWIDS);
#   * шаблон «EU AUTO balancer» — split, где `proxy` заменён клиентским балансировщиком EU_AUTO по скрытым хостам
#     «EU AUTO · …» (выход выбирает устройство, не панель). sync обновляет его, только если он уже создан (auto-create).
# Теги берутся из живого заголовка `routing`, чтобы совпадать с урезанными (chunk) geo-файлами Happ.
# Запуск: python3 json_routing.py [render|apply|sync|auto-create|auto-assign <remark>|auto-unassign <remark>] ;
# render ничего не пишет.
import base64, copy, datetime, json, os, sys, urllib.request

API = os.environ.get('REMNAWAVE_API_URL', '').rstrip('/'); KEY = os.environ.get('REMNAWAVE_API_KEY', '')
SPLIT_NAME, FULL_NAME = 'RoscomVPN split', 'Full tunnel RU'
RULE_NAME = 'Happ INCY JSON'
AUTO_NAME = 'EU AUTO balancer'
AUTO_HOST_REMARK = '🇪🇺 [AUTO] Европа'
TEST_HOST_REMARK = '🧪 AUTO Европа (тест)'
INJECT_PATTERN = '^EU AUTO · '  # remark скрытых хостов-выходов, которые панель подмешивает в конфиг
RU_HOST_PREFIX = '🇷🇺 '
DIRECT_ALWAYS = ['domain:gosuslugi.ru']  # недоступны с наших RU-нод, см. full_template
# Яндекс DNS отдаёт www.gosuslugi.ru 8 адресов, половина (часть 109.207.x) из-за рубежа молчит; Google отдаёт
# внешние 213.59.x — они открываются и из-за рубежа. Сервер с domains стоит раньше Яндекса — xray спросит его первым.
GOOGLE_DNS_DOMAINS = ['domain:gosuslugi.ru']
TEST_HWIDS = [h for h in os.environ.get('TEST_HWIDS', '').split(',') if h]


def call(m, p, b=None):
    r = urllib.request.Request(API + p, method=m); r.add_header('Authorization', 'Bearer ' + KEY)
    if b is not None: r.add_header('Content-Type', 'application/json'); b = json.dumps(b).encode()
    with urllib.request.urlopen(r, b, timeout=60) as x:
        t = x.read(); return json.loads(t) if t else {}


def profile_from_header(value):
    b64 = value.split('/onadd/', 1)[1]
    return json.loads(base64.b64decode(b64 + '=' * (-len(b64) % 4)))


INBOUNDS = [
    {'tag': 'socks', 'port': 10808, 'listen': '127.0.0.1', 'protocol': 'socks',
     'settings': {'udp': True, 'auth': 'noauth'},
     'sniffing': {'enabled': True, 'routeOnly': False, 'destOverride': ['http', 'tls', 'quic']}},
    {'tag': 'http', 'port': 10809, 'listen': '127.0.0.1', 'protocol': 'http',
     'settings': {'allowTransparent': False},
     'sniffing': {'enabled': True, 'routeOnly': False, 'destOverride': ['http', 'tls', 'quic']}},
]
OUTBOUNDS = [{'tag': 'direct', 'protocol': 'freedom'}, {'tag': 'block', 'protocol': 'blackhole'},
             # freedom по умолчанию (AsIs) резолвит домен системным DNS — тогда GOOGLE_DNS_DOMAINS не сработает.
             # Этот выход резолвит через dns-модуль шаблона, им ходят только DIRECT_ALWAYS.
             {'tag': 'direct-gu', 'protocol': 'freedom', 'settings': {'domainStrategy': 'UseIPv4'}}]
DIRECT_GU_RULE = {'type': 'field', 'domain': DIRECT_ALWAYS, 'outboundTag': 'direct-gu'}


def split_template(p):
    # Неподпавший трафик уходит в первый outbound — панель ставит туда `proxy` хоста. Поэтому общего
    # «всё остальное -> proxy» нет: при IPIfNonMatch он перехватил бы домен до проверки geoip:direct.
    remote, domestic = p['RemoteDNSDomain'], p['DomesticDNSDomain']
    rules = [
        {'type': 'field', 'ip': [p['RemoteDNSIP']], 'outboundTag': 'proxy'},
        {'type': 'field', 'ip': [p['DomesticDNSIP']], 'outboundTag': 'direct'},
        {'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'direct'},
        dict(DIRECT_GU_RULE),
    ]
    if p.get('BlockSites'): rules.append({'type': 'field', 'domain': p['BlockSites'], 'outboundTag': 'block'})
    if p.get('BlockIp'): rules.append({'type': 'field', 'ip': p['BlockIp'], 'outboundTag': 'block'})
    if p.get('ProxySites'): rules.append({'type': 'field', 'domain': p['ProxySites'], 'outboundTag': 'proxy'})
    if p.get('ProxyIp'): rules.append({'type': 'field', 'ip': p['ProxyIp'], 'outboundTag': 'proxy'})
    if p.get('DirectSites'): rules.append({'type': 'field', 'domain': p['DirectSites'], 'outboundTag': 'direct'})
    if p.get('DirectIp'): rules.append({'type': 'field', 'ip': p['DirectIp'], 'outboundTag': 'direct'})
    assert p.get('RouteOrder', 'block-proxy-direct') == 'block-proxy-direct', p.get('RouteOrder')
    assert p.get('GlobalProxy') == 'true', p.get('GlobalProxy')
    return {
        'dns': {'hosts': p.get('DnsHosts', {}), 'queryStrategy': 'UseIPv4',
                'servers': [{'address': remote},
                            {'address': remote, 'domains': GOOGLE_DNS_DOMAINS, 'skipFallback': True},
                            {'address': domestic, 'domains': p['DirectSites'], 'skipFallback': True}]},
        'routing': {'domainStrategy': p.get('DomainStrategy', 'IPIfNonMatch'), 'domainMatcher': 'hybrid',
                    'rules': rules},
        'inbounds': copy.deepcopy(INBOUNDS), 'outbounds': copy.deepcopy(OUTBOUNDS),
    }


def auto_template(p):
    # Тот же split, но `proxy` -> балансировщик EU_AUTO по скрытым хостам. Виртуальный хост как outbound не
    # добавляем, поэтому «первого outbound» для неподпавшего трафика нет: ловим его последним ip-правилом.
    t = split_template(p)
    t['routing']['domainStrategy'] = 'IPIfNonMatch'  # ip-ловушка работает только на втором проходе этой стратегии
    for r in t['routing']['rules']:
        if r.get('outboundTag') == 'proxy': del r['outboundTag']; r['balancerTag'] = 'EU_AUTO'
    t['routing']['rules'].append({'type': 'field', 'ip': ['0.0.0.0/0', '::/0'], 'balancerTag': 'EU_AUTO'})
    t['routing']['balancers'] = [{'tag': 'EU_AUTO', 'selector': ['eu'], 'strategy': {'type': 'leastPing'},
                                  'fallbackTag': 'eu'}]
    # probeInterval строкой с часами: «7d» xray не разбирает. Проверки редкие — выход закрепляет сам клиент.
    t['observatory'] = {'subjectSelector': ['eu'], 'probeUrl': 'https://cp.cloudflare.com/generate_204',
                        'probeInterval': '168h', 'enableConcurrency': True}
    t['remnawave'] = {'injectHosts': [{'selector': {'type': 'remarkRegex', 'pattern': INJECT_PATTERN},
                                       'selectFrom': 'HIDDEN', 'tagPrefix': 'eu'}],
                      'addVirtualHostAsOutbound': False}
    return t


def full_template(p):
    # Россия «всегда через VPN»: напрямую только локальная сеть и торренты, реклама режется.
    # DNS — Яндекс через туннель (правила на его IP нет, значит proxy).
    return {
        'dns': {'hosts': p.get('DnsHosts', {}), 'queryStrategy': 'UseIPv4', 'servers': [
            {'address': p['RemoteDNSDomain'], 'domains': GOOGLE_DNS_DOMAINS, 'skipFallback': True},
            p['DomesticDNSDomain']]},
        'routing': {'domainStrategy': 'IPIfNonMatch', 'domainMatcher': 'hybrid', 'rules': [
            {'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'direct'},
            {'type': 'field', 'domain': ['geosite:category-ads'], 'outboundTag': 'block'},
            {'type': 'field', 'domain': ['geosite:private'], 'outboundTag': 'direct'},
            {'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'direct'},
            # Госуслуги не пускают российские хостинги (Beget msk1/2/4 и Timeweb msk3 — TCP в никуда), зато
            # пускают из-за рубежа (nl1/de1 открывают) — значит, с устройства напрямую лучше в любой стране.
            dict(DIRECT_GU_RULE),
        ]},
        'inbounds': copy.deepcopy(INBOUNDS), 'outbounds': copy.deepcopy(OUTBOUNDS),
    }


def json_rule():
    conds = [{'headerName': 'user-agent', 'operator': 'REGEX', 'value': '^(Happ|INCY)/', 'caseSensitive': True}]
    if TEST_HWIDS:
        conds.append({'headerName': 'x-hwid', 'operator': 'REGEX',
                      'value': '^(' + '|'.join(TEST_HWIDS) + ')$', 'caseSensitive': True})
    return {'name': RULE_NAME, 'enabled': True, 'operator': 'AND', 'conditions': conds,
            'description': 'Happ/INCY get per-host JSON: RU hosts full tunnel, the rest RoscomVPN split',
            'responseType': 'XRAY_JSON', 'responseModifications': {'subscriptionTemplate': SPLIT_NAME}}


def sync_templates(p):  # по cron: заголовок обновляет remna-routing-updater, шаблоны догоняют его
    have = {t['name']: t['uuid'] for t in call('GET', '/api/subscription-templates')['response']['templates']
            if t['templateType'] == 'XRAY_JSON'}
    pairs = [(SPLIT_NAME, split_template(p)), (FULL_NAME, full_template(p))]
    if AUTO_NAME in have: pairs.append((AUTO_NAME, auto_template(p)))  # не создаём: это делает только auto-create
    for name, tpl in pairs:
        cur = call('GET', '/api/subscription-templates/' + have[name])['response']['templateJson']
        if cur != tpl:
            call('PATCH', '/api/subscription-templates', {'uuid': have[name], 'templateJson': tpl})
            print(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'updated', name, flush=True)


def auto_create(p):
    have = {t['name']: t for t in call('GET', '/api/subscription-templates')['response']['templates']
            if t['templateType'] == 'XRAY_JSON'}
    t = have.get(AUTO_NAME) or call('POST', '/api/subscription-templates',
                                    {'name': AUTO_NAME, 'templateType': 'XRAY_JSON'})['response']
    call('PATCH', '/api/subscription-templates', {'uuid': t['uuid'], 'templateJson': auto_template(p)})
    print('template', AUTO_NAME, t['uuid'])


def find_host(hosts, remark):
    found = [h for h in hosts if h['remark'] == remark]
    if len(found) != 1: sys.exit(f'host {remark!r}: expected exactly 1 match, found {len(found)}')
    return found[0]


def set_host_template(remark, assign):  # назначить/снять шаблон AUTO у одного хоста; до PATCH — бэкап хостов
    uuids = [t['uuid'] for t in call('GET', '/api/subscription-templates')['response']['templates']
             if t['templateType'] == 'XRAY_JSON' and t['name'] == AUTO_NAME]
    if assign and not uuids: sys.exit(f'template {AUTO_NAME!r} does not exist, run auto-create first')
    hosts = call('GET', '/api/hosts')['response']
    h = find_host(hosts, remark)
    cur = h.get('xrayJsonTemplateUuid')
    if cur not in set(uuids[:1]) | ({None} if assign else set()):  # чужой шаблон не трогаем; unassign снимает только AUTO
        sys.exit(f'host {remark!r} has template {cur or "none"}, expected {AUTO_NAME!r}'
                 f'{"" if assign else " to unassign"}: refusing, nothing changed')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    with open(f'hosts-backup-{stamp}.json', 'w') as f: json.dump(hosts, f, ensure_ascii=False, indent=1)
    tpl = uuids[0] if assign else None
    call('PATCH', '/api/hosts', {'uuid': h['uuid'], 'isDisabled': h['isDisabled'], 'xrayJsonTemplateUuid': tpl})
    print('host', h['remark'], '->', AUTO_NAME if assign else 'no template', f'(was {cur or "none"})')


def assign_auto(remark): set_host_template(remark, True)


def unassign_auto(remark): set_host_template(remark, False)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'render'
    if mode == 'render':  # локально: профиль из файла, только шаблоны
        p = profile_from_header(open(sys.argv[2]).read().strip())
        json.dump({'split': split_template(p), 'full': full_template(p), 'auto': auto_template(p)}, sys.stdout,
                  ensure_ascii=False, indent=1)
        return
    assert mode in ('apply', 'sync', 'auto-create', 'auto-assign', 'auto-unassign') and API and KEY
    if mode in ('auto-assign', 'auto-unassign'):
        if len(sys.argv) != 3: sys.exit(f'usage: json_routing.py {mode} <host remark>')
        return set_host_template(sys.argv[2], mode == 'auto-assign')
    ss = call('GET', '/api/subscription-settings')['response']
    p = profile_from_header(ss['customResponseHeaders']['routing'])
    if mode == 'sync': return sync_templates(p)
    if mode == 'auto-create': return auto_create(p)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    hosts = call('GET', '/api/hosts')['response']
    json.dump({'subscriptionSettings': ss, 'hosts': hosts}, open(f'subsettings-hosts-backup-{stamp}.json', 'w'),
              ensure_ascii=False, indent=1)

    have = {t['name']: t for t in call('GET', '/api/subscription-templates')['response']['templates']
            if t['templateType'] == 'XRAY_JSON'}
    uuids = {}
    for name, tpl in ((SPLIT_NAME, split_template(p)), (FULL_NAME, full_template(p))):
        t = have.get(name) or call('POST', '/api/subscription-templates',
                                   {'name': name, 'templateType': 'XRAY_JSON'})['response']
        call('PATCH', '/api/subscription-templates', {'uuid': t['uuid'], 'templateJson': tpl})
        uuids[name] = t['uuid']; print('template', name, t['uuid'])

    for h in hosts:
        if h['remark'].startswith(RU_HOST_PREFIX):
            call('PATCH', '/api/hosts', {'uuid': h['uuid'], 'isDisabled': h['isDisabled'],
                                         'xrayJsonTemplateUuid': uuids[FULL_NAME]})
            print('host', h['remark'], '->', FULL_NAME)

    rules = [r for r in ss['responseRules']['rules'] if r['name'] != RULE_NAME]
    i = next(i for i, r in enumerate(rules) if r['name'] == 'Browser Subscription') + 1
    rules.insert(i, json_rule())
    call('PATCH', '/api/subscription-settings',
         {'uuid': ss['uuid'], 'responseRules': {**ss['responseRules'], 'rules': rules}})
    print('response rule', RULE_NAME, 'at', i, 'hwid-limited' if TEST_HWIDS else 'FOR EVERYONE')


if __name__ == '__main__':
    main()
