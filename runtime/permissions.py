from __future__ import annotations

import json
import re
from fnmatch import fnmatch


# s07、教学版先保留三个清晰模式：default / plan / auto
MODES = ("default", "plan", "auto")

# s07、最小必要工具分类：读工具可自动放行，写工具与危险执行需经过权限流水线
READ_ONLY_TOOLS = {"read_file", "load_skill"}
WRITE_TOOLS = {"write_file", "edit_file", "bash", "task", "save_memory"}
ALWAYS_ALLOW_TOOLS = {"todo", "compact"}


# s07、bash 命令的前置风险校验器；先识别明显危险模式，再决定 deny 还是 ask
class BashSecurityValidator:
    VALIDATORS = [
        ("shell_metachar", r"[;&|`$]"),
        ("sudo", r"\bsudo\b"),
        ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),
        ("cmd_substitution", r"\$\("),
        ("ifs_injection", r"\bIFS\s*="),
    ]

    def validate(self, command: str) -> list[tuple[str, str]]:
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures

    def describe_failures(self, command: str) -> str:
        failures = self.validate(command)
        if not failures:
            return "未发现明显风险"
        parts = [f"{name}（模式：{pattern}）" for name, pattern in failures]
        return "命中了安全校验项：" + "，".join(parts)


bash_validator = BashSecurityValidator()


# s07、规则按顺序匹配，先命中先生效
DEFAULT_RULES = [
    {"tool": "bash", "content": "rm -rf /", "behavior": "deny"},
    {"tool": "bash", "content": "sudo *", "behavior": "deny"},
    {"tool": "read_file", "path": "*", "behavior": "allow"},
    {"tool": "load_skill", "path": "*", "behavior": "allow"},
]


# s07、权限系统核心：deny rules -> mode -> allow rules -> ask user
class PermissionManager:
    def __init__(self, mode: str = "default", rules: list[dict] | None = None):
        if mode not in MODES:
            raise ValueError(f"未知权限模式：{mode}，可选值：{MODES}")
        self.mode = mode
        self.rules = rules or list(DEFAULT_RULES)
        self.consecutive_denials = 0
        self.max_consecutive_denials = 3

    # 0. 优先校验 bash 是否危险
    #   |
    # 1. deny rules     -> 命中了就拒绝
    #   |
    # 2. mode check     -> 根据当前模式决定
    #   |
    # 3. allow rules    -> 命中了就放行
    #   |
    # 4. ask user       -> 剩下的交给用户确认
    def check(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name in ALWAYS_ALLOW_TOOLS:
            return {"behavior": "allow", "reason": "教学工具默认允许"}

        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = bash_validator.validate(command)
            if failures:
                severe = {"sudo", "rm_rf"}
                severe_hits = [failure for failure in failures if failure[0] in severe]
                description = bash_validator.describe_failures(command)
                if severe_hits:
                    return {"behavior": "deny", "reason": f"Bash 校验器拒绝：{description}"}
                return {"behavior": "ask", "reason": f"Bash 校验器提示确认：{description}"}

        for rule in self.rules:
            if rule["behavior"] != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "deny", "reason": f"命中 deny 规则：{rule}"}

        if self.mode == "plan":
            if tool_name in WRITE_TOOLS:
                return {"behavior": "deny", "reason": "Plan mode：写操作已被阻止"}
            return {"behavior": "allow", "reason": "Plan mode：允许只读操作"}

        if self.mode == "auto":
            if tool_name in READ_ONLY_TOOLS:
                return {"behavior": "allow", "reason": "Auto mode：只读工具自动放行"}

        for rule in self.rules:
            if rule["behavior"] != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                self.consecutive_denials = 0
                return {"behavior": "allow", "reason": f"命中 allow 规则：{rule}"}

        return {"behavior": "ask", "reason": f"没有命中权限规则，需要确认：{tool_name}"}

    # 询问用户工具是否执行
    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n  [权限确认] {tool_name}: {preview}")
        try:
            answer = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "always":
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True
        if answer in ("y", "yes"):
            self.consecutive_denials = 0
            return True

        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(f"  [连续拒绝 {self.consecutive_denials} 次 —— 可以考虑切到 plan mode]")
        return False

    # 比较 rule 中的字段值 和 tool_input 字段值是否匹配
    def _matches(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        # tool 名称是否匹配
        # 如：不匹配
        # rule = {"tool": "bash", "behavior": "deny"}
        # tool_name = "read_file"
        if rule.get("tool") and rule["tool"] != "*" and rule["tool"] != tool_name:
            return False

        # 路径是否匹配
        # 不匹配：
        # rule = {"tool": "read_file", "path": "docs/*", "behavior": "allow"}
        # tool_input = {"path": "src/main.py"}
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False

        # 命令是否匹配
        # 匹配：
        # rule = {"tool": "bash", "content": "sudo *", "behavior": "deny"}
        # tool_input = {"command": "sudo apt install git"}
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False

        return True
