# 附件迁移到应用服务器数据盘

本流程只迁移出纳请款应用附件到应用服务器 500GB 数据盘。不会操作数据库服务器的数据盘，也不会删除旧附件。

## 前置检查

```bash
lsblk -f
df -hT / /data
systemctl status cashier-payment --no-pager
```

必须确认 `/data` 是 500GB 数据盘的持久挂载点。若不是，停止操作并先修复挂载配置。

## 低峰期迁移

```bash
cd /www/wwwroot/cashier-payment-archive
sudo systemctl stop cashier-payment
sudo install -d -o www -g www -m 0750 /data/cashier-payment/storage

BACKUP_DIR=/data/cashier-payment/backups/$(date +%Y%m%d-%H%M%S)
sudo install -d -o www -g www -m 0750 "$BACKUP_DIR"
sudo -u www PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage \
  .venv/bin/python -m backend.app.attachment_migration backup "$BACKUP_DIR/app.db"

sudo -u www PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage \
  .venv/bin/python -m backend.app.attachment_migration inventory \
  --output "$BACKUP_DIR/attachment-inventory.json"

sudo -u www PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage \
  .venv/bin/python -m backend.app.attachment_migration migrate \
  --output "$BACKUP_DIR/attachment-migration.json"

sudo -u www PAYMENT_ATTACHMENT_STORAGE_DIR=/data/cashier-payment/storage \
  .venv/bin/python -m backend.app.attachment_migration verify \
  --output "$BACKUP_DIR/attachment-verification.json"
```

只有校验结果中的 `ok` 为 `true`，才继续启动服务。

```bash
sudo cp deploy/cashier-payment.service /etc/systemd/system/cashier-payment.service
sudo systemctl daemon-reload
sudo systemctl start cashier-payment
sudo systemctl status cashier-payment --no-pager
curl -fsS http://127.0.0.1:8011/api/health
```

## 回滚

迁移会保留旧附件原文件。需要回滚时停止服务，将备份的 SQLite 数据库恢复到原路径，撤销服务中的 `PAYMENT_ATTACHMENT_STORAGE_DIR` 环境变量后启动。

## 30 天后清理（本次不执行）

先只生成清理清单：

```bash
sudo -u www .venv/bin/python -m backend.app.attachment_migration cleanup \
  --manifest "$BACKUP_DIR/attachment-migration.json" --retention-days 30
```

必须再次确认数据库引用、数据盘文件和备份均正常后，才可加 `--execute`。本次迁移禁止执行该参数。
