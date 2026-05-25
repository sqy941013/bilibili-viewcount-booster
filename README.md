# bilibili-viewcount-booster

使用代理池对 B 站视频进行轮询点击，模拟游客播放。

## 快速开始

```bash
pip install -r requirements.txt
python booster.py <BV号> <目标播放数>
```

支持指定自定义代理源（URL 或本地 .txt）：

```bash
python booster.py <BV号> <目标播放数> [代理源]
```

## 工作原理

1. **获取代理** — 从多个免费代理源（CheckerProxy、ProxyScrape、GeoNode 等 GitHub 列表）聚合代理池，也可通过第三个参数指定自定义代理源
2. **过滤活跃代理** — 多线程测试每个代理是否可达
3. **轮询点击** — 使用活跃代理依次发送 B 站播放接口 POST 请求，达到目标播放数或每轮结束后自动等待间隔

B 站限制同一 IP 对视频点击间隔大于 5 分钟，因此每轮结束后会自动补足间隔。

## 参数说明

### booster.py

| 参数 | 说明 |
|------|------|
| `<BV号>` | 视频 BV 号（如 `BV1fz421o8J7`）或 AV 号（如 `AV123456789`） |
| `<目标播放数>` | 期望达到的播放数，当前播放数已达标时自动退出 |
| `[代理源]` | 可选，远程 URL 或本地 `.txt` 文件路径 |

```bash
# 仅使用内置代理源
python booster.py BV1ZxGj6kEVh 100

# 使用远程代理列表
python booster.py BV1ZxGj6kEVh 100 https://example.com/proxies.txt

# 使用本地代理文件
python booster.py BV1ZxGj6kEVh 100 proxies.txt

# 使用 proxy_pool（本地部署，自动获取 HTTPS 代理）
python booster.py BV1ZxGj6kEVh 100 --proxypool
python booster.py BV1ZxGj6kEVh 100 --proxypool http://10.0.0.1:5010

# 使用 BrightData 住宅代理（每次请求自动换 IP）
python booster.py BV1ZxGj6kEVh 100 --brightdata brd.superproxy.io:33335 brd-customer-xxx-zone-residential_country-cn:password
```

## 部署 proxy_pool（可选）

使用 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) 作为代理源，自动从多个免费采集器获取 HTTPS 代理，无需手动过滤。

```bash
# 一键启动 Redis + proxy_pool
cd docker
docker compose up -d

# 等待采集器获取代理（约 1-2 分钟）
docker compose logs -f proxy_pool

# 验证代理池是否就绪
curl http://127.0.0.1:5010/get/
```

proxy_pool 会持续从内置采集器拉取代理，`booster.py` 通过 `--proxypool` 直接调用 `/all/?type=https` 获取所有已验证的 HTTPS 代理，跳过本地过滤步骤。

### filter_proxies.py

从代理列表中筛选支持 HTTPS 的代理，输出为 txt 文件。

```bash
# 从远程 URL 加载代理列表
python filter_proxies.py https://example.com/proxies.txt

# 从本地文件加载
python filter_proxies.py raw_proxies.txt

# 指定输出路径
python filter_proxies.py raw_proxies.txt -o https_proxies.txt

# 调整线程数和超时
python filter_proxies.py raw_proxies.txt -t 100 --timeout 5
```

| 参数 | 说明 |
|------|------|
| `source` | 代理列表源：本地 `.txt` 路径或 URL |
| `-o, --output` | 输出文件路径（默认自动生成 `<源名>_https.txt`） |
| `-t, --threads` | 测试线程数（默认 250） |
| `--timeout` | 请求超时秒数（默认 10） |

## 运行效果

```
> python booster.py BV1ZxGj6kEVh 100

getting proxies from https://api.checkerproxy.net/v1/landing/archive/2026-05-24 ...
successfully get 1000 proxies from checkerproxy
collected 1000 proxies from available sources

filtering active proxies using http://httpbin.org/post ...
1000/1000 [━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━] 100.0%
successfully filter 86 active proxies using 1min 14s

start boosting BV1ZxGj6kEVh at 11:40:54
Initial view count: 42
100/100 [━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━] Hits: 312 | +58 done
Finish at 11:46:21
Statistics:
- Initial views: 42
- Final views: 100
- Total increase: 58
- Successful hits: 312
- Total attempts: 520
- Success rate: 60.00%
- Failed requests: 208
    - Connection timeout: 132
    - Connection refused: 58
    - HTTP 503: 18
```

## 可配置参数

脚本顶部变量可调整：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `timeout` | 3 | 代理请求超时（秒） |
| `thread_num` | 75 | 过滤代理时的线程数 |
| `round_time` | 305 | 每轮播放点击间隔（秒） |
| `update_pbar_count` | 10 | 进度条更新频率（每 N 个代理刷新一次） |

## 免责声明

本工具仅用于学习 B 站 API 交互和代理池技术的研究目的。请勿用于生产环境或违反 B 站服务条款的场景。使用本工具产生的任何后果由使用者自行承担。
