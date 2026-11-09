/**
 * Slate 输入器。
 *
 * 为什么要富文本而不是 textarea：
 *   - 我们能明确知道「这段是代码块」，序列化成 ``` 围栏后再交给模型；
 *   - 行内加粗/代码只是视觉约定，但序列化后是标准 Markdown，模型照样读懂；
 *   - `/` 与 `@` 的触发点需要拿到光标前的文本，Slate 提供了结构化选区。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createEditor, Editor, Transforms, type Descendant } from "slate";
import { withHistory } from "slate-history";
import { Editable, ReactEditor, Slate, withReact } from "slate-react";

import { filterCommands, type SlashCommand } from "../commands";
import {
  EMPTY_VALUE,
  isEmpty,
  serialize,
  textBeforeCursor,
  type BlockFormat,
  type CustomElement,
  type CustomText,
} from "../editor/types";
import type { ToolInfo } from "../types";

interface ComposerProps {
  sdkReady: boolean;
  busy: boolean;
  tools: ToolInfo[];
  onSend: (text: string) => void;
  onStop: () => void;
  onCommand: (name: string) => void;
}

type MenuState =
  | { kind: "command"; query: string; index: number }
  | { kind: "tool"; query: string; index: number }
  | null;

const MARKS: Array<{ key: "bold" | "italic" | "code"; label: string; className: string }> = [
  { key: "bold", label: "B", className: "tool-bold" },
  { key: "italic", label: "I", className: "tool-italic" },
  { key: "code", label: "</>", className: "tool-code" },
];

export function Composer(props: ComposerProps) {
  const { busy, tools, onSend, onStop, onCommand } = props;
  const editor = useMemo(() => withHistory(withReact(createEditor())), []);
  const [value, setValue] = useState<Descendant[]>(EMPTY_VALUE);
  const [menu, setMenu] = useState<MenuState>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const commandMatches = useMemo(
    () => filterCommands(menu?.kind === "command" ? menu.query : ""),
    [menu],
  );
  const toolMatches = useMemo(() => {
    if (menu?.kind !== "tool") return [];
    const q = menu.query.toLowerCase();
    return tools.filter((tool) => tool.name.toLowerCase().includes(q)).slice(0, 8);
  }, [menu, tools]);

  /* ------------------------------------------------------------------ */
  /* 斜杠 / @ 的触发检测                                                */
  /* ------------------------------------------------------------------ */
  const syncMenu = useCallback(() => {
    const before = textBeforeCursor(editor);
    const command = before.match(/^\/([A-Za-z]*)$/);
    if (command) {
      setMenu((prev) =>
        prev?.kind === "command" && prev.query === command[1]
          ? prev
          : { kind: "command", query: command[1], index: 0 },
      );
      return;
    }
    const mention = before.match(/@([\w-]*)$/);
    if (mention && tools.length > 0) {
      setMenu((prev) =>
        prev?.kind === "tool" && prev.query === mention[1]
          ? prev
          : { kind: "tool", query: mention[1], index: 0 },
      );
      return;
    }
    setMenu(null);
  }, [editor, tools.length]);

  /* ------------------------------------------------------------------ */
  /* 发送                                                               */
  /* ------------------------------------------------------------------ */
  const resetEditor = useCallback(() => {
    // Slate 的 initialValue 只在挂载时生效，清空必须直接改 editor.children。
    // 注意：选区必须落在**文本节点**上（path 形如 [0, 0]），指向段落会抛错。
    const empty: Descendant[] = [{ type: "paragraph", children: [{ text: "" }] }];
    editor.children = empty;
    editor.selection = null;
    Editor.normalize(editor, { force: true });
    editor.selection = { anchor: { path: [0, 0], offset: 0 }, focus: { path: [0, 0], offset: 0 } };
    setValue(empty);
  }, [editor]);

  const submit = useCallback(() => {
    const text = serialize(value);
    if (!text.trim() || busy) return;
    resetEditor();
    setMenu(null);
    onSend(text);
    // 发送后把焦点留在输入框，节奏更连贯
    window.setTimeout(() => ReactEditor.focus(editor), 0);
  }, [busy, editor, onSend, resetEditor, value]);

  const pickCommand = useCallback(
    (command: SlashCommand) => {
      resetEditor();
      setMenu(null);
      onCommand(command.name);
      window.setTimeout(() => ReactEditor.focus(editor), 0);
    },
    [editor, onCommand, resetEditor],
  );

  const pickTool = useCallback(
    (tool: ToolInfo) => {
      // 删掉已输入的 "@query"，再插入行内代码样式的工具名
      for (let i = 0; i <= (menu?.query.length ?? 0); i += 1) {
        Editor.deleteBackward(editor, { unit: "character" });
      }
      Editor.addMark(editor, "code", true);
      Transforms.insertText(editor, `@${tool.name}`);
      Editor.removeMark(editor, "code");
      Transforms.insertText(editor, " ");
      setMenu(null);
    },
    [editor, menu],
  );

  /* ------------------------------------------------------------------ */
  /* 键盘                                                               */
  /* ------------------------------------------------------------------ */
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const native = event.nativeEvent as KeyboardEvent & { isComposing?: boolean };
      if (native.isComposing) return;

      if (menu) {
        const matches = menu.kind === "command" ? commandMatches : toolMatches;
        if (matches.length > 0) {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setMenu({ ...menu, index: (menu.index + 1) % matches.length });
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            setMenu({ ...menu, index: (menu.index - 1 + matches.length) % matches.length });
            return;
          }
          if (event.key === "Enter" || event.key === "Tab") {
            event.preventDefault();
            const picked = matches[Math.min(menu.index, matches.length - 1)];
            if (menu.kind === "command") pickCommand(picked as SlashCommand);
            else pickTool(picked as ToolInfo);
            return;
          }
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setMenu(null);
          return;
        }
      }

      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleMark(editor, "bold");
        return;
      }
      if (mod && event.key.toLowerCase() === "i") {
        event.preventDefault();
        toggleMark(editor, "italic");
        return;
      }
      if (mod && event.key.toLowerCase() === "e") {
        event.preventDefault();
        toggleMark(editor, "code");
        return;
      }

      if (event.key === "Enter") {
        if (event.shiftKey) {
          event.preventDefault();
          Editor.insertText(editor, "\n");
          return;
        }
        event.preventDefault();
        submit();
        return;
      }

      // 空代码块/引用里按退格：先退回普通段落，别让用户被困在块里
      if (event.key === "Backspace") {
        const block = currentBlock(editor);
        if (block && Editor.string(editor, block[1]) === "") {
          const type = (block[0] as CustomElement).type;
          if (type === "code" || type === "quote") {
            event.preventDefault();
            Transforms.setNodes(editor, { type: "paragraph" }, { match: (n) => "type" in n });
            return;
          }
        }
      }

      // Markdown 快捷输入：``` → 代码块，> → 引用
      if (event.key === " ") {
        const block = currentBlock(editor);
        if (!block) return;
        const text = Editor.string(editor, block[1]);
        if (text === "```") {
          event.preventDefault();
          Transforms.delete(editor, { unit: "line", reverse: true });
          setBlock(editor, "code");
          return;
        }
        if (text === ">") {
          event.preventDefault();
          Transforms.delete(editor, { unit: "line", reverse: true });
          setBlock(editor, "quote");
        }
      }
    },
    [commandMatches, editor, menu, pickCommand, pickTool, submit, toolMatches],
  );

  /* 点击外部收起菜单 */
  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setMenu(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menu]);

  const empty = isEmpty(value);

  return (
    <div className="composer-wrap">
      <div className={`composer${busy ? " is-busy" : ""}`} ref={wrapperRef}>
        {menu && (
          <PopMenu
            state={menu}
            commands={commandMatches}
            tools={toolMatches}
            onPickCommand={pickCommand}
            onPickTool={pickTool}
          />
        )}

        <div className="composer-toolbar">
          {MARKS.map((mark) => (
            <button
              key={mark.key}
              type="button"
              className={`fmt-btn ${mark.className}${
                isMarkActive(editor, mark.key) ? " is-active" : ""
              }`}
              title={mark.key === "code" ? "行内代码（Ctrl+E）" : `加粗/斜体（Ctrl+${mark.key[0].toUpperCase()}）`}
              onMouseDown={(event) => {
                event.preventDefault();
                toggleMark(editor, mark.key);
              }}
            >
              {mark.label}
            </button>
          ))}
          <span className="toolbar-divider" />
          <button
            type="button"
            className={`fmt-btn${isBlockActive(editor, "code") ? " is-active" : ""}`}
            title="代码块"
            onMouseDown={(event) => {
              event.preventDefault();
              setBlock(editor, "code");
            }}
          >
            {"{ }"}
          </button>
          <button
            type="button"
            className={`fmt-btn${isBlockActive(editor, "quote") ? " is-active" : ""}`}
            title="引用"
            onMouseDown={(event) => {
              event.preventDefault();
              setBlock(editor, "quote");
            }}
          >
            ❝
          </button>
          <span className="toolbar-spacer" />
          <span className="toolbar-hint">
            <kbd>/</kbd> 命令 · <kbd>@</kbd> 工具
          </span>
        </div>

        <div className="composer-body">
          <Slate
            editor={editor}
            initialValue={value}
            onChange={(next) => {
              setValue(next);
              syncMenu();
            }}
          >
            <Editable
              className="composer-input"
              placeholder="说点什么，或者输入 / 看看能做什么…"
              spellCheck={false}
              renderElement={renderElement}
              renderLeaf={renderLeaf}
              onKeyDown={onKeyDown}
              onPaste={(event) => {
                const text = event.clipboardData.getData("text/plain");
                if (!text.includes("\n")) return;
                event.preventDefault();
                insertPlainText(editor, text);
              }}
            />
          </Slate>
        </div>

        <div className="composer-footer">
          <div className="composer-meta">
            <span className="hint">
              <kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行
            </span>
            {!empty && <span className="char-count">{serialize(value).length} 字</span>}
          </div>
          {busy ? (
            <button type="button" className="send-btn is-stop" onClick={onStop} title="停止生成">
              <span className="stop-square" />
            </button>
          ) : (
            <button
              type="button"
              className="send-btn"
              onClick={submit}
              disabled={empty}
              title="发送（Enter）"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path
                  d="M4.5 12h13m0 0-5-5m5 5-5 5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 菜单                                                                */
/* ------------------------------------------------------------------ */
function PopMenu(props: {
  state: NonNullable<MenuState>;
  commands: SlashCommand[];
  tools: ToolInfo[];
  onPickCommand: (command: SlashCommand) => void;
  onPickTool: (tool: ToolInfo) => void;
}) {
  const { state, commands, tools, onPickCommand, onPickTool } = props;
  const rows =
    state.kind === "command"
      ? commands.map((command) => ({
          key: command.name,
          tag: command.name,
          label: command.label,
          hint: command.hint,
          onPick: () => onPickCommand(command),
        }))
      : tools.map((tool) => ({
          key: tool.name,
          tag: "@",
          label: tool.name,
          hint: tool.description,
          onPick: () => onPickTool(tool),
        }));

  if (rows.length === 0) {
    return (
      <div className="popmenu">
        <div className="popmenu-empty">没有匹配项</div>
      </div>
    );
  }

  return (
    <div className="popmenu">
      <div className="popmenu-head">
        {state.kind === "command" ? "命令" : "插入工具名"}
        <span className="popmenu-count">{rows.length}</span>
      </div>
      {rows.map((row, index) => (
        <button
          key={row.key}
          type="button"
          className={`popmenu-row${index === state.index ? " is-active" : ""}`}
          onMouseDown={(event) => {
            event.preventDefault();
            row.onPick();
          }}
          onMouseEnter={() => undefined}
        >
          <span className="popmenu-tag">{row.tag}</span>
          <span className="popmenu-label">{row.label}</span>
          <span className="popmenu-hint">{row.hint}</span>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Slate 小工具                                                        */
/* ------------------------------------------------------------------ */
function currentBlock(editor: Editor) {
  const { selection } = editor;
  if (!selection) return null;
  try {
    return Editor.above(editor, {
      match: (node) => "type" in node,
      mode: "lowest",
    });
  } catch {
    return null;
  }
}

function isMarkActive(editor: Editor, format: "bold" | "italic" | "code"): boolean {
  const marks = Editor.marks(editor);
  return marks ? marks[format] === true : false;
}

function toggleMark(editor: Editor, format: "bold" | "italic" | "code"): void {
  if (isMarkActive(editor, format)) Editor.removeMark(editor, format);
  else Editor.addMark(editor, format, true);
}

function isBlockActive(editor: Editor, format: BlockFormat): boolean {
  const block = currentBlock(editor);
  return Boolean(block && (block[0] as CustomElement).type === format);
}

function setBlock(editor: Editor, format: BlockFormat): void {
  const active = isBlockActive(editor, format);
  Transforms.setNodes(
    editor,
    { type: active ? "paragraph" : format },
    { match: (node) => "type" in node },
  );
}

/** 粘贴多行文本：按空行切段，代码块里保持原样。 */
function insertPlainText(editor: Editor, text: string): void {
  if (isBlockActive(editor, "code")) {
    Transforms.insertText(editor, text);
    return;
  }
  const paragraphs = text.replace(/\r\n/g, "\n").split(/\n{2,}/);
  paragraphs.forEach((paragraph, index) => {
    if (index > 0) Editor.insertBreak(editor);
    Transforms.insertText(editor, paragraph);
  });
}

function renderElement(props: {
  attributes: Record<string, unknown>;
  children: React.ReactNode;
  element: CustomElement;
}) {
  const { attributes, children, element } = props;
  if (element.type === "code") {
    return (
      <pre className="ed-block ed-code" {...attributes}>
        <code>{children}</code>
      </pre>
    );
  }
  if (element.type === "quote") {
    return (
      <blockquote className="ed-block ed-quote" {...attributes}>
        {children}
      </blockquote>
    );
  }
  return (
    <p className="ed-block" {...attributes}>
      {children}
    </p>
  );
}

function renderLeaf(props: {
  attributes: Record<string, unknown>;
  children: React.ReactNode;
  leaf: CustomText;
}) {
  const { attributes, children, leaf } = props;
  let node: React.ReactNode = children;
  if (leaf.bold) node = <strong>{node}</strong>;
  if (leaf.italic) node = <em>{node}</em>;
  if (leaf.strikethrough) node = <s>{node}</s>;
  if (leaf.code) node = <code className="inline-code">{node}</code>;
  return <span {...attributes}>{node}</span>;
}
