# PQ-RAG · 电能质量监测知识问答系统

**Power Quality RAG**：面向电能质量监测现场的知识问答项目，围绕设备原理、检测排查建议和客户解释话术，探索可追溯的检索增强生成。

这是正在开发的源码快照。项目从既有知识库中台演进而来，因此前端仍保留“知序”和企业知识库演示内容；PQ 专用问答界面与端到端效果验收尚未完成。请勿将演示答案用于现场诊断、合规判断或设备选型。

## 当前包含什么

| 部分 | 当前状态 |
| --- | --- |
| React / TypeScript / Ant Design 前端 | 可独立运行的中台交互演示，默认不调用模型 |
| FastAPI / SQLAlchemy / PostgreSQL + pgvector 后端 | 文档处理、模型接入、检索及中台 API 源码 |
| 电能质量领域模块 | 来源预览、快照、分块、检索、引用和本机访问控制的开发实现 |
| 联网补证 | 包含离线协议与错误处理测试，真实执行仍关闭 |
| PQ 完整闭环 | 开发中；生成、正式界面及业务效果未完成验收 |

领域发布校验目前绑定首轮固定快照规模（12 张卡、230 条向量、25 条历史调用记录）。本仓库不附带该私有语料和记录，因此**干净下载不能直接复现该快照的生效或完整 PQ 问答**。公开测试覆盖合成夹具和无模型逻辑，不代替真实模型或业务验收。

## 先运行前端演示

需要 Node.js 24 LTS（Vite 要求 Node.js 20.19+ 或 22.12+）。

```sh
git clone https://github.com/PerpperQAQ/power-quality-rag.git
cd power-quality-rag
npm ci
npm run dev
```

打开终端显示的 `http://127.0.0.1:5173`，保持“演示”模式即可体验。数据保存在当前浏览器中，不需要模型 Key 或后端。费用、报销制度、员工手册、运行日志和回答均为界面演示样例。

已有同源浏览器数据可能保留“真实后端”选择；可使用新的浏览器配置或切回“演示”。不要将演示角色选择器当作身份认证。

## 后端开发

当前本机启动器使用 POSIX 文件权限和 `fcntl`，支持 macOS / Linux；Windows 可使用 WSL。建议 Python 3.12、PostgreSQL 16+，并为 PostgreSQL 安装 pgvector。

先阅读 [第三方许可说明](THIRD_PARTY_NOTICES.md)。PyMuPDF 是当前必装依赖，采用 AGPL / 商业双授权；项目自有代码的 MIT 许可不会替代它的条款。

创建虚拟环境：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e './backend[test]'
```

准备一个自己管理的**新空数据库**，并确保连接角色可以创建 `vector` 扩展。显式配置连接和独立数据目录；下面的数据库值是待替换占位符：

```sh
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/NEW_DATABASE'
export DATA_DIR="$PWD/.runtime/public-dev"
export WORKER_IN_API=false
.venv/bin/python scripts/serve-power-quality.py --port 8000
```

启动器只绑定 loopback，强制开启本机单操作者认证并关闭代理头信任。后台任务默认在上述命令中关闭；这不是全应用的网络隔离，之后主动配置供应商并执行请求仍可能产生费用。

- `GET http://127.0.0.1:8000/api/health` 可检查服务入口。
- `http://127.0.0.1:8000/local-session` 为本机登录页；凭证在指定 `DATA_DIR/pq-operator.key` 中生成，权限为 `0600`。仅在自己的本机使用，勿提交、截图或分享凭证。
- 本机模式要求同源请求。开发端口 5173 的代理不作为该模式的登录入口；如需查看前端，请先 `npm run build`，再从后端同源地址访问。该页面仍是既有中台界面，PQ 专用前端尚未完成。
- 如需预览自己的来源，可在启动前设置 `POWER_QUALITY_SOURCE_ROOT`；应用将该目录作为只读来源。它不会解除固定快照的发布限制。

不要把此开发服务直接暴露到公网或共享网络。配置模块会读取 `backend/.env`；仓库不提供该文件，优先使用上述显式环境变量。

## 无模型验证

```sh
npm run check
npm run test:domain
npm run test:sync
npm run test:suggestions
npm run test:answers
npm run test:observability
npm run build
```

后端公开测试使用 `httpx.MockTransport`，不连接数据库、模型或搜索服务：

```sh
pq_test_dir=$(mktemp -d)
DATA_DIR="$pq_test_dir" WORKER_IN_API=false \
DATABASE_URL='postgresql+psycopg://unused@127.0.0.1:1/unused' \
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
```

仅在确认不需要后手动清理这次生成的 `pq_test_dir`。公开包只含可自包含运行的测试子集；私有语料、历史验收脚本和完整业务验收不在仓库中。验证范围与结果见 [公开快照说明](docs/PUBLIC_SNAPSHOT.md)。

## 目录

```text
src/                         中台前端和演示数据
backend/app/                 通用后端
backend/app/power_quality/   电能质量领域模块
backend/tests/              公开的离线协议测试
scripts/                    本机安全启动入口及前端逻辑检查
docs/                       公开说明
```

`package.json`、后端包名、存储键和部分界面仍使用兼容名称；不会因为仓库改名自动迁移现有数据。模型目录是开发时的参考快照，不保证模型名称、供应商可用性或价格当前有效。

## 公开范围与许可证

本仓库不包含 APF 原件、客户资料、知识快照、向量、数据库、备份、问答记录、云端密钥、密文、本机配置、内部飞书材料或历史执行证据。使用者应自行准备有权使用的资料和模型配置。

自有代码采用 [MIT](LICENSE)。依赖保留各自许可证；RAGFlow 参考来源及 PyMuPDF 等的许可边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本次发布源码，不附带依赖二进制、容器镜像或打包安装程序。
