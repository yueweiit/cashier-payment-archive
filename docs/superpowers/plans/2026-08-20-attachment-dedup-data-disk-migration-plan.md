# 附件去重与应用服务器数据盘迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing each task and superpowers:verification-before-completion before claiming completion.

**Goal:** 修复钉钉附件重复识别，让相同内容只保存一份物理文件，并将普通附件、付款凭证和钉钉附件安全迁移到应用服务器 500GB 数据盘，同时保留 30 天旧文件回滚能力。

**Architecture:** 在现有 SQLite 中增加内容寻址的 `file_objects` 表，业务附件关系继续保留在 `attachment_links` 和 `payment_vouchers`。文件统一通过独立存储服务写入 `PAYMENT_ATTACHMENT_STORAGE_DIR/attachments/sha256/...`，读取时优先新存储、失败后回退旧 `file_path`。迁移工具先盘点和校验，再用短事务关联数据库；快照保存文件对象引用，不再复制附件实体。

**Tech Stack:** Python 3、FastAPI、SQLite、`hashlib`、`pathlib`、`shutil`、pytest；现有 React 前端无需业务改造。

---

## 实施前约束

- 保留当前工作区已有的并发安全改造，不回退 `version`、WAL、批次任务进度等变更。
- 本次不移动 SQLite 主库，不接入 OSS，不操作数据库服务器 1TB 数据盘。
- 首次上线只复制和关联，不删除系统盘任何附件。
- 线上目标目录固定为 `/data/cashier-payment/storage`，目录创建后限制为应用服务账号可写。
- 所有删除命令必须在 30 天观察期后由人工显式执行；本计划中的首次部署不运行永久清理。

### Task 1: 建立文件对象数据模型和存储配置

**Files:**
- Modify: `backend/app/db.py`
- Create: `backend/tests/test_file_storage.py`
- Modify: `README.md`

**Step 1: 编写失败测试**

在 `backend/tests/test_file_storage.py` 中使用临时 `PAYMENT_APP_DATA_DIR`、`PAYMENT_APP_DB` 和 `PAYMENT_ATTACHMENT_STORAGE_DIR`，验证：

- `init_db()` 创建 `file_objects`；
- `attachment_links` 与 `payment_vouchers` 均有 `file_object_id`；
- `attachment_links` 有 `source_instance_id`；
- `file_objects.sha256` 唯一；
- 真实来源唯一约束为 `request_id + source_system + source_instance_id + source_attachment_id`。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_file_storage.py -q`

Expected: 因 `file_objects` 或新增字段不存在而失败。

**Step 3: 实现非破坏性迁移**

在 `backend/app/db.py`：

- 增加 `ATTACHMENT_STORAGE_DIR = Path(os.environ.get("PAYMENT_ATTACHMENT_STORAGE_DIR", DATA_DIR / "storage"))`；
- 创建 `file_objects`，字段为 `id`、`sha256`、`size_bytes`、`mime_type`、`storage_backend`、`storage_path`、`status`、`created_at`、`verified_at`；
- 对两个业务附件表增加 nullable `file_object_id`；
- 对普通附件增加 nullable `source_instance_id`；
- 创建外键和查询索引；
- 用新索引替换旧的 `idx_attachment_links_external_source`，但不删除任何附件行；
- 对旧数据保留 `file_path`，确保升级可逆。

**Step 4: 更新配置文档**

在 `README.md` 记录：

```text
PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage
```

并说明 `PAYMENT_APP_DATA_DIR` 仍保存 SQLite 和非附件元数据。

**Step 5: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_file_storage.py -q`

Expected: PASS。

Commit: `feat: add content-addressed file object schema`

### Task 2: 实现内容寻址文件存储服务

**Files:**
- Create: `backend/app/file_storage.py`
- Modify: `backend/tests/test_file_storage.py`

**Step 1: 为核心行为编写失败测试**

覆盖：

- 相同内容写入两次返回同一个 `file_object_id` 和同一路径；
- 不同文件名但相同内容只生成一个物理文件；
- 临时文件与最终文件都位于数据盘，提交使用原子重命名；
- 数据库失败时留下可识别的孤儿文件，不创建错误业务引用；
- 哈希、大小和最终文件内容一致；
- 非法或越界 `storage_path` 无法读取。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_file_storage.py -q`

Expected: 无 `file_storage` 模块而失败。

**Step 3: 实现存储服务**

在 `backend/app/file_storage.py` 提供：

```python
def store_stream(conn, stream, *, mime_type: str | None) -> dict: ...
def store_path(conn, source_path: Path, *, mime_type: str | None) -> dict: ...
def resolve_file_object(row: sqlite3.Row) -> Path | None: ...
def resolve_attachment_path(row: sqlite3.Row) -> tuple[Path | None, bool]: ...
def delete_physical_file_if_unreferenced(conn, file_object_id: int) -> bool: ...
```

实现细节：

- 临时文件写到 `ATTACHMENT_STORAGE_DIR/tmp`；
- 流式计算 SHA-256 和字节数；
- 最终路径为 `attachments/sha256/{sha256[:2]}/{sha256}`；
- 目标已存在时复核大小后复用；
- 通过 `INSERT ... ON CONFLICT(sha256)` 复用对象；
- 最终路径必须位于附件根目录下；
- 删除前同时检查 `attachment_links`、`payment_vouchers` 和快照引用。

**Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_file_storage.py -q`

Expected: PASS。

Commit: `feat: add content-addressed attachment storage`

### Task 3: 接入人工附件、付款凭证和 Excel 图片

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/attachment_io.py`
- Modify: `backend/tests/test_api_workflows.py`
- Modify: `backend/tests/test_file_storage.py`

**Step 1: 编写 API 失败测试**

增加测试验证：

- 两次上传内容相同的普通附件，保留两条业务关系但共享一个文件对象；
- 普通附件和付款凭证内容相同，也共享物理文件；
- 下载和预览优先读取文件对象；
- 人工附件删除一条关系不会破坏另一条关系；
- 旧 `file_path` 且无 `file_object_id` 的附件仍可下载；
- Excel 嵌入图片写入新存储根目录。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_file_storage.py -q -k 'attachment or voucher or embedded'`

Expected: 新断言失败。

**Step 3: 替换直接磁盘写入**

- `save_image_upload`、`save_payment_voucher_upload` 和 Excel 图片提取统一调用 `store_stream`/`store_path`；
- 插入业务记录时写入 `file_object_id`，原 `file_path` 仅作旧数据兼容，不再用于新文件；
- 下载接口调用统一解析器，返回数据盘文件或旧路径；
- 删除关系后调用引用检查，当前阶段不删除任何 legacy 文件；
- 周结转复制业务引用，不复制物理文件。

**Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py backend/tests/test_file_storage.py -q -k 'attachment or voucher or embedded or rollover'`

Expected: PASS。

Commit: `refactor: route uploaded files through file objects`

### Task 4: 修复钉钉真实 fileId 去重

**Files:**
- Modify: `backend/app/external_expenses.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api_workflows.py`

**Step 1: 修改测试暴露当前错误**

扩展 `test_dingtalk_metadata_sync_adds_only_unsynced_request_attachments`：

- 第一次中间表行 ID 为 `501`、真实 `file_id` 为 `ding-file-1`；
- 第二次同步中间表行 ID 改为 `999`，但真实 `file_id` 不变；
- 最终仍只有一条业务附件，且 `source_attachment_id == "ding-file-1"`；
- 同一 `file_id` 在另一条请款中仍保留独立业务关系，但共享物理对象；
- 同一流程实例不同 `file_id` 可同时存在。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'dingtalk_metadata_sync_adds_only_unsynced_request_attachments'`

Expected: 当前代码将 `501` 当作来源 ID，测试失败。

**Step 3: 使用稳定来源身份**

- `source_attachment_id` 写真实 `attachment["file_id"]`；
- `source_instance_id` 写 `process_instance_id`；
- 中间表行 ID 只保留在来源元数据或日志；
- 下载前按新唯一键查询，已存在直接跳过；
- 下载完成后用内容寻址存储并创建关系；
- 并发同步时依靠唯一索引兜底，冲突后复用既有关系和文件对象；
- 月结、运营、采购三种来源统一执行相同规则。

**Step 4: 验证附件阶段容错**

测试一个附件失败、另一个成功时：流程状态/评论已更新，成功文件可用，失败文件可重试，不产生重复关系。

**Step 5: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'dingtalk and attachment'`

Expected: PASS。

Commit: `fix: deduplicate dingtalk attachments by real file id`

### Task 5: 调整快照为引用文件对象

**Files:**
- Modify: `backend/app/snapshots.py`
- Modify: `backend/tests/test_api_workflows.py`

**Step 1: 编写失败测试**

验证：

- 创建新快照后不会在 `snapshots/.../files` 复制已有文件对象；
- 快照 JSON 保存 `file_object_id`；
- 还原后附件关系仍指向同一文件对象；
- 老快照带 `_snapshot_file_path` 时仍可恢复；
- 文件对象缺失时还原报告异常，不伪造可下载附件；
- 删除快照不会删除仍被当前业务记录引用的文件对象。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'snapshot and attachment'`

Expected: 当前快照仍复制物理文件，测试失败。

**Step 3: 实现新旧快照兼容**

- 新快照不调用 `copy_attachment_files_for_snapshot` 复制 file-object 文件；
- 保留旧 `_snapshot_file_path` 分支，仅用于恢复历史快照；
- 新恢复先检查 `file_objects.status == ready` 且文件存在；
- 快照清理只删除历史快照自己的 legacy 副本，不删除内容寻址文件。

**Step 4: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_api_workflows.py -q -k 'snapshot or restore_point or rollover'`

Expected: PASS。

Commit: `refactor: snapshot attachment references without copying blobs`

### Task 6: 实现幂等历史迁移工具

**Files:**
- Create: `backend/app/attachment_migration.py`
- Create: `backend/tests/test_attachment_migration.py`
- Create: `docs/operations/attachment-data-disk-migration.md`

**Step 1: 编写迁移测试**

使用临时 SQLite 和文件树覆盖：

- `inventory` 只读输出关系数、唯一哈希数、重复容量、缺失文件和旧路径读取数；
- `migrate` 复制唯一文件、校验哈希、建立文件对象及关联；
- 重复运行结果不变；
- 中断后可以继续；
- 相同内容跨关系共享文件对象但不删除业务关系；
- 同一请款同真实 `fileId` 的重复关系只规范化一条；
- 无法映射真实 `fileId` 的旧关系保留；
- `verify` 检查源/目标哈希和数据库关系；
- `cleanup` 默认 dry-run，未到保留期或仍有引用时拒绝删除。

**Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_attachment_migration.py -q`

Expected: 模块不存在而失败。

**Step 3: 实现 CLI**

提供命令：

```bash
.venv/bin/python -m backend.app.attachment_migration inventory --output output/attachment-inventory.json
.venv/bin/python -m backend.app.attachment_migration backup --output /data/cashier-payment/backups/app-before-attachment-migration.db
.venv/bin/python -m backend.app.attachment_migration migrate --manifest output/attachment-inventory.json --batch-size 100
.venv/bin/python -m backend.app.attachment_migration verify --manifest output/attachment-inventory.json
.venv/bin/python -m backend.app.attachment_migration cleanup --manifest output/attachment-inventory.json --retention-days 30 --dry-run
```

要求：

- `backup` 使用 SQLite Backup API；
- manifest 保存源路径、目标路径、大小、SHA-256、关系 ID、执行状态和错误；
- 复制/哈希在事务外，数据库关联按 100 条短事务；
- 命令失败返回非零退出码；
- 日志不得包含密码、令牌或临时下载 URL；
- 不提供绕过 30 天保留期的默认参数。

**Step 4: 编写线上操作手册**

手册写清：目录权限、磁盘检查、服务停止点、备份校验、盘点审阅、复制、验证、配置切换、重启、抽样下载、回滚和 30 天后的人工清理。

**Step 5: 验证并提交**

Run: `.venv/bin/python -m pytest backend/tests/test_attachment_migration.py -q`

Expected: PASS。

Commit: `feat: add idempotent attachment migration tooling`

### Task 7: 全量回归与本地迁移演练

**Files:**
- Modify only if tests expose defects.

**Step 1: 运行后端完整测试**

Run: `.venv/bin/python -m pytest backend/tests -q`

Expected: 全部 PASS。

**Step 2: 运行前端生产构建**

Run: `npm run build`

Expected: TypeScript 检查和 Vite 构建成功。

**Step 3: 在本地副本演练**

- 使用 SQLite Backup API 创建测试副本；
- 对副本执行 `inventory → migrate → verify`；
- 抽样至少 20 个普通附件、10 个付款凭证、10 个钉钉附件；
- 比对迁移前后业务关系数、唯一物理文件数、总容量和 SHA-256；
- 验证 legacy fallback 计数只在未迁移记录上增长。

**Step 4: 审查代码和差异**

Run: `git diff --check && git status --short`

确认没有提交真实 `.env`、数据库、附件、迁移清单或备份。

Commit: `test: cover attachment migration and dedup workflows`

### Task 8: 线上低峰发布与非破坏性迁移

**Files:**
- Production config: systemd environment for `cashier-payment.service`
- Production paths: `/www/wwwroot/cashier-payment-archive`, `/data/cashier-payment/storage`

**Step 1: 只读检查**

在应用服务器确认：

```bash
df -h / /data
systemctl status cashier-payment.service --no-pager
du -sh /www/wwwroot/cashier-payment-archive/data/uploads
```

**Step 2: 部署兼容版本但暂不切换写入**

- 拉取已验证代码；
- 安装依赖并构建前端；
- 停止 8011 服务；
- 运行数据库初始化迁移；
- 启动服务，验证旧附件仍可下载。

**Step 3: 创建目标目录及一致性备份**

```bash
install -d -m 0750 /data/cashier-payment/storage /data/cashier-payment/backups
```

用迁移 CLI 创建并校验 SQLite 备份，禁止直接复制运行中的 `app.db`。

**Step 4: 盘点、迁移和验证**

- 运行 `inventory` 并检查缺失、冲突和容量；
- 运行 `migrate`；
- 运行 `verify`，任何哈希错误都停止切换；
- 抽样下载并预览附件。

**Step 5: 切换新写入**

给 systemd 服务增加：

```text
PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage
```

重启服务后上传一张普通附件和一个付款凭证，并同步一条钉钉附件，确认物理文件只写入 `/data`。

**Step 6: 观察与回滚准备**

- 系统盘旧附件原地保留 30 天；
- 每日记录 legacy fallback、missing、pending、磁盘使用率；
- 异常时移除新环境变量并重启即可回退旧路径；
- 首次发布不执行 `cleanup`。

**Step 7: 30 天后人工清理**

先运行 `cleanup --dry-run`，核对目标哈希、数据库引用、快照引用和旧路径回退次数均正常，再由用户单独确认永久删除。

---

## 最终验收标准

- 同一请款同一真实钉钉 `fileId` 重复同步不会新增关系或下载。
- 所有新附件物理文件位于 `/data/cashier-payment/storage`。
- 相同 SHA-256 只有一个物理文件对象，业务关系保持完整。
- 历史附件和付款凭证可正常预览、下载、删除和随周结转继承。
- 新快照不再复制附件实体，旧快照仍可恢复。
- 迁移前后有效业务附件关系数一致，已确认的同请款同 `fileId` 重复除外。
- 系统盘旧附件保留 30 天，首次发布无永久删除。
- 后端完整测试和前端生产构建通过。
