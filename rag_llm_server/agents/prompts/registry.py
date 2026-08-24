"""提示词中心（spec §5.7 / agent-designs §0.5）。

- 模板带 frontmatter：version / variables / changelog
- 加载全部模板并校验，失败进 errors（启动时打印警告，不崩进程）
- 运行时以 agent:kind[:version] 取用；面试会话开始即解析并固定版本（snapshot_versions）
"""
import json
import re
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_VERSION_SUFFIX_RE = re.compile(r"_v\d+(\.\d+)*$")


class Template:
    def __init__(self, name: str, meta: dict, body: str):
        self.name = name  # 如 interviewer:system
        self.version = meta["version"]
        self.variables = meta["variables"]
        self.changelog = meta.get("changelog", [])
        self.body = body

    def render(self, **values) -> str:
        """渲染模板。声明的变量必须全部提供；output_schema 为隐式可传变量。"""
        missing = [v for v in self.variables if v not in values]
        if missing:
            raise ValueError(f"模板 {self.name} 缺少变量: {missing}")
        return self.body.format(**values)


class PromptRegistry:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR):
        self._templates: dict[str, dict[str, Template]] = {}
        self._examples: dict[str, dict[str, dict]] = {}
        self._latest: dict[str, str] = {}
        self.errors: list[str] = []
        self._load(prompts_dir)

    def _load(self, prompts_dir: Path) -> None:
        for path in sorted(prompts_dir.rglob("*.md")):
            rel = path.relative_to(prompts_dir)
            name = f"{rel.parent.name}:{_VERSION_SUFFIX_RE.sub('', rel.stem)}"
            try:
                m = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                if not m:
                    raise ValueError("缺少 frontmatter（--- ... ---）")
                meta = yaml.safe_load(m.group(1)) or {}
                for req in ("version", "variables"):
                    if req not in meta:
                        raise ValueError(f"frontmatter 缺少 {req}")
                body = path.read_text(encoding="utf-8")[m.end():]
                version = str(meta["version"])
                self._templates.setdefault(name, {})[version] = Template(name, meta, body)
                self._latest[name] = max(self._templates[name])
            except Exception as e:  # 启动校验：失败进 errors，不崩进程
                self.errors.append(f"{name}: {e}")
        for path in sorted(prompts_dir.rglob("*.yaml")):
            rel = path.relative_to(prompts_dir)
            name = f"{rel.parent.name}:{_VERSION_SUFFIX_RE.sub('', rel.stem)}"
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "version" not in data:
                    raise ValueError("yaml 缺少 version 字段")
                version = str(data["version"])
                self._examples.setdefault(name, {})[version] = data
                self._latest[name] = max(self._examples[name])
            except Exception as e:
                self.errors.append(f"{name}: {e}")

    def get(self, agent: str, kind: str, version: str | None = None) -> Template:
        """按 agent:kind 取模板；version 缺省取最新。不存在抛 KeyError（调用方错误，须显式失败）。"""
        versions = self._templates[f"{agent}:{kind}"]
        return versions[version or self._latest[f"{agent}:{kind}"]]

    def get_examples(self, agent: str, kind: str, version: str | None = None) -> dict:
        versions = self._examples[f"{agent}:{kind}"]
        return versions[version or self._latest[f"{agent}:{kind}"]]

    def snapshot_versions(self) -> dict[str, str]:
        """返回 {name: 最新版本}，会话开始时固化进 state.prompt_versions。"""
        return dict(self._latest)

    def get_pinned(self, agent: str, kind: str, state: dict | None) -> Template:
        """会话状态带 prompt_versions 时按固化版本取模板；无 state 取最新（调试/冷路径语义）。"""
        version = (state or {}).get("prompt_versions", {}).get(f"{agent}:{kind}")
        return self.get(agent, kind, version)

    def get_examples_pinned(self, agent: str, kind: str, state: dict | None) -> dict:
        version = (state or {}).get("prompt_versions", {}).get(f"{agent}:{kind}")
        return self.get_examples(agent, kind, version)

    def render_all(self) -> None:
        """启动校验：每个模板以哑变量渲染一遍（所有版本），捕捉未声明占位符。"""
        for versions in self._templates.values():
            for t in versions.values():
                values = {v: "x" for v in t.variables}
                values.setdefault("output_schema", "{}")
                t.render(**values)


def schema_json(model) -> str:
    """统一的结构化输出 Schema 序列化（各 Agent 注入 output_schema 的唯一出口）。"""
    return json.dumps(model.model_json_schema(), ensure_ascii=False)


def render_structured(template: Template, model, values: dict) -> str:
    """渲染模板并自动注入 output_schema 变量（替代各 Agent 各自 json.dumps 拼接）。"""
    return template.render(**{**values, "output_schema": schema_json(model)})


registry = PromptRegistry()
