# 自进化代码迭代项目

本项目把评测反馈连接到代码修改，形成“代码推送 → Jenkins 回归 → 任务文件 → Claude Code 修改 → 人工审核 → 下一轮回归”的自进化闭环。回归用例由 Jenkins 流水线配置；Agent 接收任务文件，在同一个本地 Claude Code 会话中持续处理项目改进。

服务启动真正的交互式 CC 窗口，发送可编辑的初始 prompt 并保存会话 ID。HTTP 收到文件后自动交给 CC 执行，忙碌时排队；执行过程在窗口可见，文件和结果可在本地核对。当前使用 DeepSeek Flash。

## 两个仓库与目录

Agent 服务和目标项目各自使用独立 Git 仓库。你可以进入 CC 后在对话中指定仓库，目录使用实际项目名。`--project` 和 `--repo` 是可选的启动快捷方式。放在 Agent 仓库内的目标目录需加入外层 `.gitignore`。

```text
Agent 仓库/
├── service.py          # Agent 接收与调度服务
├── native_console.py   # 原生启动 CC，仅注入 HTTP 任务文本
├── initial_prompt.txt  # 初始项目目标、上下文和工作规则
├── CLAUDE.md           # 给维护本项目的 Agent 看
├── start.cmd           # Windows 启动入口
├── requirements.txt    # Agent 服务的 Python 依赖
├── tests/              # 只测试 Agent 服务自身
├── .venv/              # Agent 服务环境
├── task/               # HTTP 接收到的任务文件
├── runtime/            # 会话、接口口令、日志、执行结果
└── 你的项目名/         # 独立的目标项目仓库
    ├── .git/
    ├── 项目源码
    ├── 项目测试
    └── 项目自己的依赖与运行环境
```

项目代码、测试和环境均在目标项目目录内维护。外层 `tests` 和 `.venv` 属于 Agent 服务。`task` 是接收目录，里面的文件原样保存；服务不会自动生成加减法等示例项目。

## 如何使用

### 1. 配置初始 prompt

编辑 `initial_prompt.txt`，填写 Agent 的目标、工作边界和交付要求。服务启动时读取它，建立 CC 上下文，然后接受手动对话和任务文件。目标仓库可以在进入窗口后告诉 Agent。

初始化只定义工作方式。具体要求可以直接在 CC 输入，也可以放在上传的任务文件里。普通重启续接原会话，保留对话中的仓库选择；续接时不会把初始化内容作为新消息重发。在同一会话内通过对话指定其他仓库可以继续操作；启动时显式选择不同目录会建立新会话。

### 2. 打开 CC，直接告诉它仓库在哪

在 Agent 目录的 CMD 中运行：

```cmd
start.cmd
```

首次启动无需预选仓库，也不会创建业务项目。从 CC 窗口直接输入，例如：

```text
我的项目在 C:\work\my-project，请先读 README，再帮我修复测试失败。
```

也可以说“请把这个 Git 仓库克隆到 C:\work\my-project，然后处理……”。Agent 按你的要求选择和操作目标仓库；访问启动目录以外的项目时，按 CC 的正常权限提示操作，必要时使用 `/add-dir C:\work\my-project`。后续 `start.cmd` 续接原会话。已有保存的启动目录会保留，但你仍可以在对话里指定其他仓库。

如果希望启动时就预选项目，可以用 `start.cmd --project "C:\work\你的项目名"`。该目录不存在时创建；不是独立 Git 仓库时执行 `git init`，现有代码保留。已有独立仓库直接复用。

也可以在启动时从远程仓库克隆：

```cmd
start.cmd --repo "https://你的Git服务器/组织/目标项目.git"
```

未指定 `--project` 时，克隆目录取仓库名，上例为 `目标项目`。克隆只用于空目录。已有代码的非 Git 目录不会被覆盖；已有独立 Git 仓库直接复用，`--repo` 不会替换其远程地址或重建项目。

也可以指定已有项目和其他配置：

```cmd
start.cmd --project "C:\work\target-project"
start.cmd --prompt-file "C:\work\project-prompt.txt" --port 8765
```

看到 CC 回复 `READY` 后即可发送任务。初始化期间收到的任务会等待初始化完成。

CC 直接使用当前 CMD 的原生输入输出，手动对话、Shift+Tab、鼠标滚动、复制粘贴和窗口尺寸变化均由 CC 与终端处理。Python 不读取或转换你的按键、鼠标，也不重绘 CC 界面；它只在空闲时将 HTTP 任务文本输入同一会话。手动输入前可通过 `/state` 确认没有待执行的上传任务，避免输入时被自动任务打断。更新服务文件后需关闭旧服务窗口并重新运行 `start.cmd`，当前进程不会自动加载新代码。

### 3. 用 Postman 传文件

| 设置 | 值 |
|---|---|
| Method | `POST` |
| URL | `http://127.0.0.1:8765/tasks` |
| Header `X-CC-Token` | 复制 `runtime/http-token.txt` 的完整内容 |
| Header `Content-Type` | `text/plain; charset=utf-8` |
| Body | **binary**，选择任务文件 |

文件应是 UTF-8 文本，最大 64 KB，可以包含开发要求、评测报告或相关代码片段。使用 binary 发送文件原始内容。[Postman 操作说明](https://learning.postman.com/docs/use/send-requests/create-requests/parameters/)

点击 **Send** 后服务返回 HTTP `202`：

```json
{"task_id":"任务ID","session_id":"会话ID","status":"queued"}
```

任务自动进入 `queued → running → completed/failed`。`202` 表示已接收，实际执行结果以状态和项目文件为准。

`X-CC-Token` 是本服务生成的上传接口口令，与 CC 的模型 API 密钥无关。可以交给调用接口的人或 Jenkins；每次启动会重新生成。

如需其他机器上传，服务启动时选择网络监听：

```cmd
start.cmd --host 0.0.0.0 --port 8765
```

调用方使用 `http://服务机器的IP:8765/tasks`，并携带上述口令；服务机器的端口需能被调用方访问。

### 4. 自己核对执行过程

- `task/{任务ID}.txt`：收到的原始文件。
- `runtime/results/{任务ID}.json`：最终状态和 CC 回复。
- CC 窗口：查看原生执行过程；完整对话日志位于 `runtime/native-session.json` 的 `transcript_path` 指向的 CC 原生日志文件。
- `runtime/events.jsonl`：会话与任务提交事件。
- 目标项目目录：检查实际代码和 Git 差异。

用 `GET http://127.0.0.1:8765/state` 查看会话 ID 和任务状态。返回的 `project` 是启动时预选的目录，未预选时为 `null`；在对话里指定或切换的业务仓库由 CC 的会话上下文管理，以 CC 回复和实际文件为准。Jenkins 使用同一个 `POST /tasks` 接口发送报告；改动在目标仓库内保留，人工审核后再推送进入下一轮回归。

### 5. 重新初始化会话

如需主动重新初始化，关闭服务窗口后删除 `runtime`，再启动会生成新会话和运行记录。目标项目的代码与 Git 仓库保留；CC 自己的历史记录仍由本机 CC 管理。

## 如何安装

当前版本运行在 Windows，使用 Python 3.10+、Git，以及通过 npm 安装的 Claude Code。npm 版 CC 需要 Node.js 22+。脚本当前按 npm 安装目录定位 `claude.exe`。[Claude Code 安装文档](https://code.claude.com/docs/en/setup#install-with-npm)

### 1. 安装并配置 CC

```cmd
npm install -g @anthropic-ai/claude-code
claude --version
```

先确认你在本机单独运行 `claude` 能正常调用模型。本服务直接复用本机 CC 的认证与用户配置。

新配置 DeepSeek 时，可在 PowerShell 设置：

```powershell
$env:ANTHROPIC_BASE_URL = 'https://api.deepseek.com/anthropic'
$env:ANTHROPIC_AUTH_TOKEN = Read-Host 'DeepSeek API Key'
$env:ANTHROPIC_MODEL = 'deepseek-flash'
claude
```

服务从配置好的终端启动，或使用你已经配置成功的 CC 用户设置。API 密钥不写入任务文件或项目源码。[DeepSeek 的 CC 接入说明](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)

### 2. 安装 Agent 服务环境

在 Agent 仓库目录运行：

```cmd
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
start.cmd
```

Agent 服务仅使用 Python 标准库，`requirements.txt` 无第三方包。现有环境里已安装的包可以保留；目标项目的依赖按该项目自己的说明安装在其目录内，不安装到 Agent 的 `.venv`。

### 3. 测试 Agent 服务

```cmd
.venv\Scripts\python.exe -m unittest discover -s tests
```

目标项目测试从项目自己的环境运行，按该项目 README 选择测试命令。例如使用 unittest 的项目：

```cmd
cd 你的项目名
.venv\Scripts\python.exe -m unittest discover -s tests
```
