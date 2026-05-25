#!/usr/bin/env python3
"""
A/B test: send boosting requests through different country proxies,
check which ones actually count as valid views on Bilibili.

Each config sends ~20 requests through a residential proxy (IPRoyal),
then checks how many views were added.
"""
import sys
sys.path.insert(0, '.')

# Import from booster
from curl_cffi import requests as cffi_requests
import requests
import uuid
import random
import time
import re
import threading

BV = 'BV1fnAfz9ELb'
GATEWAY = 'geo.iproyal.com:12321'
AUTH_BASE = 'YzeDmBrsyNmgrvU5:Dvz0tXwyKR9vinUX'

BROWSER_TARGETS = [
    'chrome120', 'chrome124', 'chrome131', 'chrome133a',
    'edge120', 'edge131',
    'safari15_5', 'safari17_0', 'safari17_2_ios', 'safari18_0',
    'firefox133', 'firefox135',
]

configs = [
    {'name': 'CN', 'country': 'cn'},
    {'name': 'Philippines', 'country': 'ph'},
    {'name': 'Vietnam', 'country': 'vn'},
    {'name': 'India', 'country': 'in'},
    {'name': 'Brazil', 'country': 'br'},
    {'name': 'Mexico', 'country': 'mx'},
    {'name': 'US', 'country': 'us'},
]

ATTEMPTS_PER_CONFIG = 20
WATCH_TIME = 8

def get_views(proxy=None):
    r = requests.get('https://api.bilibili.com/x/web-interface/view',
                     params={'bvid': BV}, timeout=15, proxies=proxy)
    return r.json()['data']['stat']['view']

def get_video_info(proxy=None):
    r = requests.get('https://api.bilibili.com/x/web-interface/view',
                     params={'bvid': BV}, timeout=15, proxies=proxy)
    return r.json()['data']

def make_request(auth, country, attempt_num):
    """One boosting attempt. Returns (success, error)."""
    scheme = 'socks5'
    proxy = f'{scheme}://{auth}@{GATEWAY}'
    proxy_conf = {scheme: proxy}

    try:
        target = random.choice(BROWSER_TARGETS)
        session = cffi_requests.Session(impersonate=target)
        session.proxies.update(proxy_conf)
        session.verify = False
        buvid3, buvid4 = '', ''
        try:
            r = requests.get('https://api.bilibili.com/x/frontend/finger/spi', timeout=10)
            d = r.json()
            if d.get('code') == 0 and d.get('data'):
                buvid3 = d['data'].get('b_3', '')
                buvid4 = d['data'].get('b_4', '')
        except:
            pass
        if not buvid3:
            buvid3 = uuid.uuid4().hex.upper()
            buvid4 = uuid.uuid4().hex.upper()
        session.headers['Referer'] = f'https://www.bilibili.com/video/{BV}/'
        session.headers['Cookie'] = f'buvid3={buvid3}; buvid4={buvid4}'

        # Player APIs
        session.get('https://api.bilibili.com/x/player/playurl',
                    params={'aid': info['aid'], 'cid': info['cid'], 'bvid': BV,
                            'qn': 80, 'fnval': 16, 'fourk': 1},
                    timeout=3)
        session.get('https://api.bilibili.com/x/player/v2',
                    params={'aid': info['aid'], 'cid': info['cid'], 'bvid': BV},
                    timeout=3)
        time.sleep(WATCH_TIME)

        # Heartbeat
        session.post('https://api.bilibili.com/x/click-interface/web/heartbeat',
                     timeout=3,
                     data={
                         'aid': info['aid'], 'cid': info['cid'], 'bvid': BV,
                         'played_time': WATCH_TIME, 'realtime': WATCH_TIME,
                         'real_played_time': WATCH_TIME, 'dt': 2, 'play_type': 0,
                         'start_ts': 0, 'referer': f'https://www.bilibili.com/video/{BV}/',
                     })

        # Click
        resp = session.post('https://api.bilibili.com/x/click-interface/click/web/h5',
                            timeout=3,
                            data={
                                'aid': info['aid'], 'cid': info['cid'], 'bvid': BV,
                                'part': '1', 'mid': info['owner']['mid'],
                                'jsonp': 'jsonp', 'type': '1', 'sub_type': '0',
                            })
        session.close()
        code = resp.json().get('code', -1) if resp.text else -1
        return (resp.status_code == 200 and code == 0, f'status={resp.status_code} code={code}')
    except Exception as e:
        return (False, str(e)[:100])

# Use first country's proxy for baseline API calls
baseline_proxy = {'socks5': f'socks5://{AUTH_BASE}_country-{configs[0]["country"]}@{GATEWAY}'}

# Get video info once
print('Fetching video info...')
info = get_video_info(proxy=baseline_proxy)
print(f'Video: {info["title"]}')
print(f'AID: {info["aid"]}, CID: {info["cid"]}')
print()

# Baseline view count
views_before_all = get_views(proxy=baseline_proxy)
print(f'Baseline views: {views_before_all}')
print(f'Testing {len(configs)} countries x {ATTEMPTS_PER_CONFIG} attempts each')
print(f'Watch time: {WATCH_TIME}s per attempt')
print()

results = []

for cfg in configs:
    auth = f'{AUTH_BASE}_country-{cfg["country"]}'
    check_proxy = {'socks5': f'socks5://{auth}@{GATEWAY}'}
    print(f'\n--- {cfg["name"]} ({cfg["country"]}) ---')

    views_before = get_views(proxy=check_proxy)
    print(f'  Views before: {views_before}')

    success = 0
    errors = []
    for i in range(ATTEMPTS_PER_CONFIG):
        ok, detail = make_request(auth, cfg['country'], i)
        if ok:
            success += 1
        else:
            errors.append(detail)
        if (i + 1) % 5 == 0:
            views_now = get_views(proxy=check_proxy)
            print(f'  [{i+1}/{ATTEMPTS_PER_CONFIG}] success={success} views_now={views_now}')

    views_after = get_views(proxy=check_proxy)
    increase = views_after - views_before
    conversion = increase / ATTEMPTS_PER_CONFIG * 100

    error_summary = {}
    for e in errors:
        for key in ['timeout', 'refused', 'timed out', 'error']:
            if key.lower() in e.lower():
                error_summary[key] = error_summary.get(key, 0) + 1
                break
        else:
            error_summary['other'] = error_summary.get('other', 0) + 1

    results.append({
        'name': cfg['name'],
        'country': cfg['country'],
        'attempts': ATTEMPTS_PER_CONFIG,
        'success_api': success,
        'views_before': views_before,
        'views_after': views_after,
        'increase': increase,
        'conversion_rate': conversion,
        'api_success_rate': success / ATTEMPTS_PER_CONFIG * 100,
        'errors': error_summary,
    })
    print(f'  Result: +{increase} views / {ATTEMPTS_PER_CONFIG} attempts = {conversion:.1f}%')
    if error_summary:
        print(f'  Errors: {error_summary}')

    # Wait for B站 to process the views
    print(f'  Waiting 15s for Bilibili to process...')
    time.sleep(15)

print(f'\n\n{"="*80}')
print(f'RESULTS')
print(f'{"="*80}')
print(f'{"Country":<15} {"API OK":>7} {"Views+":>7} {"Rate":>6} {"Errors"}')
print(f'{"-"*80}')
for r in results:
    err_str = ', '.join(f'{k}:{v}' for k,v in r['errors'].items()) if r['errors'] else ''
    print(f'{r["name"]:<15} {r["api_success_rate"]:>6.0f}% {r["increase"]:>7} '
          f'{r["conversion_rate"]:>5.1f}%  {err_str}')
print(f'{"="*80}')

best = max(results, key=lambda x: x['conversion_rate'])
print(f'\nBest: {best["name"]} ({best["conversion_rate"]:.1f}% conversion, +{best["increase"]} views)')
worst = min(results, key=lambda x: x['conversion_rate'])
print(f'Worst: {worst["name"]} ({worst["conversion_rate"]:.1f}% conversion, +{worst["increase"]} views)')
