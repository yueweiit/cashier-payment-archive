# 墨西哥审批可用性发布核对

1. 停止服务并使用 SQLite Backup API 备份主库。
2. 启动新版本，确认 `schema_migrations` 包含 `mexico_request_region_and_china_isolation_v2`。
3. 确认 `app_settings.china_region_isolation_enabled=true`。
4. 核对“悦为智能”中执行地区为墨西哥的记录已从中国工作台和每日应付排除。
5. 触发墨西哥同步，确认审批状态先显示，附件队列继续后台运行。
