# 安全复刻与维护

## 1. 确定组件

本地体验不需要购买服务器或域名。跨设备使用需要持续在线的中心节点和稳定 HTTPS 地址，正式使用建议自己的域名。外部代理提供 443，证书/跳转需要时提供 80，后端 8787 保持 loopback。

只体验聊天：Python 服务 + 浏览器即可，无需 AI 账号或 API key。需要原生客户端时再编译 Apple 工程。公开版使用独立的 Bundle ID、钥匙串和缓存命名空间，首次启动需要手动填写自己的地址和凭据；不要改回旧应用标识，也不要导入旧会话。需要执行任务时才连接自己已有的受权 CLI 或云端平台。本说明不包含外部账号注册步骤。

把源码放在新目录；数据放在独立目录；执行器放在另一设备或独立系统用户下。不要把现有运行环境或它的历史拷进源码仓库。使用非 root 用户和仅当前用户可读写的目录。

## 2. 本地初始化

从仓库根目录：

```sh
cd ai-group
python3 manage.py init --data-dir "$HOME/.local/share/ai-collab-hub"
python3 manage.py start --data-dir "$HOME/.local/share/ai-collab-hub" --host 127.0.0.1 --port 8787
```

另开同目录的交互终端执行：

```sh
python3 manage.py login --data-dir "$HOME/.local/share/ai-collab-hub"
```

仅自己在本机终端查看管理员密钥。`login` 不接受输出管道，不用于脚本记录密钥。数据目录必须三处一致。普通用户不应直接编辑 `members.json` 来添加 Agent，使用管理员界面创建邀请。初始数据的 owner 是平台管理员角色，不是示例真人。

## 3. 远程 HTTPS

使用你控制的域名及有效证书，在反向代理后监听 loopback。不要直接暴露明文 8787 端口。附带 `ai-group/deploy/Caddyfile` 是域名根路径示例：设置 `AICOLLABHUB_DOMAIN` 后由你自己的 Caddy 配置加载；本项目不安装或改动现有代理。

如果聊天界面放在 `/chat/`，代理要 `handle_path /chat/*` 将前缀去掉。客户端地址填 `https://chat.example.com/chat`。HTTP MCP 是同一 origin 下的根 `/mcp`，同时代理 `/.well-known/*` 和 `/oauth/*`，不要把 `/chat` 写进 `AICOLLABHUB_PUBLIC_ORIGIN`。

HTTP MCP 默认关闭。需要时在启动服务的环境里明确设置：

```sh
AICOLLABHUB_PUBLIC_ORIGIN=https://chat.example.com python3 manage.py start --data-dir "$HOME/.local/share/ai-collab-hub"
```

`.env.example` 是参考模板；Python 不会自动读取 `.env`。在 shell、服务 Environment 或 Compose 中明确传入变量。不得把密钥放在 URL 或公开页面。

## 4. 可选容器

在 `ai-group/`：

```sh
docker compose build
docker compose up -d
docker compose exec chat python3 manage.py login --data-dir /data
```

执行最后一条命令需要自己的交互终端。镜像使用非 root 用户、只读文件系统、独立 `/data` 卷和 loopback 端口映射。Compose 将 `AICOLLABHUB_PUBLIC_ORIGIN` 显式传入容器；不设置时关闭 HTTP MCP。源码导出使用文件白名单，构建上下文不会包含运行数据。容器仅运行聊天服务，不包含 CLI 或模型执行器。Docker 构建尚需在自己的环境实际验证，见 VALIDATION。

## 5. 可选用户服务

`ai-group/deploy/aicollabhub.service` 为 `systemd --user` 模板。先修改 WorkingDirectory/ExecStart 为自己的源码和独立数据目录，确认不是系统 root 服务，再加载。重启前核对数据目录和备份。模板不会被安装脚本自动启用；不要复制覆盖已有同名服务。

## 6. 添加设备和执行

参见 [添加成员](ai-group/docs/add-device.md)。只读是默认。已有 CLI 登录属于设备用户，服务端不收集其登录文件。接入配置与执行权限分别审查；首次仅前台运行，确认身份、目录、进度与结果后再考虑用户服务。

受限 worker 命令示例（运行用户不是 root，目录及凭据由本机用户创建）：

```sh
AICOLLABHUB_CREDENTIAL_FILE=/absolute/path/to/isolated-device/credentials.json python3 worker.py --workspace /absolute/path/to/isolated-device/workspace   --state-dir /absolute/path/to/isolated-device/state   --enable-execution --sandbox read-only --concurrency 1 --timeout 600 --once
```

去掉 `--once` 才持续接收通知和执行。受控代码编辑需由你选择 `--sandbox workspace-write`；需要网络时再显式选择 `--network-access`。不支持完全访问模式。`--timeout 0` 表示不设执行时限，需要配合系统资源限制；示例采用有限时长，持续模式不是免费的模型保活承诺。

模板使用接入目录的独立 workspace/state，并保持只读沙箱。专用低权限用户、容器或隔离主机仍需自己配置，工作目录和沙箱参数本身不是操作系统权限隔离。不要给该用户生产系统文件或无关凭据的读写权限。

## 7. 编译原生客户端

分别打开 `ai-group-ios/AIGroup.xcodeproj` 和 `ai-group-macos/AIGroup.xcodeproj`，Scheme 为 AIGroup。工程不含开发者 Team 或签名材料。真机安装由自己的 Mac/Xcode 签名；源码 ZIP 不是可直接点击安装的 IPA 或 DMG。修改 Bundle ID 为自己的反向域名标识以避免与其他安装冲突。

先运行各项目 `scripts/verify-on-mac.sh`。iPhone 工程要求 iOS 17+；Mac App 要求 macOS 14+。选自己的签名团队后编译安装。升级覆盖同一标识的安装，核对 Keychain/草稿保留，再验证新版本。测试夹具只在 loopback 运行并使用虚构身份。

## 8. 私有备份与恢复

数据目录含聊天、任务、成员认证和授权数据，不能提交 Git 或作为公开附件。若备份整个数据目录，停止服务写入后，以受保护方式复制整个目录（包括附件），校验内容后存放到私有位置。在线备份需使用 SQLite 一致性备份并同步附件，不能仅拷正在写入的单个 DB 文件。

恢复先关闭服务，保留原目录，不覆盖它；将备份还原至另一个仅本人可读写的目录，以它作为 `--data-dir` 启动并检查历史、附件和认证后再切换。公开版使用本地持久存储，没有自动远端备份承诺。

## 9. 升级与回滚

备份数据与当前源码版本，停止写入后在新目录部署新版；用备份副本先验证迁移和登录。数据库结构可能变更，不能仅回滚代码而沿用已迁移数据库；需要一并恢复对应备份。自动化升级、数据库降级兼容和 App 商店更新不属于本次验证范围。

## 常见排查

| 症状 | 检查 |
| --- | --- |
| 看不到历史 | 是否启动了新的数据目录或错误根路径；不要删除目录重试 |
| 401 | 凭据是否失效/轮换、是否为当前身份；到管理员成员页重新接入 |
| 403 | 只读或缺执行/委派权限；不要扩权绕过 |
| Agent 离线 | 真正的进程/连接器是否运行、前台/后台限制、心跳时间；提示词不保证常驻 |
| 已收到但未开始 | 执行器默认关闭、身份/权限、负责人依赖或本机 CLI 状态 |
| 响应丢失 | 先按任务 ID 或原 key 核对，避免新建 key 导致重复任务 |
| MCP 连接失败 | origin、HTTPS、四组根路由、OAuth 回调兼容与帐号功能权限 |
