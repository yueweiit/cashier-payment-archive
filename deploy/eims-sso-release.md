# 出纳请款系统接入 EIMS SSO

本系统使用 EIMS OAuth2 授权码 + PKCE S256 登录，并在 UserInfo 返回 `app_user_id` 后签发自己的 12 小时本地会话。本地角色和数据权限不从 EIMS 下发。Access Token、Refresh Token 和 ID Token 均不落库；本系统不调用刷新端点。

## 向 EIMS 提交的资料

| 项目 | 生产值 |
| --- | --- |
| 系统编码 | `cashier_payment` |
| 系统名称 | 出纳请款明细 |
| 入口地址 | `https://payment.yueweiportal.com/` |
| SSO 启动地址 | `https://payment.yueweiportal.com/api/auth/eims/start` |
| OAuth 回调地址 | `https://payment.yueweiportal.com/api/auth/eims/callback` |
| 退出回调地址 | `https://payment.yueweiportal.com/api/auth/eims/logout/callback` |
| Scope | `openid profile` |
| `app_user_id` | 本系统 `users.id` 的十进制字符串，例如 `"101"` |
| `app_username` | 本系统 `users.username`，仅辅助展示 |
| 本地用户状态 | `active=1` 且 `deleted_at IS NULL` 才允许登录 |

测试环境须使用自己的域名、Issuer、客户端和回调地址，不得复用生产 Secret。EIMS 需要注册**授权回调和退出回调两个精确地址**，并给目标用户建立账号绑定。绑定值可在本系统“管理 → 用户 → 绑定 ID”复制；不要使用用户名、邮箱或 EIMS `sub` 代替。

## 服务端配置

系统服务模板从 `/etc/cashier-payment-archive/sso.env` 读取环境变量。该文件应只对系统管理员可读，不提交到 Git。示例内容：

```ini
PAYMENT_AUTH_MODE=hybrid
PAYMENT_PUBLIC_BASE_URL=https://payment.yueweiportal.com
EIMS_ISSUER=https://实际的-EIMS-域名
EIMS_CLIENT_ID=实际客户端ID
EIMS_CLIENT_SECRET=通过安全渠道取得的密钥
```

`PAYMENT_AUTH_MODE` 可取 `local`（默认）、`hybrid`、`eims`。建议先用 `hybrid` 联调，全部账号绑定完成后改为 `eims`。`eims` 模式下，密码登录、密码修改和重置接口被拒绝，旧本地会话也被拒绝；新增本地用户时服务端生成不可见随机密码。切换回 `local` 时，SSO 会话会被拒绝。每次启动新的 SSO 登录都会清理当前浏览器的旧本地会话，登录失败不会恢复旧账号。

每次 API 请求都会检查本地账号是否启用；EIMS 角色或账号状态变更不会主动删除已签发的本地会话，最长到 12 小时后失效。若业务要求即时撤销，需与 EIMS 另行设计会话撤销通知或在线状态校验。

生产 Issuer 和公开地址必须使用 HTTPS。仅本地 HTTP 联调可设置 `PAYMENT_SSO_ALLOW_HTTP=1`。配置文件不会由 Python 的 `.env` 读取逻辑自动加载，须由 systemd 或其他进程管理器注入。`client_secret` 不得放入前端构建变量。

部署新版 Nginx 模板时，OAuth 回调路径关闭普通访问日志，以免一次性授权码写入查询字符串日志。systemd 服务模板同时关闭 Uvicorn 访问日志；普通请求仍可在 Nginx 日志中查看。发布前执行 `nginx -t` 并检查回调路径确实经 HTTPS 代理到本服务。

## 发布顺序

1. 用 SQLite Backup API 为生产库生成一致性备份，并保留当前代码包、服务配置和 Nginx 配置。启动新版本会添加会话字段和 `sso_transactions` 表；原业务表数据不变。
2. 在测试环境注册 EIMS 客户端及外部系统目录。为至少一名普通用户和一名管理员建立绑定，分别检查本地角色、Sheet 范围及管理 API。
3. 生产部署代码和 Nginx，配置 `PAYMENT_AUTH_MODE=hybrid`，重启服务。访问 `/api/auth/config` 确认模式，再从 EIMS 门户启动登录。
4. 验证未绑定、错误绑定、停用本地用户、篡改或重放 `state`、错误回调 URI、退出回调及会话过期。确认回调日志中没有 `code`、Token 或 Secret。
5. 核对所有需要使用的本地账号均已绑定，轮换或停用默认管理员密码，再改为 `PAYMENT_AUTH_MODE=eims` 并重启。确认旧密码登录和旧本地会话被拒绝。

## EIMS 侧权限验收

EIMS 的 OAuth2 服务需要部署包含门户角色复核的版本：对关联了外部系统目录的客户端，授权确认、换码、刷新令牌和 UserInfo 都检查系统状态、`allowedRoles` 与账号绑定；未关联门户目录的通用 OAuth 客户端维持原策略。联调必须实测：**已绑定、但不再拥有该系统允许角色的用户，直接访问本系统 SSO 启动地址不能完成授权**。仅在门户 `launch()` 检查角色的旧版 EIMS 不能满足此要求。本系统仍会拒绝无绑定或停用的本地账号，但不能根据 UserInfo 独立判断 EIMS 角色。

## 回滚

如果 SSO 登录不可用，先将 `PAYMENT_AUTH_MODE` 改回 `local` 并重启服务，使用已轮换的本地管理员密码登录。新增的数据库字段和事务表可保留，无需还原业务数据库；本地模式不会接受之前的 SSO 会话。若代码本身异常，再恢复前一版代码包与服务配置。不要把测试客户端或测试 Secret 临时填入生产配置。
