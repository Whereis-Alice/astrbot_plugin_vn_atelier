import puppeteer from "@cloudflare/puppeteer";

// 反代目标站点。部署时用 wrangler.toml 的 [vars] UPSTREAM 覆盖，站点换域名不用改代码。
const DEFAULT_UPSTREAM = "https://www.touchgal.ink";

// 免费额度按「浏览器实例存活时长」计费，keep_alive 越长越省启动次数、越费时长。
// 30s 刚好覆盖「搜索 -> 详情 -> 下载」这种连续三连击。
const KEEP_ALIVE_MS = 30000;
const NAV_TIMEOUT_MS = 45000;

// 随机接口每次都要新结果，不能缓存；其余接口缓存 10 分钟，能把浏览器时长省掉一大截。
const CACHE_TTL_SECONDS = 600;
const NO_CACHE_PATHS = ["/api/home/random"];

// 只用来把页面落到目标站点的 origin 上，比加载整个首页快得多。
const LIGHT_ORIGIN_PATH = "/cdn-cgi/trace";

// 访问口令与目标站点都从环境读取，不写死在代码里：
// RELAY_TOKEN 用 wrangler secret 存，UPSTREAM 用 wrangler.toml 的 [vars] 存。
function readConfig(env) {
  const secret = String(env.RELAY_TOKEN || "").trim();
  if (!secret) throw new Error("missing RELAY_TOKEN: run wrangler secret put RELAY_TOKEN");
  let upstream = String(env.UPSTREAM || DEFAULT_UPSTREAM).trim();
  while (upstream.endsWith("/")) upstream = upstream.slice(0, -1);
  return { secret: secret, upstream: upstream, upstreamHost: new URL(upstream).host };
}

function jsonResponse(status, payload, extraHeaders) {
  const headers = Object.assign({ "content-type": "application/json; charset=utf-8" }, extraHeaders || {});
  return new Response(JSON.stringify(payload), { status: status, headers: headers });
}

function parseCookieHeader(raw, host) {
  const out = [];
  if (!raw) return out;
  for (const piece of raw.split(";")) {
    const idx = piece.indexOf("=");
    if (idx <= 0) continue;
    const name = piece.slice(0, idx).trim();
    const value = piece.slice(idx + 1).trim();
    if (!name) continue;
    out.push({ name: name, value: value, domain: host, path: "/" });
  }
  return out;
}

async function cacheKeyFor(method, pathWithQuery, body) {
  const material = method + " " + pathWithQuery + " " + (body || "");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(material));
  const hex = Array.from(new Uint8Array(digest))
    .map(function (b) { return b.toString(16).padStart(2, "0"); })
    .join("");
  return new Request("https://relay-cache.invalid/" + hex, { method: "GET" });
}

function isCacheable(rest) {
  for (const skip of NO_CACHE_PATHS) {
    if (rest === skip || rest.startsWith(skip + "?")) return false;
  }
  return true;
}

const REUSE_ATTEMPTS = 5;
const REUSE_BACKOFF_MS = 800;

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

async function tryReuse(env) {
  let sessions;
  try {
    sessions = await puppeteer.sessions(env.BROWSER);
  } catch (err) {
    return null;
  }
  for (const candidate of sessions) {
    if (candidate.connectionId) continue;
    try {
      return await puppeteer.connect(env.BROWSER, candidate.sessionId);
    } catch (err) {
      // 这个会话可能刚被别人抢走，换下一个。
    }
  }
  return null;
}

async function acquireBrowser(env) {
  // 免费额度对「新建浏览器」有每分钟限额（实测约 1 次/分钟），所以顺序是：
  // 先抢空闲会话 -> 抢不到再新建 -> 新建撞限额就退回去等空闲会话。
  // 上一次请求刚 disconnect 时，会话要过一小会儿才会被列成空闲，因此要重试。
  const reused = await tryReuse(env);
  if (reused) return { browser: reused, reused: true };

  let launchError = null;
  try {
    const fresh = await puppeteer.launch(env.BROWSER, { keep_alive: KEEP_ALIVE_MS });
    return { browser: fresh, reused: false };
  } catch (err) {
    launchError = err;
  }

  for (let attempt = 0; attempt < REUSE_ATTEMPTS; attempt += 1) {
    await sleep(REUSE_BACKOFF_MS);
    const late = await tryReuse(env);
    if (late) return { browser: late, reused: true };
  }
  throw launchError;
}

async function acquirePage(browser) {
  // 复用已有标签页：关掉最后一个标签页会让整个浏览器实例退出，keep_alive 就白设了。
  try {
    const pages = await browser.pages();
    if (pages.length > 0) return pages[0];
  } catch (err) {
    // 拿不到就新建。
  }
  return await browser.newPage();
}

async function fetchInPage(page, target, init) {
  return await page.evaluate(
    async function (fetchUrl, fetchInit) {
      const res = await fetch(fetchUrl, fetchInit);
      return {
        status: res.status,
        contentType: res.headers.get("content-type") || "application/json; charset=utf-8",
        body: await res.text(),
      };
    },
    target,
    init,
  );
}

export default {
  async fetch(request, env, ctx) {
    let cfg;
    try {
      cfg = readConfig(env);
    } catch (err) {
      return jsonResponse(500, { relayError: String(err) });
    }

    const url = new URL(request.url);
    const prefix = "/" + cfg.secret;

    if (url.pathname === prefix) {
      return new Response(null, { status: 302, headers: { location: prefix + "/" } });
    }
    if (!url.pathname.startsWith(prefix + "/")) {
      return new Response("not found", { status: 404 });
    }

    const rest = url.pathname.slice(prefix.length);

    if (rest === "/__diag") {
      const diag = { upstream: cfg.upstream };
      try { diag.sessions = await puppeteer.sessions(env.BROWSER); } catch (err) { diag.sessions = String(err); }
      try { diag.limits = await puppeteer.limits(env.BROWSER); } catch (err) { diag.limits = String(err); }
      return jsonResponse(200, diag);
    }

    const target = cfg.upstream + rest + url.search;
    const wantsApi = rest.startsWith("/api/");
    const body = request.method === "GET" || request.method === "HEAD" ? "" : await request.text();

    const cache = caches.default;
    let cacheKey = null;
    if (isCacheable(rest)) {
      cacheKey = await cacheKeyFor(request.method, rest + url.search, body);
      const hit = await cache.match(cacheKey);
      if (hit) {
        const headers = new Headers(hit.headers);
        headers.set("x-relay-cache", "hit");
        return new Response(hit.body, { status: hit.status, headers: headers });
      }
    }

    let handle;
    try {
      handle = await acquireBrowser(env);
    } catch (err) {
      const message = String(err);
      const rateLimited = message.indexOf("429") >= 0 || message.indexOf("Rate limit") >= 0;
      return jsonResponse(rateLimited ? 429 : 503, { relayError: "browser unavailable: " + message },
        rateLimited ? { "retry-after": "60" } : undefined);
    }

    const browser = handle.browser;
    try {
      const page = await acquirePage(browser);
      page.setDefaultNavigationTimeout(NAV_TIMEOUT_MS);

      // 逐条写入：站点的 NSFW 开关 cookie 名字里带竖线，单条被 CDP 拒绝时
      // 不该把登录 token 一起带走。
      for (const cookie of parseCookieHeader(request.headers.get("cookie"), cfg.upstreamHost)) {
        try { await page.setCookie(cookie); } catch (err) { /* 跳过这一条 */ }
      }

      let response;
      if (!wantsApi) {
        let navResponse = await page.goto(target, { waitUntil: "domcontentloaded" });
        if (navResponse && navResponse.status() === 403) {
          // 首次导航撞上验证页时，Chrome 已经把通行 cookie 拿到手了，重放一次即可。
          navResponse = await page.goto(target, { waitUntil: "domcontentloaded" });
        }
        const html = await page.content();
        response = new Response(html, {
          status: navResponse ? navResponse.status() : 200,
          headers: { "content-type": "text/html; charset=utf-8" },
        });
      } else {
        const init = { method: request.method, headers: {} };
        for (const name of ["content-type", "x-requested-with", "accept", "accept-language"]) {
          const value = request.headers.get(name);
          if (value) init.headers[name] = value;
        }
        if (body) init.body = body;

        const current = page.url() || "";
        if (current.indexOf(cfg.upstream) !== 0) {
          await page.goto(cfg.upstream + LIGHT_ORIGIN_PATH, { waitUntil: "domcontentloaded" });
        }
        let result = await fetchInPage(page, target, init);
        if (result.status === 403) {
          // 轻量落点没触发验证、因此也没拿到通行 cookie，退回真首页把验证走完再重放。
          await page.goto(cfg.upstream + "/", { waitUntil: "domcontentloaded" });
          result = await fetchInPage(page, target, init);
        }
        response = new Response(result.body, {
          status: result.status,
          headers: { "content-type": result.contentType },
        });
      }

      response.headers.set("x-relay-mode", handle.reused ? "browser-reused" : "browser-fresh");
      response.headers.set("x-relay-cache", cacheKey ? "miss" : "bypass");
      if (cacheKey && response.status === 200) {
        const cachedHeaders = new Headers(response.headers);
        cachedHeaders.set("cache-control", "public, max-age=" + CACHE_TTL_SECONDS);
        const cached = new Response(response.clone().body, {
          status: response.status,
          headers: cachedHeaders,
        });
        ctx.waitUntil(cache.put(cacheKey, cached));
      }
      return response;
    } catch (err) {
      return jsonResponse(502, { relayError: "relay failed: " + String(err) });
    } finally {
      // 只 disconnect、不 close：让实例按 keep_alive 留给下一次请求复用。
      try { await browser.disconnect(); } catch (err) { /* ignore */ }
    }
  },
};