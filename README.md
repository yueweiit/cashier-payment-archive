# 出纳请款明细内网网页系统

这是一个局域网多人使用的请款明细归档工具。后端使用 FastAPI + SQLite，前端使用 React + TypeScript + Vite，支持周报 Excel 导入、钉钉导出表字段映射导入、内置 Excel 式直接录入、从上周批次生成本周草稿、批次归档、审计日志、附件链接留档和按多 Sheet 模板导出 Excel。

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
  - `PAYMENT_APP_ADMIN_PASSWORD`

## 内置 Excel 式录入

- 在“工作台”中可以直接点击单元格录入。
- 支持 `Tab`、`Enter`、方向键移动单元格。
- 支持从 Excel 复制多行多列后粘贴到表格里；超出末尾会自动生成新行。
- 修改过的单元格会高亮，点击“保存更改”后统一提交。
- 切换批次或刷新页面前，如果有未保存更改，浏览器会提示确认。
- 归档批次默认只读；管理员更正归档数据时必须填写原因。

## 周迭代

- 归档上周批次后，可在工作台左侧“从上周生成本周”中选择上周批次。
- 系统会复制未完成项到新草稿批次：付款情况不含“已支付”，且实际付款日期为空。
- 新批次会记录来源批次和来源明细，审计日志会记录本次复制操作。

## 已验证

- 使用 `/Users/smk/Downloads/20260626~20260707请款明细.xlsx` 做导入回归。
- 主汇总 Sheet 导入 86 条业务记录。
- 总导入 161 条业务记录；页脚银行账号、合计行等非请款行会被过滤。
- 导出接口会生成可被 Excel 打开的 `.xlsx`，并按来源 Sheet 分组保留中文表头和合计公式。
- 已验证从上周批次生成本周草稿、批量创建/更新/删除、失败回滚和前端生产构建。
