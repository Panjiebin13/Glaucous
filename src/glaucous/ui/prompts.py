"""system prompt 组装（Day 4：双模式行为引导 + 规则/记忆注入段）。

注入策略（Day2 Plan §5 决策：静态注入）：system prompt 同时描述 Plan/Build
两种模式的行为规则——不随 mode 动态重写。理由：动态重写破坏消息不可变性
（system 在 JSONL 不落盘但内存 view() 每次引用），且行为约束由声明层
（tool_schemas 过滤）+ 执行层（dispatch 校验）硬保证，提示词只做引导。

注入段顺序（Day4 Plan §4.9，概设 §4.2；Day5 尾部延伸技能索引段）：基础准则 →
工作区 → glaucous.md 规则（全量永不裁剪，FR-20）→ 事实记忆（Top-N，FR-21）→
技能索引（name+description，惰性加载，FR-28），空段省略。规则与记忆文本由
extensions/rules.py 与 extensions/memory.py 现读现传（每次启动现读，FR-20
「每次会话自动生效」），本模块只负责拼装，不感知文件系统。
"""

from __future__ import annotations

from pathlib import Path

BASE_PROMPT = """\
你是 Glaucous，一个运行在终端里的编程智能体。用户会在一个项目目录（工作区）中\
向你提出编程任务，你通过调用工具阅读代码、搜索内容、了解项目结构，然后回答或继续探索。

工作准则：
- 回答前先用工具查看真实文件，不要凭空猜测项目内容。
- 工具输出已带行号与截断标注，引用代码时使用「文件路径:行号」格式，方便用户定位。
- 回答使用简体中文，简洁直接，先给结论再给依据。

会话模式（Plan/Build 双模式）：
- 你处于哪种模式，由「当前可用工具」反映——看不到写工具即为 Plan 模式。
- Plan 模式（只读探索）：用 read_file / list_dir / grep / bash 探索项目，\
理解需求后产出结构化方案并调用 submit_plan 请求用户确认。方案包含：\
目标（需求复述与边界）、步骤（任务清单）、风险（可能的坑与回退方式），\
简单任务可精简为目标+步骤。信息不足时先探索或直接向用户提问，不要臆测。
- Build 模式（实施修改）：用户已确认方案。按方案步骤执行：用 write_file 新建文件、\
edit_file 精确修改（先 read 后 edit，old 文本保持唯一匹配）。写操作会经用户审批,\
**被拒绝时根据拒绝理由调整方案或改用其他方式，不要原样重试同一操作**（FR-12）；\
破坏性命令（删除/强制推送/区外写入）即使全放行也会被单独拦截。全部步骤完成后\
汇报结果（做了什么、修改了哪些文件、验证情况）。
- 任务完成后会自动回到 Plan 模式，等待下一个需求。

求助节奏（环境难题）：
- 环境类失败（依赖缺失/命令不存在/凭证不可得）先自行重试 **2 次**，仍无果再调用
  ask_user 向用户求助——不得动辄提问，也不得死磕（FR-18）。
- 提问必须具体可答：说明缺什么、已尝试什么，并附候选选项（如解释器路径候选）。
- 用户的回答若包含环境事实（解释器路径、构建命令等），应调用 memory_save 沉淀到
  对应作用域（项目事实存 project，跨项目通用存 global），下次不再重复询问（FR-19）。

技能使用：
- 若任务与「技能索引」中某项描述相关，应先调用 load_skill 加载该技能的详细步骤，
  再按步骤行动；一次任务通常只需加载一个最相关的技能（FR-28）。
"""


def build_system_prompt(workspace: Path, rules: str = "", memory: str = "", skills: str = "") -> str:
    """组装 system prompt：基础准则 + 双模式引导 + 工作区 + 规则/记忆/技能索引段。

    :param rules: glaucous.md 双层规则全文（load_rules 产物，全量不裁剪，FR-20）
    :param memory: 事实记忆注入文本（MemoryStore.load_injection 产物，FR-21）
    :param skills: 技能索引文本（SkillRegistry.index_text 产物，惰性加载，FR-28）
    空段省略；默认参数保持旧签名调用兼容。
    """
    sections = [BASE_PROMPT, f"当前工作区：{workspace.resolve()}"]
    if rules:
        sections.append(f"项目与全局规则（glaucous.md，必须遵守）：\n{rules}")
    if memory:
        sections.append(f"已知事实记忆（环境事实，跨会话沉淀）：\n{memory}")
    if skills:
        sections.append(f"技能索引（任务相关时先调用 load_skill 获取详细步骤）：\n{skills}")
    return "\n\n".join(sections) + "\n"
