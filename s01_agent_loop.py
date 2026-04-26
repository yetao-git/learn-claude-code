#!/usr/bin/env python3
# 运行框架：通过循环不断把真实工具结果回填给模型。
"""
s01_agent_loop.py - Agent 循环
这个文件演示了一个最小但实用的 coding agent 模式：
    用户消息
      -> 模型回复
      -> 如果出现 tool_use：执行工具
      -> 把 tool_result 写回消息历史
      -> 继续下一轮

这里故意把循环保持得很小，但同时把循环状态显式写出来，
这样后续章节就可以在同一套结构上逐步扩展。
"""
import os
import subprocess
from dataclasses import dataclass

# 尝试启用更友好的命令行输入体验；在不支持 readline 的平台上则直接跳过。
try:
    import readline
    # #143 UTF-8 backspace fix for macOS libedit
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
    readline.parse_and_bind('set enable-meta-keybindings on')
except ImportError:
    pass
from anthropic import Anthropic
from dotenv import load_dotenv

# 先从 .env 加载配置，便于本地通过环境变量切换模型、网关地址和认证信息。
load_dotenv(override=True)
if "http://127.0.0.1:8317":
    # 走自定义网关时移除默认认证变量，避免和 base_url 模式冲突。
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 初始化模型客户端；这里显式传入 api_key 和 base_url，便于直接看清请求配置来源。
client = Anthropic(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://127.0.0.1:8317",
)
# MODEL = os.environ["MODEL_ID"]
MODEL = "gpt-5.4"

SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to inspect and change the workspace. Act first, then report clearly."
)
# 这里故意只暴露一个最小工具：bash。
# 教程的重点不是工具系统本身，而是“模型发起工具调用 -> 本地执行 -> 结果回填给模型”的闭环。
TOOLS = [{
    "name": "bash",
    "description": "在当前工作区中运行 shell 命令.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]

@dataclass
class LoopState:
    # 最小循环状态：保留完整消息历史、当前轮次，以及继续下一轮的原因。
    # 后续章节如果要加入暂停、恢复、调试信息，都可以从这里自然扩展。
    messages: list
    turn_count: int = 1
    transition_reason: str | None = None

def run_bash(command: str) -> str:
    # 这里做一层极简安全兜底，避免教程代码直接执行明显危险的命令。
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"
    output = (result.stdout + result.stderr).strip()
    return output[:50000] if output else "(no output)"

def extract_text(content) -> str:
    # 最终展示时只提取文本块，忽略工具调用等非文本内容。
    if not isinstance(content, list):
        return ""
    texts = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()

def execute_tool_calls(response_content) -> list[dict]:
    # 把模型返回的 content 逐块扫描一遍，只处理其中的 tool_use 块。
    # 每执行完一个工具，都要把结果包装成 tool_result，后面再喂回给模型。
    results = []
    for block in response_content:
        if block.type != "tool_use":
            continue
        command = block.input["command"]
        print(f"\033[33m$ {command}\033[0m")

        output = run_bash(command)

        print(output[:200])
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
    return results

def run_one_turn(state: LoopState) -> bool:
    # 单轮的职责非常明确：
    # 1) 把当前消息历史发给模型
    # 2) 如果模型请求工具，就执行工具
    # 3) 把工具结果作为下一条 user 消息补回历史
    # 4) 告诉外层循环要不要继续
    response = client.messages.create(
        model=MODEL,
        system=SYSTEM,
        messages=state.messages,
        tools=TOOLS,
        max_tokens=8000,
    )
    # 这一步很重要！把 LLM 的结果追加到历史
    state.messages.append({"role": "assistant", "content": response.content})

    # 不调用工具就结束循环
    if response.stop_reason != "tool_use":
        state.transition_reason = None
        return False
    # 工具调用
    results = execute_tool_calls(response.content)

    if not results:
        state.transition_reason = None
        return False

    state.messages.append({"role": "user", "content": results})
    state.turn_count += 1
    state.transition_reason = "tool_result"
    return True

def agent_loop(state: LoopState) -> None:
    # 外层循环只关心一件事：只要上一轮返回“还要继续”，就进入下一轮。
    while run_one_turn(state):
        pass

if __name__ == "__main__":
    # history 会在多轮对话之间持续保留，因此这个脚本天然支持连续聊天。
    history = []
    while True:
        try:
            # - 终端里显示一个青色的提示符 s01 >>
            # - 然后等待用户输入
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})

        state = LoopState(messages=history)

        # 核心循环
        agent_loop(state)

        final_text = extract_text(history[-1]["content"])
        if final_text:
            print(final_text)
        print()