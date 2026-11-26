# 绒星镶坊·珠宝镶嵌工票转版

预检票号对照规则变化，定位同间重名并核验旧票保留和别名换发方案。

## 业务流程

1. 转版审阅
2. 旧票卷宗
3. 目标试写
4. 改号交接

MySQL 真实排序规则分组、目标唯一键试写和别名碰撞重试。

制签组过去用表格不区分工作间地搜索重名，容易漏掉重音等价、覆盖老票索引，也可能让新别名撞到早已使用的票。上游接收工作间旧票台账，下游由镶嵌技师用工票 ID 找原票并领取新标。

13:00 打开 setting-tickets-north-1124.json 对应的冬展卷。13:04 核对北间 N001 与 N002。13:07 设置允许改号 6 张。13:10 执行试写。13:12 核对 2 组必须改号及西间 W001 保留。13:16 确认 N002 改为 R-N002-2。13:20 导出交接单给制签员。

源卷唯一键使用 utf8mb4_0900_bin。目标中工作间仍按二进制排序规则区分，票号切换到 utf8mb4_0900_ai_ci。先在真实 MySQL 用目标规则分组，从每组取登记最早、ID 最小的票保留。对其余票用目标唯一索引实际拒绝原号，按固定候选序列预留可用别名。演练表在同一会话隔离并自动清除，只产出交接证据。

## 启动

```sh
cp -n .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
```

打开 <http://localhost:18313>。OpenAPI 在 <http://localhost:19313/docs>，数据库映射到本机 17813。所有暴露端口只绑定回环地址。Compose 启动 frontend、backend、worker、db、redis，各自通过健康探针检查。数据库、Redis 和共享产物目录各有命名卷；任务状态及导出内容以数据库中的证据为准。

首次启动时幂等载入演示数据。手动载入可运行 `docker compose exec -T backend python -m scripts.seed_demo`。常规停止可用 `docker compose down`。验收环境清理才加 `-v`，它会删除本项目的数据卷。

## 角色与交接

演示账号共用口令 `Demo-DbWorkbench-2026`。

| 登录名 | 角色 | 可执行操作 |
|---|---|---|
| operator | 资料操作员 | 登记业务资料、设置「本次允许改号张数」、提交、查看和下载结果 |
| reviewer | 业务复核员 | 查看执行证据、签署单次复核、下载交接单 |
| viewer | 只读观察员 | 查看资料、执行结果及审计，下载证据 |

选用「setting-tickets-north-1124.json」对应的演示资料，保存一份复演资料，再以默认参数 6 提交。worker 保存结果摘要、业务明细和 SHA-256。复核员登录后在「改号交接」签署。

## 开发说明

| 部分 | 实现 |
|---|---|
| 前端 | 原生 HTML、CSS、JavaScript ES Modules、Nginx |
| 后端 | Python 3.11、FastAPI、SQLAlchemy、JWT |
| 数据库 | MySQL 8.0 |
| 后台执行 | Redis 通知、数据库持久任务队列和行锁、Python worker |

从 `backend/src/domain/scenario.py` 修改业务入口和演示口径，运行 `docker compose up -d --build --wait` 查看变更。数据库表结构在 `backend/src/models` 和 `backend/migrations`，输入模型在 `backend/src/schemas`。业务页和请求接口在 `frontend/src/pages`、`frontend/src/api`。资料、执行、复核三个工作流实体各有独立表和 API；场景输入及结果保留在所属资料和批次中。

角色枚举在 `backend/src/constants/roles.py`、`frontend/src/constants/roles.js`，用于服务端访问控制、账号初始化、前端导航与签署。批次状态在 `backend/src/constants/job_state.py`、`frontend/src/constants/states.js`，用于队列、状态 API、状态标记与轮询。登记、提交、完成和签署事件写入审计表。

## 保留期整理与交接单归档

试写批次在结束（`done`/`failed`）满 `RETENTION_DAYS`（默认 90 天，即满三个月）后纳入整理，`queued` 等在途批次不受影响：

- **已签署批次**：把交接单（含批次号、SHA-256 校验值和复核签署）以规范化 JSON 写到归档盘临时区，重新解析并校验副本内结果的 SHA-256 与库内 `digest` 一致后，才原子改名到 `archive/handover-<批次号>.json`，并在 `archives` 台账登记为 `verified`，随后清空库内 `result`，批次状态置 `archived`。校验不过则删除半成品、保留库内原件并报错，下轮可安全重试。
- **未签署批次**：在事务内锁定批次并复查仍无签署后，清空 `result`/`digest`/`failure`，状态置 `cleared`，批次记录保留。
- **并发签署裁决**：签署与清理都在事务内对 `runs` 行加锁并复查。先签成的批次转归档；批次先被清理后，复核员的补签请求返回 409 并说明「结果已按保留期清理，无法补签」。
- **断点恢复 / 不重复存档**：归档台账分 `staged`、`verified` 两阶段。中断后重跑只核验已存在的副本或重新生成丢失的临时件，已 `verified` 的批次直接幂等收口；`run_id` 与 `digest` 各有唯一约束，同一交接单不会重复存档。
- **原入口下载不变**：`GET /runs/{id}/export` 对 `archived` 批次改为从归档盘读取并核验，返回内容（批次号、校验值、签署、文件名）与库内原件逐字节一致；`cleared` 批次返回 409。

复核员可通过 `POST /maintenance/retention-sweep` 触发整理、`GET /maintenance/archives` 查看归档台账，也可在容器内运行 `python -m scripts.retention_sweep`。归档文件落在 `evidence` 命名卷的 `archive/` 目录，临时半成品在 `archive/.staging/`。


`COMPOSE_PROJECT_NAME` 设置栈名，`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 设置端口。通过 `DB_PASSWORD`、`JWT_SECRET`、`DEMO_PASSWORD` 配置本地凭据。设置 `SEED_DEMO=false` 可关闭演示数据载入，此时数据库初始没有演示账号。数据库密码调整后，已有数据卷需在数据库内同步修改用户密码。

## 验收范围

按 MySQL 目标排序规则试写临时表，通过后导出工票改号建议。 当前界面用 JSON 编辑业务资料，以 JSON 下载证据。过程和截图保存在批次的 [验收记录](../../test-artifacts/gb-6112/TESTING.md)。

[MIT License](LICENSE)
