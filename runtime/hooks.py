from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


# s08、教学版先保留三个最清晰的 hook 事件
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart")
HOOK_TIMEOUT = 30  # seconds


# s08、workspace trust marker 保持教学版最小实现
TRUST_MARKER_NAME = ".claude_trusted"
HOOK_CONFIG_NAME = ".hooks.json"


class HookManager:
    """
    加载并执行 .hooks.json 中声明的 hooks。

    教学版只做三件事：
    1. 读取 hook 定义
    2. 执行匹配当前事件的命令
    3. 汇总 block / message / input 覆盖结果给宿主循环
    """

    def __init__(self, config_path: Path | None = None, sdk_mode: bool = False, workdir: Path | None = None):
        self.hooks = {event: [] for event in HOOK_EVENTS}
        self._sdk_mode = sdk_mode
        self.workdir = Path(workdir) if workdir else None
        self.config_path = Path(config_path) if config_path else None
        if self.config_path is None:
            base_dir = self.workdir or Path.cwd()
            self.config_path = base_dir / HOOK_CONFIG_NAME
        if self.workdir is None:
            self.workdir = self.config_path.parent
        self.trust_marker = self.workdir / ".claude" / TRUST_MARKER_NAME
        self._load_hooks()

    # s08、把配置加载单独收口，便于宿主侧直接复用
    def _load_hooks(self) -> None:
        if not self.config_path.exists():
            return
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            for event in HOOK_EVENTS:
                self.hooks[event] = config.get("hooks", {}).get(event, [])
            print(f"[已加载 hooks 配置：{self.config_path}]")
        except Exception as e:
            print(f"[hook 配置解析失败：{e}]")

    # s08、教学版信任门：未信任工作区时，hooks 整体不执行
    def _check_workspace_trust(self) -> bool:
        if self._sdk_mode:
            return True
        return self.trust_marker.exists()

    # s08、执行 hook，并把结果汇总给调用方
    def run_hooks(self, event: str, context: dict | None = None) -> dict:
        result = {"blocked": False, "messages": []}
        if not self._check_workspace_trust():
            return result

        hooks = self.hooks.get(event, [])

        # --- hooks start ---
        for hook_def in hooks:
            matcher = hook_def.get("matcher")
            if matcher and context:
                tool_name = context.get("tool_name", "")
                if matcher != "*" and matcher != tool_name:
                    continue

            command = hook_def.get("command", "")
            if not command:
                continue

            env = dict(os.environ) # 构造 hook 子进程环境变量
            if context:
                env["HOOK_EVENT"] = event
                env["HOOK_TOOL_NAME"] = context.get("tool_name", "")
                env["HOOK_TOOL_INPUT"] = json.dumps(context.get("tool_input", {}), ensure_ascii=False)[:10000]
                if "tool_output" in context:
                    env["HOOK_TOOL_OUTPUT"] = str(context["tool_output"])[:10000]

            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=HOOK_TIMEOUT,
                )
                self._collect_hook_result(completed, event, context, result)
            except subprocess.TimeoutExpired:
                print(f"  [hook:{event}] 执行超时（{HOOK_TIMEOUT}s）")
            except Exception as e:
                print(f"  [hook:{event}] 执行异常：{e}")
            # --- hooks end ---

        return result

    # 这里的 returncode 是假设约定好的脚本的返回。如：python -c "import sys; sys.exit(2)"
    def _collect_hook_result(self, completed, event: str, context: dict | None, result: dict) -> None:
        if completed.returncode == 0:
            stdout = completed.stdout.strip()
            if stdout:
                print(f"  [hook:{event}] {stdout[:100]}")
            self._apply_structured_stdout(stdout, context, result)
            return

        if completed.returncode == 1:
            result["blocked"] = True
            reason = completed.stderr.strip() or "被 hook 阻止"
            result["block_reason"] = reason
            print(f"  [hook:{event}] 已阻止：{reason[:200]}")
            return

        if completed.returncode == 2:
            message = completed.stderr.strip()
            if message:
                result["messages"].append(message)
                print(f"  [hook:{event}] 注入消息：{message[:200]}")

    def _apply_structured_stdout(self, stdout: str, context: dict | None, result: dict) -> None:
        if not stdout:
            return
        try:
            hook_output = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return

        if "updatedInput" in hook_output and context:
            context["tool_input"] = hook_output["updatedInput"]
        if "additionalContext" in hook_output:
            result["messages"].append(hook_output["additionalContext"])
        if "permissionDecision" in hook_output:
            result["permission_override"] = hook_output["permissionDecision"]
