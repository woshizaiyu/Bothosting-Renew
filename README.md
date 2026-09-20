## 🚀 Bot-hosting 自动续期（GitHub Actions，多账号版）

这是一个基于 GitHub Actions 的自动化脚本，用于定时登录自动续期 [Bot-hosting](https://bot-hosting.net) 服务。

支持多账号循环续期（单账号失败不影响其他账号），跑完发逐账号通知 + 一条汇总通知。

⚠️ 有cf盾,太垃圾的机房节点可能过不了，建议用稍微干净点的节点,[B2proxy住宅代理](https://www.b2proxy.com/signup?code=0F5133)

━━━━━━━━━━━━━━━━━━━━━━

### 🔐 Secrets 配置说明

| Secret 名称         | 是否必填 | 说明                                              |
|---------------------|----------|---------------------------------------------------|
| EMAIL              | ❌ 可选  | 用于通知使用的Email,可随意填写                          |
| SESSION_TOKEN      | ❌ 可选  | Bot-hosting session_token，cookie里获取               |
| DISCORD_TOKEN      | ✅ 必填  | Discord Token，SESSION_TOKEN失效时自动OAuth登录        |
| GH_TOKEN           | ❌ 可选  | GitHub(classic) token,用于自动更新session_token,以ghp_xxx开头|
| NODE_LINK          | ❌ 可选  | 代理链接（如 vless:// vmess:// trojan:// hysteria2:// tuic:// anytls:// socks5:// )|
| TG_BOT_TOKEN       | ❌ 可选  | Telegram Bot Token（用于发送通知）                      |
| TG_CHAT_ID         | ❌ 可选  | Telegram Chat ID（接收通知的用户或群组 ID）               |

### 🔐 多账号配置（编号后缀式，最多支持 10 个）

| Secret 名称            | 说明                                              |
|------------------------|---------------------------------------------------|
| EMAIL_1 / EMAIL_2 / …  | 第 N 个账号的通知用 Email，可随意填写               |
| SESSION_TOKEN_1 / _2…  | 第 N 个账号的 session_token，cookie里获取           |
| DISCORD_TOKEN_1 / _2…  | 第 N 个账号的 Discord Token，SESSION_TOKEN失效时自动OAuth登录 |

> * 每个账号至少填 `SESSION_TOKEN_N` 或 `DISCORD_TOKEN_N` 其中之一，否则该编号会被跳过。
> * `GH_TOKEN / TG_BOT_TOKEN / TG_CHAT_ID / NODE_LINK` 全局共用，只填一次。
> * 账号 N 续期后刷新了 cookie，会自动回写到 `SESSION_TOKEN_N`（需配置 `GH_TOKEN`）。
> * 旧单账号写法（`EMAIL / SESSION_TOKEN / DISCORD_TOKEN` 不带编号）仍然兼容：只配旧变量时按单账号跑；同时配了编号和旧变量时，旧变量会被当作账号 1 纳入（仅当 `_1` 未配置时）。

━━━━━━━━━━━━━━━━━━━━━━

## 部署步骤
1：fork 本项目，在actions菜单允许工作流

2：在`setting`➡`secrets and variables`➡`Actions` 里添加上方必填的secrets

3：去actions菜单手动试运行工作流,根据自己的服务到期日期自行在[renew.yml](.github/workflows/renew.yml)里调整cron运行时间

### SESSION_TOKEN 获取
登录你的账号,按F12或页面空白处 右键➡检查➡选择应用程序或appcations 找到对应的字段点击获取对应的值，详情如图
<img width="1200" height="600" alt="image" src="https://github.com/user-attachments/assets/e532b0d6-9f12-45fd-8af9-69e1029a1a92" />

### DISCORD_TOKEN 获取（用于 SESSION_TOKEN 失效后备用登录
1. 浏览器登录 Discord（网页版）
2. 按 F12 打开开发者工具 ➡ 网络 ➡ 点击任意频道 ➡ 选择左侧的任意api 
3. 找到名为 `authorization字段` 的 值，即为discord token,详情如图所示
<img width="1200" height="600" alt="image" src="https://github.com/user-attachments/assets/7276d62d-31ff-452c-9e13-165af8323f53" />


> **作用**：当 `SESSION_TOKEN` 过期导致登录失败时，脚本会自动使用 Discord Token 走 OAuth 流程重新登录，并自动更新 `SESSION_TOKEN` Secret，实现永久免维护。


### 获取 `GH_TOKEN`(GitHub Personal Access Token)
1：点击GitHub 账户右上角头像 → Settings（设置）。

2：左侧菜单底部点击 Developer settings（开发者设置）。

3：点击 Personal access tokens → Tokens (classic)。

4：点击 Generate new token → Generate new token (classic)。

填写信息：
- Note：起一个描述性名称（如 my-token）。
- Expiration：选择过期时间（建议选No expiration永不过期）。
- Select scopes：勾选所需权限（不知道如何勾选就全部勾选）。
- 点击 Generate token，立即复制并妥保存生成的 token（离开页面后不能再查看）。

## 注意事项
* 必填变量必须要填写
* NODE_LINK支持的代理协议有：vmess,vless,hysteria2,tuic,anytls,socks5等
* 本版本为多账号版，账号间失败隔离；加第 4 个及以后账号时，Secrets 里加 `_N` 三件套，并在 `renew.yml` 里照格式加三行 env 透传即可（`app.py` 免改）
* cron运行时间不一定准确,得根据实际到期时间修改,可在设置里暂停actions功能再开启

## ⚠️ 免责声明
* 本程序仅供学习了解, 非盈利目的，如转载须注明来源。
* 使用本程序必循遵守部署服务器所在地、所在国家和用户所在国家的法律法规, 程序作者不对使用者任何不当行为负责。
