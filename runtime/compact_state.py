from dataclasses import dataclass, field
import json
import time
from pathlib import Path


# s06、上下文压缩运行时状态
CONTEXT_LIMIT = 50000
KEEP_RECENT_TOOL_RESULTS = 3 # 只保留最近 3 个工具结果的完整内容
PERSIST_THRESHOLD = 30000 # 大输出落盘
PREVIEW_CHARS = 2000 # bash 命令预览的字符
TRANSCRIPT_DIR_NAME = ".transcripts"
TOOL_RESULTS_DIR_NAME = ".task_outputs/tool-results"


@dataclass
class CompactState:
    has_compacted: bool = False
    just_compacted: bool = False
    last_summary: str = ""
    recent_files: list[str] = field(default_factory=list)


# s06、粗略估算当前消息上下文大小
def estimate_context_size(messages: list) -> int:
    return len(str(messages))


# s06、记录最近访问文件，方便 compact 后恢复上下文
def track_recent_file(state: CompactState, path: str) -> None:
    if path in state.recent_files:
        state.recent_files.remove(path)
    state.recent_files.append(path)
    if len(state.recent_files) > 5:
        state.recent_files[:] = state.recent_files[-5:]


# s06、收集历史中的 tool_result block，供微压缩使用
def collect_tool_result_blocks(messages: list) -> list[tuple[int, int, dict]]:
    blocks = []
    for message_index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append((message_index, block_index, block))
    return blocks


# s06、把较旧的工具结果压缩成短占位，保留最近几条完整结果
def micro_compact(messages: list) -> list:
    tool_results = collect_tool_result_blocks(messages)
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, block in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        content = block.get("content", "")
        if not isinstance(content, str) or len(content) <= 120:
            continue
        block["content"] = "【更早的工具结果已压缩；如需完整细节，请重新执行对应工具。】"
    return messages


# s06、大输出落盘，并向模型返回预览占位
def persist_large_output(workdir: Path, tool_use_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    tool_results_dir = workdir / ".task_outputs" / "tool-results"
    tool_results_dir.mkdir(parents=True, exist_ok=True)
    stored_path = tool_results_dir / f"{tool_use_id}.txt"
    if not stored_path.exists():
        stored_path.write_text(output)
    preview = output[:PREVIEW_CHARS]
    rel_path = stored_path.relative_to(workdir)
    return (
        "<persisted-output>\n"
        f"完整输出已保存到：{rel_path}\n"
        "预览：\n"
        f"{preview}\n"
        "</persisted-output>"
    )


# s06、把当前消息写到 transcript 文件，便于 compact 后追溯
def write_transcript(workdir: Path, messages: list) -> Path:
    transcript_dir = workdir / TRANSCRIPT_DIR_NAME
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"transcript_{int(time.time())}.jsonl"
    with path.open("w") as handle:
        for message in messages:
            handle.write(json.dumps(message, default=str, ensure_ascii=False) + "\n")
    return path


# s06、请求 LLM，把旧会话摘要成一段可续写的说明
def summarize_history(client, model: str, messages: list) -> str:
    conversation = json.dumps(messages, default=str, ensure_ascii=False)[:80000]
    prompt = (
        "请把这段 coding agent 对话总结成可继续工作的摘要。\n"
        "请务必保留：\n"
        "1. 当前目标\n"
        "2. 重要发现与决策\n"
        "3. 已读取或修改过的文件\n"
        "4. 剩余工作\n"
        "5. 用户的约束与偏好\n"
        "要求：简洁，但要具体。\n\n"
        f"{conversation}"
    )
    response = client.messages.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    texts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if texts:
        return "\n".join(texts).strip()
    raise ValueError("错误：摘要结果中没有可用文本")


# s06、提取摘要：将整段历史压缩为一条新的 user 摘要消息
def compact_history(
    workdir: Path,
    client,
    model: str,
    messages: list,
    state: CompactState,
    focus: str | None = None,
) -> tuple[list, Path]:

    transcript_path = write_transcript(workdir, messages)

    # LLM 摘要
    summary = summarize_history(client, model, messages)

    if focus:
        summary += f"\n\n下一步请重点保留：{focus}"
    if state.recent_files:
        recent_lines = "\n".join(f"- {path}" for path in state.recent_files)
        summary += f"\n\n如有需要，可优先重新打开这些最近文件：\n{recent_lines}"
    state.has_compacted = True
    state.just_compacted = True
    state.last_summary = summary
    new_messages = [{
        "role": "user",
        "content": (
            "这段对话已经执行过 compact，以便 agent 在更小的上下文中继续工作。\n\n"
            f"原始 transcript 已保存到：{transcript_path.relative_to(workdir)}\n\n"
            f"{summary}"
        ),
    }]
    return new_messages, transcript_path
