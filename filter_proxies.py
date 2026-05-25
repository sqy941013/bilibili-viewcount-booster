import sys
import os
import argparse
import threading
import urllib3
from datetime import datetime

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

timeout = 3
PROXIES_DIR = 'proxies'
TEST_URL = 'https://www.baidu.com/'

active_proxies = []
active_lock = threading.Lock()
count = 0
count_lock = threading.Lock()
total_proxies = 0


def load_proxies(source: str) -> list[str]:
    if os.path.isfile(source):
        with open(source) as f:
            return [line.strip() for line in f if line.strip() and ':' in line]
    resp = requests.get(source, timeout=10)
    resp.raise_for_status()
    return [line.strip() for line in resp.text.splitlines() if line.strip() and ':' in line]


def time_fmt(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds}s'
    return f'{seconds // 60}min {seconds % 60}s'


def pbar(n: int, total: int) -> str:
    ratio = min(n / total, 1.0)
    filled = int(ratio * 30)
    return f'\r{n}/{total} [{"━" * filled}{" " * (30 - filled)}]'


def test_proxies(proxies: list[str], test_url: str, scheme: str) -> None:
    global count
    for proxy in proxies:
        try:
            resp = requests.get(test_url,
                                proxies={scheme: f'{scheme}://{proxy}'},
                                timeout=timeout,
                                verify=False)
            if resp.status_code == 200:
                with active_lock:
                    active_proxies.append(proxy)
        except:
            pass
        with count_lock:
            count += 1
            c = count
        # print outside lock to reduce contention
        if c % 100 == 0 or c == total_proxies:
            print(f'{pbar(c, total_proxies)} {100*c/total_proxies:.1f}%   ', end='')


def main():
    parser = argparse.ArgumentParser(description='Filter HTTPS-supporting proxies from a list')
    parser.add_argument('source', help='Proxy list source: local .txt file path or URL')
    parser.add_argument('-o', '--output', default=None,
                        help='Output file path (default: proxies/YYYYMMDD-HHMMSS.txt)')
    parser.add_argument('-t', '--threads', type=int, default=250, help='Thread count (default: 250)')
    parser.add_argument('--timeout', type=int, default=3, help='Request timeout in seconds (default: 3)')
    args = parser.parse_args()

    global timeout, total_proxies
    timeout = args.timeout

    print(f'loading proxies from {args.source} ...')
    all_proxies = load_proxies(args.source)
    print(f'loaded {len(all_proxies)} proxies')

    if len(all_proxies) > 10000:
        import random
        random.shuffle(all_proxies)
        all_proxies = all_proxies[:10000]
        print(f'randomly picked 10000 proxies')

    total_proxies = len(all_proxies)

    start_time = datetime.now()
    print(f'testing proxies against {TEST_URL} (timeout={timeout}s) ...')

    chunk_size = len(all_proxies) // args.threads
    threads = []
    for i in range(args.threads):
        start = i * chunk_size
        end = start + chunk_size if i < (args.threads - 1) else None
        t = threading.Thread(target=test_proxies, args=(all_proxies[start:end], TEST_URL, 'https'))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    cost = int((datetime.now() - start_time).total_seconds())
    print(f'\nfiltered {len(active_proxies)} HTTPS proxies in {time_fmt(cost)}')

    # deduplicate and sort
    unique_proxies = sorted(set(active_proxies))

    if args.output:
        out_path = args.output
    else:
        os.makedirs(PROXIES_DIR, exist_ok=True)
        out_path = os.path.join(PROXIES_DIR, f'{start_time.strftime("%Y%m%d-%H%M%S")}.txt')

    with open(out_path, 'w') as f:
        f.write('\n'.join(unique_proxies) + '\n')
    print(f'wrote {len(unique_proxies)} proxies to {out_path}')


if __name__ == '__main__':
    main()
