---
name: demo-hello
description: 演示 Skill 的写法与按需加载用法：创建一个问候文件，并用 RunCli 验证结果。
---

# demo-hello

这是一个**示例 Skill**，用来展示 Agent Skills 的规范与动态加载机制，确认无误后可整个删除。

## 适用场景

当用户要求「演示一下 Skill 怎么用」「跑一下示例技能」时加载本 Skill。

## 操作步骤

1. 用 `createFile` 在**当前工作目录**下创建 `hello-skill.txt`，内容为：

   ```
   Hello from a TAgent skill!
   ```

2. 用 `RunCli` 执行 `cat hello-skill.txt`（Windows 上可用 `cmd` + `["/c", "type", "hello-skill.txt"]`），
   把命令的 **退出码** 与 **stdout** 原样回报给用户。

3. 用简洁的中文向用户说明：Skill 的正文只有在他调用 `LoadSkill` 之后才进入上下文。

## 注意

- 不要创建到系统目录或用户主目录，只写当前工作目录。
- 如果文件已存在，先说明再覆盖，不要静默替换。
