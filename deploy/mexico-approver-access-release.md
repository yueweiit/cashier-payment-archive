# 墨西哥审批人统计与访问权限发布

本次发布包含用户表字段迁移、墨西哥参与人权限、审批人统计、账号开通、六列审批列表，以及每日应付趋势日期修复。所有命令均在应用目录 `/www/wwwroot/cashier-payment-archive` 执行。

## 1. 发布前检查与一致性备份

选择低峰期，先从正在运行的 systemd 进程捕获其真实数据库环境，再记录当前版本并停止服务。这样即使生产机通过 `PAYMENT_APP_DATA_DIR` 或 `PAYMENT_APP_DB` 改过默认目录，后续备份、开通账号和回滚仍会操作同一个数据库。将示例时间目录替换为本次实际发布时间，不要覆盖已有备份。

```bash
cd /www/wwwroot/cashier-payment-archive
sudo systemctl status cashier-payment --no-pager
SERVICE_PID="$(sudo systemctl show cashier-payment --property=MainPID --value)"
RELEASE_ENV_FILE=/run/cashier-payment-release.env
sudo env SERVICE_PID="$SERVICE_PID" RELEASE_ENV_FILE="$RELEASE_ENV_FILE" \
  .venv/bin/python - <<'PY'
import os
import shlex
from pathlib import Path

service_pid = int(os.environ["SERVICE_PID"])
if service_pid <= 0:
    raise SystemExit("cashier-payment 服务没有正在运行的 MainPID")
entries = Path(f"/proc/{service_pid}/environ").read_bytes().split(b"\0")
service_env = {}
for entry in entries:
    if b"=" not in entry:
        continue
    key, value = entry.split(b"=", 1)
    service_env[key.decode()] = value.decode()
working_dir = Path(f"/proc/{service_pid}/cwd").resolve()
data_dir = Path(service_env.get("PAYMENT_APP_DATA_DIR", working_dir / "data")).resolve()
db_path = Path(service_env.get("PAYMENT_APP_DB", data_dir / "app.db")).resolve()
if not db_path.is_file():
    raise SystemExit(f"解析的生产数据库不存在: {db_path}")
release_env = Path(os.environ["RELEASE_ENV_FILE"])
release_env.write_text(
    f"PAYMENT_APP_DATA_DIR={shlex.quote(str(data_dir))}\n"
    f"PAYMENT_APP_DB={shlex.quote(str(db_path))}\n",
    encoding="utf-8",
)
release_env.chmod(0o600)
print(f"PAYMENT_APP_DB={db_path}")
PY
sudo chown "$(id -u):$(id -g)" "$RELEASE_ENV_FILE"
chmod 600 "$RELEASE_ENV_FILE"
. "$RELEASE_ENV_FILE"
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  .venv/bin/python -c 'from backend.app.db import DB_PATH; print(f"已确认生产数据库: {DB_PATH.resolve()}"); assert DB_PATH.resolve().is_file()'
git rev-parse HEAD
sudo install -d -o www -g www -m 0750 /data/cashier-payment/backups/20260825-120000
git rev-parse HEAD | sudo tee /data/cashier-payment/backups/20260825-120000/previous-commit.txt
sudo systemctl stop cashier-payment
```

屏幕打印的 `PAYMENT_APP_DB` 必须与当前生产配置一致；路径不存在或不符合预期时立即停止，不要继续。使用刚捕获的同一环境调用 SQLite Backup API，生成包含已提交 WAL 页的一致性副本并执行完整性校验：

```bash
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  PAYMENT_RELEASE_BACKUP_PATH=/data/cashier-payment/backups/20260825-120000/app.db \
  .venv/bin/python -c 'import json, os; from backend.app.db import backup_database; print(json.dumps(backup_database(os.environ["PAYMENT_RELEASE_BACKUP_PATH"]), ensure_ascii=False))'
```

保存命令输出中的 `sha256`。若备份或完整性校验失败，不得继续发布。

## 2. 部署代码与初始化结构

```bash
git fetch --all --prune
git checkout <release-commit>
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo systemctl start cashier-payment
curl -fsS http://127.0.0.1:8011/api/health
sudo systemctl stop cashier-payment
```

首次启动会通过 `init_db()` 增加以下字段和索引：

- `users.mexico_access_scope`
- `users.mexico_identity_name`
- 墨西哥申请人、事件操作人和当前审批任务查询索引

## 3. 幂等开通账号

脚本要求 Tiffany/周汉琴和施鸣坤各自只能匹配到一个现有账号；匹配不唯一或缺失时会在任何写入前退出。脚本不会覆盖任何现有账号的角色和密码。

```bash
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  .venv/bin/python -m backend.app.provision_mexico_users --actor-username admin
```

首次预期输出：

```json
{"created": 9, "updated": 2, "unchanged": 0}
```

再次执行预期输出：

```json
{"created": 0, "updated": 0, "unchanged": 11}
```

如果生产库此前已存在部分新账号，首次数字可以不同，但第二次必须全部为 `unchanged`。新账号初始密码为 `Yuewei123`；登录后由用户自行修改。

## 4. 启动与验收

```bash
sudo systemctl start cashier-payment
sudo systemctl status cashier-payment --no-pager
curl -fsS http://127.0.0.1:8011/api/health
```

按以下顺序验收：

1. 使用 `admin` 登录：可查看全部墨西哥审批、审批人统计、地区待核对和用户管理。
2. 使用 Tiffany、施鸣坤、Nelly 或 Angelica 登录：可查看全部墨西哥审批；确认 Tiffany 和施鸣坤仍可使用原密码。
3. 使用任一 `participant` 账号登录：只显示其作为申请人、当前审批人、历史操作人或抄送人的流程；不能通过直接详情或附件地址查看无关流程。
4. 将测试账号设为 `none`：导航中不出现“墨西哥审批”，直接请求 `/api/mexico-tracking` 返回 `403`。
5. 审批列表桌面端显示六列；公司和摘要最多两行，所有当前审批人完整显示，红色记录仅使用左侧预警色。
6. 切换到“审批人统计”，点击人名返回列表并自动筛选；再点黄色或红色 KPI 时仍保留该审批人。
7. 每日应付点击 8 月 24 日趋势点：当天明细切换到 24 日，8 月 25 日趋势点仍保留。
8. 切换中文、西语和移动端宽度，确认导航、审批卡片及统计按钮可用。
9. 检查服务日志无持续异常：

```bash
sudo journalctl -u cashier-payment -n 200 --no-pager
```

## 5. 回滚

若出现权限、数据或页面阻断问题，立即停止服务并恢复发布前代码和数据库。备份目录使用第 1 步实际目录。

```bash
cd /www/wwwroot/cashier-payment-archive
sudo systemctl stop cashier-payment
. /run/cashier-payment-release.env
git checkout "$(cat /data/cashier-payment/backups/20260825-120000/previous-commit.txt)"
sudo cp "$PAYMENT_APP_DB" /data/cashier-payment/backups/20260825-120000/failed-release-app.db
sudo cp /data/cashier-payment/backups/20260825-120000/app.db "$PAYMENT_APP_DB"
sudo chown www:www "$PAYMENT_APP_DB"
sudo rm -f "${PAYMENT_APP_DB}-wal" "${PAYMENT_APP_DB}-shm"
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  .venv/bin/python -c 'from backend.app.db import DB_PATH; print(f"已恢复生产数据库: {DB_PATH.resolve()}"); assert DB_PATH.resolve().is_file()'
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo systemctl start cashier-payment
curl -fsS http://127.0.0.1:8011/api/health
```

回滚后重新验证登录、工作台、每日应付和导出。失败版本数据库副本保留用于排查，不要直接删除。
