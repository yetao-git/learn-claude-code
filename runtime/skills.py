import re
from dataclasses import dataclass
from pathlib import Path


# s05、技能清单条目
@dataclass
class SkillManifest:
    name: str
    description: str
    path: Path


# s05、技能文档：元数据 + 正文
@dataclass
class SkillDocument:
    manifest: SkillManifest
    body: str


# s05、技能注册器：扫描、摘要展示、按需加载正文
class SkillRegistry:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.documents: dict[str, SkillDocument] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.skills_dir.exists():
            return
        for path in sorted(self.skills_dir.rglob("SKILL.md")):
            meta, body = self._parse_frontmatter(path.read_text())
            name = meta.get("name", path.parent.name)
            description = meta.get("description", "暂无说明")
            manifest = SkillManifest(name=name, description=description, path=path)
            self.documents[name] = SkillDocument(manifest=manifest, body=body.strip())

    def _parse_frontmatter(self, text: str) -> tuple[dict, str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        meta = {}
        for line in match.group(1).strip().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        return meta, match.group(2)

    def describe_available(self) -> str:
        if not self.documents:
            return "（当前没有可用技能）"
        lines = []
        for name in sorted(self.documents):
            manifest = self.documents[name].manifest
            lines.append(f"- {manifest.name}: {manifest.description}")
        return "\n".join(lines)

    def load_full_text(self, name: str) -> str:
        document = self.documents.get(name)
        if not document:
            known = "、".join(sorted(self.documents)) or "当前没有可用技能"
            return f"错误：未知技能“{name}”。可用技能：{known}"
        return (
            f"<skill name=\"{document.manifest.name}\">\n"
            f"{document.body}\n"
            "</skill>"
        )


def build_skills_dir(workdir: Path) -> Path:
    return workdir / "skills"


def build_skill_registry(workdir: Path) -> SkillRegistry:
    return SkillRegistry(build_skills_dir(workdir))
