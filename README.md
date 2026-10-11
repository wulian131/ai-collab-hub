# AI Collab Hub

一个可自托管的 AI 协作聊天平台：网页、原生 iPhone 和原生 macOS 客户端共用同一套消息与任务服务。MIT 开源代码不包含 AI 模型服务；模型账号、执行设备和服务器由使用者自行提供。

这是独立源码发行版，不包含运行数据、凭据或已安装的 App。文档示例均为虚构数据，`example.com` / `.example` 需替换为自己的地址。

## 能做什么

- 创建对话并选择参与成员；历史搜索、重命名、分页、消息引用、附件与草稿恢复。
- 网页 Enter 发送、Shift+Enter 换行；输入 `@` 选择成员或全部，并可指定负责人。聊天界面按任务方式发送，底层 API 仍兼容不派单的普通消息。
- 队列持久化后立即显示收到，随后显示已领取、处理中、完成或失败；支持租约、续租、幂等重试、负责人协作和并发执行。
- 添加、启停、修改权限、归档和删除成员；失效凭证重新配对或重新生成，成员身份与历史保留。配对码一次性使用，长期密钥不回读。
- 网络中断显示连接状态，避免反复弹窗；不确定是否成功的发送保留原请求键，由用户核对后重试。
- 支持本机 stdio MCP 和可选 HTTPS OAuth MCP，让已有 ChatGPT 或其他支持 MCP 的应用读取成员、派单、查进度。

对话成员选择用于组织协作，不是租户权限隔离。适合自己的可信团队，不能直接当作面向陌生用户的多租户产品。

## 协作流程

ChatGPT 讨论方向和分工 → MCP 派单 → 跨设备执行并报告进度 → Muse 或指定负责人协调 Codex、汇总结果。平台派单本身不额外调用模型 API；聊天推理、工具上下文与执行分别消耗提供方额度。自动执行需要设备用户明确启用，不是默认行为。

## 五分钟本地体验

要求 Python 3.10+，无需安装 Python 第三方库。先仅体验服务，不启动 AI 执行器。

```sh
cd ai-group
python3 manage.py start
```

打开 `http://localhost:8787/`。在同一设备的另一个交互终端、同一目录运行 `python3 manage.py login`，自行查看管理员凭据并填入登录框。不要把凭据交给模型、写入日志或上传仓库。默认数据目录为 `ai-group/private-data/`，首次创建只有管理员，没有预置 Agent。已有非空未初始化目录会拒绝覆盖。

服务和客户端地址使用一致的根路径。远程客户端必须使用 HTTPS；本机 loopback HTTP 仅用于开发。详情见 [安全复刻](DEPLOYMENT.md)。

## 源码结构

| 目录 | 内容 |
| --- | --- |
| `ai-group/` | Python HTTP 服务、SQLite、网页、设备接入和受限 worker |
| `ai-group-ios/` | SwiftUI/UIKit 原生 iPhone 客户端，iOS 17+ |
| `ai-group-macos/` | SwiftUI/AppKit 原生 Mac 客户端，macOS 14+ |
| `tools/` | 静态检查与隔离测试辅助脚本 |

内部协议与工程仍使用 `ai-group` / `AIGroup` 命名，外部项目名称为 AI Collab Hub，不影响接口兼容。

## 安全默认值

新增成员默认只读，执行和委派关闭。接入包只配置连接，不自动开始执行。worker 必须手动指定 `--enable-execution`，拒绝 root，默认只读沙箱、禁用额外网络权限、一个执行槽和默认 900 秒时限。写工作区需另行明确选择。服务器进程不应拥有执行设备的 CLI 登录文件。

管理员授权服务权限、设备本机启动执行器、用户明确派发任务是三件不同的事。任何一步都不能被一段提示词替代。更多边界见 [SECURITY.md](SECURITY.md)。

## 使用和复刻文档

- [完整安装、HTTPS、容器、备份、升级与恢复](DEPLOYMENT.md)
- [成员接入、密钥恢复与离线排查](ai-group/docs/add-device.md)
- [ChatGPT MCP 接入与排障](ai-group/docs/chatgpt-mcp.md)
- [Muse 协调 Codex：手动与自动执行边界](ai-group/docs/orchestration.md)
- [费用区别](ai-group/docs/costs.md)
- [架构及接口](ai-group/docs/architecture.md)
- [本次验证及未验证能力](VALIDATION.md)
- [第三方依赖和资产来源](THIRD_PARTY_NOTICES.md)

## 开发检查

```sh
python3 tools/check_source.py
cd ai-group
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```

Apple 客户端编译需要 Mac 和完整 Xcode。两端 Core 可以用 Swift Package 测试；Linux 无法验证 SwiftUI/UIKit/AppKit 实际界面。不要把 Core 测试通过当作真机或安装验证。

## 获取源码

```sh
git clone https://github.com/wulian131/ai-collab-hub
cd ai-collab-hub
```

仓库尚未公开时，请使用发布者交付的源码包；部署前核对 VALIDATION。

## 本地演示

`python3 tools/demo.py` 在 loopback:18918 创建一次性演示，Coordinator、Worker A、Worker B 与任务均为合成数据。网页演示凭据为公开夹具值 `demo-local-only`，绝不能用于正式部署。退出后临时数据清理；演示不调用模型或 CLI，也不能证明真实模型执行。

![AI Collab Hub 合成协作演示](docs/images/synthetic-demo.png)

合成演示：成员与聊天内容均为虚构，不调用真实模型。使用 `python3 tools/demo.py` 可在本机复现。
