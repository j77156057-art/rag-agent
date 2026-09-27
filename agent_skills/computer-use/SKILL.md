---
name: computer-use
version: 1.0.0
description: 通过受控桌面截图和已批准的应用连接器观察或操作 Windows 软件
when_to_use: 用户要求查看、验收或修改原生桌面软件、编辑器、CAD 或引擎窗口时
---

# 桌面软件与视觉验收

## 能力边界

- 先调用 `dev_desktop_capture` 获取当前项目嵌入窗口或前台窗口的真实画面。
- 截图只是视觉观察，不代表软件内部状态、源码或保存结果；必须结合日志、文件和应用工具复核。
- 修改软件状态优先使用已启用并经用户批准的应用 MCP、插件或命令行工具；没有专用接口时，可以使用 `dev_desktop_action` 执行受控的单步桌面输入。
- 没有可用连接器时，向用户说明缺口；不要把截图工具伪装成可编辑接口。

## 标准流程

1. 调用 `dev_list_connectors` 和 `dev_route_connector` 查找当前软件的连接器。
2. 调用 `dev_list_connector_tools` 确认截图、读取状态、修改和保存工具。
3. 使用 `dev_desktop_capture` 获取修改前画面。
4. 通过已批准的应用 MCP、插件或 `dev_desktop_action` 执行一个修改动作，保留返回的状态和文件证据；每次动作都必须单独调用，不能批量猜测坐标。
5. 再次调用 `dev_desktop_capture` 或应用截图工具获取修改后画面，再决定下一步。
6. 使用 `self_verify`、应用验收工具和用户确认完成最终验收。

## 受控桌面动作

`dev_desktop_action` 的输入为多行键值：

```text
action: click|type|drag|key|save
target: embedded|foreground
x: 320
y: 180
to_x: 500
to_y: 240
text: 要输入的文字
key: Control_L+s
```

- `click` 需要 `x/y`；`drag` 还需要 `to_x/to_y`，坐标均为最新截图中目标客户区的像素坐标。
- `type` 使用 `text`，`key` 使用受控键名或组合键；`save` 等价于 `Control_L+s`。
- 首次调用会返回 `desktop_action` 审批请求。用户批准后，必须使用完全相同的参数重试；参数、目标或文本变化都会重新触发审批。
- 每次动作前必须重新观察目标窗口，每次动作后必须立即重新截图或读取应用状态。审批不代表动作已经成功。
- 保存、导出、上传、安装、登录、外部写入以及任何不可逆操作仍需保持审批；不得输入密码、验证码或安全设置。

## 审批边界

- 点击、输入、保存、导出、联网、安装依赖和外部写入都必须通过对应工具的审批门。
- `dev_desktop_capture` 只读；`dev_desktop_action` 只接受 `embedded`/`foreground` 目标，不提供任意窗口句柄、任意进程枚举、终端或脚本执行入口。
- 适配器需要长期复用时，先生成项目级适配器草稿；用户批准后才能激活。
- 领域适配器可以由 Agent 生成 `runtime: python|node` 的模块代码。模块入口固定为 `adapt(payload)`，只接收已批准工具的 JSON 结果并返回 JSON artifact；它在项目 `.docmind/preview-adapters/.pending` 中等待审批，激活后才会进入隔离子进程运行。模块不能自行联网、启动命令、读取项目外文件或写入项目。
- Windows 上的适配器进程会加入 Job Object，限制进程树、内存、生命周期和未处理异常；如果无法建立该边界，运行会失败并等待处理，不会降级为普通无界进程。网络访问仍以代码静态检查和清理环境变量为边界，不能把它当作完整网络防火墙。
- 适配器代码替换前会保存历史版本；执行失败不会伪造成功，用户需要回退时使用 `dev_preview_adapter_rollback`，该操作也必须经过审批。
- 真实软件联验前可调用 `dev_preview_adapter_test`，用脱敏的固定 JSON fixture 重放 MCP 返回结构；fixture 测试不代表真实软件状态已经通过。
