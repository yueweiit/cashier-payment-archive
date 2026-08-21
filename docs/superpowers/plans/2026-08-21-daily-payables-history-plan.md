# 每日应付与历史日终状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing each task and superpowers:verification-before-completion before claiming completion.

**Goal:** 新增跨全部批次、按历史日终状态查询的“每日应付”页面，正确展示每日到期、当日付款、日终待付及简化明细，并避免周结转重复统计。

**Architecture:** 为请款增加稳定 `logical_request_id`，通过 `copied_from_request_id` 一次性补齐现有链路；新增只追加的 `payable_history_versions` 保存影响应付口径的完整状态版本。所有业务写操作在原事务内追加历史，查询按指定日期选择每个逻辑请款的最后版本。前端新增独立导航页，消费专用 summary/trend/details 接口，不扫描附件或访问外部钉钉库。

**Tech Stack:** Python 3、FastAPI、SQLite、pytest、React 19、TypeScript、Vite、现有 i18n 和样式体系。

---

### Task 1: 建立逻辑请款标识和历史表

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/tests/test_api_workflows.py`

**Step 1: 编写失败测试**

新增数据库测试验证：

- `payment_requests` 存在 `logical_request_id` 和索引；
- `payable_history_versions` 及查询索引存在；
- 原始请款的逻辑标识等于自身 ID；
- 通过 `copied_from_request_id` 形成的历史链统一指向最早根请款；
- 初始化时写入部署日基线且每个逻辑请款仅一条；
- 系统设置保存 `daily_payables_history_start_date`。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'daily_payable_schema or logical_request'`

Expected: 字段和历史表不存在，测试失败。

**Step 3: 实现非破坏性数据库迁移**

在 `backend/app/db.py`：

- 增加 `payment_requests.logical_request_id`；
- 创建 `payable_history_versions` 和 `app_settings`；
- 从 `copied_from_request_id` 链回溯并补齐逻辑标识；
- 为现有逻辑请款写部署日基线；
- 创建 `(logical_request_id, effective_at, id)`、`(effective_at, currency)`、`needed_payment_date` 索引；
- 增加历史起始日期读取函数。

**Step 4: 运行测试**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'daily_payable_schema or logical_request'`

Expected: PASS。

**Step 5: 提交**

Commit: `feat: add logical request history schema`

### Task 2: 建立同事务历史写入服务

**Files:**
- Create: `backend/app/payable_history.py`
- Create: `backend/tests/test_payable_history.py`
- Modify: `backend/app/db.py`

**Step 1: 编写失败测试**

覆盖：

- `record_request_state()` 写入完整状态；
- 相同事件键重复执行不会重复写版本；
- 跨批次副本只更新同一个逻辑请款；
- 付款日当天减少日终待付；
- 后续付款不修改更早的已记录历史版本；
- 终止、拒绝、恢复状态分别改变是否纳入统计；
- 事务回滚时历史版本一起回滚。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_payable_history.py -q`

Expected: 模块不存在，测试失败。

**Step 3: 实现历史写入服务**

提供：

```python
def ensure_logical_request_id(conn, request_id: int) -> int: ...
def record_request_state(conn, request_id: int, *, event_type: str, event_key: str, effective_at: str | None, actor_id: int | None) -> None: ...
def seed_history_baseline(conn, *, start_date: str) -> int: ...
```

规则：

- 使用当前请款和付款汇总生成完整状态；
- 通过唯一 `event_key` 幂等；
- 付款新增的 `effective_at` 使用付款日期当天结束；
- 普通修改使用微秒操作时间；
- 保存来源 Sheet、币种和人民币基准金额；
- 保存当时的终止/拒绝判定。

**Step 4: 运行测试**

Run: `.venv/bin/python -m pytest backend/tests/test_payable_history.py -q`

Expected: PASS。

**Step 5: 提交**

Commit: `feat: record immutable payable state versions`

### Task 3: 接入请款、付款、结转和流程同步写路径

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/excel_io.py`
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/tests/test_payable_history.py`

**Step 1: 编写 API 失败测试**

验证以下操作均产生历史版本并与业务数据同事务：

- 手工新增、编辑和删除请款；
- 批量保存；
- 付款新增、修改和删除；
- 钉钉自动付款和流程终止/恢复；
- Excel 新增/合并导入；
- 币种换算/更正；
- 周结转继承 `logical_request_id` 且不产生重复逻辑对象；
- 接口失败或版本冲突不写孤立历史版本。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_payable_history.py -q -k 'payable_history or logical_request or rollover'`

Expected: 新历史断言失败。

**Step 3: 在所有业务写事务中追加历史**

- 新增请求插入后设置逻辑标识并记录 `request.created`；
- 周结转直接继承来源逻辑标识；
- 更新、批量保存、付款、币种和同步操作在提交前记录最终状态；
- 删除请求前写入不纳入统计的墓碑版本；
- 导入使用操作 ID 和记录 ID构造稳定事件键；
- 保留现有版本检查和批次长任务短事务规则。

**Step 4: 运行测试**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_payable_history.py -q -k 'payable_history or logical_request or rollover'`

Expected: PASS。

**Step 5: 提交**

Commit: `feat: capture payable history on business mutations`

### Task 4: 实现每日应付查询服务和接口

**Files:**
- Modify: `backend/app/payable_history.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_payable_history.py`
- Modify: `backend/tests/test_api_workflows.py`

**Step 1: 编写失败测试**

创建跨批次、跨币种、带付款和流程状态变化的数据，验证：

- 20,000 当天付 10,000，日终待付 10,000；
- 逾期未付进入后续日期；
- 后续付款不改变更早日期；
- 无需求付款日期不统计；
- 当时终止/拒绝不统计，恢复后重新出现；
- 周结转副本不重复；
- CNY/USD/MXN 分别汇总；
- 业务人员只看到授权 Sheet；
- 早于历史起始日期返回明确 422；
- 趋势超过 93 天返回明确 422。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_payable_history.py backend/tests/test_api_workflows.py -q -k 'daily_payables'`

Expected: 接口 404 或断言失败。

**Step 3: 实现查询函数和 API**

实现：

- `daily_summary()`；
- `daily_details()`；
- `daily_trend()`；
- `GET /api/daily-payables/summary`；
- `GET /api/daily-payables/details`；
- `GET /api/daily-payables/trend`。

每个接口返回 `history_start_date`、原币种汇总及折合人民币补充值，并应用 Sheet 权限。

**Step 4: 运行测试**

Run: `.venv/bin/python -m pytest backend/tests/test_payable_history.py backend/tests/test_api_workflows.py -q -k 'daily_payables'`

Expected: PASS。

**Step 5: 提交**

Commit: `feat: add daily payable reporting api`

### Task 5: 新增每日应付前端页面

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/i18n.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Step 1: 增加前端类型和 API 调用**

在 `frontend/src/api.ts` 增加 summary、trend、details 类型和请求函数，保留结构化错误码。

**Step 2: 建立独立导航页**

在 `App.tsx`：

- 顶部导航增加“每日应付 / Pagos diarios pendientes”；
- 默认选择当天，趋势默认最近 14 天；
- 展示 CNY/USD/MXN 三币种概览；
- 使用轻量 SVG/CSS 趋势图，点击日期更新详情；
- 详情表不展示附件、银行字段和行内编辑；
- 展示历史起始日期提示。

**Step 3: 补充响应式和双语样式**

- 桌面显示趋势和表格；
- 手机使用币种卡片和明细卡片；
- 不出现整体横向溢出；
- 全部系统文案接入现有语言切换。

**Step 4: 运行前端验证**

Run: `npm run build`

Expected: TypeScript 和 Vite 生产构建通过。

**Step 5: 提交**

Commit: `feat: add daily payable dashboard`

### Task 6: 完整回归与本地验收

**Files:**
- Modify: `README.md`
- Modify: `backend/tests/test_payable_history.py`

**Step 1: 更新运维和口径文档**

记录：

- 历史从部署日开始；
- SQLite Backup API 的上线备份要求；
- 不能手工修改历史表；
- 页面查询不触发外部同步；
- 时区和币种口径。

**Step 2: 运行完整后端测试**

Run: `.venv/bin/python -m pytest backend/tests -q`

Expected: 全部通过，无 warning 引发的隐藏错误。

**Step 3: 运行生产构建**

Run: `npm run build`

Expected: PASS。

**Step 4: 本地真实流程验收**

在本地创建一条 20,000 请款，录入当天 10,000 付款，验证当天日终 10,000；再录入次日付款，确认前一天不变。创建周结转副本，确认汇总仍只统计一次。

**Step 5: 检查工作区和提交**

Run: `git status --short && git log -5 --oneline`

Expected: 只有用户原有的 `output/` 未跟踪目录，不包含临时数据库、构建产物或敏感配置。

Commit: `docs: document daily payable history operations`

### Task 7: 线上发布（需本地验收后单独执行）

**Files:**
- No code changes

**Step 1: 停止 8011 服务并一致性备份 SQLite**

使用现有 `backup_database()` 生成备份并执行 `PRAGMA integrity_check`，记录 SHA-256。

**Step 2: 部署代码和启动迁移**

部署固定提交，启动单实例服务，由 `init_db()` 非破坏性增加字段、历史表和部署日基线。

**Step 3: 线上冒烟验证**

验证工作台、每日应付、付款新增、周结转和 Sheet 权限；确认历史起始日为部署日且旧日期不可选。

**Step 4: 观察指标**

检查接口耗时、SQLite 锁冲突、历史版本增长和服务日志；出现异常立即回滚代码和一致性备份。
