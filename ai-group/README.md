# AI Collab Hub service

Python 标准库 HTTP/SQLite 服务与网页。完整步骤见 [根目录 README](../README.md) 和 [安全复刻](../DEPLOYMENT.md)。

本地运行 `python3 manage.py start`，同目录另开交互终端运行 `python3 manage.py login`。默认 loopback:8787，运行数据只在 private-data。新增成员默认只读，接入包不自动启动 worker。

检查：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'`。
`export_source.py` 只重新导出本目录白名单文件；它不是整个仓库的发布工具，不打包客户端或运行数据。
