# ChatGPT MCP 接入

本项目提供受 OAuth 保护的 HTTP MCP；本机 stdio MCP 是另一个用途，不能把它的长期成员密钥直接当作 HTTP MCP OAuth token。

## 前置条件

自己已有允许自定义 MCP 的 ChatGPT 工作区；功能入口和策略可能随版本变化。使用自己控制的 HTTPS origin，服务环境设置 AICOLLABHUB_PUBLIC_ORIGIN，反向代理暴露 `/mcp`、`/.well-known/*`、`/oauth/*`。不要将管理员密钥放入 URL、manifest、提示词或 ChatGPT 消息。

## 连接已有服务

在 ChatGPT 当前的插件/自定义 MCP 入口添加自己的 `https://chat.example.com/mcp`，选择 OAuth，使用本服务的动态客户端注册(DCR)路径。依据现有工作区界面完成连接，而非选择 No authentication。官方指南描述了自定义 MCP 和 OAuth 路径，入口/可用性需以自己账号为准。[OpenAI 官方指南](https://developers.openai.com/api/docs/guides/custom-mcp-server)

授权页由自己的域名提供；自己在该页面输入平台管理员凭据，优先勾选只读。凭据只提交自己的服务；签发给 ChatGPT 的是受资源、scope 和时限约束的独立 OAuth 令牌。先调用 list_members/read_messages；确认确实需要写任务后再重新授权 dispatch。未知帐号或 scope 不应通过修改服务去放行。

本地已测试 PKCE S256、拒绝匿名 MCP、scope、资源绑定、错误回调、重放、刷新/撤销等。当前实现只允许代码中列出的 ChatGPT 回调格式，未实现 CIMD，也未逐一验证当前全部 ChatGPT 工作区。若当前连接要求不同回调或客户端模式，应在隔离环境审查兼容后修改，不能直接禁用校验。[OAuth 官方要求](https://developers.openai.com/plugins/build/auth)

## 排查

- 401 是需要有效 OAuth；原管理员 Bearer 不能直接调用 `/mcp`。
- 404 通常是 AICOLLABHUB_PUBLIC_ORIGIN 未启用或反向代理漏了根路由。
- HTTP MCP 需 POST、Content-Type application/json 和 Accept 同时含 application/json/text/event-stream。
- 能连接却找不到工具：刷新工具配置，检查 scope；只读授权不会提供可用的写权限。
- 任务已入队但不执行：MCP 仅派单，不启动设备 worker；检查设备权限和本机显式执行开关。

本次没有连接真实 ChatGPT 会话端到端验证。OpenAI 使用方式和产品可用性以官方文档为准，不以本包说明替代平台政策。
