/** 斜杠命令清单：只描述「是什么」，执行由 App 分派。 */

export interface SlashCommand {
  name: string;
  label: string;
  hint: string;
  group: "会话" | "上下文" | "帮助";
}

export const COMMANDS: SlashCommand[] = [
  { name: "/new", label: "新建会话", hint: "先保存当前会话，再开一个干净的", group: "会话" },
  { name: "/resume", label: "历史会话", hint: "列出存档并恢复", group: "会话" },
  { name: "/save", label: "保存会话", hint: "写一份快照到 .agents/sessions", group: "会话" },
  { name: "/compact", label: "压缩上下文", hint: "归档原文并生成摘要", group: "上下文" },
  { name: "/help", label: "快捷键与说明", hint: "查看全部操作方式", group: "帮助" },
];

export function filterCommands(query: string): SlashCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return COMMANDS;
  return COMMANDS.filter(
    (command) =>
      command.name.slice(1).startsWith(q) ||
      command.label.includes(q) ||
      command.hint.includes(q),
  );
}
