# 墨西哥钉钉审批跟进与中国应付隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立的墨西哥钉钉审批跟进页，直接发现并缓存运营支出、采购支出和月结付款流程；默认追踪仍在审批的流程，同时让中国工作台、每日应付、付款统计和默认导出只保留中国数据。

**Architecture:** FastAPI 继续作为唯一应用服务，SQLite 保存墨西哥流程当前状态、事件、附件元数据、同步任务和地区人工结论，PostgreSQL 钉钉中间库保持只读。同步采用“外部查询与附件下载不持有 SQLite 写事务、状态先落库、附件后补齐”的两阶段模型；前端新增独立 `MexicoTrackingPage`，列表、统计、筛选和详情均读取本地缓存。地区判定以钉钉执行地区优先、精确 Sheet 映射兜底、管理员结论持久优先，无法确定或冲突的数据进入待核对且不进入任一默认业务口径。

**Tech Stack:** Python 3、FastAPI、SQLite WAL、psycopg/PostgreSQL 只读网关、pytest、React 19、TypeScript、Vite、现有 i18n、文件存储和认证权限体系。

---

### Task 1: 建立地区判定、节点停留和催办文案纯规则

**Files:**
- Create: `backend/app/mexico_tracking.py`
- Create: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写失败测试覆盖地区判定**

  在 `backend/tests/test_mexico_tracking.py` 固定以下精确映射：

  ```python
  CHINA_SHEETS = {
      "悦为智能 YW Tech_Ai",
      "拉丁购",
      "凌翔产品&开发",
      "凌翔供应链及采购执行单元",
      "星铭HR人力资源中心",
      "星铭FC财务中心",
      "凌翔/星铭供应链及职能中心",
  }

  MEXICO_SHEETS = {
      "YW MOLDES MX模具",
      "YUEWEI MX核心制造",
      "LEMOS MX供应链开发及管理",
      "LEMOS MX 销售",
      "UV IMPRESION MX彩印",
      "FC 财务中心 Centro Financiero (FC)",
  }
  ```

  验证：明确执行地区优先；缺执行地区时使用精确 Sheet；`星铭FC财务中心` 与 `FC 财务中心 Centro Financiero (FC)` 不混淆；币种不单独决定地区；地区与 Sheet 冲突返回 `review`；未知 Sheet 返回 `review`；已保存的管理员结论继续生效且不会被下一次同步静默覆盖。

- [ ] **Step 2: 编写节点停留和文案失败测试**

  验证 Asia/Shanghai 自然日口径、当天为 0 天、超过 2 天才黄色、超过 5 天才红色；阈值修改后无需改写记录即可重算。验证中文和西班牙语催办文本都包含钉钉单号、申请人、当前节点、当前审批人、停留天数和流程链接。

- [ ] **Step 3: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q`

  Expected: `backend.app.mexico_tracking` 不存在，测试失败。

- [ ] **Step 4: 实现最小纯函数**

  在 `backend/app/mexico_tracking.py` 提供 `resolve_region()`、`node_age_days()`、`warning_level()` 和 `build_bilingual_reminder()`。`node_age_days()` 将时间转换到 `ZoneInfo("Asia/Shanghai")` 后比较日期；`warning_level()` 按红色、黄色、正常的优先级返回；催办函数以固定字段模板生成中文段落和西班牙语段落。

  `RegionDecision.region` 仅允许 `china | mexico | review`，同时保存 `source`、原始执行地区和冲突原因。管理员结论在本系统内最高优先，但必须继续保存新的原始事实，供管理员看到后再次调整。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q`

  Expected: PASS。

  Commit: `feat: add mexico tracking domain rules`

### Task 2: 建立墨西哥缓存、事件、附件、关联和同步任务表

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写数据库迁移失败测试**

  验证初始化和重复初始化后存在：

  - `mexico_approval_tracking`，`approval_no` 唯一，带 `version`；
  - `mexico_approval_events`，`(approval_no, event_key)` 唯一；
  - `mexico_approval_request_links`，`(approval_no, request_id)` 唯一；
  - `mexico_approval_attachments`，`(approval_no, source_file_id)` 唯一并包含 `pending/downloading/ready/failed` 状态；
  - `mexico_sync_runs`，包含租约、游标、阶段、进度、耗时和错误摘要；
  - `app_settings` 中黄色阈值 2、红色阈值 5、缓存过期 300 秒，以及 `china_region_isolation_enabled=false` 的安全灰度默认值。

  同时验证索引覆盖 `resolved_region + workflow_status`、`source_sheet`、`applicant_id`、`current_approver_id`、`current_node_name`、`request_date`、`last_synced_at` 和事件时间。

- [ ] **Step 2: 运行迁移测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'schema or migration or index'`

  Expected: 新表和索引不存在，测试失败。

- [ ] **Step 3: 实现非破坏性迁移**

  在 `backend/app/db.py::init_db()` / `migrate_schema()` 创建表和索引，不删除或重建现有业务表。给 `payment_requests` 增加：

  ```sql
  resolved_region TEXT NOT NULL DEFAULT 'review',
  region_resolution_source TEXT NOT NULL DEFAULT 'unknown',
  region_review_status TEXT NOT NULL DEFAULT 'pending',
  region_reviewed_by INTEGER,
  region_reviewed_at TEXT
  ```

  给 `payable_history_versions` 增加同一时点的 `resolved_region` 和 `region_review_status`，使历史日终查询不依赖当前请款值。

- [ ] **Step 4: 实现幂等的配置和迁移辅助函数**

  提供设置读取/更新函数；阈值必须满足 `0 <= yellow_days < red_days <= 365`。迁移只补空值，不覆盖管理员已经确认的地区。`china_region_isolation_enabled` 只允许布尔值，发布前保持关闭，首次全量同步和地区核对完成后再由发布步骤开启。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py backend/tests/test_payable_history.py -q -k 'schema or migration or region'`

  Expected: PASS。

  Commit: `feat: add mexico tracking cache schema`

### Task 3: 为现有和未来请款持久化统一地区结论

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/excel_io.py`
- Modify: `backend/app/payable_history.py`
- Modify: `backend/tests/test_mexico_tracking.py`
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/tests/test_payable_history.py`

- [ ] **Step 1: 编写历史回填和写路径失败测试**

  验证既有记录按 `raw_extra.external_source.execution_region` 优先、精确 Sheet 映射兜底；导入、手工新增、Excel、新周结转、Sheet 移动和钉钉元数据更新后都重新得到正确地区。明确冲突进入 `review`，管理员确认的覆盖值不被普通保存覆盖。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py backend/tests/test_payable_history.py -q -k 'region_backfill or region_persist or region_history'`

  Expected: 地区字段未被填充或写路径未更新，测试失败。

- [ ] **Step 3: 实现统一地区写入入口**

  在 `mexico_tracking.py` 增加 `persist_request_region(conn, request_id, actor_id, preserve_admin_override=True)` 和 `backfill_request_regions(conn)`。前者返回完整 `RegionDecision`，后者返回 `china/mexico/review/preserved_override` 四项计数。

  所有写路径只调用此入口，禁止各处自行根据 `MX`、币种或摘要猜测。对确实没有 Sheet/执行地区的数据保留 `review`。

- [ ] **Step 4: 让历史版本记录当时地区**

  `record_request_state()` 将当时的 `resolved_region` 和 `region_review_status` 写入 `payable_history_versions`；旧基线补齐时使用请求当前已解析地区，并记录迁移审计摘要。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py backend/tests/test_payable_history.py -q -k 'region'`

  Expected: PASS。

  Commit: `feat: persist request region classification`

### Task 4: 实现三类来源的全量发现和增量候选计划

**Files:**
- Modify: `backend/app/external_expenses.py`
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写模拟 PostgreSQL 网关失败测试**

  使用假网关覆盖运营 `approval_expense_operation`、采购 `approval_expense_purchase` 和固定月结流程。验证：

  - 首次同步不设历史截点，发现三类全部记录；
  - 后续同步读取每类来源游标之后的变化，同时无条件复查本地所有 `RUNNING` 流程；
  - `businessId` / 申请单号去首尾空格后作为全局键；
  - 同一申请单号在重复来源出现时不任意选取，返回来源冲突；
  - 保存申请人、二级部门/子公司、原币金额、来源更新时间、执行地区和流程实例；
  - 外部 SQL 全部参数化且只读、带连接和语句超时。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'discovery or incremental or source_conflict'`

  Expected: 发现函数不存在，测试失败。

- [ ] **Step 3: 实现发现网关和候选数据结构**

  在 `external_expenses.py` 增加不依赖 `payment_requests` 的 `discover_expense_workflows(cursors, running_approval_nos)` 查询入口，返回包含 `candidates`、`next_cursors`、`source_conflicts`、`query_timings` 的 `DiscoveryResult`。

  查询结果只生成内存候选，不写 SQLite。来源 Sheet 使用现有申请人部门/二级部门映射；缺失时公司显示“未归属公司”，但明确墨西哥执行地区的流程仍可进入跟进缓存。

- [ ] **Step 4: 实现全局去重和冲突计划**

  对同一申请号聚合来源事实；相同来源 ID 重复行去重；不同业务来源且关键事实冲突时设置 `source_conflict=True`、地区 `review`，保留原始候选摘要用于管理员核对。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'discovery or incremental or source_conflict'`

  Expected: PASS。

  Commit: `feat: discover mexico approval sources`

### Task 5: 实现流程节点、事件和当前审批人的独立同步

**Files:**
- Modify: `backend/app/external_expenses.py`
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写流程解析失败测试**

  基于 `ding_approval_instance.raw_payload.operationRecords` 和现有用户映射，验证：

  - 事件按真实时间和流程顺序保存；
  - 当前节点、当前审批人和节点进入时间从最新有效任务得到；
  - 完成、拒绝、终止转入历史，恢复 `RUNNING` 后重新进入待审批；
  - 未识别人员保留用户 ID，显示“未识别人员”；
  - 稳定事件键重复同步不新增事件；
  - 同一流程评论顺序变化时不会把不同事件错误合并。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'workflow or event_key or current_node'`

  Expected: 新的独立解析/落库服务不存在，测试失败。

- [ ] **Step 3: 复用底层解析而不复用请求绑定**

  将现有 `fetch_dingtalk_workflows(approval_nos)` 中可复用的 PostgreSQL 查询和人员解析提取为独立函数，保留现有批次请款同步调用兼容性。事件键使用：

  ```python
  sha256("|".join([
      process_instance_id, activity_id or "", event_type,
      operator_id or "", event_time_iso, node_name or "",
      normalized_comment,
  ]).encode("utf-8")).hexdigest()
  ```

- [ ] **Step 4: 实现短事务 upsert**

  候选计划全部准备好后，单个 SQLite `BEGIN IMMEDIATE` 事务：按 `approval_no` upsert 当前状态、幂等插入事件、重建与全部匹配 `payment_requests` 的链接，并递增有实际变化记录的 `version`。不在事务内访问 PostgreSQL 或下载文件。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py backend/tests/test_api_workflows.py -q -k 'workflow or dingtalk'`

  Expected: PASS，现有请款流程同步测试不回归。

  Commit: `feat: cache mexico workflow state and events`

### Task 6: 建立可复用的全局分阶段同步任务

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写任务并发和失败恢复测试**

  覆盖：自动打开和手动点击并发时只创建一个 `mexico_sync_runs`；第二个请求返回同一任务 ID；5 分钟内 `only_if_stale_seconds=300` 不重复同步；租约过期可接管；外部查询失败保留上次完整缓存；状态落库失败不推进游标；页面读取在外部查询期间继续成功。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'sync_run or lease or stale or reuse'`

  Expected: 同步任务接口和租约逻辑不存在，测试失败。

- [ ] **Step 3: 实现任务状态机**

  状态阶段固定为：

  ```text
  queued -> querying_sources -> resolving_regions -> querying_workflows
         -> committing_state -> syncing_attachments -> complete|failed|interrupted
  ```

  全局只允许一个未过期的墨西哥同步任务。任务租约 30 分钟；每个外部查询阶段和每批附件完成时刷新租约。阶段耗时分别记录 `source_seconds`、`workflow_seconds`、`commit_seconds`、`attachment_query_seconds`、`attachment_download_seconds`。

- [ ] **Step 4: 增加异步启动和轮询 API**

  在 `main.py` 增加：

  ```text
  POST /api/mexico-tracking/sync?only_if_stale_seconds=300
  GET  /api/mexico-tracking/sync-runs/{run_id}
  ```

  POST 返回 `202` 和任务快照；已有运行任务返回 `reused: true`。本期仍由当前 FastAPI 进程的受控线程执行，不引入 Redis/Worker。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'sync_run or lease or stale or reuse'`

  Expected: PASS。

  Commit: `feat: add reusable mexico sync jobs`

### Task 7: 分离附件补齐并限制并发下载

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/file_storage.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_mexico_tracking.py`
- Modify: `backend/tests/test_file_storage.py`

- [ ] **Step 1: 编写附件失败测试**

  验证状态同步先完成后才补附件；同一来源文件 ID 不重复；最多同时下载 4 个；附件失败只标记该附件 `failed`，不回滚流程状态；重试只处理 `pending/failed`；下载完成后短事务绑定；未被引用的失败临时文件被清理；无权限用户无法读取附件。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'attachment'`

  Expected: 墨西哥附件表和下载流程未接入，测试失败。

- [ ] **Step 3: 实现附件候选与并发下载**

  先只读查询表单、评论和流程事件中的图片/附件标识，写入待下载元数据；用 `ThreadPoolExecutor(max_workers=4)` 下载到现有应用数据盘存储，复用 `file_storage` 的安全文件名、哈希和根目录校验。每次下载不持有 SQLite 写锁。

- [ ] **Step 4: 增加受权限保护的文件访问路由**

  增加 `GET /api/mexico-tracking/{tracking_id}/attachments/{attachment_id}/content`，权限与列表一致；业务人员只有在该流程 Sheet 已授权时可访问。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py backend/tests/test_file_storage.py -q -k 'attachment or storage'`

  Expected: PASS。

  Commit: `feat: sync mexico workflow attachments in parallel`

### Task 8: 提供列表、概览、详情、配置和地区核对 API

**Files:**
- Modify: `backend/app/mexico_tracking.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写 API 权限和分页失败测试**

  覆盖以下接口：

  ```text
  GET  /api/mexico-tracking/summary?view=pending|history|review
  GET  /api/mexico-tracking?page=1&page_size=50&view=pending
  GET  /api/mexico-tracking/filter-options
  GET  /api/mexico-tracking/{tracking_id}
  GET  /api/mexico-tracking/settings
  PUT  /api/mexico-tracking/settings
  POST /api/mexico-tracking/{tracking_id}/resolve-region
  ```

  验证默认只返回 `RUNNING + mexico + resolved`，默认按停留天数倒序，每页最大 100；历史包含完成/拒绝/终止；待核对只对管理员开放。业务人员仅能读取其授权 `source_sheet`；财务、总经理、管理员可查看全部；只有管理员能改阈值和地区。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'api or permission or pagination or settings'`

  Expected: 接口 404 或权限断言失败。

- [ ] **Step 3: 实现索引友好的查询服务**

  列表只选择当前行字段，不联表加载完整事件/附件；详情再按单个申请号加载时间线、附件和关联请款。筛选支持关键词、公司、来源、申请人、审批人、当前节点、预警等级和申请日期。预警使用 SQL `CASE` 或查询后轻量计算，不批量写回。

- [ ] **Step 4: 实现乐观锁地区核对和审计**

  `resolve-region` 请求必须包含 `expected_version` 和 `region=china|mexico`；版本过期返回现有 `409 VERSION_CONFLICT`。保存管理员覆盖、原因和原始冲突事实，写入 `mexico.region_resolve` 审计。

- [ ] **Step 5: 运行测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'api or permission or pagination or settings'`

  Expected: PASS。

  Commit: `feat: add mexico tracking read and admin api`

### Task 9: 将中国工作台、批次统计、每日应付和默认导出统一隔离

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/daily_payables.py`
- Modify: `backend/app/excel_io.py`
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/tests/test_payable_history.py`
- Modify: `backend/tests/test_mexico_tracking.py`

- [ ] **Step 1: 编写中国口径失败测试**

  在同一批次建立中国、墨西哥和待核对请款，先验证灰度开关关闭时保持旧口径；再开启 `china_region_isolation_enabled`，验证默认请求列表、Sheet 标签数量、批次记录数、应付/已付/待付、付款进度、付款状态统计、每日应付 summary/details/trend 和默认 Excel 导出都只包含 `resolved_region=china AND region_review_status=resolved`。

  另验证：已有墨西哥 `payment_requests`、付款、附件、审计和归档数据均保留；墨西哥跟进详情仍能关联它们；管理员显式诊断查询可以读取待核对记录，但普通中国工作台不显示。

- [ ] **Step 2: 运行测试确认失败**

  Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_payable_history.py backend/tests/test_mexico_tracking.py -q -k 'china_scope or mexico_isolation or default_export'`

  Expected: 当前统计仍包含墨西哥或待核对数据，测试失败。

- [ ] **Step 3: 建立唯一中国可见谓词**

  在 `main.py` 暴露统一 SQL 片段/参数构造器，替换请求列表、`batch_public_for_user()`、Sheet 计数、统计和导出的散落过滤。灰度开关关闭时保持现有查询口径，开启后统一应用中国可见谓词；不得再以币种、名称包含 `MX` 或 UI 当前 Sheet 推断。

- [ ] **Step 4: 修正每日应付历史和导出口径**

  `daily_payables.py` 使用历史版本中当时的地区字段；默认导出仅导出中国记录，筛选导出使用当前中国可见集合。Excel 内“全部”Sheet 同样只含中国可见记录。

- [ ] **Step 5: 运行完整相关测试并提交**

  Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_payable_history.py backend/tests/test_mexico_tracking.py -q`

  Expected: PASS。

  Commit: `fix: isolate china payable views from mexico workflows`

### Task 10: 增加前端 API 类型、双语文案和导航入口

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/i18n.tsx`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/mexicoTracking.ts`
- Create: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: 编写前端源结构失败测试**

  在 `frontend/tests/mexico-tracking.test.mjs` 验证：导航存在稳定 `mexico-tracking` tab；页面组件不复用付款表单；API 层存在 summary/list/detail/sync/settings/resolve 方法；催办复制有 Clipboard API 失败回退；中西文案通过 `t()` 而不是硬编码混排。

- [ ] **Step 2: 运行测试确认失败**

  Run: `node --test --test-name-pattern='mexico tracking' frontend/tests/mexico-tracking.test.mjs`

  Expected: 新导航和 API 不存在，测试失败。

- [ ] **Step 3: 增加类型和 API 方法**

  在 `api.ts` 增加 `MexicoTrackingItem`、`MexicoTrackingDetail`、`MexicoTrackingSummary`、`MexicoSyncRun`、`MexicoTrackingSettings` 和分页响应；API 错误继续保留 HTTP 状态和后端错误码。

- [ ] **Step 4: 集成导航和完整双语文案**

  扩展：

  ```ts
  type Tab = "workspace" | "daily-payables" | "mexico-tracking" | "archive" | "admin";
  ```

  中文标题“墨西哥流程”，西语标题“Seguimiento de aprobaciones de México”。导航只影响页面切换，不改变现有工作台批次状态。

- [ ] **Step 5: 运行测试和类型检查并提交**

  Run: `npm run test:frontend && npm run build`

  Expected: PASS。

  Commit: `feat: add mexico tracking frontend contracts`

### Task 11: 实现追踪列表、筛选、分页和同步进度

**Files:**
- Create: `frontend/src/MexicoTrackingPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: 扩充前端失败测试**

  验证页面默认 `view=pending&page_size=50`；概览仅显示待审批、正常、黄色和红色数量；默认按节点停留最长优先；筛选变化重新请求第一页；页面打开只调用带 `only_if_stale_seconds=300` 的同步；手动同步复用返回任务并轮询进度；筛选和翻页不调用外部同步接口。

- [ ] **Step 2: 运行测试确认失败**

  Run: `node --test frontend/tests/mexico-tracking.test.mjs`

  Expected: 页面组件不存在或结构断言失败。

- [ ] **Step 3: 实现 A 方案追踪列表**

  列表列固定为：预警、停留天数、钉钉单号、申请日期、申请人、应付款公司/来源 Sheet、来源类型、摘要、原币金额、当前节点、当前审批人、同步时间、操作。历史和地区待核对使用同一页面视图切换，不混入默认待审批统计。

- [ ] **Step 4: 实现非阻塞同步反馈**

  轮询任务时显示“正在查询来源”“正在查询流程”“正在保存状态”“正在同步 8/32 个附件”等具体进度；旧列表保持可浏览。失败只显示短提示、最近成功同步时间和重试按钮，不清空缓存。

- [ ] **Step 5: 运行测试和构建并提交**

  Run: `npm run test:frontend && npm run build`

  Expected: PASS。

  Commit: `feat: add mexico approval tracking list`

### Task 12: 实现流程详情、附件、关联请款和管理员配置

**Files:**
- Modify: `frontend/src/MexicoTrackingPage.tsx`
- Modify: `frontend/src/mexicoTracking.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: 编写详情交互失败测试**

  验证详情抽屉展示完整时间线、当前节点、评论、附件和全部关联请款；不展示付款录入、银行账号或审批修改控件。复制按钮生成中西双语催办文本。管理员可修改 2/5 天阈值、查看地区冲突原始事实并提交地区结论；非管理员无入口且直接调用接口仍为 403。

- [ ] **Step 2: 运行测试确认失败**

  Run: `node --test frontend/tests/mexico-tracking.test.mjs`

  Expected: 详情和管理员交互断言失败。

- [ ] **Step 3: 实现详情抽屉和剪贴板回退**

  首选 `navigator.clipboard.writeText()`；不可用或失败时创建只读 textarea、选中并使用 `document.execCommand("copy")`，最后清理 DOM。复制成功显示 3 秒非持久提示。

- [ ] **Step 4: 实现管理员设置和地区核对**

  设置变更成功后仅刷新 summary/list，预警即时重算；地区核对提交 `expected_version`，冲突时保留抽屉并刷新最新事实，不自动覆盖。

- [ ] **Step 5: 运行测试和构建并提交**

  Run: `npm run test:frontend && npm run build`

  Expected: PASS。

  Commit: `feat: add mexico workflow detail and review tools`

### Task 13: 完成响应式、性能和大数据回归

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/MexicoTrackingPage.tsx`
- Modify: `backend/tests/test_mexico_tracking.py`
- Modify: `frontend/tests/mexico-tracking.test.mjs`

- [ ] **Step 1: 增加大数据和查询计划测试**

  生成 30,000 条跟进记录，验证默认 pending 查询、按 Sheet、审批人和日期筛选使用已建索引；列表不加载事件全文或附件 BLOB；第一页固定 50 条且返回稳定总数。通过 `EXPLAIN QUERY PLAN` 断言核心默认查询命中 `resolved_region/workflow_status` 复合索引。

- [ ] **Step 2: 增加响应式验收断言**

  390px/430px 使用流程卡片，展示预警、申请人、当前节点、审批人和停留天数；筛选使用全屏/底部面板；详情全屏；桌面保留服务端分页表格。页面除桌面表格容器外不得整体横向溢出。

- [ ] **Step 3: 运行测试确认失败并实现最小修正**

  Run: `.venv/bin/python -m pytest backend/tests/test_mexico_tracking.py -q -k 'large_dataset or query_plan' && npm run test:frontend && npm run build`

  Expected before fix: 新性能/响应式断言失败；完成索引和样式后 PASS。

- [ ] **Step 4: 提交**

  Commit: `perf: harden mexico tracking for large datasets`

### Task 14: 全量验证、预览迁移和分阶段上线

**Files:**
- Create: `docs/deployment/mexico-tracking-release.md`
- Modify: `README.md`

- [ ] **Step 1: 运行后端完整测试**

  Run: `.venv/bin/python -m pytest backend/tests -q`

  Expected: 全部 PASS，无 SQLite busy、版本冲突或现有钉钉同步回归。

- [ ] **Step 2: 运行前端完整验证**

  Run: `npm run test:frontend && npm run build`

  Expected: Node 测试、TypeScript 检查和 Vite 生产构建全部 PASS。

- [ ] **Step 3: 增加只读预览命令和发布清单**

  `docs/deployment/mexico-tracking-release.md` 写明：停止 8011 前备份 SQLite；部署代码并启动迁移；先由管理员执行首次全量同步；核对三类来源总数、中国/墨西哥/待核对数量、两个 FC Sheet、6 个墨西哥 Sheet 和 7 个中国 Sheet；在核对通过前不启用中国统计地区隔离开关。

  预览输出只包含数量和申请号样例，不打印数据库密码、连接串或原始个人敏感字段。

- [ ] **Step 4: 分阶段启用**

  1. 部署表结构、同步和只读 API；
  2. 首次全量同步并人工核对地区；
  3. 启用“墨西哥流程”导航；
  4. 将 `china_region_isolation_enabled` 切换为 `true`，同时启用中国工作台、每日应付和默认导出的中国地区口径；
  5. 验证业务人员 Sheet 权限、附件访问和催办复制；
  6. 观察 24 小时任务耗时、失败附件、待核对数、查询耗时和 SQLite busy 次数。

- [ ] **Step 5: 记录验收证据并提交**

  在发布文档记录实际迁移版本、测试命令、首次同步任务 ID、三类来源数量、待核对数量和回滚路径，不记录密码。

  Commit: `docs: add mexico tracking release runbook`

---

## 最终验收清单

- [ ] 未导入请款的墨西哥运营、采购和月结流程可以被发现。
- [ ] 同一钉钉申请号跨来源、跨批次只展示一次，事件和附件重复同步不重复。
- [ ] 默认仅显示墨西哥待审批，历史可追溯完成、拒绝和终止。
- [ ] 当前节点停留超过 2 天为黄色、超过 5 天为红色，管理员可配置。
- [ ] 手动和自动同步复用同一任务，状态快速落库，附件最多 4 个并发补齐。
- [ ] 外部查询和下载期间页面可读，SQLite 不持有长写事务。
- [ ] 业务人员只看到授权 Sheet；管理员地区核对使用乐观锁并写审计。
- [ ] 中国工作台、批次统计、每日应付、付款进度和默认导出不包含墨西哥或待核对记录。
- [ ] 墨西哥页面不创建付款、不修改钉钉状态、不显示付款维护表单。
- [ ] 中西双语催办内容可复制，详情时间线、评论、附件和关联请款正常。
- [ ] 30,000 条缓存数据下服务端分页和核心筛选命中索引。
- [ ] 后端完整测试、前端测试、TypeScript 检查和生产构建通过。
