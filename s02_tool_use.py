#!/usr/bin/env python3
# 运行框架：工具分发 —— 扩展模型可触达的能力范围。
"""
s02_tool_use.py - 工具分发 + 消息规范化
s01 中的 agent 循环本身没有变化。这里新增了 dispatch map，
并加入了 normalize_messages()，在每次调用 API 之前整理消息列表。

关键洞察："循环本身完全没变，我只是增加了工具。"
"""
import os
import subprocess
from pathlib import Path

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
client = Anthropic(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://127.0.0.1:8317",
)
# MODEL = os.environ["MODEL_ID"]
MODEL = "gpt-5.4"
SYSTEM = f"你是一个位于 {WORKDIR} 的 coding agent。请使用工具解决任务，先行动，不要空讲。"


def safe_path(path_str: str) -> Path:
    path = (WORKDIR / path_str).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"路径越过了当前工作区边界: {path_str}")
    return path


def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "错误：已拦截危险命令"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "（无输出）"
    except subprocess.TimeoutExpired:
        return "错误：命令执行超时（120 秒）"


def run_read(path: str, limit: int = None) -> str:
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"……（其余 {len(lines) - limit} 行已省略）"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"错误：{e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"已写入 {len(content)} 个字节到 {path}"
    except Exception as e:
        return f"错误：{e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        content = file_path.read_text()
        if old_text not in content:
            return f"错误：在 {path} 中未找到要替换的文本"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"已完成对 {path} 的编辑"
    except Exception as e:
        return f"错误：{e}"


# -- 并发安全分类 --
# 只读工具可以安全并行运行；修改型工具必须串行执行。
CONCURRENCY_SAFE = {"read_file"}
CONCURRENCY_UNSAFE = {"write_file", "edit_file"}

# -- dispatch map：{tool_name: handler} --
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
}

TOOLS = [
    {
        "name": "bash",
        "description": "在当前工作区中运行 shell 命令.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取文件内容.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "把内容写入文件.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "精确替换文件中的文本.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
]


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


def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL,
            system=SYSTEM,
            messages=normalize_messages(messages),
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"未知工具：{block.name}"
                print(f"> {block.name}:")
                print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(block.text)
        print()
