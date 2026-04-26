from __future__ import annotations

import os
import re
from pathlib import Path


# s09、教学版长期记忆只保留四类最常见的信息
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MAX_INDEX_LINES = 200
MEMORY_DIR_NAME = ".memory"
MEMORY_INDEX_NAME = "MEMORY.md"
MEMORY_GUIDANCE = """
什么时候应该保存 memory：
- 用户明确表达偏好（如：我喜欢 tabs、默认用 pytest）-> user
- 用户纠正你的做法（如：不要这样做、上次错在这里）-> feedback
- 学到当前代码里不容易直接推导出的项目事实 -> project
- 学到外部资源位置（面板、文档、看板）-> reference
什么时候不应该保存：
- 代码结构、函数签名、目录布局
- 临时任务状态（当前分支、当前待办、这轮正在做什么）
- 密钥、令牌、密码等敏感信息
""".strip()


class MemoryManager:
    """
    加载、整理并保存可跨会话复用的长期记忆。

    教学版保持最直接的结构：
    1. 每条记忆一个 markdown 文件
    2. 用 MEMORY.md 维护紧凑索引
    3. 由宿主按需把记忆内容注入 system prompt
    """

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = Path(memory_dir) if memory_dir else Path.cwd() / MEMORY_DIR_NAME
        self.index_path = self.memory_dir / MEMORY_INDEX_NAME
        self.memories: dict[str, dict] = {}

    def load_all(self) -> None:
        self.memories = {}
        if not self.memory_dir.exists():
            return
        for md_file in sorted(self.memory_dir.glob("*.md")):
            if md_file.name == MEMORY_INDEX_NAME:
                continue
            parsed = self._parse_frontmatter(md_file.read_text(encoding="utf-8"))
            if not parsed:
                continue
            name = parsed.get("name", md_file.stem)
            self.memories[name] = {
                "description": parsed.get("description", ""),
                "type": parsed.get("type", "project"),
                "content": parsed.get("content", ""),
                "file": md_file.name,
            }

    # 读取 memory
    # {
    #   "Prefer Tabs": {
    #     "description": "用户偏好使用 tabs",
    #     "type": "user",
    #     "content": "以后默认使用 tabs。"
    #   }
    # }
    # 输出：
    # [
    #       "# 长期记忆（跨会话保留）",
    #       "",
    #       "## [user]",
    #       "### Prefer Tabs: 用户偏好使用 tabs",
    #       "以后默认使用 tabs。",
    #       "",
    # ]
    def load_memory_prompt(self) -> str:
        if not self.memories:
            return ""
        sections = ["# 长期记忆（跨会话保留）", ""]
        for mem_type in MEMORY_TYPES:
            typed = {k: v for k, v in self.memories.items() if v["type"] == mem_type}
            if not typed:
                continue
            sections.append(f"## [{mem_type}]")
            for name, memory in typed.items():
                sections.append(f"### {name}: {memory['description']}")
                if memory["content"].strip():
                    sections.append(memory["content"].strip())
                sections.append("")
        return "\n".join(sections).strip()

    # 保存 memory 文件
    def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
        if mem_type not in MEMORY_TYPES:
            return f"错误：memory 类型必须属于 {MEMORY_TYPES}"
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
        if not safe_name:
            return "错误：memory 名称无效"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {mem_type}\n"
            f"---\n"
            f"{content}\n"
        )
        file_name = f"{safe_name}.md"
        file_path = self.memory_dir / file_name
        file_path.write_text(frontmatter, encoding="utf-8")
        self.memories[name] = {
            "description": description,
            "type": mem_type,
            "content": content,
            "file": file_name,
        }
        self._rebuild_index()
        return f"已保存长期记忆：{name} [{mem_type}]"

    # 重建 memory 文件索引
    def _rebuild_index(self) -> None:
        lines = ["# Memory Index", ""]
        for name, memory in self.memories.items():
            lines.append(f"- {name}: {memory['description']} [{memory['type']}]")
            if len(lines) >= MAX_INDEX_LINES:
                lines.append(f"...（已在 {MAX_INDEX_LINES} 行处截断）")
                break
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 加载 memory 文件时，格式化 md 文件
    def _parse_frontmatter(self, text: str) -> dict | None:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return None
        header, body = match.group(1), match.group(2)
        result = {"content": body.strip()}
        for line in header.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result

    # 用户输入 /memories 指令时，列举当前的 memory 文件摘要
    def summary_lines(self) -> list[str]:
        lines = []
        for name, memory in self.memories.items():
            lines.append(f"[{memory['type']}] {name}: {memory['description']}")
        return lines


class DreamConsolidator:
    """
    s09、Dream 是长期记忆的低频整理器。

    教学版保留它的 gate / phase / lock 思想，重点让读者看到：
    记忆系统除了“会写”，还要考虑“会不会越积越乱”。
    """

    COOLDOWN_SECONDS = 86400
    SCAN_THROTTLE_SECONDS = 600
    MIN_SESSION_COUNT = 5
    LOCK_STALE_SECONDS = 3600
    PHASES = [
        "梳理：扫描 MEMORY.md 的结构与分类",
        "收集：读取各条 memory 正文",
        "合并：归并重复或相关记忆",
        "裁剪：确保索引不超过上限",
    ]

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = Path(memory_dir) if memory_dir else Path.cwd() / MEMORY_DIR_NAME
        self.lock_file = self.memory_dir / ".dream_lock"
        self.enabled = True
        self.mode = "default"
        self.last_consolidation_time = 0.0
        self.last_scan_time = 0.0
        self.session_count = 0

    def should_consolidate(self) -> tuple[bool, str]:
        import time

        now = time.time()
        if not self.enabled:
            return False, "Gate 1：Dream 已禁用"
        if not self.memory_dir.exists():
            return False, "Gate 2：memory 目录不存在"
        memory_files = [f for f in self.memory_dir.glob("*.md") if f.name != MEMORY_INDEX_NAME]
        if not memory_files:
            return False, "Gate 2：当前没有 memory 文件"
        if self.mode == "plan":
            return False, "Gate 3：plan mode 不执行 Dream 整理"
        time_since_last = now - self.last_consolidation_time
        if time_since_last < self.COOLDOWN_SECONDS:
            remaining = int(self.COOLDOWN_SECONDS - time_since_last)
            return False, f"Gate 4：冷却中，还需 {remaining} 秒"
        time_since_scan = now - self.last_scan_time
        if time_since_scan < self.SCAN_THROTTLE_SECONDS:
            remaining = int(self.SCAN_THROTTLE_SECONDS - time_since_scan)
            return False, f"Gate 5：扫描节流中，还需 {remaining} 秒"
        if self.session_count < self.MIN_SESSION_COUNT:
            return False, f"Gate 6：当前会话数只有 {self.session_count}，至少需要 {self.MIN_SESSION_COUNT}"
        if not self._acquire_lock():
            return False, "Gate 7：已有其他进程持有 Dream 锁"
        return True, "All 7 gates passed"

    def consolidate(self) -> list[str]:
        import time

        can_run, reason = self.should_consolidate()
        if not can_run:
            print(f"[Dream] 暂不整理：{reason}")
            return []
        print("[Dream] 开始整理长期记忆...")
        self.last_scan_time = time.time()
        completed_phases = []
        for index, phase in enumerate(self.PHASES, 1):
            print(f"[Dream] Phase {index}/4: {phase}")
            completed_phases.append(phase)
        self.last_consolidation_time = time.time()
        self._release_lock()
        print(f"[Dream] 整理完成：共执行 {len(completed_phases)} 个阶段")
        return completed_phases

    def _acquire_lock(self) -> bool:
        import time

        if self.lock_file.exists():
            try:
                lock_data = self.lock_file.read_text(encoding="utf-8").strip()
                pid_str, timestamp_str = lock_data.split(":", 1)
                pid = int(pid_str)
                lock_time = float(timestamp_str)
                if (time.time() - lock_time) > self.LOCK_STALE_SECONDS:
                    self.lock_file.unlink()
                else:
                    try:
                        os.kill(pid, 0)
                        return False
                    except OSError:
                        self.lock_file.unlink()
            except (ValueError, OSError):
                self.lock_file.unlink(missing_ok=True)
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.lock_file.write_text(f"{os.getpid()}:{time.time()}", encoding="utf-8")
            return True
        except OSError:
            return False

    def _release_lock(self) -> None:
        try:
            if self.lock_file.exists():
                lock_data = self.lock_file.read_text(encoding="utf-8").strip()
                pid_str = lock_data.split(":")[0]
                if int(pid_str) == os.getpid():
                    self.lock_file.unlink()
        except (ValueError, OSError):
            pass
