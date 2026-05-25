import sys
import os
import threading
import random
import urllib3
from time import sleep
from collections import Counter
from typing import Optional
from datetime import date, datetime, timedelta

import requests
from requests.exceptions import RequestException
from fake_useragent import UserAgent

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# parameters
timeout = 3  # seconds for proxy connection timeout
thread_num = 75  # thread count for filtering active proxies
round_time = 305  # seconds for each round of view count boosting
update_pbar_count = 10  # update view count progress bar for every xx proxies

if len(sys.argv) < 3:
    print(f'Usage: python {sys.argv[0]} <BV/AV_ID> <target_views> [proxy_list_url]')
    sys.exit(1)

bv = sys.argv[1]  # video BV/AV id (raw input)
target = int(sys.argv[2])  # target view count
proxy_list_url = sys.argv[3] if len(sys.argv) > 3 else None  # optional custom proxy list .txt URL


def fetch_from_checkerproxy(min_count: int = 100, max_lookback_days: int = 7) -> list[str]:
    day = date.today()
    for _ in range(max_lookback_days):
        day = day - timedelta(days=1)
        proxy_url = f'https://api.checkerproxy.net/v1/landing/archive/{day.strftime("%Y-%m-%d")}'
        print(f'getting proxies from {proxy_url} ...')
        try:
            response = requests.get(proxy_url, timeout=timeout)
            response.raise_for_status()
        except RequestException as err:
            print(f'checkerproxy unavailable: {err}')
            continue

        data = response.json()
        proxies_obj = data['data']['proxyList']
        if isinstance(proxies_obj, list):
            total_proxies = proxies_obj
        elif isinstance(proxies_obj, dict):
            total_proxies = [proxy for proxy in proxies_obj.values() if proxy]
        else:
            raise TypeError(f'Unexpected type of $.data.proxyList: {type(proxies_obj)}')

        if len(total_proxies) >= min_count:
            print(f'successfully get {len(total_proxies)} proxies from checkerproxy')
            return total_proxies
        print(f'only have {len(total_proxies)} proxies from checkerproxy')
    return []


def fetch_from_proxyscrape() -> list[str]:
    proxy_url = ('https://api.proxyscrape.com/v2/?request=getproxies&protocol=http'
                 '&timeout=2000&country=all')
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxyscrape')
    return proxies


def fetch_from_proxylistdownload() -> list[str]:
    proxy_url = 'https://www.proxy-list.download/api/v1/get?type=http'
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, timeout=timeout + 2)
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip()]
    print(f'successfully get {len(proxies)} proxies from proxy-list.download')
    return proxies


def fetch_from_geonode(limit: int = 300) -> list[str]:
    proxy_url = 'https://proxylist.geonode.com/api/proxy-list'
    params = {
        'limit': limit,
        'page': 1,
        'sort_by': 'lastChecked',
        'sort_type': 'desc',
        'protocols': 'http',
    }
    print(f'getting proxies from {proxy_url} ...')
    response = requests.get(proxy_url, params=params, timeout=timeout + 2)
    response.raise_for_status()
    data = response.json().get('data', [])
    proxies = [f"{item['ip']}:{item['port']}" for item in data if item.get('ip') and item.get('port')]
    print(f'successfully get {len(proxies)} proxies from geonode')
    return proxies


def fetch_plaintext_proxy_list(url: str, label: str) -> list[str]:
    print(f'getting proxies from {url} ...')
    response = requests.get(url, timeout=max(timeout, 5))
    response.raise_for_status()
    proxies = [line.strip() for line in response.text.splitlines() if line.strip() and ':' in line]
    print(f'successfully get {len(proxies)} proxies from {label}')
    return proxies


def fetch_from_speedx() -> list[str]:
    return fetch_plaintext_proxy_list(
        'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
        'TheSpeedX GitHub list')


def fetch_from_monosans() -> list[str]:
    return fetch_plaintext_proxy_list(
        'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
        'monosans GitHub list')


def fetch_from_custom_url(source: str) -> list[str]:
    if os.path.isfile(source):
        print(f'loading proxies from local file: {source}')
        with open(source) as f:
            proxies = [line.strip() for line in f if line.strip() and ':' in line]
        print(f'successfully get {len(proxies)} proxies from local file')
        return proxies
    return fetch_plaintext_proxy_list(source, f'custom URL: {source}')


def build_view_params(video_id: str) -> dict[str, str]:
    """Return API query params for either BV or AV id."""
    normalized = video_id.strip()
    if not normalized:
        raise ValueError('video id is empty')
    lowered = normalized.lower()
    if lowered.startswith('av'):
        aid = normalized[2:]
        if not aid.isdigit():
            raise ValueError(f'invalid av id: {video_id}')
        return {'aid': aid}
    if normalized.isdigit():
        return {'aid': normalized}
    return {'bvid': normalized}


def fetch_video_info(video_id: str) -> dict:
    """Fetch video metadata and ensure API response is valid."""
    params = build_view_params(video_id)
    response = requests.get(
        'https://api.bilibili.com/x/web-interface/view',
        params=params,
        headers={'User-Agent': UserAgent().random},
        timeout=timeout + 2
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('code') != 0 or 'data' not in payload:
        msg = payload.get('message', 'unknown error')
        raise RuntimeError(f'bilibili API error: code={payload.get("code")} message={msg}')
    data = payload['data']
    if not data.get('aid') or not data.get('bvid'):
        raise RuntimeError('video info missing key identifiers')
    return data


def get_total_proxies() -> list[str]:
    fetchers = [
        ('checkerproxy', fetch_from_checkerproxy),
        ('proxyscrape', fetch_from_proxyscrape),
        ('proxy-list.download', fetch_from_proxylistdownload),
        ('geonode', fetch_from_geonode),
        ('speedx', fetch_from_speedx),
        ('monosans', fetch_from_monosans),
    ]
    if proxy_list_url:
        fetchers.insert(0, ('custom_url', lambda: fetch_from_custom_url(proxy_list_url)))
    all_proxies: set[str] = set()
    for name, fetcher in fetchers:
        try:
            proxies = fetcher()
        except RequestException as err:
            print(f'{name} source failed: {err}')
            continue
        except Exception as err:
            print(f'{name} source error: {err}')
            continue
        for proxy in proxies:
            all_proxies.add(proxy)
        if len(all_proxies) >= 500:
            break
    if all_proxies:
        print(f'collected {len(all_proxies)} proxies from available sources')
        return list(all_proxies)
    raise RuntimeError('failed to fetch proxies from all sources')


def time(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    else:
        return f'{int(seconds / 60)}min {seconds % 60}s'

def pbar(n: int, total: int, hits: Optional[int], view_increase: Optional[int]) -> str:
    ratio = min(n / total, 1.0)
    filled = int(ratio * 30)
    progress = '━' * filled
    blank = ' ' * (30 - filled)
    pct = f'{n}/{total}'
    if hits is None or view_increase is None:
        return f'\r{pct} [{progress}{blank}]'
    else:
        return f'\r{pct} [{progress}{blank}] Hits: {hits} | +{view_increase}'

# 1.get proxy
print()
total_proxies = get_total_proxies()

# 2.filter proxies by multi-threading
if len(total_proxies) > 10000:
    print('more than 10000 proxies, randomly pick 10000 proxies')
    random.shuffle(total_proxies)
    total_proxies = total_proxies[:10000]

active_proxies = []
count = 0
def filter_proxys(proxies: 'list[str]') -> None:
    global count
    for proxy in proxies:
        count = count + 1
        try:
            requests.post('http://httpbin.org/post',
                          proxies={'http': 'http://'+proxy},
                          timeout=timeout)
            active_proxies.append(proxy)
        except:  # proxy connect timeout
            pass
        if count % 100 == 0 or count == len(total_proxies):
            print(f'{pbar(count, len(total_proxies), hits=None, view_increase=None)} {100*count/len(total_proxies):.1f}%   ', end='')

start_filter_time = datetime.now()
print('\nfiltering active proxies using http://httpbin.org/post ...')
thread_proxy_num = len(total_proxies) // thread_num
threads = []
for i in range(thread_num):
    # calculate the start and end index of the proxies that this thread needs to process
    start = i * thread_proxy_num
    end = start + thread_proxy_num if i < (thread_num - 1) else None  # the last thread processes the remaining proxies
    thread = threading.Thread(target=filter_proxys, args=(total_proxies[start:end],))
    thread.start()
    threads.append(thread)
for thread in threads:
    thread.join()  # wait for all threads to finish
filter_cost_seconds = int((datetime.now()-start_filter_time).total_seconds())
print(f'\nsuccessfully filter {len(active_proxies)} active proxies using {time(filter_cost_seconds)}')

# 3.boost view count
print(f'\nstart boosting {bv} at {datetime.now().strftime("%H:%M:%S")}')
current = 0
info = {}  # Initialize info dictionary

# Get initial view count
try:
    info = fetch_video_info(bv)
    bv = info['bvid']  # ensure BV id is normalized for later requests
    initial_view_count = info['stat']['view']
    current = initial_view_count
    print(f'Initial view count: {initial_view_count}')
except Exception as e:
    print(f'Failed to get initial view count: {e}')
    sys.exit(1)

# Check if already at or past target
if current >= target:
    print(f'Already at {current} views (target: {target}), done.')
    print(f'\nFinish at {datetime.now().strftime("%H:%M:%S")}')
    print(f'Statistics:')
    print(f'- Initial views: {initial_view_count}')
    print(f'- Final views: {current}')
    print(f'- Total increase: {current - initial_view_count}')
    print(f'- Successful hits: 0')
    print(f'- Success rate: 0.00%\n')
    sys.exit(0)

round_num = 0
total_successful_hits = 0
total_attempted = 0
failure_counter = Counter()

while True:
    reach_target = False
    start_time = datetime.now()
    round_hits = 0

    # send POST click request for each proxy
    for i, proxy in enumerate(active_proxies):
        try:
            if i % update_pbar_count == 0:  # update progress bar
                info = fetch_video_info(bv)
                current = info['stat']['view']
                if current >= target:
                    reach_target = True
                    print(f'{pbar(target, target, total_successful_hits, current - initial_view_count)} done                 ', end='')
                    break

            resp = requests.post('https://api.bilibili.com/x/click-interface/click/web/h5',
                          proxies={'https': 'https://'+proxy},
                          headers={'User-Agent': UserAgent().random},
                          timeout=timeout,
                          verify=False,
                          data={
                              'aid': info['aid'],
                              'cid': info['cid'],
                              'bvid': bv,
                              'part': '1',
                              'mid': info['owner']['mid'],
                              'jsonp': 'jsonp',
                              'type': info['desc_v2'][0]['type'] if info['desc_v2'] else '1',
                              'sub_type': '0'
                          })
            if resp.status_code >= 400:
                failure_counter[f'HTTP {resp.status_code}'] += 1
            else:
                round_hits += 1
                total_successful_hits += 1
        except requests.exceptions.Timeout:
            failure_counter['Connection timeout'] += 1
        except requests.exceptions.ConnectionError as e:
            reason = str(e).lower()
            if 'refused' in reason:
                failure_counter['Connection refused'] += 1
            elif 'timed out' in reason:
                failure_counter['Connection timed out'] += 1
            else:
                failure_counter['Connection error'] += 1
        except Exception as e:
            failure_counter['Other error'] += 1
        total_attempted += 1

        # update progress bar every update_pbar_count proxies
        if (i + 1) % update_pbar_count == 0:
            print(f'{pbar(current, target, total_successful_hits, current - initial_view_count)} proxy({i+1}/{len(active_proxies)}) round {round_num+1}   ', end='')

    if reach_target:  # reach target view count
        break
    round_num += 1
    remain_seconds = int(round_time-(datetime.now()-start_time).total_seconds())
    if remain_seconds > 0:
        for second in reversed(range(remain_seconds)):
            print(f'{pbar(current, target, total_successful_hits, current - initial_view_count)} next round: {time(second)}          ', end='')
            sleep(1)

success_rate = (total_successful_hits / total_attempted) * 100 if total_attempted else 0
failed_total = total_attempted - total_successful_hits
print(f'\nFinish at {datetime.now().strftime("%H:%M:%S")}')
print(f'Statistics:')
print(f'- Initial views: {initial_view_count}')
print(f'- Final views: {current}')
print(f'- Total increase: {current - initial_view_count}')
print(f'- Successful hits: {total_successful_hits}')
print(f'- Total attempts: {total_attempted}')
print(f'- Success rate: {success_rate:.2f}%')
if failed_total > 0:
    print(f'- Failed requests: {failed_total}')
    for reason, count in failure_counter.most_common():
        print(f'    - {reason}: {count}')
print()
