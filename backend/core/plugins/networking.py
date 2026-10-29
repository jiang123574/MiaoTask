"""Proxy configuration shared by the official activity download paths.

Both the GitHub API/raw fetcher and release-asset downloads build their
opener here. Without explicit configuration urllib keeps using the system
and ``HTTPS_PROXY`` environment proxies; ``MIAO_DOWNLOAD_PROXY`` can
point those downloads at a specific proxy or force a direct connection when
the system proxy is unavailable. Local 127.0.0.1 test sources never use it.
"""
import logging
import os
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

logger = logging.getLogger(__name__)

PROXY_ENV = 'MIAO_DOWNLOAD_PROXY'
_DIRECT_TOKENS = frozenset({'off', 'direct', 'none', '0', 'false', 'no'})
_UNSET = object()


def normalize_proxy(raw):
    """Validate one proxy declaration into urllib proxy mappings.

    An empty/direct token returns ``{}`` so ProxyHandler disables proxies.
    Only HTTP(S) proxies are supported (urllib has no native SOCKS support).
    """
    value = (raw or '').strip()
    if not value or value.lower() in _DIRECT_TOKENS:
        return {}
    if '://' not in value:
        value = 'http://' + value
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in ('http', 'https') or not parsed.hostname or not parsed.port:
            raise ValueError
        if not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('下载代理地址无效，示例：http://127.0.0.1:7890；'
                         '仅支持 HTTP/HTTPS 代理，填 off 可强制直连') from None
    # netloc already holds the validated host:port and optional userinfo;
    # log only the host portion so credentials never reach the logs.
    url = f'{scheme}://{parsed.netloc}'
    logger.info('活动下载使用代理 %s://%s:%s', scheme, parsed.hostname, parsed.port)
    return {'http': url, 'https': url}


def resolve_proxies(value=_UNSET):
    """Return explicit proxy mappings, {} for direct, or None for defaults."""
    raw = os.environ.get(PROXY_ENV) if value is _UNSET else value
    if raw is None:
        return None
    return normalize_proxy(raw)


def official_opener(redirect_handler):
    """Opener for official GitHub downloads with the configured proxy."""
    proxies = resolve_proxies()
    if proxies is None:
        return build_opener(redirect_handler)
    if not proxies:
        logger.info('活动下载按 %s=off 强制直连，不使用系统代理', PROXY_ENV)
    return build_opener(ProxyHandler(proxies), redirect_handler)
