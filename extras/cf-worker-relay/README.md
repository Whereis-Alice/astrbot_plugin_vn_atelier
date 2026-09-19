# Cloudflare Worker 反代（Browser Rendering 版）

给 [Gal工房](../../README.md) 用的一个可选组件：一个部署在 Cloudflare 上的小服务，
替 AstrBot 用**真实的无头 Chrome** 去访问 TouchGal，把「浏览器验证（Cloudflare Managed Challenge）」这一关走完，
再把结果原样交回插件。

**它解决的问题**：AstrBot 跑在云服务器 / VPS 上时，TouchGal 的请求常年 `403`，日志里刷
「返回 403，疑似风控页」「浏览器指纹重试仍失败」。

**它的代价**：Cloudflare 免费版每天只给 **10 分钟浏览器运行时长**。具体可用次数取决于冷启动、页面耗时、保活和缓存命中率，不适合高频使用。
详见下面的[免费额度](#免费额度说明必读)。

**你需要付出**：一个 Cloudflare 免费账号，以及大约 5 分钟。不需要服务器额外内存，不需要买代理。

---

## 先确认你真的需要它

满足下面任一条，才说明你撞的是浏览器验证：

- AstrBot 日志里出现「开启了浏览器验证」；
- 响应头带 `cf-mitigated: challenge`；
- 返回的正文是标题为 `Just a moment...` 的等待页。

在**运行 AstrBot 的那台机器上**跑一次，最直接：

```bash
/root/AstrBot/.venv/bin/python -c "from curl_cffi import requests as r; x = r.get('https://www.touchgal.ink/api/home/random', impersonate='chrome136', timeout=20); print(x.status_code, x.headers.get('cf-mitigated', '-'))"
```

输出 `403 challenge` → 需要本方案（或代理）。输出 `200 -` → 你的问题不在这里，别白折腾。

如果你手边已经有一个能正常访问 TouchGal 的代理，**优先填插件的「代理地址」**，那个更简单也没有额度限制。
本方案是给「没有代理可用」的人准备的。

---

## 为什么普通反代不管用

此前测试中，普通 Worker 返回 403，诊断端点显示 `tls=off` / `http=unknown`；
Browser Rendering 版本返回 200。这是当时那组请求的观察结果，不能证明所有反代都无效，
也不能从连接信息单独确定站点的判定原因。目标站点策略可能变化，部署后应验证实际结果。

---

## 部署

需要满足当前 Wrangler 要求的 Node.js 版本（建议 Node.js 22 LTS 或更新版本）（`node -v` 确认）。全程在你自己电脑上操作，不动服务器。

**1. 拿到这个目录**

```bash
git clone https://github.com/Whereis-Alice/astrbot_plugin_vn_atelier.git
cd astrbot_plugin_vn_atelier/extras/cf-worker-relay
npm install
```

**2. 登录 Cloudflare**（会打开浏览器授权，免费账号即可）

```bash
npx --yes wrangler@4 login
```

**3. 设一个访问口令**

这个口令会成为 URL 里的一段路径。没有它的请求一律 404，避免你的 Worker 被别人当免费代理白嫖。
随手生成一串 16 位以上的随机字符（只用字母和数字，别带斜杠）：

```bash
npx --yes wrangler@4 secret put RELAY_TOKEN
```

**4. 部署**

```bash
npx --yes wrangler@4 deploy
```

成功后会打印形如 `https://touchgal-relay.你的子域.workers.dev` 的地址。

> 首次部署时 Cloudflare 可能提示需要在控制台启用 Browser Rendering，按提示点一下即可（免费版可用）。

**5. 自检**

把地址和口令拼起来访问诊断路由（注意 `__diag` 是两个下划线）：

```bash
curl "https://touchgal-relay.你的子域.workers.dev/你的口令/__diag"
```

返回一段 JSON（含 `upstream`、`sessions`、`limits`）就算通了。返回 `404` 说明口令拼错了。

---

## 在插件里怎么填

插件配置里只改两项，**不需要改任何代码，也不需要重装插件**：

| 配置项 | 填什么 |
| --- | --- |
| **TouchGal 站点地址** | `https://touchgal-relay.你的子域.workers.dev/你的口令/` |
| **单次请求超时** | `60`（默认 30 偏紧，浏览器冷启动可能要十几秒） |

结尾的斜杠漏了也没关系，插件会自动补上。但**口令那一段不能漏** —— 少了它 Worker 一律回 404。

填完保存，插件会自动重载配置。之后 `工房 找 fate` 之类的指令就能正常出结果了。

需要看登录后才可见的内容（含 R18）时，「TouchGal 登录 Token」照原样填，反代会把 Cookie 一并转发过去。

---

## 免费额度说明（必读）

Browser Rendering 在 Workers Free 计划下的关键限制，以及实测表现：

| 项目 | 免费版限制 | 说明 |
| --- | --- | --- |
| 每天浏览器运行时长 | **10 分钟** | 按浏览器实例**存活时长**计费，不是按请求数 |
| 新建浏览器实例频率 | 约 **1 次 / 分钟** | 超了返回 `429` |
| 并发浏览器实例 | 以当前账号限额为准 | 用诊断路由查看 `maxConcurrentSessions` |

10 分钟听着很少，但这个 Worker 做了两件事把它省下来：

- **会话复用**：一次请求结束后浏览器实例保活 30 秒，紧接着的请求直接接上去，不重新启动。
  「搜索 → 看详情 → 拿下载」这种连续操作基本只消耗一次启动。
- **响应缓存**：除随机接口外，同样的请求 10 分钟内直接走 Cloudflare 缓存，一点浏览器时长都不花。

此前一组「搜索 + 详情 + 资源列表」请求耗时约 10 秒，**这不等于全部计费时长**。
浏览器还会保活 30 秒，等待也消耗额度；零散请求的成本可能明显高于连续请求。
请结合实际用量判断，不能把每天 10 分钟直接折算成固定查询次数。

额度耗尽时上游会返回 `429 Browser time limit exceeded for today`，Worker 把它转成 `429` + `retry-after`，
插件那边表现为一次失败并写日志，不会崩。第二天自动恢复。

额度与费用以 [Cloudflare 官方说明](https://developers.cloudflare.com/browser-rendering/platform/limits/) 和账号当前套餐为准；保活时长也计入浏览器用量。

---

## 它是怎么工作的

一次 API 请求（例如 `/api/home/random`）的完整路径：

1. **校验口令** —— 路径前缀不匹配 `RELAY_TOKEN` 就直接 404。
2. **查缓存** —— 命中就直接返回，响应头带 `x-relay-cache: hit`。随机接口永不缓存。
3. **取浏览器** —— 先用 `puppeteer.sessions()` 找空闲实例复用；找不到才新建；
   新建撞上每分钟限额时，回头重试 5 次去等上一个请求刚释放的实例（这一步是让连续请求不报 429 的关键）。
4. **带上 Cookie** —— 把插件发来的 Cookie 逐条写进浏览器。
   逐条是有意的：站点的 NSFW 开关 Cookie 名字里带竖线，整批写入时可能被拒，那样会把登录 Token 一起带走。
5. **在页面里发请求** —— 先把标签页导航到目标站点一个极轻量的路径，落到同一个 origin 上，
   然后在页内用 `fetch()` 发真正的请求。这样请求是同源的、带 Cookie 的、由真实 Chrome 发出的。
   万一还是 403，就退回去加载一次首页把验证走完，再重放。
6. **收尾** —— 只 `disconnect()`，**绝不关标签页也不关浏览器**。
   关掉最后一个标签页会让实例直接退出，保活就白设了。

网页 HTML（详情页）走另一条更简单的路：直接 `page.goto()` 然后取 `page.content()`，
返回的是 Chrome 渲染后的 HTML —— 插件现有的解析逻辑对它是兼容的，无需改动。

响应头里的三个字段可以用来判断发生了什么：

| 响应头 | 取值 | 含义 |
| --- | --- | --- |
| `x-relay-mode` | `browser-fresh` | 新启动了一个浏览器实例（花时长） |
| `x-relay-mode` | `browser-reused` | 复用了现成实例（省时长） |
| `x-relay-cache` | `hit` / `miss` / `bypass` | 缓存命中 / 未命中 / 不缓存 |

---

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| `404 not found` | 口令拼错，或地址少了结尾斜杠 |
| `500 missing RELAY_TOKEN` | 第 3 步跳过了，跑一次 `wrangler secret put RELAY_TOKEN` 再重新部署 |
| `429` | 撞上每分钟新建限额或当日 10 分钟总额，等一分钟或等第二天 |
| `502 relay failed` | 上游超时或页面加载失败，一般重试即可；持续出现看 `npx wrangler@4 tail` 日志 |
| 插件报超时 | 「单次请求超时」调到 60；冷启动确实会慢 |
| 仍然 403 | 目标站点可能升级了防护等级，或 `UPSTREAM` 域名已失效 |

看实时日志：

```bash
npx --yes wrangler@4 tail
```

---

## 不用了怎么删

```bash
npx --yes wrangler@4 delete touchgal-relay
```

然后把插件的「TouchGal 站点地址」改回 `https://www.touchgal.ink`。

---

## 几点说明

- **这是可选组件**，不装它插件照样能用；VNDB、以图识番等功能本来就不经过 TouchGal。
- **它不是给别人用的公开代理**。口令请自己保管，别贴到公开的地方。
- 目标站点可以换：改 `wrangler.toml` 里 `[vars] UPSTREAM` 那一行，重新部署即可。
- 请遵守目标站点的使用条款，仅用于自己的正常查询，不要拿它做批量抓取。
