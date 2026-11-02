# -*- coding: utf-8 -*-
"""YAML Frontmatter 的解析与合成（纯文本逻辑，无任何 I/O）。

为什么放在领域层而不是 infrastructure：
    `SKILL.md` 的格式本身就是 Agent Skills 的**领域约定**，
    解析它不需要知道文件在哪、怎么读。放到这里之后，
    「读取」（infrastructure）与「生成」（services）可以共用同一套规则，
    否则 `createSkill` 写出去的 Frontmatter 和自己解析回来的可能不一致。

两块能力：
    - `parse_frontmatter(text)`  → `(元数据字典, 正文)`；
    - `compose_skill_markdown()` → 拼出规范的 `SKILL.md` 全文。

实现上刻意**不强制依赖 PyYAML**：装了就用完整 YAML 语义，
没装则回退到内置最小解析器（`key: value` 与 `|` / `>` 块标量），
保证零依赖也能跑——Agent Skills 的 Frontmatter 实际只需要这两种写法。
"""

from __future__ import annotations

import re
from typing import Any

#: Frontmatter 位于文件开头，先只扫这么多字节；
#: 这段里没找到结束分隔符时才回退读全文（正常情况下正文根本不会被读到）
FRONTMATTER_SCAN_BYTES = 8192

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

#: 从 Word / 微信 / 网页复制粘贴时混进来的不可见字符。
#: 它们会让 `---` 分隔符失配（实测一个 `\u200b---` 就让整段正文被吞进 Frontmatter），
#: 因此解析前一律清掉。
_INVISIBLE_CHARS = "\ufeff\u200b\u200c\u200d\u2060\u00ad"

#: 可以不加引号直接写进 YAML 的字符集合（保守：排除 `:` `#` 引号等有语义的符号）
_PLAIN_SAFE_RE = re.compile(
    r"^[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff .,/()（）\-_]*$")


# ==========================================================================
# 解析
# ==========================================================================
def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """切分 Frontmatter 与正文。

    Returns:
        `(元数据字典, 去掉 Frontmatter 后的正文)`；
        文件没有 Frontmatter 时返回 `({}, 全文)`。
    """
    text = strip_invisible(text or "")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    return _load_mapping(_normalize_colons(match.group(1))), text[match.end():]


def starts_with_frontmatter(text: str) -> bool:
    """文本开头是否是**完整**的 Frontmatter（用于「只读开头一小段」的探测）。"""
    return bool(_FRONTMATTER_RE.match(strip_invisible(text or "")))


def strip_invisible(text: str) -> str:
    """去掉零宽字符与 BOM。"""
    for char in _INVISIBLE_CHARS:
        if char in text:
            text = text.replace(char, "")
    return text


def _normalize_colons(raw: str) -> str:
    """把中文全角冒号当成分隔符（仅在该行没有半角冒号时，避免误伤正文）。"""
    lines: list[str] = []
    for line in raw.splitlines():
        if ":" not in line and "：" in line:
            line = line.replace("：", ":", 1)
        lines.append(line)
    return "\n".join(lines)


def _load_mapping(raw: str) -> dict[str, Any]:
    """把 Frontmatter 文本解析成字典（PyYAML 优先，回退内置解析器）。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        try:
            loaded = yaml.safe_load(raw)
        except Exception:  # noqa: BLE001 - YAML 解析失败时回退，不让坏文件炸掉发现流程
            loaded = None
        if isinstance(loaded, dict):
            return loaded
    return _minimal_yaml(raw)


def _minimal_yaml(raw: str) -> dict[str, Any]:
    """最小 YAML 子集解析器：支持 `key: value` 与 `|` / `>` 块标量。"""
    result: dict[str, Any] = {}
    lines = raw.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            continue

        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()

        # 块标量：key: |   /   key: >   （含 - / + 修饰）
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            block: list[str] = []
            while index < len(lines):
                nxt = lines[index]
                if nxt.strip() and not nxt[:1].isspace():
                    break  # 缩进结束，块标量结束
                block.append(nxt.strip())
                index += 1
            joined = "\n".join(x for x in block if x) if rest.startswith("|") \
                else " ".join(x for x in block if x)
            result[key] = joined.strip()
            continue

        result[key] = _unquote(_strip_inline_comment(rest))

    return result


def _unquote(value: str) -> str:
    """去掉成对的外层引号（双引号内还原转义）。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _strip_inline_comment(value: str) -> str:
    """去掉未加引号值后面的 `# 注释`（引号内的 # 保留）。"""
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)  # 先截断到闭合引号，注释自然被丢弃
        return value[: end + 1] if end > 0 else value
    marker = value.find(" #")
    if marker >= 0:
        return value[:marker].rstrip()
    return value


# ==========================================================================
# 合成
# ==========================================================================
def yaml_scalar(value: Any) -> str:
    """把一个标量渲染成安全的 YAML 值。

    简单值保持裸写（人类可读），含 `:` `#` 引号等有语义字符时加双引号，
    保证 `createSkill` 写出的 Frontmatter 一定能被自己解析回来。
    """
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        return '""'
    if _PLAIN_SAFE_RE.match(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % escaped


def is_scalar(value: Any) -> bool:
    """能否无损地当成一行 YAML 值写入。"""
    return isinstance(value, (str, int, float, bool))


def compose_skill_markdown(name: str,
                           description: str,
                           body: str,
                           extras: dict[str, Any] | None = None) -> str:
    """拼出符合 Agent Skills 规范的 `SKILL.md` 全文。

    Args:
        name:        归一化后的 Skill 名称（与目录名一致）。
        description: 一句话说明（必填，影响模型是否会用到这个技能）。
        body:        给模型看的操作指令正文。
        extras:      额外的 Frontmatter 字段（如 `license`）。
                     只保留标量值，`name` / `description` 永远由本函数决定。

    Returns:
        以 `---` 开头、正文后带单个换行的完整 Markdown 文本。
    """
    lines = [
        "---",
        "name: %s" % yaml_scalar(name),
        "description: %s" % yaml_scalar(description),
    ]
    for key, value in (extras or {}).items():
        text_key = str(key).strip()
        if not text_key or text_key in ("name", "description") or not is_scalar(value):
            continue
        lines.append("%s: %s" % (text_key, yaml_scalar(value)))
    lines.append("---")
    lines.append("")
    lines.append((body or "").strip())
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "FRONTMATTER_SCAN_BYTES",
    "parse_frontmatter",
    "starts_with_frontmatter",
    "strip_invisible",
    "yaml_scalar",
    "is_scalar",
    "compose_skill_markdown",
]
