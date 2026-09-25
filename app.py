#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Bot-hosting 自动续期（多账号版）
# 配置方式（编号后缀式，兼容旧单账号）：
#   单账号（旧）：SESSION_TOKEN / DISCORD_TOKEN / EMAIL
#   多账号：SESSION_TOKEN_1 / DISCORD_TOKEN_1 / EMAIL_1，_2，_3 ...（最多支持 10 个）
#   GH_TOKEN / TG_BOT_TOKEN / TG_CHAT_ID 全局共用，只需填一次
# 回写规则：账号 N 刷新后自动 gh secret set SESSION_TOKEN_N；单账号旧模式回写 SESSION_TOKEN

import os, re, sys, time, json, requests, subprocess
import urllib.request, urllib.parse, urllib.error
from datetime import datetime
from seleniumbase import SB

# 全局共用配置
GH_TOKEN      = os.environ.get("GH_TOKEN") or ""        # GitHub PAT token,用于自动更新session token,可选
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""      # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""    # TG bot token

MAX_ACCOUNTS = 10


def parse_dc_token(raw: str) -> str:
    # 兼容旧逻辑：DISCORD_TOKEN 可能带 "xxx,token" 前缀，取逗号后一段
    if not raw:
        return ""
    parts = raw.split(",", 1)
    return parts[-1].strip()


def collect_accounts():
    """收集账号列表。优先编号式；无编号时回退到旧单账号。"""
    accounts = []
    for i in range(1, MAX_ACCOUNTS + 1):
        email = (os.environ.get(f"EMAIL_{i}") or "").strip()
        sess = (os.environ.get(f"SESSION_TOKEN_{i}") or "").strip()
        dc_raw = (os.environ.get(f"DISCORD_TOKEN_{i}") or "").strip()
        dc = parse_dc_token(dc_raw)
        if sess or dc:
            accounts.append({
                "idx": i,
                "email": email,
                "session_token": sess,
                "dc_token": dc,
                "secret_name": f"SESSION_TOKEN_{i}",
                "legacy": False,
            })
    if accounts:
        # 兼容：有人已配好 _2/_3，但 _1 仍用旧变量名，也一并纳入
        legacy_sess = (os.environ.get("SESSION_TOKEN") or "").strip()
        legacy_dc = parse_dc_token((os.environ.get("DISCORD_TOKEN") or "").strip())
        legacy_email = (os.environ.get("EMAIL") or "").strip()
        idxs = {a["idx"] for a in accounts}
        if (legacy_sess or legacy_dc) and 1 not in idxs:
            accounts.insert(0, {
                "idx": 1,
                "email": legacy_email,
                "session_token": legacy_sess,
                "dc_token": legacy_dc,
                "secret_name": "SESSION_TOKEN_1",
                "legacy": False,
            })
        accounts.sort(key=lambda a: a["idx"])
        return accounts
    # 无编号变量：回退旧单账号模式
    legacy_sess = (os.environ.get("SESSION_TOKEN") or "").strip()
    legacy_dc = parse_dc_token((os.environ.get("DISCORD_TOKEN") or "").strip())
    legacy_email = (os.environ.get("EMAIL") or "").strip()
    if not legacy_sess and not legacy_dc:
        return []
    return [{
        "idx": 1,
        "email": legacy_email,
        "session_token": legacy_sess,
        "dc_token": legacy_dc,
        "secret_name": "SESSION_TOKEN",
        "legacy": True,
    }]


def mask_email(email: str) -> str:
    if '@' in email:
        name, domain = email.split('@', 1)
        if len(name) > 4:
            return f"{name[:2]}****{name[-2:]}@{domain}"
        return f"{name}@{domain}"
    if email:
        return email[:2] + '****'
    return "（未填）"


# 获取cookie到期时间
def get_cookie_info(sb, name):
    cookies = sb.get_cookies()
    for c in cookies:
        if c.get('name') == name:
            value = c.get('value')
            expiry_ts = c.get('expiry')
            expiry_dt = datetime.fromtimestamp(expiry_ts) if expiry_ts else None
            return value, expiry_dt
    return None, None

# 检查是否需要更新cookie
def should_update_cookie(new_value, old_value, expiry_dt, days_threshold=3):
    if new_value is None:
        return False
    if new_value != old_value:
        return True
    if expiry_dt:
        remaining = (expiry_dt - datetime.now()).total_seconds()
        if remaining < days_threshold * 24 * 3600:
            return True
    return False

# 更新cookie到secrets
def update_github_secret(secret_name, new_value):
    if not new_value:
        print(f"⚠️ 跳过更新 {secret_name}：新值为空")
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    print(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False,
            env=env
        )
        if proc.returncode == 0:
            return True
        else:
            print(f"❌ 更新失败: {proc.stderr.strip()}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

# 发送tg通知
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

# 通知格式（多账号：按账号填充邮箱与登录方式）
def format_notification(status: str, email: str = "", login_method: str = "SESSION_TOKEN",
                        extra: str = "", error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    masked_email = mask_email(email)

    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked_email}",
    ]
    if login_method != "SESSION_TOKEN":
        lines.append(f"🔐 登录方式: {login_method}")
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)

# 关闭 Google Funding Choices 同意弹窗（CI 干净 profile + EU 出口必弹，全屏遮罩挡点击）。
# 找不到就静默返回，绝不抛异常。
def dismiss_consent_popup(sb, tag):
    try:
        if sb.is_element_visible('button:contains("Do not consent")', timeout=3):
            sb.click('button:contains("Do not consent")')
            print(f"{tag} 🍪 已关闭同意弹窗（主文档）")
            sb.sleep(1)
            return True
    except Exception:
        pass
    try:
        iframes = sb.driver.find_elements("css selector", "iframe")
    except Exception:
        return False
    for i in range(len(iframes)):
        try:
            sb.driver.switch_to.frame(i)
            try:
                btns = sb.driver.find_elements("xpath", ".//button[contains(., 'Do not consent')]")
            except Exception:
                btns = []
            for b in btns:
                try:
                    if b.is_displayed():
                        b.click()
                        print(f"{tag} 🍪 已关闭同意弹窗（iframe#{i}）")
                        sb.sleep(1)
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            try:
                sb.driver.switch_to.default_content()
            except Exception:
                pass
    return False


# Turnstile 双层探测：① 票据（getResponse / cf-turnstile-response 隐藏域，前端 POST 就用它）；
# ② 组件状态（iframe 内复选框）。票据为空但组件显示已解时也放行，后端 60s 轮询是最终裁判。
def probe_turnstile(sb):
    token = ""
    try:
        token = sb.execute_script(
            "var t='';"
            "try{ if(typeof turnstile!=='undefined'&&turnstile.getResponse){t=turnstile.getResponse()||'';} }catch(e){}"
            "if(!t){var h=document.querySelector('input[name=\"cf-turnstile-response\"]'); if(h){t=h.value||'';}}"
            "return t;") or ""
    except Exception:
        token = ""
    solved = False
    dbg = []
    try:
        n = sb.execute_script(
            "return document.querySelectorAll('iframe[src*=\"challenges.cloudflare.com\"]').length") or 0
        dbg.append(f"iframes={n}")
    except Exception as e:
        dbg.append(f"iframes=?({str(e)[:60]})")
    try:
        sb.switch_to_frame('iframe[src*="challenges.cloudflare.com"]')
        try:
            state = sb.execute_script(
                "var el=document.querySelector('input[type=\"checkbox\"]');"
                "return el ? String(el.getAttribute('aria-checked')||el.checked) : 'no-checkbox';")
            dbg.append(f"checkbox={state}")
            solved = str(state).lower() == "true"
        finally:
            sb.switch_to_default_content()
    except Exception as e:
        dbg.append(f"frame-read-fail:{str(e)[:80]}")
    return token, solved, "; ".join(dbg)


def wait_for_turnstile_token(sb, timeout=30):
    start = time.time()
    last_dbg = ""
    while time.time() - start < timeout:
        token, solved, last_dbg = probe_turnstile(sb)
        if token:
            print(f"✅ Turnstile 票据已就绪（长度 {len(token)}）[{last_dbg}]")
            return True
        if solved:
            print(f"⚠️ 未读到票据但组件显示已解，先放行由后端校验 [{last_dbg}]")
            return True
        time.sleep(2)
    print(f"❌ Turnstile 票据超时未就绪 [{last_dbg}]")
    return False

# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

# 时间格式化
def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except:
        return countdown_str

# 获取过期日期
def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",   # Expires 2026/07/07
        r"[Ee]xpires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",   # Expires 07/07/2026 (MM/DD/YYYY)
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew",        # 2026/07/07 - renew
        r"(\d{2}/\d{2}/\d{4})\s*[\-–]\s*renew",        # 07/07/2026 - renew
        r"(\d{4}/\d{2}/\d{2})\s*[\-–]\s*renew manually to extend for 4 days", # 2026/07/07 - renew manually to extend for 4 days
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            date_str = match.group(1)
            # 如果是 MM/DD/YYYY 格式，转换为 YYYY/MM/DD
            if len(date_str.split('/')[-1]) == 4:  # 年份长度4
                parts = date_str.split('/')
                if len(parts[0]) == 2:  # 第一部分是2位（月）
                    # 修正：将 MM/DD/YYYY 转为 YYYY/MM/DD
                    return f"{parts[2]}/{parts[0]}/{parts[1]}"
            return date_str
    return None

#   Discord OAuth 登录（SESSION_TOKEN 失效时的备用方案）
DISCORD_CLIENT_ID   = "884382422530158623"
OAUTH_REDIRECT_URI  = "https://bot-hosting.net/login"
OAUTH_SCOPE         = "identify email guilds"
DISCORD_API         = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
STATE_RE = re.compile(r"[?&]state=([^&]+)")


def capture_discord_state(sb) -> str:
    """打开 /login/discord，从落地页 URL 里提取本次会话的 state"""
    print("🔎 获取 Discord OAuth state...")
    sb.uc_open_with_reconnect("https://bot-hosting.net/login/discord", reconnect_time=4)
    time.sleep(2)

    url = sb.get_current_url()
    if "discord.com" not in url:
        print(f"⚠️ 未跳转到 Discord 相关页面，当前 URL：{url}")
        return ""

    m = STATE_RE.search(url)
    if not m:
        print(f"❌ 未能从 URL 中解析出 state，当前 URL：{url}")
        return ""

    state = urllib.parse.unquote(m.group(1))
    print(f"✅ 已捕获 state（当前落地页：{urllib.parse.urlparse(url).path}）")
    return state


def discord_authorize(state: str, dc_token: str) -> str:
    """用 DC_TOKEN 直接完成 Discord 侧授权，返回跳转回 bot-hosting.net 的 location"""
    query = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "scope":         OAUTH_SCOPE,
        "state":         state,
    })
    authorize_url = f"{DISCORD_API}?{query}"

    referer = (
        "https://discord.com/oauth2/authorize?" +
        urllib.parse.urlencode({
            "client_id":     DISCORD_CLIENT_ID,
            "redirect_uri":  OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope":         OAUTH_SCOPE,
            "state":         state,
        })
    )

    headers = {
        "accept":           "*/*",
        "authorization":    dc_token,
        "content-type":     "application/json",
        "origin":           "https://discord.com",
        "referer":          referer,
        "user-agent":       DISCORD_UA,
        "x-discord-locale": "zh-CN",
    }

    body = json.dumps({
        "permissions": "0",
        "authorize": True,
        "integration_type": 0,
        "location_context": {
            "guild_id": "10000",
            "channel_id": "10000",
            "channel_type": 10000,
        },
    })

    # 如果配置了代理，Discord API 请求也走代理
    proxies = None
    _is_proxy = os.environ.get("IS_PROXY", "false").lower() == "true"
    _proxy_server = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    if _is_proxy:
        proxies = {"http": _proxy_server, "https": _proxy_server}

    try:
        resp = requests.post(authorize_url, headers=headers, data=body, proxies=proxies, timeout=20)
        if resp.status_code != 200:
            print(f"❌ Discord OAuth2 授权失败: HTTP {resp.status_code} - {resp.text[:300]}")
            return ""
        resp_data = resp.json()
    except Exception as e:
        print(f"❌ Discord OAuth2 授权异常: {e}")
        return ""

    location = resp_data.get("location", "")
    if not location:
        print(f"❌ 授权响应中未找到 location 字段: {resp_data}")
        return ""

    masked = re.sub(r"code=[^&]+", "code=***", location)
    print(f"✅ 拿到回调 URL: {masked}")
    return location


def do_discord_login(sb, dc_token: str) -> bool:
    """通过 Discord Token 走完整 OAuth 流程登录 bot-hosting.net"""
    print("\n🔑 通过 Discord Token 登录...")

    state = capture_discord_state(sb)
    if not state:
        sb.save_screenshot("login_no_state.png")
        return False

    location = discord_authorize(state, dc_token)
    if not location:
        return False

    print("↩️ 携带授权码打开回调链接...")
    sb.uc_open_with_reconnect(location, reconnect_time=4)
    time.sleep(3)

    url = sb.get_current_url()

    if "/error/banned" in url:
        print("🚫 账号已被封禁")
        sb.save_screenshot("login_banned.png")
        return False

    if "bot-hosting.net" not in url:
        print(f"❌ 回调后未跳转至 bot-hosting.net，当前 URL：{url}")
        sb.save_screenshot("login_no_redirect.png")
        return False

    try:
        body_text = sb.get_text("body")
    except Exception:
        body_text = ""
    if "fraud" in body_text.lower():
        print("🚫 触发风控（fraud attempt），可能是 IP 被拦截")
        sb.save_screenshot("login_fraud.png")
        return False

    for _ in range(30):
        url = sb.get_current_url()
        path = urllib.parse.urlparse(url).path
        if "bot-hosting.net" in url and path != "/login" and not path.startswith("/login/discord"):
            print(f"✅ Discord OAuth 登录成功！当前页面：{url}")
            return True
        time.sleep(0.5)

    print(f"❌ 登录超时或未跳转成功，最终停留在：{url}")
    try:
        body_text = sb.get_text("body")
        print(f"📄 页面正文片段：{body_text[:200].strip()!r}")
    except Exception:
        pass
    sb.save_screenshot("login_timeout.png")
    return False


def renew_one_account(sb, acct) -> dict:
    """单个账号的完整续期流程。返回 {'ok': bool, 'summary': str}，内部已发送该账号的 TG 通知。"""
    idx = acct["idx"]
    email = acct["email"] or f"账号{idx}"
    session_token = acct["session_token"]
    dc_token = acct["dc_token"]
    secret_name = acct["secret_name"]
    tag = f"[账号{idx} {mask_email(email)}]"
    login_method = "SESSION_TOKEN"
    result = {"ok": False, "summary": "未知"}

    # 账号隔离：清掉上一个账号的 cookie
    try:
        sb.delete_all_cookies()
    except Exception:
        pass

    # 构造cookie
    cookies = {
        "session_token": session_token,
        "login": "true",
        "theme": "system",
    }

    login_ok = False

    # 方式1: SESSION_TOKEN Cookie 登录（默认）
    if session_token:
        print(f"{tag} 🚀 Cookie 登录...")
        sb.open("https://bot-hosting.net/")
        sb.wait_for_ready_state_complete()
        sb.sleep(2)
        dismiss_consent_popup(sb, tag)

        print(f"{tag} 📝 注入 Cookie...")
        for name, value in cookies.items():
            if value:
                sb.add_cookie({"name": name, "value": value, "domain": "bot-hosting.net"})

        print(f"{tag} 🌐 访问 https://bot-hosting.net/a/billings ...")
        sb.open("https://bot-hosting.net/a/billings")
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        current_url = sb.get_current_url()
        current_title = sb.get_title()
        print(f"{tag} 📝 当前URL: {current_url}, Title: {current_title}")

        if "/a/billings" in current_url and "/login" not in current_url and "error=" not in current_url:
            login_ok = True
            print(f"{tag} ✅ SESSION_TOKEN 登录成功, 当前已到达账单页")
        else:
            print(f"{tag} ❌ SESSION_TOKEN 登录失败，当前URL: {current_url}, 当前标题: {current_title}")

    # 方式2: Discord OAuth 登录（备用）
    if not login_ok and dc_token:
        login_method = "Discord Token"
        print(f"\n{tag} 🔄 SESSION_TOKEN 登录失败或未配置，尝试 Discord OAuth 登录...")
        if do_discord_login(sb, dc_token):
            print(f"{tag} 🌐 访问 https://bot-hosting.net/a/billings ...")
            sb.open("https://bot-hosting.net/a/billings")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            current_url = sb.get_current_url()
            current_title = sb.get_title()
            print(f"{tag} 📝 当前URL: {current_url}, Title: {current_title}")

            if "a/billings" in current_url:
                login_ok = True
                print(f"{tag} ✅ Discord OAuth 登录成功,当前已到达账单页")
            else:
                print(f"{tag} ❌ Discord OAuth 登录后仍未到达账单页，当前URL: {current_url}")
        else:
            print(f"{tag} ❌ Discord OAuth 登录失败")

    if not login_ok:
        error_msg = "Cookie 已失效或页面异常"
        if not session_token and dc_token:
            error_msg = "Discord OAuth 登录失败"
        elif session_token and dc_token:
            error_msg = "SESSION_TOKEN 和 Discord OAuth 均失败"
        send_telegram_message(format_notification("❌ 登录失败", email=email, login_method=login_method, error=error_msg))
        result["summary"] = f"❌ 登录失败（{error_msg}）"
        return result

    if login_method == "Discord Token":
        print(f"{tag} ℹ️ 本次使用 Discord OAuth 登录，新的 SESSION_TOKEN 将自动更新到 Secrets")

    # 提取当前到期日期（先清同意弹窗，否则外层 Renew 点不到）
    dismiss_consent_popup(sb, tag)
    sb.sleep(2)
    page_source = sb.get_page_source()
    current_expiry = extract_expiry_date(page_source)
    if current_expiry:
        print(f"{tag} 📅 当前到期日期: {current_expiry}")
    else:
        print(f"{tag} ⚠️ 未能提取当前到期日期")

    # 寻找外部续期按钮
    outer_renew_selector = None
    countdown_text = None
    possible_selectors = [
        'button:contains("Renew")',
        'button:contains("Renew free plan")',
        'a:contains("Renew")',
        '[class*="renew"]',
        '[class*="Renew"]',
    ]

    for selector in possible_selectors:
        try:
            if sb.is_element_visible(selector):
                button_text = sb.get_text(selector)
                if "Renew in" in button_text:
                    match = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", button_text)
                    if match:
                        countdown_text = match.group(1)
                    break
                elif "Renew" in button_text and "in" not in button_text.lower():
                    outer_renew_selector = selector
                    print(f"{tag} ✅ 续期按钮可用: '{button_text}'")
                    break
        except Exception as e:
            pass

    # 点击外部续期按钮等待弹窗
    if outer_renew_selector:
        print(f"{tag} 🔄 点击外部续期按钮，等待验证窗口...")
        try:
            sb.sleep(2)
            sb.click(outer_renew_selector)
            sb.sleep(15)  # 等待模态框加载，可能因网络因素加载慢
        except Exception as e:
            print(f"{tag} ❌ 点击外部按钮失败: {e}")
            send_telegram_message(format_notification("❌ 续期失败", email=email, login_method=login_method, error="点击外部续期按钮出错"))
            result["summary"] = "❌ 续期失败（点击按钮出错）"
            return result

        # 处理弹窗中的 Turnstile：票据就绪后才能点确认（后端只认 turnstileToken）
        print(f"{tag} 🔒 等待弹窗中的 Turnstile 票据...")
        token_ok = False
        for attempt in range(1, 4):
            try:
                sb.uc_gui_click_captcha()
                time.sleep(12)
            except Exception as e:
                print(f"{tag} ⚠️ 点击 Turnstile 出错: {e}")

            if wait_for_turnstile_token(sb, timeout=30):
                token_ok = True
                break
            else:
                print(f"{tag} ⏳ 第 {attempt} 次未拿到票据，重试点击...")

        if not token_ok:
            print(f"{tag} ❌ Turnstile 票据最终未就绪，点确认也会被后端拒收，脚本退出")
            try:
                sb.save_screenshot(f"turnstile_fail_{idx}.png")
            except Exception:
                pass
            send_telegram_message(format_notification("❌ 续期失败", email=email, login_method=login_method, error="Turnstile 票据未就绪（后端会拒收）"))
            result["summary"] = "❌ 续期失败（Turnstile 未通过）"
            return result

        # 点击弹窗确认（录制 grounded：#renew-dialog 内按钮；文案不断言，只打印）
        print(f"{tag} ⏳ 等待续期按钮可用并点击...")
        time.sleep(3)
        modal_selector = "#renew-dialog button.inline-flex.w-full"
        try:
            sb.wait_for_element_visible(modal_selector, timeout=10)
            try:
                modal_text = sb.get_text(modal_selector)
            except Exception:
                modal_text = "（文案不可读）"
            print(f"{tag} 📝 弹窗确认按钮文案: {modal_text.strip()!r}")
            sb.click(modal_selector, timeout=8)
            print(f"{tag} ✅ 已点击续期按钮")
        except Exception as e:
            print(f"{tag} ❌ 弹窗确认按钮点击失败: {e}")
            try:
                sb.save_screenshot(f"modal_click_fail_{idx}.png")
            except Exception:
                pass
            send_telegram_message(format_notification("❌ 续期失败", email=email, login_method=login_method, error=f"弹窗确认按钮点击失败: {str(e)[:120]}"))
            result["summary"] = "❌ 续期失败（弹窗按钮点击失败）"
            return result

        # 轮询确认结果（60s）：等倒计时或日期变化，替代固定 sleep(6)
        print(f"{tag} ⏳ 等待新的过期时间（轮询60s）...")
        new_expiry = None
        new_countdown = None
        deadline = time.time() + 60
        while time.time() < deadline:
            sb.sleep(5)
            new_page_text = sb.get_page_source()
            new_match = re.search(r"Renew in (\d{2}:\d{2}:\d{2})", new_page_text)
            if new_match:
                new_countdown = new_match.group(1)
                new_expiry = extract_expiry_date(new_page_text)
                break
            cand = extract_expiry_date(new_page_text)
            if cand and cand != current_expiry:
                new_expiry = cand
                break

        # 提取新的到期日期和倒计时
        if new_countdown:
            print(f"{tag} ✅ 续期成功！新的倒计时: {new_countdown}")
            if new_expiry:
                print(f"{tag} 📅 新的到期日期: {new_expiry}")
            send_telegram_message(
                format_notification(
                    "✅ 续期成功",
                    email=email, login_method=login_method,
                    extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)}后",
                    expiry_date=new_expiry or "（未获取到）"
                )
            )
            result["ok"] = True
            result["summary"] = f"✅ 续期成功（到期 {new_expiry or '未知'}）"
        else:
            if new_expiry:
                print(f"{tag} ✅ 续期成功，到期日期已更新为: {new_expiry}")
                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        email=email, login_method=login_method,
                        extra="到期日期已更新",
                        expiry_date=new_expiry
                    )
                )
                result["ok"] = True
                result["summary"] = f"✅ 续期成功（到期 {new_expiry}）"
            else:
                print(f"{tag} ⚠️ 续期结果未知，到期日期未变化，请手动检查")
                try:
                    sb.save_screenshot(f"renew_unknown_{idx}.png")
                    body_txt = sb.get_text("body").strip().replace("\n", " ")[:300]
                except Exception:
                    body_txt = "（正文不可读）"
                print(f"{tag} 📄 页面正文片段: {body_txt!r}")
                send_telegram_message(
                    format_notification(
                        "⚠️ 续期可能未成功",
                        email=email, login_method=login_method,
                        extra="请登录后台检查",
                        error=f"页面提示: {body_txt[:120]}",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )
                result["summary"] = "⚠️ 结果未知（日期未变化）"

    else:
        if countdown_text:
            friendly = format_countdown(countdown_text)
            print(f"{tag} ⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
            send_telegram_message(
                format_notification(
                    "⏳ 未到续期时间",
                    email=email, login_method=login_method,
                    extra=f"⏱️ 可续期时间: {friendly}后",
                    expiry_date=current_expiry or "（未获取到）"
                )
            )
            result["ok"] = True
            result["summary"] = f"⏳ 未到时间（{friendly}后，到期 {current_expiry or '未知'}）"
        else:
            print(f"{tag} ℹ️ 未找到续期按钮或倒计时，状态未知")
            send_telegram_message(
                format_notification(
                    "ℹ️ 无需续期",
                    email=email, login_method=login_method,
                    extra="当前状态未知，请手动检查",
                    expiry_date=current_expiry or "（未获取到）"
                )
            )
            result["summary"] = "ℹ️ 状态未知"

    # 更新SESSION_TOKEN
    print(f"{tag} 🔄 检查 SESSION_TOKEN 是否需要更新")
    new_token, token_expiry = get_cookie_info(sb, "session_token")
    old_token = session_token

    if should_update_cookie(new_token, old_token, token_expiry):
        print(f"{tag} 🔄 SESSION_TOKEN 需要更新")
        if GH_TOKEN:
            if update_github_secret(secret_name, new_token):
                print(f"{tag} ✅ SESSION_TOKEN 更新成功")
            else:
                print(f"{tag} ⚠️ 更新失败，请检查 GH_TOKEN 权限")
        else:
            print(f"{tag} ⚠️ 未设置 GH_TOKEN，无法自动更新")
            print(f"{tag} 📋 请手动设置 {secret_name} = {new_token[:4]}...{new_token[-4:]}")
    else:
        print(f"{tag} ✅ SESSION_TOKEN 无需更新")

    return result


# 主流程（多账号循环，浏览器复用，单账号失败不影响其他账号）
def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期（多账号）")
    print("#" * 25)

    accounts = collect_accounts()
    if not accounts:
        print("ℹ️ 未配置 SESSION_TOKEN(_N) 和 DISCORD_TOKEN(_N)，脚本终止。")
        print("   单账号填 SESSION_TOKEN/DISCORD_TOKEN；多账号填 SESSION_TOKEN_1/DISCORD_TOKEN_1/_2…")
        sys.exit(1)

    print(f"📋 共发现 {len(accounts)} 个账号：")
    for a in accounts:
        has_sess = "有" if a["session_token"] else "无"
        has_dc = "有" if a["dc_token"] else "无"
        print(f"   - 账号{a['idx']} {mask_email(a['email'])}（SESSION:{has_sess} / Discord:{has_dc} / 回写:{a['secret_name']}）")

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": HEADLESS}

    if IS_PROXY:
        print(f"🔗 挂载代理: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("🍭 未使用代理，直连访问")

    results = {}
    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

        for acct in accounts:
            idx = acct["idx"]
            print("\n" + "=" * 40)
            print(f"▶️ 开始处理 账号{idx}（{mask_email(acct['email'])}）")
            print("=" * 40)
            try:
                results[idx] = renew_one_account(sb, acct)
            except Exception as e:
                print(f"[账号{idx}] ❌ 执行异常: {e}")
                import traceback
                traceback.print_exc()
                results[idx] = {"ok": False, "summary": f"❌ 执行异常：{e}"}
                try:
                    send_telegram_message(format_notification(
                        "❌ 续期失败", email=acct["email"] or f"账号{idx}",
                        login_method="SESSION_TOKEN", error=f"执行异常：{e}"))
                except Exception:
                    pass

    # 汇总通知
    print("\n🏁 全部账号执行完毕，汇总：")
    summary_lines = ["🇫🇮 Bot-hosting 多账号续期汇总", ""]
    for acct in accounts:
        idx = acct["idx"]
        r = results.get(idx, {"ok": False, "summary": "未执行"})
        mark = "✅" if r["ok"] else "❌"
        print(f"   {mark} 账号{idx} {mask_email(acct['email'])}：{r['summary']}")
        summary_lines.append(f"{mark} 账号{idx}({mask_email(acct['email'])})：{r['summary']}")
    try:
        send_telegram_message("\n".join(summary_lines))
    except Exception as e:
        print(f"⚠️ 汇总通知发送失败: {e}")

if __name__ == "__main__":
    main()
