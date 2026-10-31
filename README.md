# 知序 · 每日计划系统

Python 后端与独立 HTML/CSS/JavaScript 前端。按日期自主安排每日任务，完成后勾选，保存实际用时、笔记和历史记录。课程资料库和补充推荐仅供选择，不会自动生成任务。

## 本机启动

需要 Python 3.10 或以上版本。应用使用 Python 标准库，不需要安装第三方依赖。

```powershell
python start.py
```

浏览器打开 `http://127.0.0.1:5174`。首次使用请自行注册账号，数据库会在 `backend/study.db` 创建。后端监听本机 `8001` 端口，前端使用 `5174` 端口。保持启动进程运行，Ctrl+C 停止。

在 PyCharm 打开仓库文件夹，配置可用的 Python 解释器，右键 `start.py` → Run。仓库不包含个人电脑的 `.idea` 配置。

## 功能

- 用户注册、登录、退出及按账号隔离的数据。
- 每日待办表格，勾选完成，修改日期、分类、预计/实际用时与笔记。
- 任务可点击开始计时，完成时保存结束时间并自动计算实际用时；也可手动填写、修正开始与结束时间。
- 课程和课时表格；视频课时逐节打钩、撤销，自动汇总已学进度；每日任务仍由自己安排。
- 本地移动硬盘课程索引、视频播放及资料读取；不会复制视频。
- 学习记录、近期投入统计、JSON 导出与旧模板任务可恢复归档。
- 云端 WSGI 入口，任务、课程进度与目录使用持久 SQLite；可打开自行添加的在线课程链接，本地移动硬盘视频不上传。

## 导入自己的本地课程

课程索引和媒体属于个人资料，不包含在本仓库。请先启动并注册自己的账号，再按课程集合/课程的两级目录组织资料，然后执行：

```powershell
python backend/import_materials.py --username YOUR_LOCAL_USERNAME --root "G:/人工智能学习"
```

该命令扫描资料文件名并生成 `backend/materials_catalog.json`，课程原文件保留在所选硬盘。拔掉硬盘后目录仍在，视频暂时无法读取。该索引被 `.gitignore` 排除。

## 云端部署

`deploy/wsgi_app.py` 复用原 Python API，在同一 HTTPS 域名服务网页与 `/api`。在支持 WSGI 和持久文件存储的托管平台配置 `deploy/pythonanywhere_wsgi.example.py`。必须替换示例用户名，设置准确的 `STUDY_PUBLIC_ORIGIN` 与私有绝对路径 `STUDY_DB`。

`deploy/package_cloud.py --username YOUR_LOCAL_USERNAME` 可在自己的电脑上生成带指定账号数据的私人迁移包。该包包含数据库，只能上传到本人受控的私有托管空间，不能提交到 GitHub。`deploy/ready/` 和所有数据库文件均被忽略。

云端和本机数据库不会自动双向合并。跨设备使用时，统一登录同一云端网址，以云端数据库为准，任务、计时和逐节进度保存在同一账号。云端只能打开有效的在线课程链接，不能读取电脑的移动硬盘；本地媒体不会自动上传或同步。

## 项目结构

```text
backend/        Python JSON API、SQLite 与受限本地资料服务
frontend/       独立网页、表格布局和交互
deploy/         WSGI 生产入口、示例和私人迁移打包工具
tests/          标准库 unittest 回归测试
start.py        本机双服务启动器
```

## 验证

```powershell
python -m unittest discover -s tests -v
python deploy/test_wsgi.py
node --check frontend/app.js
```

Node.js 仅用于可选的 JavaScript 语法检查，运行本系统不需要 Node。

## 数据与备份

本仓库只备份源代码，不包含账号、密码哈希、登录会话、任务数据库、私人课程目录、视频、日志或备份。账号和记录需由部署者保管并单独备份。JSON 导出用于查阅，当前不提供 JSON 导入恢复。不要公开数据库或私人迁移 ZIP。

### 管理员密码恢复

`deploy/create_password_reset.py` 仅供实例所有者在自己的私有部署环境中按需生成短时、一次性恢复请求。输出的链接和 JSON 文件属于私密凭证，不能提交仓库或发到公开页面。恢复页面不会自动改变密码；使用者本人填写并提交后，请求失效，旧登录会话也会撤销。生成文件默认不包含在源码仓库中。

## 开源许可

源代码使用 [MIT License](LICENSE)。使用者自行导入的课程视频、教材和其他第三方资料不属于本仓库，也不适用本项目的代码许可。
