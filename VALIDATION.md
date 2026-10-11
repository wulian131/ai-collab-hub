# 验证记录与能力边界

日期：2026-10-11。使用新目录、临时数据库、虚构成员和本机 loopback 夹具，不读取真实聊天、生产凭据或运行服务数据。

## 已验证

| 检查 | 结果与范围 |
| --- | --- |
| Python 服务完整回归 | Linux 232 项通过；最后新增安全/协议检查及跨平台夹具定向通过；Mac Python 3.12 最终全量 233 项、120.725 秒、全部通过；含复用基础测试，不是 233 个独立功能 |
| 最后安全及协议定向检查 | 7 项通过：默认不执行、拒绝 root、默认只读/单并发/900 秒、拒绝凭据重定向、仅管理员初始化、遗留新增身份默认只读、四种设备协议类型真实配对与身份核对 |
| Linux 两端 Swift Core | 每端 22 项，0 失败，各跳过 1 项 Linux 不支持的 URLSession WebSocket；真实隔离 HTTP 服务集成未跳过 |
| Swift 语法 | 两端全部 App Swift 文件解析通过；语法检查不等于 Apple SDK 编译 |
| Mac 执行者复核 | 协议类型恢复后两端 Core 再验均为 22 项、0 失败；iPhone 真实 HTTP 与通知 WebSocket 均通过；两端增量 Apple SDK 无签名编译均 BUILD SUCCEEDED。跨平台邀请夹具在 Darwin 用 codex-macos、Linux 用 codex-linux，未放松平台检查 |
| 独立初始化与启动 | 首页和 /health 返回 200；匿名 /api/members 返回 401；初始只有 owner；重复初始化不覆盖凭据；非交互 login 不输出凭据；数据目录 0700 |
| 源码导出与容器上下文 | 服务子包白名单 65 文件可导出；Docker 输入白名单包含导出器全部依赖；实际 Docker build 未运行 |
| clone 后静态检查 | 临时 git init 的隔离副本通过；检查忽略 .git 与编译缓存，不要求用户删除仓库历史 |
| 静态和私密信息复核 | Python、JS、JSON、plist、scheme 解析通过；只含源码、文档与合成资产，无运行数据或生产地址；PNG 无元数据块 |
| 合成演示 | 真实临时服务上模拟负责人→委派→两位 worker→汇总；没有模型或 CLI 调用，不能据此证明真实 AI 输出 |

macOS Core 夹具使用合成接口；Linux Core 与 iPhone 完整 Core 夹具使用真实临时聊天服务。不同测试不能互相替代。测试日志带运行环境路径，未放入公开源码。

## 尚未验证或不保证

- 最终 ZIP 的逐文件核对及从公开仓库 clone 的复刻由发布者继续确认；未进行签名、手机真机安装、不同设备实际点击及商店发行。
- Docker 实际构建、独立公网部署、HTTPS 证书/代理环境、从公开 GitHub clone 后的完整复刻。
- 当前全部 ChatGPT 工作区的真实 OAuth/MCP 接入，新的回调模式、CIMD 或平台版本兼容。
- 真实模型任务、外部操作的效果与持续在线；不同 Muse 云平台的后台入口、凭据库和续租能力。
- 多租户权限隔离、外部副作用 exactly-once、模型金额硬上限、完整渗透测试或法律权利认证。
- 停用服务端成员不强杀正在运行的本机进程；设备用户仍需停止执行器，并核对已经发生的操作。

## 复测

在仓库根目录运行 `python3 tools/check_source.py`，再在 ai-group 目录运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'`。

Mac 使用各项目 `bash scripts/verify-on-mac.sh`。显式 XCTest runner 在临时 scratch 目录构建，使用可用 loopback 端口；加载失败或执行 0 项测试必须非零退出。测试夹具不得配置真实服务地址或凭据。

## 安全隔离复审追加记录

公开版独立使用浏览器存储键、AICOLLABHUB_* 环境变量、服务与执行器状态目录、Apple Bundle ID、钥匙串 service 及缓存目录。首次启动不迁移旧会话；iOS 也要求本公开版明确保存过会话才能恢复。

- 隔离修正后 Mac Python 3.12 全量 233 项、123.026 秒、0 失败；随后新增拒绝旧环境凭据复用的回归，8 项安全定向检查全部通过。
- iOS Simulator Debug、macOS Release 无签名 SDK 编译成功；macOS 原生合成状态测试 22 项通过。没有签名、安装或访问真实模型。
- 最终公开下载内容和 Git 历史应按根目录 MANIFEST.json 重新核验。源码扫描与这些检查不能保证未知漏洞为零。
