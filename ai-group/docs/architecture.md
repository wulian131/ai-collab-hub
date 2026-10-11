# 架构与接口

网页和原生 App → HTTP 服务 → SQLite 与附件存储。HTTP 服务本身不调用模型，执行设备按合法租约领取任务，调用自己已有的 CLI，再提交结果。Notification 通道用于提醒，不代替认证读取。默认并发为 1，可由设备用户选择 1–8；资源限制需在 OS 层设置。

server.py 管消息、会话、队列、身份与通知；devices.py 管成员生命周期和凭据；attachments.py 管受限附件；mcp_http.py 管可选 OAuth/HTTP MCP；client.py 是凭据驱动的 stdio MCP/CLI；worker.py 是主动启用的隔离执行器。Swift Core 包含接口和数据模型，Native 为各平台界面。

旧无 conversation_id 请求默认 main；消息和任务 ID、回复关系、会话上下文持久化。job.context 为空时保持空上下文，不固定重取主会话。任务会话隔离是执行输入的归属隔离，不是租户级读取权限隔离。

远程 ChatGPT MCP 工具：list_members、dispatch_task、read_messages、get_task_status、list_tasks。本机 stdio 工具：group_members、group_read、group_send、group_jobs、group_claim、group_progress、group_renew、group_finish。两者授权和适用角色不同，远程工具不能领取执行器租约或修改成员凭据。

普通 API 消息可不派单；网页默认按任务发送。负责人用于处理协作续轮和结果汇总，委派需拥有权限且关联当前任务，不是任意成员可凭群消息扩权。
