---
name: example-skill
description: Use when the task needs a minimal demo skill to verify that load_skill can discover and load skills correctly.
---

# 示例技能

## 作用
这是一个最小可用的示例技能，用来验证 `load_skill` 是否能正确发现并加载技能正文。

## 何时使用
- 需要演示 `skills/` 目录结构时
- 需要验证 `SkillRegistry` 能否扫描到 `SKILL.md` 时
- 需要确认 `load_skill` 工具已经接通时

## 示例建议
当你读取到这个技能后，可以告诉用户：
- 当前 skills 机制已生效
- skill 会先以摘要形式出现在 system prompt 中
- 只有调用 `load_skill` 后，完整正文才会进入上下文
