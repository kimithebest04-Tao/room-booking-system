# 部署与提交实操手册（四步手把手）

> 适用对象：本项目 
>
> `room-booking-system`
>
>  的作者本人。
> 环境：Windows + PyCharm + 本项目根目录 
>
> `C:\Users\MyFxxkinLife\Doubao\chats\2026-11-21\new-chat`
>
> 。
> 目标：把 "只能在你自己账号里做的事" 一次做完 ——① 云部署（Neon + Render + Streamlit）② Git + GitHub ③ CI 失败→修复演示 ④ 一次真实换库 trial 并留证据。



***

## 0. 先看全局：为什么是这个顺序



```
装 Git ──> 推送 GitHub ──> Neon 建库 ──> Render 部署后端（连 Neon）
                                  │
                                  └──> Streamlit Cloud 部署前端（连 Render）
                                                │
                    CI 失败→修复演示（在 GitHub 上做）──┤
                                                │
                                    换库 trial（在你电脑上跑 CLI）
                                                │
                                     填证据 → 改 README → 提前 3 天邮件
```



* **Git 必须最先**：Render 和 Streamlit 都从 GitHub 仓库拉代码，没有仓库后面两步做不了。

* **Neon 在 Render 之前**：Render 的环境变量需要 Neon 的连接串。

* **换库 trial 最后**：它需要 Render 上已有线上后端、且线上库里已经有预订数据。

* 每一步都有 "完成标志"，没达到就不要往下走。

预计耗时：Git+GitHub 40 分钟｜Neon 15 分钟｜Render 30 分钟（含首次构建）｜Streamlit 15 分钟｜CI 演示 20 分钟｜换库 trial 40 分钟。合计约 2.5–3 小时，建议分两次做。



***

## 第 1 步：装 Git，把代码推上 GitHub

### 1.1 装 Git（你现在这台机器还没有 Git）

PowerShell 里执行：



```
winget install --id Git.Git -e --source winget
```

装完后**关掉当前 PowerShell 窗口重开**（PATH 才会刷新），验证：



```
git --version
```

看到 `git version 2.x.x` 就算成功。若 `winget` 不可用，去 [https://git-scm.com/download/win](https://git-scm.com/download/win) 下载 64-bit 安装包，安装选项一路默认即可。

### 1.2 让 PyCharm 认得 Git

PyCharm → `File` → `Settings` → `Version Control` → `Git`。

`Path to Git executable` 应自动填成 `C:\Program Files\Git\cmd\git.exe`；若为空，手动点 `Test` 选择该文件。测试通过会显示 Git 版本号。

### 1.3 设置提交身份（只需一次）



```
git config --global user.name "Tao Yuhao"
git config --global user.email "你的GitHub注册邮箱"
git config --global init.defaultBranch main
```

### 1.4 在 GitHub 建仓库



1. 登录 [https://github.com](https://github.com) → 右上角 `+` → `New repository`。

2. Repository name：`room-booking-system`。

3. 选 `Public`（老师要能打开，且 Streamlit 免费版要求公开仓库）。

4. **不要**勾 `Add a README` / `.gitignore` / `license`（本地已有文件，勾了会冲突）。

5. `Create repository`，复制页面上 `https://github.com/<你的用户名>/room-booking-system.git` 这一行。

### 1.5 本地初始化并首次推送

在本项目根目录（`C:\Users\MyFxxkinLife\Doubao\chats\2026-11-21\new-chat`）打开 PowerShell：



```
cd "C:\Users\MyFxxkinLife\Doubao\chats\2026-11-21\new-chat"
git init -b main
git add .
git status --short
```

`git status --short`**&#x20;这一步是关键检查**：输出里**不能**出现 `.env`、`.venv/`、`data/`、`logs/`、`backups/`、`maintenance_state/`。这些已在 `.gitignore` 里；如果出现了，说明 `.gitignore` 被改坏，先修好再继续。



```
git commit -m "Room booking system: backend, frontend, maintenance controller, tests"
git remote add origin https://github.com/<你的用户名>/room-booking-system.git
git push -u origin main
```

第一次推送会弹出一个 GitHub 登录窗口（Git for Windows 自带 Credential Manager），点 `Sign in with your browser` 授权即可。若它反而问你用户名密码，密码处粘贴一个 Personal Access Token（GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → 勾 `repo` 权限）。

### 1.6 完成标志



* 浏览器打开 `https://github.com/<你的用户名>/room-booking-system`，能看到 `backend/`、`frontend/`、`maintenance/`、`tests/`、`README.md`。

* 仓库里**看不到** `.env`。

* GitHub 的 `Actions` 标签页自动出现一次运行（CI 已触发）。

> 常见错误：
>
> `remote origin already exists`
>
>  → 先 
>
> `git remote remove origin`
>
>  再加。
> `Updates were rejected`
>
>  → 仓库里有多余文件（多半是建库时勾了 README），用 
>
> `git pull --rebase origin main`
>
>  后再推。



***

## 第 2 步：Neon 建云数据库

### 2.1 建项目



1. 打开 [https://neon.com](https://neon.com)，用 GitHub 账号登录（省一次注册）。

2. 控制台点 `New Project`。

3. 填写：

* Name: `room-booking-prod`

* Postgres version: `17`

* Region: **AWS eu-central-1 (Frankfurt)** —— 离匈牙利最近，Render 也放法兰克福。

1. `Create`。等 10 秒左右，页面出现 `Connection string`。

2. 点复制按钮，得到形如：



```
postgresql://neondb_owner:np_xxxxxxxx@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require
```

这条串**先粘到记事本**，第 3 步要用。

> 代码已经把 
>
> `postgresql://`
>
>  自动改写成 
>
> `postgresql+psycopg://`
>
> （本项目只装了 psycopg 3），所以你
>
> **直接粘 Neon 原串就行**
>
> ，不用手动改前缀；想显式写 
>
> `postgresql+psycopg://...`
>
>  也完全一样能用。

### 2.2 记下 Project ID

Neon 控制台左侧选中该项目 → `Settings` → `General`，复制 `Project ID`（形如 `damp-recipe-88779456`）。第 6 步换库要用。

### 2.3 生成 API Key（换库试验必需）

右上角头像 → `Account settings`（旧界面叫 `Settings`）→ `API keys` → `Create new API key`。



* 名字填 `maintenance-controller`。

* 生成的 token 形如 `napi_xxxxxxxx`，**只显示一次**，立刻复制到记事本。

> 不要用 Organization key 之外的花样，Personal key 就够（本项目所有项目都在你自己的组织下）。

### 2.4 完成标志



* 项目状态 `Active`，能点开 `SQL Editor` 执行 `select 1;` 返回 1。

* 记事本里有：连接串、Project ID、API Key 三样东西。

> 免费额度（2026 年 11 月官方口径）：100 个项目、每项目 100 CU-hours / 月、1 GB 存储、闲置 5 分钟自动挂起（下次查询自动唤醒，约 1 秒）。
>
> **项目数量不是瓶颈**
>
> ，换库试验可以放心建第二个项目。



***

## 第 3 步：Render 部署后端

### 3.1 建 Web Service



1. [https://render.com](https://render.com) 用 GitHub 登录，授权访问你的仓库（只勾 `room-booking-system` 即可）。

2. 右上 `New +` → `Web Service`。

3. 选 `room-booking-system` 仓库 → `Connect`。

4. 填表：



| 字段                 | 值                                                      |
| ------------------ | ------------------------------------------------------ |
| Name               | `room-booking-api`                                     |
| Language / Runtime | `Python 3`                                             |
| Region             | `Frankfurt (EU Central)`                               |
| Branch             | `main`                                                 |
| Build Command      | `pip install -r requirements.txt`                      |
| Start Command      | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type      | `Free`                                                 |



1. 往下展开 `Advanced`，把 `Health Check Path` 填 `/health`。

2. `Environment Variables` 里逐条加（`Add Environment Variable`）：



| Key                  | Value                                          |
| -------------------- | ---------------------------------------------- |
| `DATABASE_URL`       | 第 2 步 Neon 的连接串（原样粘）                           |
| `ADMIN_TOKEN`        | 自己生成的长随机串，例如 `adm_9f3c...`（**记住它**，第 6 步要用同一个） |
| `AUTO_CREATE_SCHEMA` | `true`                                         |
| `PYTHON_VERSION`     | `3.14.10`                                      |

> `PYTHON_VERSION`
>
>  说明：Render 2026-11-03 之后新建的服务默认 Python 是 
>
> **3.14.10**
>
> ；要固定版本必须写全三段（写 
>
> `3.14`
>
>  无效）。本项目在 3.14 上验证过，写 
>
> `3.14.10`
>
>  最稳。不加这个变量也能跑，只是版本随平台漂移。



1. `Create Web Service`，等构建。首次构建 3–6 分钟，日志里看到 `Uvicorn running on http://0.0.0.0:...` 即成功。

### 3.2 验证后端

浏览器打开：



* `https://room-booking-api-0khx.onrender.com/health` → 返回 `{"status":"ok","database":"ok",...}`

* `https://room-booking-api-0khx.onrender.com/docs` → 看到 Swagger 页面，三个业务端点都在

再用 PowerShell 造两条预订（故意留一组首尾相接的边界对）：



```
$body1 = '{"room":"Room A","booker":"Tao Yuhao","start_at":"2026-12-02T09:00:00","end_at":"2026-12-02T10:00:00"}'
Invoke-WebRequest -Uri "https://room-booking-api-0khx.onrender.com/bookings" -Method Post -ContentType "application/json" -Body $body1 | Select-Object -ExpandProperty Content

$body2 = '{"room":"Room A","booker":"Tao Yuhao","start_at":"2026-12-02T10:00:00","end_at":"2026-12-02T11:00:00"}'
Invoke-WebRequest -Uri "https://room-booking-api-0khx.onrender.com/bookings" -Method Post -ContentType "application/json" -Body $body2 | Select-Object -ExpandProperty Content
```

第二条应返回 **201**（首尾相接不算冲突）。再试一条重叠的：



```
$body3 = '{"room":"Room A","booker":"Tao Yuhao","start_at":"2026-12-02T09:30:00","end_at":"2026-12-02T10:30:00"}'
Invoke-WebRequest -Uri "https://room-booking-api-0khx.onrender.com/bookings" -Method Post -ContentType "application/json" -Body $body3 -SkipHttpErrorCheck | Select-Object StatusCode, Content
```

应返回 **409**，且响应体里有 `conflicting_booking_ids`。这两条就是 "数据跨重启存活 + 规则在云端生效" 的现场证据，截图留着。

### 3.3 拿 Render 的 Service ID 和 API Key（换库试验必需）



* **Service ID**：服务页 → `Settings` → 最上方 `Service ID`，形如 `srv-d0xxxxxx`，复制。

* **API Key**：右上头像 → `Account Settings` → `API Keys` → `Create API Key`。新版控制台会让你选 workspace 并勾权限，勾上 **Services（读写）、Deploys、Environment variables**，否则换库时会报 `insufficient_token_scope`。Key 形如 `rnd_xxxx`，也只显示一次。

### 3.4 完成标志



* `/health`、`/docs` 都能打开。

* 201 / 409 两条实测通过。

* 记事本现在有：Neon 连接串、Neon Project ID、Neon API Key、Render URL、Render Service ID、Render API Key、ADMIN\_TOKEN。

> Render 免费实例闲置 15 分钟会休眠，之后第一次请求要等 40–60 秒才醒来。
>
> **答辩演示前先手动打开一次&#x20;**
>
> `/health`
>
> **&#x20;预热**
>
> ，别让老师看着转圈。



***

## 第 4 步：Streamlit Community Cloud 部署前端

### 4.1 部署



1. 打开 [https://share.streamlit.io](https://share.streamlit.io)，用 GitHub 登录。

2. `Create app` → `Deploy a public app from GitHub`。

3. 填：

* Repository: `<你的用户名>/room-booking-system`

* Branch: `main`

* Main file path: `frontend/app.py`

* App URL（子域名）: 例如 `room-booking-tao`

1. 点 `Advanced settings...`：

* **Python version**：选 `3.13`（CI 也是在 3.13 上跑绿的，保持一致最稳；默认 3.12 也能跑，但选已验证的版本更省事）

* **Secrets**（TOML 格式，注意是顶层键）：



```
API_BASE_URL = "https://room-booking-api-0khx.onrender.com"
```

> 顶层键会自动变成环境变量，正好被 
>
> `frontend/app.py`
>
>  里的 
>
> `os.getenv("API_BASE_URL")`
>
>  读到。
>
> **不要写成&#x20;**
>
> `[section]`
>
> **&#x20;下面**
>
> ，那样读不到。



1. `Deploy`。首次装依赖 2–4 分钟。

### 4.2 验证



* 页面左侧栏底部显示 `API: https://room-booking-api-0khx.onrender.com`（如果还显示 `127.0.0.1`，说明 Secrets 没生效：App 右上 `⋮` → `Settings` → `Secrets` 改完点 `Save`，再 `Reboot app`）。

* 在 "Bookings" 页签能看到第 3 步造的三条预订。

* 在 "New booking" 页签新建一条，冲突时界面显示 409 的友好提示（不是 traceback）。

* 在 "Utilization" 页签看到 Room A 的柱状图。

> 界面语言：课程为英语授课，前端已是纯英文（页签 New booking / Bookings / Utilization，
> 按钮 "Book the room"，侧栏 "How to use" 与 "Backend healthy · v1.0.0"）。

### 4.3 完成标志



* 公网 URL `https://<你的子域名>.streamlit.app` 可访问，且能创建 / 查询 / 看图表。

* 复制这个 URL，交作业要用。



***

## 第 5 步：CI "失败→修复" 演示（这份作业的硬性证据）

原理：`.github/workflows/ci.yml` 在每次 push 都跑 `python -m pytest -q`。我们要**故意改坏区间判断的一个字符**，让边界用例失败，拿到一次红灯 run；再改回来，拿到绿灯 run。两次 run 的 URL 就是 `docs/bug-fix-demo.md` 里的证据。

### 5.1 拉一个演示分支



```
cd "C:\Users\MyFxxkinLife\Doubao\chats\2026-11-21\new-chat"
git checkout -b demo/ci-failure
```

### 5.2 制造真实缺陷

打开 `backend/processing.py`，找到 `intervals_overlap` 函数，把这一行：



```
return start_a < end_b and start_b < end_a
```

改成（只把第一个 `<` 换成 `<=`）：



```
return start_a <= end_b and start_b < end_a
```

这会破坏 "首尾相接不算冲突" 的边界规则，属于真实逻辑缺陷，不是跳过测试。



```
git add backend/processing.py
git commit -m "demo: boundary condition regression for the CI failure evidence"
git push -u origin demo/ci-failure
```

### 5.3 抓红灯



1. 打开 GitHub 仓库 → `Actions` 标签页 → 最新一次运行（提交信息含 `boundary condition regression`）。

2. 点进去，`test` 这一步变红，展开日志，你能看到（我在本地实测过这条缺陷的失败形态）：



```
FAILED tests/test_domain_rule.py::test_overlap_rule_matches_the_hand_computed_table[09:00-10:00-08:00-09:00-False]
1 failed, 30 passed
```

失败的就是 "已有 9:00–10:00、新订 8:00–9:00 首尾相接" 那一格：`<=` 把它误判成冲突。

（只改第一个 `<` 时，另一个方向的边界用例仍通过，所以只红 1 条 —— 这恰好说明手算表按方向覆盖了边界。）



1. **复制这个 run 的完整 URL**（形如 `https://github.com/<你>/room-booking-system/actions/runs/1234567890`），粘到记事本 `红灯URL`。

### 5.4 修复并抓绿灯



```
git revert --no-edit HEAD
git push
```

`git revert` 会生成一条 "反向提交"，历史里能清楚看到 "引入缺陷 → 修复缺陷"，比强推干净，也符合 "修复后成功 run" 的要求。

等 Actions 变绿（约 1–2 分钟），复制这次 run 的 URL 粘到记事本 `绿灯URL`。

### 5.5 合并回 main

GitHub 上会提示 `demo/ci-failure had recent pushes` → `Compare & pull request` → 标题写 `Revert the boundary regression` → `Create pull request` → `Merge pull request`。

合并后 main 上的代码就是原样（缺陷已回滚），CI 在 main 上也是绿的。

### 5.6 填证据文档

打开 `docs/bug-fix-demo.md`，把里面的 `<paste ...>` 占位替换成：



* 红灯 URL、失败测试名、失败断言那几行日志

* 绿灯 URL、`31 passed` 那一行

* 两次提交的 commit hash（`git log --oneline -5` 里能看）

* 一句结论：缺陷是边界条件（`<` 写成 `<=`），测试用 parametrize 的手算表直接抓住

### 5.7 完成标志



* Actions 里能同时看到一红一绿两次 run，且都在 `demo/ci-failure` 分支历史里。

* `docs/bug-fix-demo.md` 没有 `<paste` 字样。

* 本地 `git status` 干净，`main` 分支测试仍 31 passed。



***

## 第 6 步：真实换库 trial（最容易被卡的一步，按顺序来）

这一步是维护控制器在你电脑上**真实**调用 Neon 和 Render 的 API：停写 → 备份 → 建新库 → 恢复并校验 → 改 Render 的 `DATABASE_URL` 并重新部署 → 通过 API 验证数据 → 解除停写。

### 6.1 准备 `.env`

项目根的 `.env`（没有就从 `.env.example` 复制一份）：



```
# 本地后端仍用 SQLite，不要改成 Neon
DATABASE_URL=sqlite:///./data/app.db

# 必须与 Render 上的 ADMIN_TOKEN 完全一致
ADMIN_TOKEN=adm_你第3步生成的那个

# Neon
NEON_API_KEY=napi_你的key
NEON_PROJECT_ID=damp-recipe-88779456
NEON_PROJECT_NAME=room-booking-prod
NEON_REGION_ID=aws-eu-central-1
NEON_PG_VERSION=17

# Render
RENDER_API_KEY=rnd_你的key
RENDER_SERVICE_ID=srv-你的服务ID

# 控制器要调的线上后端
BACKEND_BASE_URL=https://room-booking-api-0khx.onrender.com

MAINTENANCE_EXPIRY_WARNING_HOURS=72
```

> 注意：这里的 
>
> `DATABASE_URL`
>
>  是给你
>
> **本地**
>
> 后端用的，控制器备份用的是从 Neon API 读到的线上实例连接串，两者不冲突，保持 sqlite 即可。

### 6.2 先做一次只读体检



```
cd "C:\Users\MyFxxkinLife\Doubao\chats\2026-11-21\new-chat"
.\.venv\Scripts\Activate.ps1
python -m maintenance.run check
```

期望输出一段 JSON：`instance_id` 是你的 Neon 项目 ID、`backend_ok: true`、`maintenance_mode: false`、`bookings: 3`（第 3 步造的数据条数）。

这一步同时验证了三件事：Neon key 有效、Render 后端可达、`ADMIN_TOKEN` 对得上。**只要有任何一项不对，先把这里修好，不要往下跑 replace。**

### 6.3 跑替换



```
python -m maintenance.run replace --reason "documented maintenance trial" --confirm
```

不加 `--confirm` 会直接拒绝执行（这是防误操作的设计）。

它会依次打印并写入 `logs/maintenance.log`：



1. `Writes are blocked` —— 后端进入维护模式，此时 `POST /bookings` 返回 503（可以另开窗口验证一下，这是 "停写" 证据）

2. `Created provider instance room-booking-prod-<runid>` —— 新 Neon 项目

3. `Restore verified: {...}` —— 记录数、内容摘要、规则快照全部一致

4. `Identifier generation verified: new id N > restored max M` —— 恢复后自增 ID 不会撞

5. `Updated environment variable DATABASE_URL` + `Deployment ... finished with status live`

6. `Post-switch verification passed for instance <新ID>`

7. `Maintenance mode released`

全程 3–8 分钟，取决于 Render 重新构建的速度。**中途不要关窗口。**

成功时最后打印一段 JSON，`"status": "completed"`，里面记着 `run_id`、`backup_id`、`new_instance_id`、`deploy_id`。

### 6.4 收尾两件事（很容易忘）



1. **更新&#x20;**`.env`**&#x20;的&#x20;**`NEON_PROJECT_ID`：改成上面 JSON 里的 `new_instance_id`。否则下次 `check` 还在读旧项目。

2. **删除旧实例（可选）**：确认新库一切正常后，`python -m maintenance.run replace --reason "cleanup" --confirm --delete-source` 会在成功跑完后删掉源实例；或者直接在 Neon 控制台手动删。答辩时 "新旧并存" 也完全可以，不删更安全。

### 6.5 验证换库真的生效



```
python -m maintenance.run check
```

`instance_id` 应该已经是**新**项目 ID，`bookings` 条数不变。

再到线上后端手工验证三条规则（和第 3 步一样）：



* 原有时间段再订一次 → **409**

* Room A `10:00–11:00`（首尾相接）→ **201**

* `end < start` → **422**

结果与换库前完全一致，就是 "恢复后规则同结果" 的证据。

### 6.6 失败时怎么读、怎么继续

先看 `python -m maintenance.run state`，它打印 `run_state.json`：`status`、已完成的 `steps`、`message`。



| 报错关键字                                                       | 含义                        | 处理                                                                                                            |
| ----------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `rejected the API key`                                      | Neon/Render key 错、过期或权限不够 | 换 key 重跑 `check`                                                                                              |
| `insufficient_token_scope`                                  | Render 新 key 权限没勾全        | 重建 key，勾 Services/Deploys/Env vars                                                                            |
| `refused the new instance because of a plan or quota limit` | 项目数 / 额度到顶                | 免费额度 100 项目，先查 Neon 用量                                                                                        |
| `Instance creation ended in an unknown state`               | 创建结果不明                    | **不要手动删 / 重建**；去 Neon 控制台看有没有 `room-booking-prod-<runid>`，有就 `python -m maintenance.run resume --confirm` 复用它 |
| `Restored data does not match`                              | 恢复校验没过                    | 保持维护模式，看 `backups/<backup_id>/manifest.json` 与日志，人工排查                                                         |
| `Deployment ... finished with status build_failed`          | 新代码 / 依赖在云端构建失败           | 看 Render 构建日志；修好后 `resume --confirm`（不会重复建库）                                                                  |

**失败时的行为是设计好的**：写操作保持关闭（503）、备份留在 `backups/`、状态文件可续跑 —— 这正是作业第 6 节要求的 "部分失败要独立、可重试、不猜"。如果失败后想恢复正常服务，先 `python -m maintenance.run resume --confirm` 续跑；实在要放弃，用 admin 端点手动解除停写：



```
Invoke-WebRequest -Uri "https://room-booking-api-0khx.onrender.com/admin/maintenance" -Method Post `
  -Headers @{"X-Admin-Token"="adm_你的token"} -ContentType "application/json" -Body '{"enabled": false}'
```

### 6.7 把证据写进 `docs/maintenance-trial.md`

需要粘进去的（**贴之前把 key/token/ 连接串里的密码删掉**）：



* `check` 前后的 JSON（`instance_id` 从旧变新、`bookings` 条数不变）

* `logs/maintenance.log` 里 6.3 列的 7 条关键行

* `cat backups/<backup_id>/manifest.json`（含创建时间、源实例 ID、SHA-256、行数）

* `python -m maintenance.run state` 的完整 JSON

* 三条规则复测结果（409 / 201 / 422）

* Neon 控制台新旧两个项目的截图（新项目名带 runid）、Render 环境变量里 `DATABASE_URL` 已指向新主机的截图

* 一段结论：写限制跨重部署仍然生效（切换后 `GET /health` 里 `maintenance_mode` 仍为 true，直到验证通过才释放）

### 6.8 完成标志



* `run_state.json` 里 `status: completed`，`steps` 含 `writes_blocked / backup_created / instance_created / restored / backend_switched / content_verified / probe_write_verified / maintenance_released`。

* 线上后端 `instance` 已换新，数据条数与规则行为不变。

* `docs/maintenance-trial.md` 无 `<paste` 占位。



***

## 第 7 步：交作业前的最后 4 件事



1. ~~改 README 的云链接~~ **已完成**：README 顶部已是三条真实地址（2026-11-23）。

2. **改 README 的 AI 使用声明**（第 8 节）：按你的真实情况写清哪部分用了 AI 辅助、你能解释哪些设计，并写明 "代码在答辩现场可被修改"。

3. ~~重跑一遍本地测试并更新 README 记录~~ **已完成**：`python -m pytest` → `31 passed, 1 warning`，README 已同步。

4. **提前 ≥3 天发邮件**到 `kovacs.adam.eke@gmail.com`，三个链接齐全、能打开（发之前自己点一遍）。

### 访问确认（结论：无需改动，公开可访问）

2026-11-23 用**无痕浏览器会话**打开 `https://room-booking-tao.streamlit.app/`，
页面完整渲染（左侧 `API: https://room-booking-api-0khx.onrender.com`、
`Backend healthy · v1.0.0`，三个页签 New booking / Bookings / Utilization 正常），
且没有出现开发者专属的 `Manage app` 按钮，
说明访客身份即可直接使用。App 的 `Sharing` 为 **"This app is public and searchable"**，
与预期一致，**不需要再改任何设置**。

注意：用脚本直接 `Invoke-WebRequest` / `curl` 访问这个域名会得到 **303 跳到
`share.streamlit.io/-/auth/app`**。这是 Streamlit 对非浏览器请求的统一鉴权拦截，
不是私有应用的标志——同一时间请求官方 gallery 里三个公认公开的应用
（`30days.streamlit.app`、`30daysofai.streamlit.app`、`adventure.streamlit.app`）
同样全部 303。判断能否公开访问，必须以真实浏览器的结果为准。

发邮件前只需自己做一次最终确认：开一个无痕窗口点开三个链接，能看到预约表单即可。

邮件模板（英文，正式简短）：



```
Subject: Python Project submission links - Tao Yuhao (FCESKQ)

Dear Dr. Kovacs,

I would like to submit the links for my Python project (room booking system):

- GitHub repository: https://github.com/kimithebest04-Tao/room-booking-system
- Backend (Render): https://room-booking-api-0khx.onrender.com/docs
- Frontend (Streamlit): https://room-booking-tao.streamlit.app/

The repository contains the README (application, usage, maintenance, verification,
sources and extensions, design decisions), the single pytest suite, the GitHub
Actions workflow with one failing and one fixed run, and the documented database
replacement trial in docs/maintenance-trial.md.

The Render free instance sleeps when idle, so the first request may take about a
minute to wake up. I am happy to answer any questions before the demo.

Best regards,
Tao Yuhao
```



***

## 附：Week 13 现场 15 分钟演示提纲（照着念就行）



| 时间        | 内容                                                           | 现场动作                                          |
| --------- | ------------------------------------------------------------ | --------------------------------------------- |
| 0–2 min   | 主题与架构：FastAPI + Streamlit + SQLAlchemy，前端只调 API 不碰库          | 指 `/docs` 与 Streamlit 页面                      |
| 2–5 min   | 领域规则：半开区间，首尾相接允许                                             | 现场造 09–10、10–11、09:30–10:30 三条，展示 201/201/409 |
| 5–8 min   | 三范式：过程式在工作流、函数式在 `processing.py` 纯函数、OOP 在 `BookingService`  | 打开对应文件各指一处                                    |
| 8–10 min  | 测试与 CI：31 项、parametrize 手算表、一红一绿两次 run                       | 打开 Actions 两次 run                             |
| 10–13 min | 维护与换库：五步流程、写限制、备份校验、真实 trial 日志                              | 打开 `docs/maintenance-trial.md`                |
| 13–15 min | 现场小改动：老师随机要求（如把 `CLOSE_HOUR` 改 18 或把 `<` 改 `<=`），当场跑测试看红 / 绿 | 改完 `python -m pytest -q`                      |

现场改动前先 `git status` 确认干净，改完能一键 `git checkout -- .` 还原。



***

## 常见坑速查



| 现象                                                          | 原因                                       | 解决                                             |
| ----------------------------------------------------------- | ---------------------------------------- | ---------------------------------------------- |
| Render 构建报 `No module named psycopg2`                       | `DATABASE_URL` 是 `postgresql://` 且代码未规范化 | 本项目已自动改写；若你手动改过 `db.py`，确认 `_normalise_url` 还在 |
| Streamlit 显示 `无法连接后端 127.0.0.1`                             | Secrets 没生效或键名写错                         | `API_BASE_URL` 必须是顶层键，改完 `Reboot app`          |
| Streamlit 部署报 `ModuleNotFoundError: backend`                | Main file path 填错                        | 必须是 `frontend/app.py`，且仓库根有 `requirements.txt` |
| `python -m maintenance.run check` 报 `NEON_API_KEY is empty` | `.env` 没被读到                              | 必须在项目根目录执行，且文件名就是 `.env`（不是 `.env.txt`）        |
| Render 上 `POST /bookings` 返回 503                            | 维护模式还开着（trial 失败后遗留）                     | 用 6.6 的 admin 命令解除                             |
| 换库后 `check` 还显示旧实例                                          | `.env` 的 `NEON_PROJECT_ID` 没更新           | 改成 `new_instance_id`                           |
| GitHub Actions 报 `Permission denied` 推不上去                   | 凭据失效                                     | `git credential-manager` 重新登录，或用 PAT           |