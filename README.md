# AI Collab Hub

给自己的 AI 同事建一个协作群：**在 ChatGPT 对话中确定方案与分工，通过 MCP 直接派发任务；分布在不同设备上的 Agent 认领执行，并将进度与产出统一汇总。**

AI Collab Hub 是一个基于 MIT 协议开源、支持完全自托管的聊天与任务管理平台，提供 Web 端以及原生 iPhone、Mac 客户端。模型账号、执行设备与服务地址均由使用者自行提供。复刻部署会生成独立的数据、凭据与成员，不会使用发布者的服务器，也不会自动连接发布者的任何设备或 Agent。

![AI Collab Hub 合成协作演示](docs/images/synthetic-demo.png)

> **演示说明**：配图使用虚构对话与模拟角色（Coordinator、Worker A、Worker B），未调用真实模型。可在本机运行 `python3 tools/demo.py` 复现该界面；配图不代表真实模型的执行效果。

---

## 核心用途

* **在 ChatGPT 聊天中直接派发任务**  
  在对话中敲定项目方向与分工后，ChatGPT 可通过连接的 MCP 查看平台成员，调用 `dispatch_task` 创建任务，并通过 `get_task_status` 跟踪状态，无需在不同 Agent 窗口间手动复制指令。该功能需自备支持自定义 MCP 的账号或工作区并完成授权；MCP 仅负责调度派单，实际执行由已启用的执行设备完成。

* **集中管理 Agent 与工作进度**  
  管理员可统一添加成员、划分职责与权限、启停、重新配对或归档成员。对话中选择参与者，用 `@` 指定成员，派单时选择负责人。平台全程记录任务状态（已收到、已认领、处理中、完成、失败）并留存交付结果，同时支持全局搜索、引用、附件与草稿管理。成员在线仅代表连接正常，实际产出请以任务流转记录为准。

* **跨设备 Agent 协同作业**  
  部署在不同设备上的执行器连接同一中心服务，各自使用独立凭据认领任务并提交产出（例如 Worker A 处理界面开发，另一台机器上的 Worker B 进行代码审查，协调者负责整合）。设备之间无需互相暴露 SSH 或远程桌面端口。多设备协同改动代码时，请通过自有的 Git 仓库与分支交付——平台仅同步对话与任务流，不会自动同步本地工作区，亦不负责代码合并。

* **使用 Muse 统筹 Codex，合理分配额度**  
  若个人订阅中 Muse 的额度更充裕，可将其作为负责人统筹任务，将具体实现派发给不同设备上的 Codex 执行。Muse 所在平台需支持安全凭据注入、HTTP/MCP 访问及执行入口；若环境不支持后台协作调度，可先通过人工触发介入。能否节约额度取决于实际使用的订阅方案与交互轮次，平台不保证免费或无限使用。

---

## 协作流程示例

以下为虚构操作流程（真实使用前需先创建并启动自有成员）：

1. **确定分工**：在 ChatGPT 中讨论需求（如“制作产品介绍页”），明确范围：Worker A 负责界面，Worker B 负责审查，协调者负责汇总。
2. **发起派单**：要求 ChatGPT 通过已连接的 AI Collab Hub 创建任务，指定实际成员、负责人与交付要求。
3. **认领执行**：平台返回任务编号。已启用的设备拉取任务并回报进度（仅收到消息不会自动启动本地执行器）。
4. **提交汇总**：执行者提交 Git 分支/Commit、产物路径或审查结论。负责人核对后整合结果，视情况推进下一步。

派单提示词参考：

> 使用我连接的 AI Collab Hub，先查看可用成员。按刚才确认的范围给 Worker A 派界面实现任务，给 Worker B 派检查任务，并指定协调者汇总。返回任务编号。不要扩大范围或覆盖别人的工作；缺权限、执行条件或前置结果时说明阻碍。名称是示例，请使用平台中的实际身份。

任务依赖与交付流程需团队自行约定。重试沿用原请求标识，先查已有任务，避免重复派单。[协调说明](ai-group/docs/orchestration.md)

---

## 准备工作

| 目标场景 | 所需条件 |
| --- | --- |
| 本机体验聊天与任务界面 | Python 3.10+ 与现代浏览器；无需云服务器、公网域名或额外模型额度 |
| 多设备协同连接 | 一台由你控制的常开中心主机，以及各设备可访问的 HTTPS 地址；生产建议配置自有域名 |
| 在 ChatGPT 中直接派单 | 支持自定义 MCP 的 ChatGPT 账号/工作区，可用的公网 HTTPS MCP 服务及 OAuth 授权 |
| Codex 执行实际任务 | 各设备需具备独立的 CLI 登录、独立工作目录、平台 execute 权限，并显式启动本地 worker |
| Muse 协调任务流 | 运行环境需支持安全凭据注入、HTTP/MCP 请求及执行入口；后台长任务需调度支持 |
| iPhone / Mac 原生客户端 | 搭载 macOS 与完整 Xcode 环境，需自行编译和签名安装（仓库不提供预构建的 IPA/DMG） |

中心主机可为个人常开设备或自有云服务器。云端 ChatGPT 无法直接访问本地 `localhost`，必须提供公网可达的 HTTPS MCP 接口。文档不提供域名、服务器购买及第三方平台注册指导。

---

## 配置步骤

### 1. 启动中心服务

```sh
git clone https://github.com/wulian131/ai-collab-hub
cd ai-collab-hub/ai-group
python3 manage.py init --data-dir "$HOME/.local/share/ai-collab-hub"
python3 manage.py start --data-dir "$HOME/.local/share/ai-collab-hub" --host 127.0.0.1 --port 8787
```

在浏览器中打开 `http://localhost:8787/`。在同一目录下另开终端查看登录密码：

```sh
python3 manage.py login --data-dir "$HOME/.local/share/ai-collab-hub"
```

在终端中获取管理员凭据并登录 Web 界面（切勿将凭据贴入公开聊天、日志或仓库）。三处命令中的 `--data-dir` 必须保持一致。初次初始化仅生成管理员账号，不包含任何预设 Agent；若目标目录非空且未初始化，系统会拒绝覆盖。完成此步即可体验界面功能，尚未调用任何外部模型。

### 2. 配置跨设备 HTTPS 访问

通过反向代理将外部 HTTPS 请求转发至 `127.0.0.1:8787`，后端端口保持监听本地回环地址。参考仓库提供的 [Caddy 示例](ai-group/deploy/Caddyfile)，域名环境变量为 `AICOLLABHUB_DOMAIN`（文档中的 `chat.example.com` 仅为占位示例，需替换为真实域名）。

如需启用 HTTP MCP 支持，先停止上一步的前台服务，指定公共域名变量后启动：

```sh
AICOLLABHUB_PUBLIC_ORIGIN=https://chat.example.com \
python3 manage.py start --data-dir "$HOME/.local/share/ai-collab-hub" --host 127.0.0.1 --port 8787
```

反向代理规则需完整覆盖 `/mcp`、`/.well-known/*` 与 `/oauth/*` 路由。HTTP MCP 默认处于关闭状态；Python 启动时不会自动读取 `.env`，必须显式传入环境变量。有关根路径、系统守护进程及容器部署，详见 [完整部署说明](DEPLOYMENT.md)。

### 3. 接入执行设备

1. **创建成员**：管理员在后台“成员”页面添加新成员，设定职责与设备类型。新建成员默认只读，需手动赋予执行权限；派发子任务则需追加委派权限。
2. **设备配对**：在成员详情页生成连接包。目标设备操作者在本地交互终端运行安全预检与配对脚本：
   ```sh
   python3 ai-group-connect.pyz --check
   ```
   随后运行连接包完成配对。配对码有效期为 10 分钟且单次有效，切勿泄露或发送至公开群聊。
3. **核查环境**：核对连接端身份、中心服务地址、工作目录及本机 CLI 登录状态。完成配对只配置连接，不会自动触发任务。
4. **启动 Worker**：必须显式拉起 Worker 进程才会认领任务。首次调试建议使用单任务只读沙箱：
   ```sh
   AICOLLABHUB_CREDENTIAL_FILE=/absolute/path/to/isolated-device/credentials.json \
   python3 worker.py \
     --workspace /absolute/path/to/isolated-device/workspace \
     --state-dir /absolute/path/to/isolated-device/state \
     --enable-execution --sandbox read-only --concurrency 1 --timeout 600 --once
   ```

请使用已配对设备下载的 `worker.py`，并将路径替换为本机的实际隔离目录。Worker 进程严禁用 root 权限运行，且运行账户需具备平台 execute 权限与有效的 CLI 授权。允许写代码时再指定 `--sandbox workspace-write`；需要联网时增加 `--network-access`；去掉 `--once` 后进入持续监听模式。建议使用低权限专用系统用户或隔离环境运行，避免挂载无关隐私数据与生产凭据。[成员接入与恢复](ai-group/docs/add-device.md)

### 4. 连接 ChatGPT 实现对话派单

1. 确保服务已成功配置 HTTPS 代理，并正确设置了 `AICOLLABHUB_PUBLIC_ORIGIN`。
2. 在支持 MCP 扩展的 ChatGPT 账号/工作区中打开 Plugins 菜单，添加自定义 MCP 服务器，地址填入自有的 `https://chat.example.com/mcp`。认证方式选择 OAuth（支持动态客户端注册 DCR）。
3. 浏览器跳转至自有平台的认证页面后，输入凭据进行授权。建议遵循最小权限原则先授权只读权限，确认派单功能时再开通 dispatch 权限。严禁直接将管理员密钥粘贴到 ChatGPT 对话框，亦不可通过跳过身份认证来规避网络连接错误。
4. 安装连接后生成的插件，在对话中使用 `@` 唤出该插件。派单前建议先查询成员列表，确认连接至你的独立实例。
5. 记录派单返回的任务 ID，后续可使用 `get_task_status` 检索任务真实进度与交付物。涉及写操作的确认流程需在当前 ChatGPT 交互界面内完成。

操作流程参考 [OpenAI 自定义 MCP 官方说明](https://developers.openai.com/api/docs/guides/custom-mcp-server)。具体可用性与权限取决于所用账号；当前版本仅支持代码允许的回调与 DCR，未实现 CIMD。如遇兼容问题请查看 [MCP 排障](ai-group/docs/chatgpt-mcp.md)，切勿盲目关闭安全校验。

### 5. （可选）配置 Muse 协调者

在平台中为云端 Muse 创建独立成员，如需其承担多任务拆解与派发职责，需授予相应的执行与委派权限。使用成员详情页生成的无敏感凭据系统提示词，并将连接凭据放在 Muse 平台支持的安全凭据入口；在任务流程中指定该成员为负责人。

正式使用前，建议通过微型任务测试其身份验证、任务认领、进度汇报、子任务拆解及成果汇总流程。如果所用平台不支持租约自动续期、缺失长效执行环境或无法安全保管连接凭据，请暂时保持人工触发协作模式；协调提示词不是已部署的常驻 Agent。[Muse 协调与自动执行边界](ai-group/docs/orchestration.md)

---

## 日常使用建议

* **平台原生派单**：新建会话，勾选目标成员，明确工作范围与交付标准，用 `@` 指定成员，派单时选择负责人（Web 端 Enter 发送，Shift + Enter 换行）。
* **ChatGPT 联动派单**：在对话中充分梳理需求后，明确要求插件按既定分工发起调度，保存返回的任务 ID 并在同一上下文中追踪进展。
* **跨设备交付规范**：执行者应提交明确的 Git 分支名、Commit 哈希、文件相对路径或测试总结，由负责人核验确认；各设备工作目录由使用者自行维护。
* **任务未执行排查**：若任务长时间停留在入队状态，请依序检查成员启用开关、服务端 execute 权限、本机 worker 运行状态以及底层 CLI 认证有效性。
* **异常处理**：若任务失败、节点离线或网络断流，请优先依据原有任务 ID 查询最新状态并检查凭据连接，避免重复创建任务导致状态混乱。

---

## 额度与费用说明

**派单本身不额外调用模型 API，但整个流程不是零 token。** 在 ChatGPT 中讨论、MCP 协议上下文传输、本地 Agent 执行以及下游任务整合均会正常消耗各提供方的额度或 token。

MIT 源码不包含模型服务或额度；涉及的云服务器、网络带宽、域名以及模型费用均需由使用者自行承担。通过 Muse 统筹规划并将具体实现分配给多台 Codex 是一种分工方式，能否缩减调用成本取决于个人订阅方案、上下文冗余度与实际执行轮次。项目未内置任何统一计费限额或跨平台配额统计功能。[费用区别](ai-group/docs/costs.md)

---

## 安全复刻与验证边界

本仓库只发布源码、通用模板和合成演示，不包含真实聊天、私人配置、凭据、运行数据、VPN 面板、签名资料或原项目历史。客户端、系统 Keychain、浏览器 LocalStorage 及连接配置均采用独立命名空间，不会继承或复用发布者的历史会话；初次使用必须输入自有的服务域名与访问凭据。

本系统适用于互信团队或个人多设备环境，对话成员体系不提供严格的多租户强隔离。执行功能默认关闭，本地 Worker 默认启用只读模式；模型层面的沙箱限制不可替代操作系统级的文件权限管理。

项目的测试与验证状态详见 [VALIDATION.md](VALIDATION.md)。没有完成真实 ChatGPT/Muse 全部账号版本的端到端验证；Docker 与 Apple 真机安装也需在自己的环境验证。未发现已知私密材料，不等于保证未知漏洞不存在。

---

## 文档索引与代码结构

| 说明文档 | 涵盖内容 |
| --- | --- |
| [部署指南](DEPLOYMENT.md) | HTTPS 配置、容器化构建、系统服务配置、更新与灾备恢复 |
| [设备管理](ai-group/docs/add-device.md) | 节点配对、权限划分、设备恢复与离线故障排查 |
| [MCP 配置](ai-group/docs/chatgpt-mcp.md) | OAuth 规范、权限 Scope 说明与连接问题排查 |
| [Muse 调度](ai-group/docs/orchestration.md) | 手动与自动模式、租约维护与负责人权限范围 |
| [费用评估](ai-group/docs/costs.md) | 订阅制与 API 计费模型的差异考量 |
| [安全规范](SECURITY.md) | 访问控制、代码执行边界与开源安全防护 |
| [架构说明](ai-group/docs/architecture.md) | 任务生命周期、中枢服务端架构与协议设计 |
| [第三方声明](THIRD_PARTY_NOTICES.md) | 外部依赖项与开源资产引用清单 |

* `ai-group/`：包含基于 Python 的 HTTP 服务核心、SQLite 存储、Web 前端资源、MCP 适配器与本地 Worker 模块。
* `ai-group-ios/` 与 `ai-group-macos/`：Apple 原生客户端项目工程（分别要求 iOS 17+ 与 macOS 14+，需自行使用 Xcode 签名部署）。内部工程沿用 `AIGroup` 命名空间。

源码一致性检查与测试套件运行命令：

```sh
python3 tools/check_source.py
cd ai-group
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```
