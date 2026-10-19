"""Прод-подобные хосты и ноды панели (срез 2026-10-18) для тестов блока «Локации».

Используется тестами сведения устройств и HTTP-маршрутами локаций.
"""

from __future__ import annotations

from app.external.remnawave_api import RemnaWaveHost, RemnaWaveNode


# UUID инбаундов (configProfileInboundUuid).
U_3443 = 'in-3443'
U_AUTO = 'in-auto'
U_DE = 'in-de'
U_GH = 'in-gh'
U_ADS = 'in-ads'
U_SE = 'in-se'

# Теги инбаундов на нодах.
TAG_3443 = 'vless-inbound-3443'
TAG_AUTO = 'vless-to-eu-auto'
TAG_DE = 'vless-to-de1-ss'
TAG_GH = 'vless-inbound-sni-github'
TAG_ADS = 'vless-inbound-ads'
TAG_SE = 'vless-inbound-se'

OVERRIDES: dict[str, str] = {'msk2.zanity.net': 'MSK-2'}


def make_node(
    name: str,
    address: str,
    tags: dict[str, str],
    users_online: int,
) -> RemnaWaveNode:
    """Нода с минимальным набором полей; tags — uuid инбаунда -> тег."""
    return RemnaWaveNode(
        uuid=f'node-{name}',
        name=name,
        address=address,
        country_code='',
        is_connected=True,
        is_disabled=False,
        users_online=users_online,
        traffic_used_bytes=None,
        traffic_limit_bytes=None,
        active_inbound_uuids=list(tags),
        active_inbound_tags=dict(tags),
    )


def make_host(
    remark: str,
    address: str,
    inbound_uuid: str,
    view_position: int,
    *,
    is_disabled: bool = False,
    is_hidden: bool = False,
) -> RemnaWaveHost:
    return RemnaWaveHost(
        uuid=f'host-{view_position}-{remark}',
        remark=remark,
        address=address,
        is_disabled=is_disabled,
        is_hidden=is_hidden,
        config_profile_inbound_uuid=inbound_uuid,
        view_position=view_position,
    )


def prod_nodes() -> list[RemnaWaveNode]:
    return [
        make_node('MSK-1', 'msk1.zanity.net', {U_3443: TAG_3443}, 1),
        make_node('MSK-2', 'remnanode', {U_3443: TAG_3443}, 2),
        make_node('MSK-4', 'msk4.zanity.net', {U_AUTO: TAG_AUTO, U_DE: TAG_DE}, 29),
        make_node('NL-1', 'nl1.zanity.net', {U_GH: TAG_GH, U_ADS: TAG_ADS}, 34),
        make_node('SE-1', '191.44.114.69', {U_SE: TAG_SE}, 8),
    ]


def prod_hosts() -> list[RemnaWaveHost]:
    # Намеренно не по порядку view_position, чтобы проверить сортировку.
    return [
        make_host('🇩🇪 Германия', 'msk4.zanity.net', U_DE, 3),
        make_host('🇪🇺 [AUTO] Европа', 'msk4.zanity.net', U_AUTO, 1),
        make_host('🎬 YouTube — Германия', 'msk4.zanity.net', U_DE, 9, is_disabled=True),
        make_host('👇 🎮 Для игр 👇', 'msk1.zanity.net', U_3443, 4),
        make_host('🇷🇺 #1 Россия (СПБ)', 'msk1.zanity.net', U_3443, 5),
        make_host('🇷🇺 #2 Россия (МСК)', 'msk2.zanity.net', U_3443, 6),
        make_host('🇳🇱 Нидерланды (с рекламой)', 'nl1.zanity.net', U_ADS, 7),
        make_host('🇳🇱 Нидерланды', 'nl1.zanity.net', U_GH, 2),
        make_host('🇸🇪 Швеция', 'se1.zanity.net', U_SE, 8),
    ]
