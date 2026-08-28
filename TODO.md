# Glaucous TODO

> 按开发计划表维护每日进度勾选（评委可见的开发过程素材）。

## M0 原型闭环（8/27–8/28）

### Day 1（8/27）

- [x] 0.1 WSL2 环境检查（Python 3.11.15 经 Miniconda 环境就绪）
- [x] 0.2 GitHub 新建仓库（github.com/Panjiebin13/Glaucous，已推送）
- [x] 0.3 项目骨架：pyproject.toml、src/glaucous/ 包结构、pytest 配置
- [x] 0.4 LLM 客户端：OpenAI 兼容请求 + 重试退避 + 流式读取
- [x] 0.5 工具基座：Tool 协议、registry、JSON Schema 定义
- [x] 0.6 三个只读工具：read_file / list_dir / grep
- [x] 0.7 主循环 v0：请求 → tool_calls → 执行 → 回喂 → 终止
- [x] 0.8 简版 CLI：input 循环 + print 输出（无主题）

> 0.3~0.8 代码已按 SDD 流程（Plan Review 4 轮 + Code Review 3 轮）完成编码。
> ✅ **Day 1 验收已通过**（2026-08-27）：真实 LLM 端到端测试（deepseek-v4-flash），官方验收用例「看看这个项目的结构」正确回答（9× list_dir + 3× read_file）；另验证 grep 搜索、错误路径回喂、多轮上下文记忆、优雅退出，均符合设计契约。

### Day 2（8/28）

- [x] 0.9 bash 工具（含超时、UTF-8、kill；先全部放行）
- [x] 0.10 write_file / edit_file 工具（含唯一匹配校验）
- [x] 0.11 Plan 语义 v0：Plan 下不注册写工具（声明层隐藏）
- [x] 0.12 submit_plan + 三选一切换确认（简版交互）
- [x] 0.13 edit 前打印 diff、用户 y/n 确认（审批的雏形）
- [x] 0.14 会话 JSONL 落盘（--resume 可续）
- [x] 0.15 端到端验证：真实小项目完整走一遍修 bug 流程（2026-08-28 用户环境实测通过）

> 0.9~0.14 代码已按 SDD 流程（Plan Review 2 轮 + Code Review 2 轮）完成编码；
> ✅ **M0 验收已通过**（2026-08-28）：任务 0.15 端到端验证完成（需求→探索→方案→授权→修改→汇报）。

### Day 3 / M1 权限成型（8/29）

- [x] 1.1 工作区沙箱：realpath 规范化 + 前缀校验 + 符号链接解析；未指定默认当前目录
- [x] 1.2 危险命令分类器：首词白名单 + 参数模式表；未识别保守升级（后记：M1 验收实测后 cd 加入 SAFE 白名单——无害探测命令，复合段仍独立定级，65 用例全绿）
- [x] 1.3 Build 审批三选项：同意 / 同意同类型 / 拒绝附理由；结构化回喂
- [x] 1.4 授权策略：per-action / auto-approve；auto-approve 仍拦区外+破坏性
- [x] 1.5 Plan 模式 bash 白名单（只放行 SAFE）
- [x] 1.6 审计日志 audit.log
- [x] 1.7 单测：沙箱逃逸、分类器正反例、审批流、auto-approve 守卫（2026-08-28 补齐，61 用例全绿：test_workspace_escape / test_classifier / test_approval_flow / test_autoprivilege_guard；模式工具暴露矩阵、循环审批拦截不计熔断等扩展项仍留 M4）
- [x] 1.8 stdin 输入净化：Windows cp936 终端中文输入经 surrogateescape 产生孤立代理字符（如 \udcef），发往 LLM API / 写会话 JSONL 时抛 `UnicodeEncodeError: surrogates not allowed`（WSL 中文输入实测复现）；修复：cli.py 新增 `sanitize_input()`，对全部 4 处 `input()` 结果净化——无代理原样放行；有则还原原始字节按 UTF-8 → GBK → replace 降级（保留 surrogateescape 以便 GBK 二次解码，故不对 stdin reconfigure replace）；已验证 UTF-8/GBK 还原与兜底可编码

> 1.1~1.6 代码已按 SDD 流程（Plan Review 2 轮 + Code Review 8 轮，含分类器复合命令/管道/引号/重定向安全修复）完成编码；
> 按用户要求本轮未做运行验证，M1 验收（场景 A/C）待环境就绪后执行；测试债务登记于 Plan §9 由 M4 偿还。

### Day 4 / M2 记忆与上下文（8/30）

- [x] 2.1 glaucous.md 双层注入：extensions/rules.py（全局 ~/.glaucous/glaucous.md + 项目 <workspace>/glaucous.md，超 4000 字符附提醒），build_system_prompt 注入段（FR-20）
- [x] 2.2 memory_save：extensions/memory.py MemoryStore（双作用域 JSON 存储、原子写、去重刷新 last_used、Top-N 注入），tools/memory_tool.py（FR-21/22）
- [x] 2.3 ask_user：tools/interactive.py + CLI 提问卡回调（EOF→「用户未响应」控制信号，非交互环境不挂死）；BASE_PROMPT 增求助节奏引导（FR-17/18/19）
- [x] 2.4 Token 记账：context/budget.py（ASCII/4+CJK/1.5 估算、70%/85% 两档、build_report）+ loop 守卫点接线 + budget 占用条事件渲染（FR-25/31）
- [x] 2.5 L0 输出截断：safety/output_limit.py（>300 行或 >50KB，头 200+尾 50，完整输出落盘 outputs/）+ tools/output.py read_output 分段回取（单次≤1000 行）
- [x] 2.6 L1 裁剪：history._meta 记账入史 + context/compactor.trim_history（_meta 派生一行摘要、最近 K=2 轮豁免、_trimmed 幂等、submit_plan 决策回喂保留锚行原文）
- [x] 2.7 L2 压缩：compactor.compact_history（早期历史压成≤500 字合成摘要消息 + 最新方案锚段；失败降级 L1 加深，连续失败 2 次且仍 critical 走终止③）；SubmitPlanTool 方案落盘 .glaucous/plans/ + 决策回喂附锚行 + history.view() 视图层锚替换（方案全文不常驻 API 视图，概设 §5.2）+ ReadPlanTool 回读
- [x] 2.8 基线回归：既有 65 用例全绿（python -m pytest tests/ -q）；Code-First 测试债务（Day4 新增模块单测）登记于 Plan §9，由 M4 偿还；端到端验证（场景 B/E）按用户决定留待自行在 WSL 环境验证

> 2.1~2.7 代码已按 SDD 流程（spec Review 2 轮阻塞归零 + Code Review 待进行）完成编码；
> 上下文压缩管线（L1/L2/锚替换/预算终止）为纯内存变换，会话 JSONL 保留全量原文（resume 后重新裁剪）。
