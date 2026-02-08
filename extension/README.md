# AI Collab VS Code Extension

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

A VS Code extension that provides an AI collaboration panel with role-based permissions.

## Features

- **Chat UI**: Modern, responsive chat interface built with Tailwind CSS
- **Role-Based Permissions**: Lead and Member roles with different UI capabilities
  - **Lead**: Full access (Create Summary, Generate Changes, Auto Apply)
  - **Member**: Chat only
- **Real-time Updates**: UI updates automatically when settings change
- **Generate Changes**: Generate code modifications via AI agent
- **Diff Preview**: Preview proposed changes before applying
- **Apply Changes**: Apply generated changes to workspace files
- **Auto Apply Toggle**: Enable/disable automatic application of safe changes
- **Policy Evaluation**: Automatic safety checks before applying changes
  - Max 2 files per change
  - Max 50 lines changed
  - Forbidden paths: `infra/`, `db/`, `security/`
- **Join Only Mode**: Even without a local backend running, users can still join other people's sessions
  - "Start Session" button is disabled (requires local backend)
  - "Join Session" button remains functional

## Quick Start

```bash
cd extension
npm install
npm run compile
```

Then press `F5` in VS Code to launch the extension.

## Project Structure

```
extension/
├── src/
│   ├── extension.ts          # Main extension entry point
│   └── services/
│       ├── permissions.ts    # Role-based permission logic
│       └── diffPreview.ts    # Diff preview & apply changes
├── media/
│   ├── chat.html             # Chat UI with pending changes card
│   ├── tailwind.css          # Compiled Tailwind CSS
│   └── input.css             # Tailwind source
├── out/                      # Compiled JavaScript (generated)
├── package.json              # Extension manifest
├── tsconfig.json             # TypeScript configuration
└── tailwind.config.js        # Tailwind configuration
```

## Building & Installation

### Prerequisites

- Node.js 18+
- npm 9+
- VS Code 1.85+

### Step 1: Install Dependencies

```bash
cd extension
npm install
```

### Step 2: Compile TypeScript

```bash
# One-time compile
npm run compile

# Or watch mode (auto-compile on save)
npm run watch
```

### Step 3: Build VSIX Package

```bash
# Install vsce tool (first time only)
npm install -g @vscode/vsce

# Build the package
vsce package
```

This creates `ai-collab-0.0.1.vsix` in the extension folder.

### Step 4: Install the Extension

#### Option A: Install from VSIX (Recommended for distribution)

1. Open VS Code
2. Press `Ctrl+Shift+P` (Windows/Linux) or `Cmd+Shift+P` (Mac)
3. Type **"Extensions: Install from VSIX..."**
4. Select the `ai-collab-0.0.1.vsix` file
5. Click **"Reload"** when prompted

#### Option B: Development Mode (For testing)

1. Open the `extension/` folder in VS Code
2. Press `F5` to launch Extension Development Host
3. A new VS Code window opens with the extension loaded

#### Option C: Copy to Extensions Folder

```bash
# Find your VS Code extensions folder
# Windows: %USERPROFILE%\.vscode\extensions
# Mac/Linux: ~/.vscode/extensions

# Unzip the VSIX (it's a zip file)
unzip ai-collab-0.0.1.vsix -d ~/.vscode/extensions/ai-collab-0.0.1
```

### Step 5: Verify Installation

1. Open VS Code
2. Look for the **AI Collab** icon in the sidebar (Activity Bar)
3. Or press `Ctrl+Shift+P` → **"AI Collab: Open Panel"**

### Sharing with Team Members

1. Build the VSIX package: `vsce package`
2. Send the `.vsix` file to team members
3. They install using Option A above

### Updating the Extension

1. Uninstall the old version: Extensions sidebar → AI Collab → Uninstall
2. Install the new VSIX file
3. Reload VS Code

---

<a name="中文"></a>
## 中文

一个提供 AI 协作面板和基于角色权限的 VS Code 扩展。

## 功能特性

- **聊天界面**: 使用 Tailwind CSS 构建的现代响应式聊天界面
- **基于角色的权限**: Lead 和 Member 角色具有不同的 UI 功能
  - **Lead**: 完全访问权限（创建摘要、生成更改、自动应用）
  - **Member**: 仅聊天
- **实时更新**: 设置更改时 UI 自动更新
- **生成更改**: 通过 AI 代理生成代码修改
- **差异预览**: 应用前预览建议的更改
- **应用更改**: 将生成的更改应用到工作区文件
- **自动应用开关**: 启用/禁用安全更改的自动应用
- **策略评估**: 应用更改前的自动安全检查
- **仅加入模式**: 即使本地后端未运行，用户仍可以加入其他人的会话
  - "启动会话"按钮被禁用（需要本地后端）
  - "加入会话"按钮仍然可用

## 编译与安装

### 前置要求

- Node.js 18+
- npm 9+
- VS Code 1.85+

### 第一步：安装依赖

```bash
cd extension
npm install
```

### 第二步：编译 TypeScript

```bash
# 一次性编译
npm run compile

# 或监视模式（保存时自动编译）
npm run watch
```

### 第三步：构建 VSIX 包

```bash
# 安装 vsce 工具（仅首次需要）
npm install -g @vscode/vsce

# 构建包
vsce package
```

这会在 extension 文件夹中创建 `ai-collab-0.0.1.vsix`。

### 第四步：安装扩展

#### 方式 A：从 VSIX 安装（推荐用于分发）

1. 打开 VS Code
2. 按 `Ctrl+Shift+P`（Windows/Linux）或 `Cmd+Shift+P`（Mac）
3. 输入 **"Extensions: Install from VSIX..."**
4. 选择 `ai-collab-0.0.1.vsix` 文件
5. 出现提示时点击 **"Reload"**

#### 方式 B：开发模式（用于测试）

1. 在 VS Code 中打开 `extension/` 文件夹
2. 按 `F5` 启动扩展开发主机
3. 新的 VS Code 窗口会打开并加载扩展

#### 方式 C：复制到扩展文件夹

```bash
# 找到你的 VS Code 扩展文件夹
# Windows: %USERPROFILE%\.vscode\extensions
# Mac/Linux: ~/.vscode/extensions

# 解压 VSIX（它是一个 zip 文件）
unzip ai-collab-0.0.1.vsix -d ~/.vscode/extensions/ai-collab-0.0.1
```

### 第五步：验证安装

1. 打开 VS Code
2. 在侧边栏（活动栏）中查找 **AI Collab** 图标
3. 或按 `Ctrl+Shift+P` → **"AI Collab: Open Panel"**

### 与团队成员分享

1. 构建 VSIX 包：`vsce package`
2. 将 `.vsix` 文件发送给团队成员
3. 他们使用上述方式 A 安装

### 更新扩展

1. 卸载旧版本：扩展侧边栏 → AI Collab → 卸载
2. 安装新的 VSIX 文件
3. 重新加载 VS Code

---

## 测试指南 / Testing Guide

### 启动扩展 / Launch the Extension

```bash
# 编译（代码更改后需要）
npm run compile

# 然后在 VS Code 中按 F5 开始调试
```

### 打开面板 / Open the Panel

在扩展开发主机窗口中：
- 按 `Cmd+Shift+P`（Mac）或 `Ctrl+Shift+P`（Windows/Linux）
- 输入 **"AI Collab: Open Panel"** 并选择

### 测试角色权限 / Test Role-Based Permissions

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 打开面板 | 显示 👤 Member 徽章，仅聊天 |
| 2 | 打开设置 (`Cmd+,` / `Ctrl+,`) | 设置窗口打开 |
| 3 | 搜索 `aiCollab.role` | 找到角色设置 |
| 4 | 更改为 `lead` | 通知："Role changed to: lead" |
| 5 | 检查面板 | 显示 👑 Lead 徽章 + 3 个操作按钮 |
| 6 | 改回 `member` | 按钮消失，徽章变化 |

### 故障排除 / Troubleshooting

| 问题 | 解决方案 |
|------|----------|
| 旧 UI 显示 "Welcome..." | 运行 `npm run compile`，重启调试 (Shift+F5, 然后 F5) |
| 角色更改无效 | 重启调试会话 (Shift+F5, 然后 F5) |
| 角色更改无通知 | 检查开发者工具控制台的错误 |

---

## 开发 / Development

### 命令 / Commands

```bash
npm run compile    # 编译 TypeScript
npm run watch      # 监视模式（保存时自动编译）
npm run build:css  # 重建 Tailwind CSS
```

### 项目结构 / Project Structure

```
extension/
├── src/
│   ├── extension.ts          # 主扩展入口点
│   └── services/
│       ├── permissions.ts    # 基于角色的权限逻辑
│       ├── session.ts        # 会话管理
│       ├── conductorStateMachine.ts  # 状态机
│       ├── conductorController.ts    # 控制器
│       └── diffPreview.ts    # 差异预览和应用更改
├── media/
│   ├── chat.html             # 聊天 UI
│   ├── tailwind.css          # 编译后的 Tailwind CSS
│   └── input.css             # Tailwind 源文件
├── out/                      # 编译后的 JavaScript（生成的）
├── package.json              # 扩展清单
├── tsconfig.json             # TypeScript 配置
└── tailwind.config.js        # Tailwind 配置
```

## License

MIT
