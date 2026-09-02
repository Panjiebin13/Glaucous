从零实现一个简易编程智能体（本目录当前是空项目）：

1. agent.py：提供 run(task: str) -> str——不接入任何 LLM API，按任务文本关键词路由到工具（如任务含「写入」或「write」则调用 write_file，含「读取」或「read」则调用 read_file，其余返回「不支持的任务」提示），返回执行结果摘要；
2. tools.py：提供 read_file(path) -> str 与 write_file(path, content) 两个工具函数（UTF-8 编码；路径不存在时抛出带明确信息的错误）；
3. tests/：用 pytest 编写测试——工具读写往返、不存在路径报错、run() 冒烟（合法任务与不支持任务各一）；
4. pyproject.toml：声明 pytest 依赖与 pytest 配置（测试目录指向 tests/）。
