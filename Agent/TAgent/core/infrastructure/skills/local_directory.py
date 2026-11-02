# -*- coding: utf-8 -*-
"""本地目录 Skill 仓库：从 `.agents/skills/<skill>/SKILL.md` 发现并读取技能。

本模块是**唯一**知道「Skill 在磁盘上长什么样」的地方：

    <项目根>/.agents/skills/
        code-review/
            SKILL.md            ← 入口，开头是 YAML Frontmatter
            scripts/lint.py     ← 附加文件（按需加载）
        release-notes/
            SKILL.md

三条硬约束在这里落地：

    1. **发现阶段只读元信息**——`discover()` 只解析 Frontmatter 的
       name / description，正文永不进入上下文（见 `_read_head`）；
    2. **路径不越界**——所有读取都先解析再校验落在 Skill 目录内；
    3. **坏 Skill 不拖垮全局**——缺文件 / Frontmatter 不合法只记 `issues` 后跳过。
"""

from __future__ import annotations

import os

from ...domain.frontmatter import (
    FRONTMATTER_SCAN_BYTES,
    parse_frontmatter,
    starts_with_frontmatter,
)
from ...domain.skills import SkillCatalog, SkillContent, SkillCreation, SkillDraft, SkillMetadata
from ...errors import (
    SkillAlreadyExistsError,
    SkillFileError,
    SkillFileNotFoundError,
    SkillPathError,
)
from ...ports.skills import SkillStore

#: 项目根下的固定技能目录（Agent Skills 规范）
SKILLS_DIRNAME = os.path.join(".agents", "skills")
#: Skill 入口文件名
ENTRY_FILENAME = "SKILL.md"

#: 读取正文时的解码顺序（与 `FileSystemService.DECODE_ORDER` 保持一致）
DECODE_ORDER = ("utf-8-sig", "utf-8", "gbk")

#: 递归搜索时要忽略的目录
_SKIP_DIRS = frozenset({"__pycache__", ".git", "node_modules", ".venv", "venv"})


# ==========================================================================
# 本地目录实现
# ==========================================================================
class LocalDirectorySkillStore(SkillStore):
    """基于本地目录的 Skill 仓库。

    Args:
        root:         技能根目录绝对路径；缺省为 `<project_root>/.agents/skills`。
        project_root: 项目根目录；缺省为程序启动时的工作目录。
        max_read_bytes: 单个文件读取上限（超出截断并标注）。
    """

    def __init__(self,
                 root: str | None = None,
                 project_root: str | None = None,
                 max_read_bytes: int = 1024 * 1024) -> None:
        self._project_root = os.path.abspath(project_root or os.getcwd())
        self._root = os.path.abspath(
            root or os.path.join(self._project_root, SKILLS_DIRNAME))
        self.max_read_bytes = max_read_bytes

    # ------------------------------------------------------------------
    @property
    def project_root(self) -> str:
        return self._project_root

    def root(self) -> str:
        return self._root

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    def discover(self) -> SkillCatalog:
        """扫描技能根目录，产出只含 name / description 的清单。"""
        root = self.root()
        if not os.path.isdir(root):
            return SkillCatalog(root=root)  # 目录不存在是正常状态：项目还没放技能

        skills: list[SkillMetadata] = []
        issues: list[str] = []
        seen: dict[str, str] = {}

        for dirname in sorted(os.listdir(root)):
            directory = os.path.join(root, dirname)
            if dirname.startswith(".") or not os.path.isdir(directory):
                continue

            entry = self._find_entry(directory)
            if entry is None:
                issues.append("%s：目录下没有 %s，已跳过" % (dirname, ENTRY_FILENAME))
                continue

            try:
                metadata = self._read_metadata(directory, entry)
            except SkillFileError as exc:
                issues.append("%s：%s" % (dirname, exc.message))
                continue

            if not metadata.valid:
                issues.append(
                    "%s：%s 的 Frontmatter 必须同时声明 name 与 description，已跳过"
                    % (dirname, ENTRY_FILENAME))
                continue

            clash = seen.get(metadata.name)
            if clash:
                issues.append(
                    "%s：Skill 名称「%s」与目录 %s 重复，已跳过后者"
                    % (dirname, metadata.name, clash))
                continue

            seen[metadata.name] = dirname
            skills.append(metadata)

        skills.sort(key=lambda s: s.name)
        return SkillCatalog(root=root, skills=tuple(skills), issues=tuple(issues))

    @staticmethod
    def _find_entry(directory: str) -> str | None:
        """定位 `SKILL.md`（大小写不敏感兜底）。"""
        direct = os.path.join(directory, ENTRY_FILENAME)
        if os.path.isfile(direct):
            return direct
        try:
            for item in sorted(os.listdir(directory)):
                if item.lower() == ENTRY_FILENAME.lower() and \
                        os.path.isfile(os.path.join(directory, item)):
                    return os.path.join(directory, item)
        except OSError:
            return None
        return None

    def _read_metadata(self, directory: str, entry: str) -> SkillMetadata:
        """只解析 Frontmatter，正文读完即弃（不进入上下文）。"""
        head = self._read_head(entry)
        mapping, _body = parse_frontmatter(head)
        return SkillMetadata(
            name=str(mapping.get("name") or "").strip(),
            description=str(mapping.get("description") or "").strip(),
            directory=os.path.abspath(directory),
            entry=os.path.abspath(entry),
        )

    @staticmethod
    def _read_head(path: str) -> str:
        """只读文件开头的一小段（用于解析 Frontmatter）。"""
        try:
            with open(path, "rb") as f:
                head = f.read(FRONTMATTER_SCAN_BYTES)
                text = head.decode("utf-8", errors="ignore")
                if not starts_with_frontmatter(text):
                    # 开头没匹配上（Frontmatter 特别长或根本没有）→ 读全文再判
                    text = (head + f.read()).decode("utf-8", errors="ignore")
        except OSError as exc:
            raise SkillFileError("读取 %s 失败：%s" % (path, exc))
        return text

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create(self, draft: SkillDraft) -> SkillCreation:
        """在 `<root>/<draft.name>/` 下写出 `SKILL.md`（绝不覆盖）。

        写入顺序刻意是「先建目录、再检查入口、最后以 `x` 模式打开」：
        即使两个进程同时创建同一个 Skill，也只有一方能成功，
        另一方会拿到 `FileExistsError` → `SkillAlreadyExistsError`。
        """
        root = self.root()
        directory = self._canonical(os.path.join(root, draft.name))
        root_key = self._key(root)
        if directory == self._canonical(root) or \
                not self._within(root_key, self._key(directory)):
            raise SkillPathError(draft.name, draft.name)

        existed = os.path.isdir(directory)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise SkillFileError(
                "创建 Skill 目录 %s 失败：%s" % (directory, exc))

        # 目录里已经有入口（含大小写不同的 skill.md）→ 明确拒绝，一个字节都不写
        existing_entry = self._find_entry(directory)
        entry = os.path.join(directory, ENTRY_FILENAME)
        if existing_entry is not None:
            raise SkillAlreadyExistsError(draft.name, directory, existing_entry)

        try:
            # `x` 模式是最后一道防线：文件已存在时直接失败，绝不截断原内容
            with open(entry, "x", encoding="utf-8", newline="\n") as f:
                f.write(draft.markdown)
        except FileExistsError:
            raise SkillAlreadyExistsError(draft.name, directory, entry)
        except OSError as exc:
            raise SkillFileError(
                "写入 %s 失败：%s" % (os.path.join(directory, ENTRY_FILENAME), exc))

        return SkillCreation(
            skill=SkillMetadata(
                name=draft.name,
                description=draft.description,
                directory=directory,
                entry=entry,
            ),
            draft=draft,
            bytes_written=len(draft.markdown.encode("utf-8")),
            created_directory=not existed,
        )

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def read(self, skill: SkillMetadata, relative: str) -> SkillContent:
        """读取 Skill 目录内的文件（路径必须先解析到目录内部）。"""
        base = self._canonical(skill.directory)
        target = self._resolve(skill, relative)
        if not os.path.isfile(target):
            raise SkillFileNotFoundError(skill.name, relative or "(空路径)")

        try:
            with open(target, "rb") as f:
                raw = f.read(self.max_read_bytes + 1)
        except OSError as exc:
            raise SkillFileError("读取 %s 失败：%s" % (relative, exc))

        truncated = len(raw) > self.max_read_bytes
        raw = raw[: self.max_read_bytes]
        size = os.path.getsize(target) if truncated else len(raw)

        if b"\x00" in raw:
            raise SkillFileError(
                "%s 含有空字节，疑似二进制文件，不支持通过 LoadSkill 读取" % relative)

        for encoding in DECODE_ORDER:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            return SkillContent(
                skill=skill.name,
                path=target,
                relative=self._relative(base, target),
                text=text,
                truncated=truncated,
                size=size,
            )

        raise SkillFileError("无法按 UTF-8 / GBK 解码 %s，可能不是文本文件" % relative)

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------
    def candidates(self, skill: SkillMetadata, filename: str) -> list[str]:
        """解析模型给出的文件名，返回相对 Skill 目录的候选路径。

        优先精确路径匹配（相对 Skill 目录 / 相对项目根 / 绝对路径），
        全部落空时才按文件名在 Skill 目录内递归搜索。
        """
        wanted = (filename or "").strip().replace("\\", "/")
        if not wanted:
            return []

        base = self._canonical(skill.directory)
        base_key = self._key(skill.directory)

        # 1) 精确路径匹配
        tries: list[str] = []
        if os.path.isabs(wanted):
            tries.append(wanted)
        tries.append(os.path.join(skill.directory, wanted))
        tries.append(os.path.join(self._project_root, wanted))

        for candidate in tries:
            resolved = self._canonical(candidate)
            if self._within(base_key, self._key(resolved)) and os.path.isfile(resolved):
                return [self._relative(base, resolved)]

        # 绝对路径但不在 Skill 目录内：明确拒绝，不再退回模糊搜索
        if os.path.isabs(wanted):
            raise SkillPathError(skill.name, wanted)

        # 2) 模糊搜索（只在 Skill 目录内部进行）
        #    注意：不能用 lstrip("./")——那是按字符集合裁剪，会把
        #    "../sibling/SKILL.md" 削成 "sibling/SKILL.md"，导致读到错误的文件。
        target = wanted[2:] if wanted.startswith("./") else wanted
        if ".." in target.split("/"):
            raise SkillPathError(skill.name, wanted)  # 向上穿越一律拒绝

        all_files = self._walk_files(skill.directory, base)
        strong = {r for r in all_files if r == target or r.endswith("/" + target)}
        if strong:
            return sorted(strong)
        if "/" in target:
            return []  # 给了目录结构却没精确命中：不猜，避免张冠李戴
        weak = {r for r in all_files if os.path.basename(r) == target}
        return sorted(weak)

    def _walk_files(self, directory: str, base: str) -> list[str]:
        """列出 Skill 目录内所有文件的相对路径（跳过无关目录）。"""
        found: list[str] = []
        for current, dirs, files in os.walk(directory):
            dirs[:] = sorted(d for d in dirs
                             if not d.startswith(".") and d not in _SKIP_DIRS)
            for name in files:
                full = os.path.join(current, name)
                if os.path.isfile(full):
                    found.append(self._relative(base, self._canonical(full)))
        return found

    def _resolve(self, skill: SkillMetadata, relative: str) -> str:
        """把相对路径解析成绝对路径，并拒绝越界。"""
        base_key = self._key(skill.directory)
        target = self._canonical(os.path.join(skill.directory, relative or ""))
        if not self._within(base_key, self._key(target)):
            raise SkillPathError(skill.name, relative)
        return target

    # ------------------------------------------------------------------
    @staticmethod
    def _canonical(path: str) -> str:
        """归一化路径：绝对化 + 软链接解析（**保留原始大小写**，供展示用）。"""
        return os.path.realpath(os.path.abspath(path))

    @staticmethod
    def _key(path: str) -> str:
        """比较用键：在 `_canonical` 之上再做大小写归一化（Windows 必需）。"""
        return os.path.normcase(LocalDirectorySkillStore._canonical(path))

    @staticmethod
    def _within(base_key: str, target_key: str) -> bool:
        return target_key == base_key or target_key.startswith(base_key + os.sep)

    @staticmethod
    def _relative(base: str, target: str) -> str:
        return os.path.relpath(target, base).replace(os.sep, "/")


__all__ = ["LocalDirectorySkillStore", "parse_frontmatter", "SKILLS_DIRNAME", "ENTRY_FILENAME"]
