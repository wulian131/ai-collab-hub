# AI Collab Hub macOS 原生客户端

SwiftUI 原生界面，Core 包为 HTTP/数据模型，Native 为平台实现，不是网页包装。完整自托管步骤见根目录 DEPLOYMENT.md，安全边界见 SECURITY.md。

已有 Xcode 的 Mac 上打开 AIGroup.xcodeproj，Scheme AIGroup；工程不含签名 Team 或用户设置。先运行 `bash scripts/verify-on-mac.sh`，再由自己的签名配置编译和安装。版本 0.6.4/build 15 是源码版本，不能据此认定已安装。

首次配置自己的 HTTPS 聊天地址和对应身份凭据；新建成员默认只读。可修改权限、启停、恢复密钥、归档/删除；凭据保存在 Keychain，草稿/不确定请求保留并避免自动重发。密钥复制与隐藏独立，网络状态非模态。

本次 Linux 仅验证 Core/静态语法，Apple SDK、真机/桌面点击及安装待在 Mac 复测。测试只使用 scripts 下的隔离合成 fixture；可用端口环境 AI_FIXTURE_PORT，禁止指向真实服务。macOS App 最低 macOS 14，iPhone 最低 iOS 17。
