#!/usr/bin/env python3
# 运行框架：工具分发 —— 扩展模型可触达的能力范围。
"""
s02_tool_use.py - 工具分发 + 消息规范化
s01 中的 agent 循环本身没有变化。这里新增了 dispatch map，
并加入了 normalize_messages()，在每次调用 API 之前整理消息列表。

关键洞察："循环本身完全没变，我只是增加了工具。"
"""
import os
import re
from pathlib import Path

# skill 注册 / to-do / permission / hook / memory 计划状态
from learn_claude_code.runtime import CONTEXT_LIMIT, MAX_INDEX_LINES, MEMORY_GUIDANCE, MEMORY_TYPES, CompactState, DreamConsolidator, HookManager, MemoryManager, MODES, PermissionManager, PlanItem, PlanningState, SkillDocument, SkillManifest, SkillRegistry, TodoManager, build_skill_registry, build_skills_dir, compact_history, estimate_context_size, micro_compact
# 基础工具 定义、执行
from learn_claude_code.tools import CHILD_TOOLS, TOOLS, build_tool_handlers

try:
    import readline
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if "http://127.0.0.1:8317":
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
SKILLS_DIR = build_skills_dir(WORKDIR)
client = Anthropic(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://127.0.0.1:8317",
)
# MODEL = os.environ["MODEL_ID"]
MODEL = "gpt-5.4"
PLAN_REMINDER_INTERVAL = 3

SKILL_REGISTRY = build_skill_registry(WORKDIR)
BASE_SYSTEM = f"""你是一个位于 {WORKDIR} 的 coding agent。
- 请使用工具解决任务，先行动，不要空讲。
- 遇到多步骤任务时，请使用 todo 工具维护当前会话计划。
- 必要时可以使用 task 工具委派探索或子任务。
- 当任务需要专门流程、规范或领域知识时，可先使用 load_skill 工具加载对应技能。
- 同一时间只能有一个步骤处于 in_progress。
- 随着工作推进，及时刷新计划。优先使用工具，而不是空讲。
- 请一步一步持续推进；如果对话变得过长，可使用 compact 工具压缩上下文。
- 尽量保持输出中文
- 当前可用技能：
{SKILL_REGISTRY.describe_available()}
"""

TODO = TodoManager(reminder_interval=PLAN_REMINDER_INTERVAL)
COMPACT_STATE = CompactState() # s06、把上下文压缩状态放在宿主侧，跨多轮主循环持续复用
PERMISSION_MANAGER = PermissionManager() # s07、把权限系统也放在宿主侧，统一拦截父代理与子代理的工具调用
HOOK_MANAGER = HookManager(workdir=WORKDIR) # s08、把 hook 系统也放在宿主侧，不改主循环骨架地扩展工具执行前后行为
MEMORY_MANAGER = MemoryManager(WORKDIR / ".memory") # s09、把长期记忆系统也放在宿主侧，跨会话复用用户偏好与项目事实
DREAM_CONSOLIDATOR = DreamConsolidator(WORKDIR / ".memory") # s09、Dream 作为长期记忆的低频整理器，先以低干扰方式接入

# s04、概念占位：解析 subagent markdown frontmatter 形式的 agent 定义
class AgentTemplate:
    """
    从 markdown frontmatter 中解析 agent 定义。

    真实的 Claude Code 会从 .claude/agents/*.md 中加载 agent 定义。
    常见 frontmatter 字段包括 name、tools、disallowedTools、skills、hooks、
    model、effort、permissionMode、maxTurns、memory、isolation、color、
    background、initialPrompt、mcpServers。
    这里保留教学版的核心解析逻辑。
    """
    def __init__(self, path):
        self.path = Path(path)
        self.name = self.path.stem
        self.config = {}
        self.system_prompt = ""
        self._parse()

    def _parse(self):
        text = self.path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            self.system_prompt = text
            return
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                self.config[key.strip()] = value.strip()
        self.system_prompt = match.group(2).strip()
        self.name = self.config.get("name", self.name)


# -- 并发安全分类 --
# 只读工具可以安全并行运行；修改型工具必须串行执行。
CONCURRENCY_SAFE = {"read_file"}
CONCURRENCY_UNSAFE = {"write_file", "edit_file"}

# s09、system prompt 改为动态构建：保留当前规则主体，再按需追加 memory section 与 memory guidance
def build_system_prompt() -> str:
    parts = [BASE_SYSTEM]

    # 系统提示词 + 跨会话记忆
    memory_section = MEMORY_MANAGER.load_memory_prompt()
    if memory_section:
        parts.append(memory_section)

    parts.append(MEMORY_GUIDANCE) # 让 LLM 知道哪些该存、哪些不该存
    return "\n\n".join(part for part in parts if part)


# s04/s09、子代理也感知长期记忆，避免父子代理对用户偏好与项目事实理解不一致
def build_subagent_system_prompt() -> str:
    parts = [
        f"你是一个位于 {WORKDIR} 的 coding 子代理。",
        "请完成父代理交给你的具体任务，必要时使用工具。",
        "完成后只返回最终总结，不要暴露完整内部上下文。",
    ]
    memory_section = MEMORY_MANAGER.load_memory_prompt()
    if memory_section:
        parts.append(memory_section)
    parts.append(MEMORY_GUIDANCE)
    return "\n".join(parts)


# -- dispatch map：{tool_name: handler} --
TOOL_HANDLERS = build_tool_handlers(WORKDIR, compact_state=COMPACT_STATE, memory_manager=MEMORY_MANAGER)
TOOL_HANDLERS["load_skill"] = lambda **kw: SKILL_REGISTRY.load_full_text(kw["name"]) # s05、按需加载技能正文
TOOL_HANDLERS["todo"] = lambda **kw: TODO.update(kw["items"]) # s03、把 to-do 接成一个工具

# s07、统一执行单个工具调用前，先经过 permission pipeline
# s07、这里不关心 “工具怎么执行”，只负责决定 “当前这次调用能不能执行”
def execute_tool_with_permission(tool_name: str, tool_input: dict, executor) -> str:
    decision = PERMISSION_MANAGER.check(tool_name, tool_input)
    if decision["behavior"] == "deny":
        return f"权限拒绝：{decision['reason']}"
    if decision["behavior"] == "ask":
        if not PERMISSION_MANAGER.ask_user(tool_name, tool_input):
            return f"用户拒绝了工具调用：{tool_name}"
    return str(executor())


# s08、把 PreToolUse / PostToolUse 的接入细节单独收口，保持 handle_tool_use_round 主体仍然适合初学者阅读
# s08、顺序保持清晰：PreToolUse -> permission -> tool execute -> PostToolUse
# s08、如果前置 hook 改写了输入，后面的 permission 与 handler 都继续使用改写后的结果
def run_tool_with_hooks(block):
    results = [] # 工具结果、hook 结果
    tool_input = dict(block.input or {})
    # hook 上线文对线，收敛 hook 修改的范围
    context = {"tool_name": block.name, "tool_input": tool_input}

    pre_result = HOOK_MANAGER.run_hooks("PreToolUse", context)
    for message in pre_result.get("messages", []):
        results.append({
            "type": "text",
            "text": f"[Hook message]: {message}",
        })
    if pre_result.get("blocked"): # 如果 hook 阻断，结束（真实场景里，hook 还能阻断主循环吗？）
        reason = pre_result.get("block_reason", "被 hook 阻止")
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Tool blocked by PreToolUse hook: {reason}",
        })
        return results, block.name, False, None

    tool_input = context["tool_input"] # 这里是 hook 改写过后的 tool_input
    payload = dict(tool_input)
    payload["tool_use_id"] = block.id # s08、前置 hook 改写输入后，真正传给 handler 的 payload 也要同步更新

    if block.name == "task":
        output = execute_tool_with_permission(
            block.name,
            tool_input,
            lambda: run_task(
                tool_input.get("prompt", ""),
                tool_input.get("description", ""),
            ),
        )
    elif block.name == "compact":
        output = execute_tool_with_permission(
            block.name,
            tool_input,
            lambda: "正在压缩更早的对话上下文。",
        )
    else:
        handler = TOOL_HANDLERS.get(block.name)
        if not handler:
            output = f"未知工具：{block.name}"
        else:
            output = execute_tool_with_permission(
                block.name,
                tool_input,
                lambda: handler(**payload),
            )

    context["tool_output"] = output

    post_result = HOOK_MANAGER.run_hooks("PostToolUse", context)

    for message in post_result.get("messages", []):
        output += f"\n[Hook note]: {message}"

    results.append({
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": str(output),
    })
    return results, block.name, True, tool_input


# s04、在子代理中分发基础工具
# s07、子代理也要复用同一套权限系统，避免通过 task 绕过宿主权限控制
def run_subagent_tool(block) -> str:
    handler = TOOL_HANDLERS.get(block.name)
    if not handler:
        return f"未知工具：{block.name}"
    payload = dict(block.input)
    payload["tool_use_id"] = block.id # s06、把 tool_use_id 透传给 handler，便于超长输出落盘并返回预览
    return execute_tool_with_permission(block.name, dict(block.input), lambda: handler(**payload))


# s02、调用 LLM 前，规范化信息
def normalize_messages(messages: list) -> list:
    """在发送给 API 之前清理消息列表。

    这里主要做三件事：
    1. 去掉接口不认识的内部字段
    2. 确保每个 tool_use 最终都有对应的 tool_result
    3. 合并连续同角色消息，满足严格交替要求
    """
    cleaned = []
    for msg in messages:
        clean = {"role": msg["role"]}
        if isinstance(msg.get("content"), str):
            clean["content"] = msg["content"]
        elif isinstance(msg.get("content"), list):
            content = []
            for block in msg["content"]:
                if isinstance(block, dict):
                    normalized_block = block
                elif hasattr(block, "model_dump"):
                    normalized_block = block.model_dump()
                else:
                    continue
                content.append({
                    k: v for k, v in normalized_block.items()
                    if not k.startswith("_")
                })
            clean["content"] = content
        else:
            clean["content"] = msg.get("content", "")
        cleaned.append(clean)

    existing_results = set()
    for msg in cleaned:
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    existing_results.add(block.get("tool_use_id"))

    for msg in cleaned:
        if msg["role"] != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for block in msg["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") not in existing_results:
                cleaned.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "（已取消）",
                    }],
                })

    if not cleaned:
        return cleaned

    merged = [cleaned[0]]
    for msg in cleaned[1:]:
        if msg["role"] == merged[-1]["role"]:
            prev = merged[-1]
            prev_content = prev["content"] if isinstance(prev["content"], list) else [{"type": "text", "text": str(prev["content"])}]
            curr_content = msg["content"] if isinstance(msg["content"], list) else [{"type": "text", "text": str(msg["content"])}]
            prev["content"] = prev_content + curr_content
        else:
            merged.append(msg)
    return merged


# s04、子代理：使用全新上下文执行子任务，只把最终总结返回父代理
def run_subagent(prompt: str, description: str = "") -> str:
    sub_messages = [{"role": "user", "content": prompt}]
    last_response = None
    for _ in range(30):
        last_response = client.messages.create(
            model=MODEL,
            system=build_subagent_system_prompt(),
            messages=normalize_messages(sub_messages),
            tools=CHILD_TOOLS,
            max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": last_response.content})
        if last_response.stop_reason != "tool_use":
            break

        results = []
        for block in last_response.content:
            if block.type != "tool_use":
                continue
            try:
                output = run_subagent_tool(block)
            except Exception as e:
                output = f"错误：{e}"
            print(f"> 子代理工具 {block.name}:")
            print(str(output)[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output)[:50000],
            })
        sub_messages.append({"role": "user", "content": results})

    if last_response is None:
        return "（子代理没有产生结果）"
    summary = extract_text(last_response.content)
    return summary or "（子代理没有返回总结）"


# s04、父代理 task 工具入口
def run_task(prompt: str, description: str = "") -> str:
    short_desc = description or "未命名子任务"
    print(f"> 子代理任务（{short_desc}）: {prompt[:80]}")
    return run_subagent(prompt, description)


# s06、每轮开头先处理上下文长度，避免后面请求模型时历史已经过大
def prepare_messages_for_next_turn(messages: list) -> None:
    if COMPACT_STATE.just_compacted:
        COMPACT_STATE.just_compacted = False # s06、防抖：compact 后先放过下一轮，避免立刻再次触发 auto compact
        return

    # s06、每轮先微压缩较早的 tool_result，优先把活动上下文留给最近几轮
    messages[:] = micro_compact(messages)

    # s06、当整段历史过大时，再升级成完整 compact
    if estimate_context_size(messages) > CONTEXT_LIMIT:
        compacted_messages, transcript_path = compact_history(
            WORKDIR,
            client,
            MODEL,
            messages,
            COMPACT_STATE,
        )
        messages[:] = compacted_messages
        print(f"[自动压缩触发] 已保存 transcript: {transcript_path.relative_to(WORKDIR)}") # s06、把旧历史收束成摘要后，再继续走原来的 agent 循环


# s03、最终答复已产生时，由宿主本地收尾计划状态，避免只为改 to-do 再多调用一轮模型
def finish_final_response_if_needed(response) -> bool:
    if response.stop_reason == "tool_use":
        return False

    in_progress_items = []
    for plan_item in TODO.state.items:
        if plan_item.status == "in_progress":
            in_progress_items.append(plan_item)
    if len(in_progress_items) == 1:
        in_progress_items[0].status = "completed"
        print("> TODO：")
        print(TODO.render())
    return True


# ⭐️ 把一轮 tool_use 的执行细节收进这里，主循环只保留编排骨架
# s07/s08、工具执行顺序现在是：PreToolUse hook -> permission -> tool execute -> PostToolUse hook
def handle_tool_use_round(response, messages: list) -> None:
    results = [] # 工具结果
    used_todo = False
    manual_compact = False
    compact_focus = None

    for block in response.content:
        if block.type != "tool_use":
            continue
        try:
            block_results, tool_name, executed, tool_input = run_tool_with_hooks(block)
        except Exception as e:
            # 异常捕获 -> 异常喂给模型 -> 下一轮决策
            block_results = [{
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"错误：{e}",
            }]
            tool_name = block.name
            executed = False
            tool_input = dict(block.input or {})

        if tool_name == "compact" and executed:
            manual_compact = True # s06、允许模型显式请求 compact，而不是只能被动等到自动触发
            compact_focus = tool_input.get("focus")

        if tool_name != "task":
            for block_result in block_results:
                if block_result["type"] == "tool_result":
                    print(f"> {tool_name}:")
                    print(str(block_result["content"])[:200])
                    break

        results.extend(block_results)
        if tool_name == "todo" and executed:
            used_todo = True

    # s03：如果使用 to-do 更新计划、就清空 “连续多少轮没更新计划” 的计数器，否则就累计，超过 3 次没 to-do 提醒
    if used_todo:
        TODO.state.rounds_since_update = 0
    else:
        TODO.note_round_without_update()
        reminder = TODO.reminder()
        if reminder:
            results.insert(0, {"type": "text", "text": reminder})
    messages.append({"role": "user", "content": results})

    # s06：先把本轮 compact 工具的 tool_result 写回历史，再整体压缩，避免丢失这次决策痕迹
    if manual_compact:
        compacted_messages, transcript_path = compact_history(
            WORKDIR,
            client,
            MODEL,
            messages,
            COMPACT_STATE,
            focus=compact_focus,
        )
        messages[:] = compacted_messages
        print(f"[manual compact] 已保存 transcript: {transcript_path.relative_to(WORKDIR)}")


# ⭐️核心循环
def agent_loop(messages: list):
    while True:
        # 先判断是否需要较早的 tool_result、上下文是否到阈值
        prepare_messages_for_next_turn(messages)

        # LLM 调用
        response = client.messages.create(
            model=MODEL,
            system=build_system_prompt(), # 基础系统 prompt + memory
            messages=normalize_messages(messages),
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        # 判断是否结束
        if finish_final_response_if_needed(response):
            return

        # tool_use
        handle_tool_use_round(response, messages)

# 列表 list 提取文本
def extract_text(content) -> str:
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


# 用户命令分发单独收口，避免 __main__ 持续膨胀
# 返回 True 表示命中了内置命令，本轮输入已经处理完成
# 返回 False 表示这不是内置命令，继续交给 agent 处理
def handle_user_command(query: str) -> bool:
    # s07、/mode 用来在运行时切换权限模式，便于实验三种模式的区别
    if query.startswith("/mode"):
        parts = query.split()
        if len(parts) == 2 and parts[1] in MODES:
            PERMISSION_MANAGER.mode = parts[1]
            DREAM_CONSOLIDATOR.mode = parts[1]
            print(f"[已切换到 {parts[1]} 模式]")
        else:
            print(f"用法：/mode <{'|'.join(MODES)}>")
        return True

    # s07、/rules 直接打印当前规则，方便观察 deny / allow / always 追加后的结果
    if query.strip() == "/rules":
        for index, rule in enumerate(PERMISSION_MANAGER.rules):
            print(f"  {index}: {rule}")
        return True

    # s09、/memories 直接列出当前已加载的长期记忆摘要，方便观察 save_memory 的效果
    if query.strip() == "/memories":
        summary_lines = MEMORY_MANAGER.summary_lines()
        if summary_lines:
            for line in summary_lines:
                print(f"  {line}")
        else:
            print("  （当前没有长期记忆）")
        return True

    return False


if __name__ == "__main__":
    # s09、REPL 启动时先装载长期记忆，让后续动态 system prompt 能立刻看到历史偏好与项目事实
    MEMORY_MANAGER.load_all()
    memory_count = len(MEMORY_MANAGER.memories)
    if memory_count:
        print(f"[已加载 {memory_count} 条长期记忆]")
    else:
        print("[当前没有长期记忆，可在合适时使用 save_memory 工具保存]")

    # s08、REPL 启动时先触发一次 SessionStart，演示“不改主循环也能挂载启动行为”的扩展点
    HOOK_MANAGER.run_hooks("SessionStart", {"tool_name": "", "tool_input": {}})

    # s07、教学版 REPL：启动时先选权限模式，方便直接观察 default / plan / auto 的差异
    print(f"权限模式：{'、'.join(MODES)}")
    mode_input = input("模式（default）：").strip().lower() or "default"
    if mode_input in MODES:
        PERMISSION_MANAGER.mode = mode_input
        DREAM_CONSOLIDATOR.mode = mode_input
    else:
        print("[未知权限模式，已回退到 default]")
    print(f"[当前权限模式：{PERMISSION_MANAGER.mode}]")

    history = []
    while True:
        try:
            query = input("\033[36m请输入指令：>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        if handle_user_command(query):
            continue

        history.append({"role": "user", "content": query})

        # ⭐⭐⭐ 核心入口
        agent_loop(history)
        # ⭐⭐⭐

        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()
