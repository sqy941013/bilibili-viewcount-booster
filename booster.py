import sys
import os
import uuid
import threading
import random
import urllib3
from time import sleep
from collections import Counter
from typing import Optional
from datetime import date, datetime, timedelta

try:
    from curl_cffi import requests as cffi_requests
    USE_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    USE_CURL_CFFI = False

import requests
from requests.exceptions import RequestException
from fake_useragent import UserAgent

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Browser impersonation targets (JA3 + HTTP/2 fingerprints)
BROWSER_TARGETS = [
    # Chrome
    'chrome120', 'chrome124', 'chrome131', 'chrome133a',
    # Safari
    'safari15_5', 'safari17_0', 'safari17_2_ios', 'safari18_0',
    # Edge
    'edge120', 'edge131',
    # Firefox
    'firefox120', 'firefox133',
]


def gen_buvid() -> tuple[str, str]:
    """Generate fake B站 device fingerprints."""
    buvid3 = uuid.uuid4().hex.upper()
    buvid4 = uuid.uuid4().hex.upper()
    return buvid3, buvid4


def fetch_buvid_from_api() -> tuple[str, str]:
    """Fetch real buvid fingerprints from bilibili's fingerprint API."""
    try:
        r = requests.get('https://api.bilibili.com/x/frontend/finger/spi', timeout=10)
        d = r.json()
        if d.get('code') == 0 and d.get('data'):
            return d['data'].get('b_3', ''), d['data'].get('b_4', '')
    except:
        pass
    return '', ''


def make_session(session, bv: str, ua: Optional[str] = None) -> None:
    """Set up session headers for bilibili."""
    session.verify = False
    if ua:
        session.headers['User-Agent'] = ua
    else:
        session.headers['User-Agent'] = UserAgent().random
    buvid3, buvid4 = fetch_buvid_from_api()
    if not buvid3 or not buvid4:
        buvid3, buvid4 = gen_buvid()
    session.headers['Referer'] = f'https://www.bilibili.com/video/{bv}/'
    session.headers['Cookie'] = f'buvid3={buvid3}; buvid4={buvid4}'


def make_browser_session(**kwargs) -> cffi_requests.Session:
    """Create a session that impersonates a real Chrome browser."""
    target = random.choice(BROWSER_TARGETS)
    return cffi_requests.Session(impersonate=target, **kwargs)


def send_heartbeat(session, info: dict, bv: str, timeout: int, played_time: int = 3) -> bool:
    """Send heartbeat to bilibili to simulate active viewing."""
    try:
        session.post('https://api.bilibili.com/x/click-interface/web/heartbeat',
                     timeout=timeout,
                     data={
                         'aid': info['aid'],
                         'cid': info['cid'],
                         'bvid': bv,
                         'played_time': played_time,
                         'realtime': played_time,
                         'real_played_time': played_time,
                         'dt': 2,
                         'play_type': 0,
                         'start_ts': 0,
                         'referer': f'https://www.bilibili.com/video/{bv}/',
                     })
        return True
    except:
        return False


# parameters
timeout = 3  # seconds for proxy connection timeout
round_time = 305  # seconds for each round of view count boosting
update_pbar_count = 10  # update view count progress bar for every xx proxies

if len(sys.argv) < 3:
    print(f'Usage: python {sys.argv[0]} <BV/AV_ID> <target_views> [proxy_list_url]')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --proxypool [url]')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --residential <gateway:port> <user:pass>')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --proxytype http|socks5  (default: http)')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --threads N  (default: 75, filter concurrency)')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --boost-threads N  (default: 1, boosting concurrency)')
    print(f'       python {sys.argv[0]} <BV/AV_ID> <target_views> --watch-range MIN MAX  (default: 3 5, random watch seconds)')
    sys.exit(1)

bv = sys.argv[1]  # video BV/AV id (raw input)
target = int(sys.argv[2])  # target view count

# Determine proxy source mode
proxy_list_url = None
proxypool_url = None
residential_gateway = None  # e.g. gate.smartproxy.io:7000
residential_auth = None     # e.g. username:password
proxy_type = 'http'         # http or socks5
thread_num = 75             # thread count for filtering
boost_threads = 1           # thread count for boosting
watch_time_min = 3          # minimum watch seconds
watch_time_max = 5          # maximum watch seconds

i = 3
while i < len(sys.argv):
    if sys.argv[i] == '--proxypool':
        proxypool_url = sys.argv[i + 1] if i + 1 < len(sys.argv) else 'http://127.0.0.1:5010'
        i += 2
    elif sys.argv[i] in ('--brightdata', '--residential'):
        residential_gateway = sys.argv[i + 1]
        residential_auth = sys.argv[i + 2]
        i += 3
    elif sys.argv[i] == '--proxytype':
        proxy_type = sys.argv[i + 1].lower()
        i += 2
    elif sys.argv[i] == '--threads':
        thread_num = int(sys.argv[i + 1])
        i += 2
    elif sys.argv[i] == '--boost-threads':
        boost_threads = int(sys.argv[i + 1])
        i += 2
    elif sys.argv[i] == '--watch-range':
        watch_time_min = int(sys.argv[i + 1])
        watch_time_max = int(sys.argv[i + 2])
        i += 3
    else:
        proxy_list_url = sys.argv[i]
        i += 1

if residential_gateway and ':' not in residential_gateway:
    residential_gateway += ':33335'


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


def fetch_from_proxypool(base_url: str) -> list[str]:
    """Fetch HTTPS proxies from jhao104/proxy_pool."""
    api_url = f'{base_url}/all/?type=https'
    print(f'getting HTTPS proxies from proxy_pool at {api_url} ...')
    response = requests.get(api_url, timeout=timeout + 5)
    response.raise_for_status()
    data = response.json()
    proxies = [item['proxy'] for item in data if item.get('proxy')]
    print(f'successfully get {len(proxies)} HTTPS proxies from proxy_pool')
    return proxies


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
    # Mode 1: proxy_pool (pre-vetted HTTPS proxies, no filtering needed)
    if proxypool_url:
        try:
            proxies = fetch_from_proxypool(proxypool_url)
        except Exception as err:
            raise RuntimeError(f'proxy_pool failed: {err}')
        if not proxies:
            raise RuntimeError('no HTTPS proxies found in proxy_pool (is the service running?)')
        print(f'collected {len(proxies)} proxies from proxy_pool')
        return proxies

    # Mode 2: custom file/URL
    if proxy_list_url:
        try:
            proxies = fetch_from_custom_url(proxy_list_url)
        except Exception as err:
            raise RuntimeError(f'custom proxy source failed: {err}')
        if not proxies:
            raise RuntimeError('no proxies found in custom source')
        print(f'collected {len(proxies)} proxies from custom source')
        return proxies

    fetchers = [
        ('checkerproxy', fetch_from_checkerproxy),
        ('proxyscrape', fetch_from_proxyscrape),
        ('proxy-list.download', fetch_from_proxylistdownload),
        ('geonode', fetch_from_geonode),
        ('speedx', fetch_from_speedx),
        ('monosans', fetch_from_monosans),
    ]
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
if residential_gateway:
    # Residential proxy: single gateway, no proxy list needed
    total_proxies = [residential_gateway]
    print(f'using residential proxy gateway: {residential_gateway} (auto-rotate IP per request)')
else:
    total_proxies = get_total_proxies()

# 2.filter proxies by multi-threading (skip for proxy_pool/brightdata, already vetted)
if len(total_proxies) > 10000:
    print('more than 10000 proxies, randomly pick 10000 proxies')
    random.shuffle(total_proxies)
    total_proxies = total_proxies[:10000]

if proxypool_url:
    # proxy_pool proxies are already vetted HTTPS
    active_proxies = total_proxies
    print(f'using {len(active_proxies)} proxy_pool proxies (skipping filter step)')
elif not residential_gateway:
    active_proxies = []
    count = 0
    filter_ok = 0
    filter_fail = 0
    filter_lock = threading.Lock()
    def filter_proxys(proxies: 'list[str]') -> None:
        global count, filter_ok, filter_fail
        for proxy in proxies:
            try:
                scheme = 'socks5' if proxy_type == 'socks5' else 'http'
                requests.post('http://httpbin.org/post',
                              proxies={scheme: f'{scheme}://'+proxy},
                              timeout=timeout)
                active_proxies.append(proxy)
                with filter_lock:
                    filter_ok += 1
            except:  # proxy connect timeout
                with filter_lock:
                    filter_fail += 1
            with filter_lock:
                count += 1
                c = count
            if c % 100 == 0 or c == len(total_proxies):
                with filter_lock:
                    s, f = filter_ok, filter_fail
                print(f'{pbar(c, len(total_proxies), hits=None, view_increase=None)} OK: {s} | Fail: {f} {100*c/len(total_proxies):.1f}%   ', end='')

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
if USE_CURL_CFFI:
    print('TLS mode: curl_cffi (Chrome browser impersonation)')
else:
    print('TLS mode: requests (no browser impersonation, install curl_cffi for better results)')
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
stats_lock = threading.Lock()

if residential_gateway:
    scheme = 'socks5' if proxy_type == 'socks5' else 'https'
    boost_round = 0

    def get_residential_proxy_for_round() -> dict:
        """Generate a unique session ID for this round to get a fresh IP."""
        session_id = uuid.uuid4().hex[:8]
        # IPRoyal format: username-session-ID-lifetime-5min
        if 'iproyal' in residential_gateway.lower():
            user, passwd = residential_auth.split(':', 1)
            sticky_user = f'{user}-session-{session_id}-lifetime-5'
            return {scheme: f'{scheme}://{sticky_user}:{passwd}@{residential_gateway}'}
        else:
            return {scheme: f'{scheme}://{residential_auth}@{residential_gateway}'}

    def do_boost_residential() -> bool:
        """One boosting attempt for residential proxy with unique IP."""
        global total_successful_hits, total_attempted
        watch_time = random.randint(watch_time_min, watch_time_max)
        try:
            session = make_browser_session()
            session.proxies.update(get_residential_proxy_for_round())
            make_session(session, bv)
            session.get(f'https://www.bilibili.com/video/{bv}/', timeout=watch_time + 5)
            sleep(watch_time)
            send_heartbeat(session, info, bv, timeout, played_time=watch_time)
            resp = session.post('https://api.bilibili.com/x/click-interface/click/web/h5',
                          timeout=timeout,
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
            session.close()
            with stats_lock:
                total_attempted += 1
                if resp.status_code < 400:
                    total_successful_hits += 1
            return True
        except cffi_requests.Timeout:
            failure_counter['Connection timeout'] += 1
        except cffi_requests.ConnectionError as e:
            reason = str(e).lower()
            if 'refused' in reason:
                failure_counter['Connection refused'] += 1
            elif 'timed out' in reason:
                failure_counter['Connection timed out'] += 1
            else:
                failure_counter['Connection error'] += 1
        except Exception:
            failure_counter['Other error'] += 1
        with stats_lock:
            total_attempted += 1
        return False

    while True:
        info = fetch_video_info(bv)
        current = info['stat']['view']
        if current >= target:
            print(f'{pbar(target, target, total_successful_hits, current - initial_view_count)} done                 ', end='')
            break

        watch_display = f'{watch_time_min}-{watch_time_max}s'
        print(f'{pbar(current, target, total_successful_hits, current - initial_view_count)} round {boost_round + 1} ({boost_threads} threads, watching {watch_display})   ', end='')

        threads = []
        for _ in range(boost_threads):
            t = threading.Thread(target=do_boost_residential)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        boost_round += 1
else:
    proxy_index = threading.Semaphore(boost_threads)

    def do_boost_proxy(proxy: str) -> bool:
        """One boosting attempt for a proxy."""
        global total_successful_hits, total_attempted
        watch_time = random.randint(watch_time_min, watch_time_max)
        proxy_scheme = 'socks5' if proxy_type == 'socks5' else 'https'
        proxy_conf = {proxy_scheme: f'{proxy_scheme}://{proxy}'}
        try:
            session = make_browser_session()
            session.proxies.update(proxy_conf)
            make_session(session, bv)
            session.get(f'https://www.bilibili.com/video/{bv}/', timeout=watch_time + 5)
            sleep(watch_time)
            send_heartbeat(session, info, bv, timeout, played_time=watch_time)
            resp = session.post('https://api.bilibili.com/x/click-interface/click/web/h5',
                          timeout=timeout,
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
            session.close()
            with stats_lock:
                total_attempted += 1
                if resp.status_code < 400:
                    total_successful_hits += 1
                    round_hits += 1
        except cffi_requests.Timeout:
            failure_counter['Connection timeout'] += 1
        except cffi_requests.ConnectionError as e:
            reason = str(e).lower()
            if 'refused' in reason:
                failure_counter['Connection refused'] += 1
            elif 'timed out' in reason:
                failure_counter['Connection timed out'] += 1
            else:
                failure_counter['Connection error'] += 1
        except Exception:
            failure_counter['Other error'] += 1
        with stats_lock:
            total_attempted += 1
        proxy_index.release()

    while True:
        reach_target = False
        start_time = datetime.now()
        round_hits = 0

        # send viewing simulation for each proxy with concurrency limit
        for i, proxy in enumerate(active_proxies):
            if i % update_pbar_count == 0:  # update progress bar
                info = fetch_video_info(bv)
                current = info['stat']['view']
                if current >= target:
                    reach_target = True
                    print(f'{pbar(target, target, total_successful_hits, current - initial_view_count)} done                 ', end='')
                    break

            proxy_index.acquire()
            t = threading.Thread(target=do_boost_proxy, args=(proxy,))
            t.start()

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
