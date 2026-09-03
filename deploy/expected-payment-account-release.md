# 预计支付账户发布与回滚

本次发布为 `payment_requests` 增加 `expected_payment_account` 和 `expected_payment_account_source`，并把钉钉明确值、服务主体默认值、人工值贯通到导入、同步、工作台、Excel、结转、合并与快照。每日应付口径不变。生产目录为 `/www/wwwroot/cashier-payment-archive`，服务为 `cashier-payment`。

## 1. 发布前检查和一致性备份

先从正在运行的进程解析真实数据库路径；不要假设生产库一定是仓库中的 `data/app.db`。记录旧提交，停止服务，再通过 SQLite Backup API 备份，避免遗漏 WAL 中已提交页面。

```bash
cd /www/wwwroot/cashier-payment-archive
sudo systemctl status cashier-payment --no-pager
SERVICE_PID="$(sudo systemctl show cashier-payment --property=MainPID --value)"
RELEASE_ENV_FILE=/run/cashier-payment-expected-account.env
sudo env SERVICE_PID="$SERVICE_PID" RELEASE_ENV_FILE="$RELEASE_ENV_FILE" .venv/bin/python - <<'PY'
import os
import shlex
from pathlib import Path

pid = int(os.environ["SERVICE_PID"])
if pid <= 0:
    raise SystemExit("cashier-payment 服务未运行")
entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
service_env = {}
for entry in entries:
    if b"=" in entry:
        key, value = entry.split(b"=", 1)
        service_env[key.decode()] = value.decode()
working_dir = Path(f"/proc/{pid}/cwd").resolve()
data_dir = Path(service_env.get("PAYMENT_APP_DATA_DIR", working_dir / "data")).resolve()
db_path = Path(service_env.get("PAYMENT_APP_DB", data_dir / "app.db")).resolve()
if not db_path.is_file():
    raise SystemExit(f"生产数据库不存在: {db_path}")
target = Path(os.environ["RELEASE_ENV_FILE"])
target.write_text(
    f"PAYMENT_APP_DATA_DIR={shlex.quote(str(data_dir))}\n"
    f"PAYMENT_APP_DB={shlex.quote(str(db_path))}\n",
    encoding="utf-8",
)
target.chmod(0o600)
print(db_path)
PY
sudo chown "$(id -u):$(id -g)" "$RELEASE_ENV_FILE"
chmod 600 "$RELEASE_ENV_FILE"
. "$RELEASE_ENV_FILE"
BACKUP_DIR="/data/cashier-payment/backups/$(date +%Y%m%d-%H%M%S)-expected-account"
sudo install -d -o www -g www -m 0750 "$BACKUP_DIR"
git rev-parse HEAD | sudo tee "$BACKUP_DIR/previous-commit.txt"
sudo systemctl stop cashier-payment
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  PAYMENT_RELEASE_BACKUP_PATH="$BACKUP_DIR/app.db" \
  .venv/bin/python -c 'import json, os; from backend.app.db import backup_database; print(json.dumps(backup_database(os.environ["PAYMENT_RELEASE_BACKUP_PATH"]), ensure_ascii=False))'
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" \
  .venv/bin/python -c 'import sqlite3; from backend.app.db import DB_PATH; conn=sqlite3.connect(DB_PATH); print(conn.execute("PRAGMA integrity_check").fetchone()[0])'
```

只有备份返回完整性通过并给出 `sha256` 后才能继续。保存终端打印的 `BACKUP_DIR`、数据库绝对路径、旧提交和哈希。

## 2. 部署代码和迁移字段

```bash
git fetch --all --prune
git checkout <release-commit>
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo systemctl start cashier-payment
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/)" = 200
. "$RELEASE_ENV_FILE"
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" .venv/bin/python - <<'PY'
from backend.app.db import connect

with connect() as conn:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(payment_requests)")}
required = {"expected_payment_account", "expected_payment_account_source"}
missing = required - columns
if missing:
    raise SystemExit(f"缺少字段: {sorted(missing)}")
print("预计支付账户字段已就绪")
PY
```

首次启动通过幂等迁移增加两列，不回填历史数据。

## 3. 同步前后核对

先在网页打开当前草稿批次，导出一份 Excel；再用数据库只读查询保存非空人工值和账户性质的基线。触发一次“同步钉钉流程”后检查：

1. 钉钉已填写“预计支付账户”的记录采用原始显示值，来源为 `dingtalk_explicit`。
2. 钉钉未填写该字段、但服务主体能识别的记录采用对应公司账户，来源为 `service_subject_default`。
3. 已由网页或 Excel 人工填写且来源为 `manual` 的值不变化。
4. 人工清空的值可在同步后重新填充；未知服务主体仍为空并出现导入警告。
5. `payment_account` 继续只由“是否有发票”推导，原人工结果不被同步覆盖。
6. 第二次同步不产生额外字段变更审计，证明同步幂等。

可用以下只读汇总辅助抽查：

```bash
. "$RELEASE_ENV_FILE"
sudo -u www env PAYMENT_APP_DATA_DIR="$PAYMENT_APP_DATA_DIR" PAYMENT_APP_DB="$PAYMENT_APP_DB" .venv/bin/python - <<'PY'
from backend.app.db import connect

with connect() as conn:
    rows = conn.execute(
        """
        SELECT COALESCE(expected_payment_account_source, '<空>') AS source, COUNT(*) AS total
        FROM payment_requests
        GROUP BY COALESCE(expected_payment_account_source, '<空>')
        ORDER BY source
        """
    ).fetchall()
    for row in rows:
        print(dict(row))
PY
```

## 4. 页面和文件验收

1. 中文和西语工作台中，“预计支付账户 / Cuenta de pago prevista”紧跟“账户性质”，默认可见且可在列设置隐藏。
2. 桌面表格、请款抽屉和手机卡片显示一致；人工修改保存后来源为 `manual`，再次同步不被覆盖。
3. 工作台导出的“全部”和部门 Sheet 都包含“预计支付账户”，重新导入和“合并更新”后值不丢失。
4. 从上周生成本周、草稿还原和合并撤回后，值与内部来源都恢复正确。
5. 每日应付页面和每日应付区间导出的列、金额、地区过滤保持原样，不出现预计支付账户列。
6. 检查服务状态、首页和日志：

```bash
sudo systemctl status cashier-payment --no-pager
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/)" = 200
sudo journalctl -u cashier-payment -n 200 --no-pager
```

## 5. 回滚

若出现数据覆盖、字段迁移、页面或同步阻断问题，停止服务并恢复同一次发布创建的旧提交和一致性数据库备份。先把失败库另存，便于排查。

```bash
cd /www/wwwroot/cashier-payment-archive
. /run/cashier-payment-expected-account.env
sudo systemctl stop cashier-payment
git checkout "$(cat "$BACKUP_DIR/previous-commit.txt")"
sudo cp "$PAYMENT_APP_DB" "$BACKUP_DIR/failed-release-app.db"
sudo cp "$BACKUP_DIR/app.db" "$PAYMENT_APP_DB"
sudo chown www:www "$PAYMENT_APP_DB"
sudo rm -f "${PAYMENT_APP_DB}-wal" "${PAYMENT_APP_DB}-shm"
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
sudo systemctl start cashier-payment
test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/)" = 200
```

新终端执行回滚前，先把本次实际 `BACKUP_DIR` 重新赋值；路径不明确时不要执行复制或删除命令。回滚后重新验证登录、工作台、Excel 和每日应付。
