# s04、工具 schema 常量

BASE_TOOLS = [
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

CHILD_TOOLS = BASE_TOOLS

# s05、把指定技能的完整内容加载到当前上下文中
LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": "把指定技能的完整内容加载到当前上下文中，供后续决策使用.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
    },
}

TODO_TOOL = {
    "name": "todo",
    "description": "重写当前会话的多步骤计划.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {
                            "type": "string",
                            "description": "可选的当计划正在进行中时，可以用更自然的进行时描述.",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["items"],
    },
}

TASK_TOOL = {
    "name": "task",
    "description": "启动一个拥有全新上下文的子代理来完成子任务，并只返回总结结果.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "description": {
                "type": "string",
                "description": "对子任务的简短说明.",
            },
        },
        "required": ["prompt"],
    },
}

# s06、把更早的对话压缩成摘要，继续在较小上下文中工作
COMPACT_TOOL = {
    "name": "compact",
    "description": "把更早的对话压缩成摘要，方便在较小上下文中继续工作.",
    "input_schema": {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": "可选，说明 compact 后下一步最应保留的重点.",
            },
        },
    },
}

# s09、把值得跨会话保留的信息写入长期记忆
SAVE_MEMORY_TOOL = {
    "name": "save_memory",
    "description": "把值得跨会话保留的信息写入长期记忆.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "简短名称，例如 prefer_tabs"},
            "description": {"type": "string", "description": "一句话概括这条记忆保存了什么"},
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": "记忆分类：用户偏好、用户反馈、项目事实、外部资源指针",
            },
            "content": {"type": "string", "description": "记忆正文，可为多行"},
        },
        "required": ["name", "description", "type", "content"],
    },
}

TOOLS = [*BASE_TOOLS, LOAD_SKILL_TOOL, TODO_TOOL, TASK_TOOL, COMPACT_TOOL, SAVE_MEMORY_TOOL]
