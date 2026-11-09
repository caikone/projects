# frontend/ —— 浏览器端工程（React 18 + TypeScript + Slate）

给 `../web/` 网关配的前端。构建产物 `dist/` 由网关直接托管，所以线上只有一个进程。

## 开发

```bash
npm install
npm run dev        # http://localhost:5173，/api 自动代理到 127.0.0.1:8760
npm run build      # 产出 dist/（tsc -b && vite build）
npm run typecheck
```

后端先跑起来：`python run.py --ui web`。

## 目录

```
src/
├── App.tsx               三栏骨架 + 全局快捷键 + 命令分派
├── useAgent.ts           状态机：bootstrap → SSE 增量合并 → 会话切换
├── api.ts                唯一通信层（fetch + 手写 SSE 帧解析）
├── types.ts              与 core 事件一一对应的类型
├── format.ts             tokens / 相对时间 / 耗时格式化
├── commands.ts           斜杠命令清单
├── theme.css             设计系统（颜色、圆角、节奏都在这）
├── editor/types.ts       Slate 节点类型 + → Markdown 序列化
└── components/
    ├── Composer.tsx      Slate 输入器（/ 命令、@ 工具、代码块、行内格式）
    ├── ChatStream.tsx    对话流（自动跟随底部、空状态引导）
    ├── MessageBubble.tsx 用户气泡 / 助手分块 / 错误提示
    ├── ToolCard.tsx      工具调用卡片（参数、结果、复制）
    ├── Markdown.tsx      GFM 渲染 + 代码块复制
    ├── Sidebar.tsx       会话 + MCP 服务器
    ├── Inspector.tsx     上下文环、统计、工具清单
    ├── SessionPicker.tsx 历史会话弹层（↑↓ / Enter / Esc）
    └── icons.tsx         线性图标
```

## 几个约定

- **事件驱动**：只有 `useAgent` 改状态，组件只读 props；新事件先加进 `types.ts` 的
  `AgentEvent` 联合类型，再在 `handleEvent` 里合并。
- **消息分块**：一条助手消息是 `text | tool` 的数组，工具调用天然夹在文字中间，
  流式过程中不会因为「先文字后工具」而错位。
- **样式只有一份**：颜色/圆角/间距都走 `theme.css` 的 CSS 变量，组件里不写死色值。
- **不引重型依赖**：Markdown 用 `react-markdown + remark-gfm`，不做语法高亮，
  代码块保持等宽 + 可复制，够用且包体小。
