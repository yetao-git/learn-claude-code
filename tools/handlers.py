import subprocess
from pathlib import Path

from learn_claude_code.runtime import MemoryManager, persist_large_output, track_recent_file


def safe_path(workdir: Path, path_str: str) -> Path:
    path = (workdir / path_str).resolve()
    if not path.is_relative_to(workdir):
        raise ValueError(f"路径越过了当前工作区边界: {path_str}")
    return path


def run_bash(workdir: Path, command: str, tool_use_id: str | None = None) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "错误：已拦截危险命令"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip() or "（无输出）"
        if tool_use_id:
            return persist_large_output(workdir, tool_use_id, output)[:50000]
        return output[:50000]
    except subprocess.TimeoutExpired:
        return "错误：命令执行超时（120 秒）"


def run_read(
    workdir: Path,
    path: str,
    limit: int = None,
    tool_use_id: str | None = None,
    compact_state=None,
) -> str:
    try:
        if compact_state is not None:
            track_recent_file(compact_state, path)
        text = safe_path(workdir, path).read_text()
        lines = text.splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"……（其余 {len(lines) - limit} 行已省略）"]
        output = "\n".join(lines)
        return output[:50000]
    except Exception as e:
        return f"错误：{e}"


def run_write(workdir: Path, path: str, content: str) -> str:
    try:
        file_path = safe_path(workdir, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return f"已写入 {len(content)} 个字节到 {path}"
    except Exception as e:
        return f"错误：{e}"


def run_edit(workdir: Path, path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(workdir, path)
        content = file_path.read_text()
        if old_text not in content:
            return f"错误：在 {path} 中未找到要替换的文本"
        file_path.write_text(content.replace(old_text, new_text, 1))
        return f"已完成对 {path} 的编辑"
    except Exception as e:
        return f"错误：{e}"


def build_tool_handlers(workdir: Path, compact_state=None, memory_manager=None) -> dict:
    if memory_manager is None:
        memory_manager = MemoryManager(workdir / ".memory")
        
    handlers = {
        "bash": lambda **kw: run_bash(workdir, kw["command"], kw.get("tool_use_id")),
        "read_file": lambda **kw: run_read(
            workdir,
            kw["path"],
            kw.get("limit"),
            kw.get("tool_use_id"), # 暂时不用了
            compact_state,
        ),
        "write_file": lambda **kw: run_write(workdir, kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(workdir, kw["path"], kw["old_text"], kw["new_text"]),
    }
    if memory_manager is not None:
        handlers["save_memory"] = lambda **kw: memory_manager.save_memory(
            kw["name"],
            kw["description"],
            kw["type"],
            kw["content"],
        )
    return handlers
