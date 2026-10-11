# 依赖与资产来源

项目自身代码与文档采用根目录 MIT，署名 AI Collab Hub contributors。公开包采用源文件白名单重新生成，无生产 Git 历史。

- 服务端与网页使用 Python 标准库、SQLite 和浏览器内置 API；不打包第三方 Python/JavaScript 依赖。
- 原生代码使用 Apple SDK、Swift Foundation/SwiftUI/UIKit/AppKit/Security 与系统 SF Symbols。使用者需遵守对应 SDK 和平台协议，本项目不转授 Apple SDK、平台图标或商标权。
- Codex CLI、ChatGPT、Muse 是外部工具或平台；不随包提供程序、账号、额度或服务许可。品牌名仅描述兼容连接类型，不代表官方合作或背书。
- AppIcon 是 `tools/generate-icons.py` 用标准库绘制的几何聊天图标，Mac 使用同一重新生成 PNG。无外部下载图像或个人截图；PNG 只含 IHDR/IDAT/IEND。旧位图及旧图标绘制实现未复制。
- 本次静态检查未发现随包的第三方依赖、字体或外部图片，但无法仅凭源码证明所有历史贡献的权利。发布者仍需确认对贡献代码有权采用 MIT；如新增第三方文件，须另附许可和来源。
