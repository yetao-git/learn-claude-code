from dataclasses import dataclass, field


@dataclass
class PlanItem:
    content: str  # 当前会话的 plan 内容
    status: str = "pending"  # plan 状态
    active_form: str = ""  # 正在进行中时，可以用更自然的进行时描述


@dataclass
class PlanningState:
    items: list[PlanItem] = field(default_factory=list)
    rounds_since_update: int = 0


# 03、定义当前会话的计划管理器
class TodoManager:
    def __init__(self, reminder_interval: int = 3):
        self.state = PlanningState()
        self.reminder_interval = reminder_interval

    # 03、整体更新当前计划
    def update(self, items: list) -> str:
        if len(items) > 12:
            raise ValueError("会话计划最多保留 12 项")
        normalized = []
        in_progress_count = 0
        for index, raw_item in enumerate(items):
            content = str(raw_item.get("content", "")).strip()
            status = str(raw_item.get("status", "pending")).lower()
            active_form = str(raw_item.get("activeForm", "")).strip()
            if not content:
                raise ValueError(f"第 {index} 项缺少 content")
            if status not in {"pending", "in_progress", "completed"}:
                raise ValueError(f"第 {index} 项的状态非法: {status}")
            if status == "in_progress":
                in_progress_count += 1
            normalized.append(PlanItem(
                content=content,
                status=status,
                active_form=active_form,
            ))
        if in_progress_count > 1:
            raise ValueError("同一时间只能有一个计划项处于 in_progress")
        self.state.items = normalized
        self.state.rounds_since_update = 0
        return self.render()

    def note_round_without_update(self) -> None:
        self.state.rounds_since_update += 1

    # 03、如果连续几轮没更新计划，就提醒
    def reminder(self) -> str | None:
        if not self.state.items:
            return None
        if self.state.rounds_since_update < self.reminder_interval:
            return None
        return "<reminder>请先刷新当前计划，再继续后续工作。</reminder>"

    # 03、把计划渲染成可读文本
    def render(self) -> str:
        if not self.state.items:
            return "当前还没有会话计划。"
        lines = []
        for item in self.state.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[✅]",
            }[item.status]
            line = f"{marker} {item.content}"
            if item.status == "in_progress" and item.active_form:
                line += f" ({item.active_form})"
            lines.append(line)
        completed = sum(1 for item in self.state.items if item.status == "completed")
        lines.append(f"\n({completed}/{len(self.state.items)} completed)")
        return "\n".join(lines)
