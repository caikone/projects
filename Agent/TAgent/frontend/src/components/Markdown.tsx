/** Markdown 渲染：GFM + 代码块一键复制。 */
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkGfm from "remark-gfm";

import { IconCheck, IconCopy } from "./icons";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        // remark-cjk-friendly：修掉 CommonMark 的中文标点边界问题。
        // 例：`。**立即停止**这` —— 闭合的 ** 紧跟中文标点，规范判定为「不可闭合」，
        // 结果整段星号原样输出。中文回答里这类写法极多，必须开。
        remarkPlugins={[remarkGfm, remarkCjkFriendly]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          table: ({ children }) => (
            <div className="table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    const text = extractText(children);
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      },
      () => undefined,
    );
  };

  return (
    <div className="code-block">
      <button type="button" className="code-copy" onClick={copy} title="复制代码">
        {copied ? <IconCheck /> : <IconCopy />}
        <span>{copied ? "已复制" : "复制"}</span>
      </button>
      <pre>{children}</pre>
    </div>
  );
}

function extractText(node: React.ReactNode): string {
  if (node === null || node === undefined) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (typeof node === "object" && "props" in (node as never)) {
    return extractText((node as { props: { children?: React.ReactNode } }).props.children);
  }
  return "";
}
