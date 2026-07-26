# Stripe 沙箱配置与联调

本项目只接受 Stripe 测试环境凭据：`sk_test_...` / `rk_test_...`。如果误填
`sk_live_...`，API 网关会拒绝创建 Checkout、Portal 或处理生产 Webhook。
前端使用 Stripe 托管的 Checkout，因此不需要 `pk_test_...` 发布密钥。

## 1. 创建并进入 Stripe Sandbox

1. 登录 [Stripe Dashboard](https://dashboard.stripe.com/)。
2. 用右上角账户/环境选择器创建或选择一个 **Sandbox**。
3. 后续所有产品、价格、Customer Portal 和 Webhook 操作都要在这个 Sandbox
   内完成；Sandbox 和正式环境的数据、密钥及 Portal 配置互相独立。

## 2. 创建两个按月价格

在 **Product catalog / 产品目录** 中分别创建：

| 产品名 | 价格 | 计费方式 | 环境变量 |
|---|---:|---|---|
| AI Writing Basic | USD 9.00 | Recurring，Monthly | `STRIPE_PRICE_BASIC` |
| AI Writing Pro | USD 29.00 | Recurring，Monthly | `STRIPE_PRICE_PRO` |

每个产品保存后，打开对应价格并复制以 `price_` 开头的 **Price ID**。不要复制
`prod_` 开头的 Product ID。金额或币种如需调整，请同时修改前端
`src/routes/subscription.tsx` 中展示的文案。

建议把 Basic 和 Pro 建成两个独立产品，Customer Portal 的套餐切换配置更直观。

## 3. 配置 Customer Portal

在 Sandbox 的 **Settings → Billing → Customer portal** 中：

1. 开启 Payment methods / 更新支付方式。
2. 开启 Cancel subscriptions / 取消订阅，建议选择“在当前账期结束时取消”。
3. 开启 Switch plans / 切换套餐。
4. 在可切换的产品目录中加入刚创建的 Basic 和 Pro 月付价格。
5. 保存 Sandbox 的 Portal 配置。

如果没有开启 Portal，已有订阅用户点击 **Manage billing** 时 Stripe 会返回配置错误。

Dashboard 中的 Default redirect link / 默认返回地址是可选项。本项目每次创建
Checkout 和 Customer Portal Session 时，都会由后端通过 `FRONTEND_URL` 动态传入
成功、取消和返回地址，所以找不到该设置也不影响集成。

## 4. 获取测试 Secret Key

在 Sandbox 的 **Developers / Workbench → API keys** 中显示并复制
`sk_test_...` Secret key。此密钥只能放在后端环境变量中，不要写进前端、提交到
Git，或发到聊天/工单中。

编辑 `infrastructure/.env`：

```env
STRIPE_SECRET_KEY=sk_test_替换为你的测试密钥
STRIPE_PRICE_BASIC=price_替换为Basic价格ID
STRIPE_PRICE_PRO=price_替换为Pro价格ID
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173
```

`STRIPE_WEBHOOK_SECRET` 在下一步获取。

如果前端部署在 Vercel，生产后端应使用：

```env
FRONTEND_URL=https://你的正式前端域名.vercel.app
CORS_ORIGINS=http://localhost:5173,https://你的正式前端域名.vercel.app
```

`FRONTEND_URL` 使用一个固定的正式入口；`CORS_ORIGINS` 可以用英文逗号同时配置
本地和生产前端，逗号后有无空格均可。

## 5. 配置本地 Webhook

安装 [Stripe CLI](https://docs.stripe.com/stripe-cli/install)，然后执行：

```powershell
stripe login

stripe listen `
  --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted `
  --forward-to http://localhost:8000/api/v1/billing/webhook
```

CLI 会打印：

```text
Ready! Your webhook signing secret is 'whsec_...'
```

把该 `whsec_...` 写入 `infrastructure/.env`：

```env
STRIPE_WEBHOOK_SECRET=whsec_替换为CLI输出
```

然后重建 API 网关：

```powershell
cd infrastructure
docker compose up -d --build api_gateway
```

联调期间要保持 `stripe listen` 窗口运行。CLI 每次生成的本地签名密钥可能不同；
发生变化时要更新 `.env` 并重启 API 网关。

部署到有 HTTPS 公网域名的测试服务器后，可以在 Sandbox 的
**Workbench → Webhooks / Event destinations** 新增：

```text
https://你的测试API域名/api/v1/billing/webhook
```

选择以下四类事件并复制该端点自己的 `whsec_...`：

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Dashboard 端点的签名密钥和 Stripe CLI 的本地签名密钥不是同一个值。

## 6. 更新已有数据库

全新 PostgreSQL 数据卷会通过 `infrastructure/init.sql` 自动创建完整字段。

如果数据库以前启动过，`docker-entrypoint-initdb.d` 不会再次执行，需要手动应用
一次迁移：

```powershell
cd infrastructure
Get-Content -Raw .\migrations\001_stripe_billing.sql |
  docker compose exec -T postgres psql -U platform -d platform
```

该迁移使用 `IF NOT EXISTS`，重复执行不会重复增加字段或索引。

## 7. 启动并完成一次支付

1. 后端启动后，前端仓库运行 `npm run dev`，访问
   `http://localhost:5173`。
2. 注册/登录普通用户，进入 **Plan / Subscription**。
3. 点击 Basic 或 Pro 的 **Upgrade**，浏览器会跳到 Stripe Checkout。
4. 成功测试卡：
   - 卡号：`4242 4242 4242 4242`
   - 有效期：任意未来日期，例如 `12/34`
   - CVC：任意三位数字
   - 其余字段：任意测试值
5. 支付后返回订阅页，应显示 `Payment confirmed` 和新套餐。
6. 点击 **Manage billing**，验证切换套餐、更新卡片和取消订阅。

可再用以下卡号检查失败场景：

| 场景 | 测试卡号 |
|---|---|
| 通用拒付 | `4000 0000 0000 0002` |
| 余额不足 | `4000 0000 0000 9995` |

## 8. 排查清单

- Checkout 返回 `Stripe sandbox secret key is not configured`：检查是否填写
  `sk_test_...`，然后重启 API 网关。
- Checkout 返回 Price 配置错误：确认填的是两个不同的 `price_...`，且都属于当前
  Sandbox。
- 支付成功但套餐仍是 Free：检查 `stripe listen` 是否运行、转发路径是否正确，
  以及 API 网关日志中的 Webhook 签名错误。
- Manage billing 报错：确认 Customer Portal 已在同一个 Sandbox 中启用并保存。
- 套餐切换后没有更新：确认 Portal 产品目录包含两个 Price，并订阅更新事件已加入
  Webhook。
