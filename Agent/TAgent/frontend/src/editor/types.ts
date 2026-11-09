/**
 * Slate 的自定义类型与序列化规则。
 *
 * 输入框不是「一个 textarea」：它要能吃下代码块、引用、行内格式，
 * 并且能在发送前干净地还原成 Markdown —— 模型读得懂，用户也看得懂。
 */
import type { BaseEditor, Descendant } from "slate";
import type { HistoryEditor } from "slate-history";
import type { ReactEditor } from "slate-react";

export type BlockFormat = "paragraph" | "code" | "quote";

export type FormattedText = {
  text: string;
  bold?: boolean;
  italic?: boolean;
  code?: boolean;
  underline?: boolean;
  strikethrough?: boolean;
};

export type CustomText = FormattedText;

export type CustomElement = {
  type: BlockFormat;
  /** 代码块的语言标注（可选，仅用于 Markdown 围栏） */
  language?: string;
  children: CustomText[];
};

declare module "slate" {
  interface CustomTypes {
    Editor: BaseEditor & ReactEditor & HistoryEditor;
    Element: CustomElement;
    Text: CustomText;
  }
}

export const EMPTY_VALUE: Descendant[] = [
  { type: "paragraph", children: [{ text: "" }] },
];

const LEAF_MARKS: Array<[keyof Omit<FormattedText, "text">, string]> = [
  ["bold", "**"],
  ["italic", "*"],
  ["code", "`"],
  ["strikethrough", "~~"],
];

/** 行内标记 → Markdown。 */
export function serializeLeaf(leaf: CustomText): string {
  let text = leaf.text;
  if (!text) return "";
  for (const [mark, token] of LEAF_MARKS) {
    if (leaf[mark]) text = `${token}${text}${token}`;
  }
  if (leaf.underline) text = `<u>${text}</u>`;
  return text;
}

/** 整篇文档 → 发送给模型的 Markdown。 */
export function serialize(nodes: Descendant[]): string {
  const blocks = nodes.map(serializeNode).filter((block) => block.trim().length > 0);
  return blocks.join("\n\n").trim();
}

function serializeNode(node: Descendant): string {
  if (!("type" in node)) return serializeLeaf(node as CustomText);
  const inner = node.children.map(serializeNode).join("");
  switch (node.type) {
    case "code": {
      const fence = node.language ? `\`\`\`${node.language}` : "```";
      return `${fence}\n${inner.replace(/\n$/, "")}\n\`\`\``;
    }
    case "quote":
      return inner
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n");
    default:
      return inner;
  }
}

/** 编辑器是否为空（只有空段落）。 */
export function isEmpty(nodes: Descendant[]): boolean {
  if (nodes.length !== 1) return false;
  const only = nodes[0];
  if ("type" in only && only.type === "paragraph" && only.children.length === 1) {
    return only.children[0].text.trim() === "";
  }
  return false;
}

/** 光标前的一小段文本，用于识别 `/` 与 `@` 触发。 */
export function textBeforeCursor(editor: BaseEditor): string {
  const { selection } = editor;
  if (!selection) return "";
  try {
    const [node] = editor.node(selection.anchor);
    const text = (node as CustomText).text ?? "";
    return text.slice(0, selection.anchor.offset);
  } catch {
    return "";
  }
}
