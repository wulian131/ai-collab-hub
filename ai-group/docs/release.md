# 发布候选与复核

本目录是无生产历史的源码导出；发布不等于安装或真实模型联调。发布者先核对根目录 MANIFEST.json、执行 tools/check_source.py、复测服务和 Apple 工程，再以新仓库初始提交发布，不导入生产历史或运行数据。MIT 使用 AI Collab Hub contributors，不使用真实个人签名。

使用临时克隆的仓库作为发布目录，按 MANIFEST 清单逐项加入文件。审查 Git staged 内容和 `git status`，不要 `git add` 私有工作目录。可用局部 commit identity：name=AI Collab Hub contributors，email=contributors@users.noreply.github.com，不更改全局 Git 设置。

发布后从空目录 clone，验证启动、权限、MCP 和客户端，记录真实平台结果。没有通过的项目明确保留为未验证，详情见根目录 VALIDATION.md。
