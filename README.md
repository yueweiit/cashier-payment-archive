# 出纳请款明细内网网页系统

这是一个局域网多人使用的请款明细归档工具。后端使用 FastAPI + SQLite，前端使用 React + TypeScript + Vite，支持周报 Excel 导入、钉钉导出表字段映射导入、内置 Excel 式直接录入、多笔分次付款与凭证、从上周批次生成本周草稿、批次归档、审计日志、附件留档和按多 Sheet 模板导出 Excel。

## 默认账号

- 账号：`admin`
- 密码：`admin123`

生产使用前请设置环境变量 `PAYMENT_APP_ADMIN_PASSWORD` 后重新初始化数据库，或登录后在“管理”里新建管理员并停用默认账号。

## 本机启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npm run build
.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

打开 `http://127.0.0.1:8000`。同一局域网内其他电脑访问本机 IP 的 `8000` 端口。

## 开发启动

```bash
.venv/bin/uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
npm run dev
```

开发前端地址是 `http://127.0.0.1:5173`，API 会代理到 `8000`。

## 数据与文件

- SQLite 数据库默认在 `data/app.db`。
- 上传文件默认保存在 `data/uploads/`。
- 可用环境变量修改：
  - `PAYMENT_APP_DATA_DIR`
  - `PAYMENT_APP_DB`
  - `PAYMENT_ATTACHMENT_STORAGE_DIR`（附件独立存储根目录；线上使用 `/data/cashier-payment/storage`）
  - `PAYMENT_APP_ADMIN_PASSWORD`

`PAYMENT_APP_DATA_DIR` 继续保存 SQLite 和应用元数据；普通附件、付款凭证及钉钉附件通过
`PAYMENT_ATTACHMENT_STORAGE_DIR` 写入内容寻址目录。未配置时默认使用
`data/storage/`，历史 `data/uploads/` 文件仍可兼容读取。

## 钉钉支出中间表

- 将 `.env.example` 复制为 `.env`，填写 PostgreSQL 的 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER` 和 `DB_PASSWORD`；建议使用只读数据库账号。`DINGTALK_USER_DB_NAME` 指定 `ding_user_snapshot` 所在数据库，默认为 `dingtalk_oa`。
- 也可通过 `PAYMENT_SOURCE_ENV_FILE` 指定独立配置文件。真实 `.env` 和 `env` 文件已被 Git 忽略，不要将密码提交到仓库。
- “数据导入”中的“从中间表拉取”会先按日期、来源、钉钉单号和申请人预览，再把勾选项导入当前草稿批次。
- 申请人姓名优先从 `dingtalk_oa.public.ding_user_snapshot` 按用户 ID 获取，未命中时从审批标题解析；新导入记录按申请部门分 Sheet。
- “同步钉钉流程”会刷新申请人、部门、审批状态和流程评论；打开草稿批次时五分钟内只后台同步一次，普通筛选和切换 Sheet 不访问钉钉数据库。
- `DINGTALK_AUTO_PAYMENT_MODE=preview` 仅标记可信财务“已支付”候选；核对无误后改为 `apply` 才会按待付款金额生成付款明细。复杂、部分或矛盾评论只进入待核对。
- 来源固定只读取中国区的 `COMPLETED` 和 `RUNNING` 记录，排除已终止或明确拒绝项；钉钉单号在所有批次全局去重。

## 内置 Excel 式录入

- 在“工作台”中可以直接点击单元格录入。
- 支持 `Tab`、`Enter`、方向键移动单元格。
- 支持从 Excel 复制多行多列后粘贴到表格里；超出末尾会自动生成新行。
- 主表只读展示“应付金额、累计已付、待付款”和付款状态；点击“付款 N 笔”后在请款抽屉维护每笔付款。
- 每笔付款记录金额、日期、付款人、付款账户、银行流水号、备注及图片/PDF 凭证；累计已付、待付款、状态、最近付款日期和付款人由系统自动汇总。
- 财务、总经理和管理员可维护付款；业务人员只读。归档后只有总经理或管理员可凭更正原因调整，结转继承的付款明细只读。
- 修改过的单元格会高亮，点击“保存更改”后统一提交。
- 切换批次或刷新页面前，如果有未保存更改，浏览器会提示确认。
- 归档批次默认只读；管理员更正归档数据时必须填写原因。

## 周迭代

- 归档上周批次后，可在工作台左侧“从上周生成本周”中选择上周批次。
- 系统默认复制未完成项到新草稿批次，也可选择复制全部。
- 新批次会复制完整付款历史和凭证，并记录付款来源；继承明细只读，后续更正旧批次不会改写已经生成的新批次。

## 每日应付历史

- 顶部“每日应付”页面跨全部批次按业务请款统计，并通过稳定逻辑标识去除周结转副本，避免重复计算。
- 页面按 Asia/Shanghai 当天结束时展示当天新增到期、当天支付、日终待付和逾期待付；没有需求付款日期的请款不纳入。
- 历史从功能首次初始化当天开始记录，不推算更早日期；起始日期保存在 `app_settings.daily_payables_history_start_date`。
- `payable_history_versions` 是只追加的业务历史表。请款、付款、币种和钉钉流程状态变更会在原业务事务中同步追加版本，不应手工修改或删除。
- CNY、USD、MXN 原币金额分开显示，折合人民币仅作补充；业务人员的汇总和明细继续受 Sheet 权限限制。
- 查询每日应付只读取本地 SQLite 历史，不触发钉钉、附件或外部 PostgreSQL 查询。
- 线上迁移或备份 SQLite 时应先停服务，或使用 SQLite Backup API 生成一致性副本；启用 WAL 后不要在服务运行时只复制 `app.db`。

## Excel 付款明细

- 导出文件固定包含“付款明细”Sheet，列出请款标识、钉钉单号、来源 Sheet、付款日期、本次金额、付款人、付款账户、流水号、备注、来源标记和凭证信息。
- 导入优先按当前批次内的请款标识匹配，缺失时使用“钉钉单号 + 来源 Sheet”；重复明细跳过，无法匹配或超额的行会出现在导入结果错误列表。
- 有“付款明细”Sheet 时以明细合计为准；没有该 Sheet 时，兼容把主表累计已付金额转换为一笔 Excel 汇总付款。
- Sheet 内嵌图片会绑定到对应付款；PDF 凭证通过网页上传，单个凭证上限 12MB。

## 已验证

- 使用 `/Users/smk/Downloads/20260626~20260707请款明细.xlsx` 做导入回归。
- 主汇总 Sheet 导入 86 条业务记录。
- 总导入 161 条业务记录；页脚银行账号、合计行等非请款行会被过滤。
- 导出接口会生成可被 Excel 打开的 `.xlsx`，并按来源 Sheet 分组保留中文表头和合计公式。
- 已验证三笔分次付款汇总、超额拦截、权限与归档更正、历史迁移、跨周只读结转、草稿快照、付款凭证、Excel 明细导入导出与撤回，以及前端生产构建。
