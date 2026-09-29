# R12 真实设备联验报告（设备侧，2026-09-29）

## 结论

本机没有可供浏览器授权的独立摄像头或麦克风设备，无法把摄像头、麦克风、扬声器和屏幕共享标记为真机通过。浏览器工作台与本地 WebSocket 网关可以启动；原生实时模型握手可用，但合成媒体联验发现一个会影响真实视频对话的顺序阻塞：**必须先向 DashScope 送入音频，再送视频帧**。若先送视频帧，服务端返回 `vendor_error`：

```text
Error append image before append audio.
```

当前 UI 的摄像头/屏幕共享按钮会先建立视频流；“开麦对话”需要用户随后点击，生产流程可能因此先送视频。该问题需要在前端/网关加入音频就绪闸门或由 provider 适配器明确支持视频先行后，才能进行真机闭环验收。

## 环境与范围

- 本地服务：`uvicorn api:app --host 127.0.0.1 --port 8000`。
- 工作台：`http://127.0.0.1:8000/workbench`，项目 `godot_sample`（项目 ID `prj-3da07fde89a5`）。
- 浏览器：Codex In-app Browser（Edge 内核）。
- 模型配置：`dashscope_omni`，`/api/vision/realtime-status` 报 `native-realtime` / `native_available: true`。
- 设备状态：浏览器未显示摄像头/麦克风授权对话框；点击“打开摄像头”和“共享屏幕给 AI”没有取得可持续媒体流，面板最终显示“屏幕共享已结束”。因此不记录摄像头、麦克风、扬声器的通过结论。

## 可复核证据

### 1. 工作台与原生握手

工作台能载入项目并进入“开发舱”。本地 WebSocket 连接发送 `hello` 后收到：

```json
{
  "type": "hello.ok",
  "mode": "native-realtime",
  "degraded_to": null,
  "provider": "dashscope_omni",
  "provider_capabilities": ["audio.in", "video.in", "text.out", "audio.out", "interrupt"]
}
```

这只证明网关和 provider 可握手，不代表浏览器设备采集或语音播放已通过。

### 2. 媒体顺序对照

使用当前时间戳和协议合法的合成 `video.frame` / `audio.chunk`，连接项目 `prj-3da07fde89a5` 的真实本地网关：

| 顺序 | 结果 |
| --- | --- |
| 先发 2 个视频帧，再发 10 个音频块 | 两个 `vendor_error`：`Error append image before append audio.` |
| 先发 1 个静音音频块，再发 1 个视频帧 | 4 秒观察窗口内未出现上述顺序错误 |

复验命令（服务已运行时）：

```powershell
@' ... websockets.connect("ws://127.0.0.1:8000/api/vision/live-stream?project_id=prj-3da07fde89a5") ... '@ |
  .\\.venv\\Scripts\\python.exe -
```

完整原始回包保留在本次任务工具输出中；关键错误来自真实 `dashscope_omni` provider，非 fake provider。

## 设备项目逐项状态

| 项目 | 状态 | 证据/阻塞 |
| --- | --- | --- |
| 摄像头采集 | 未验 | IAB 未提供可持续视频设备/授权；点击后无采集面板，最终媒体流结束 |
| 麦克风 PCM16 上行 | 未验 | 无可用麦克风设备；未能验证 16k、静音尾和真实 VAD |
| 屏幕共享 | 未验 | 未出现可选窗口/屏幕授权；按钮后的流立即结束 |
| 扬声器播放 | 未验 | 无真实 `model.audio` 回包可听测；采样率仍需真机确认 |
| 连续多轮 | 未验 | 依赖真实麦克风和可听播放 |
| 抢话 | 未验 | 前端代码路径存在，但无真实音频输出与输入无法证明手感 |
| 断线恢复 | provider/网关代码已有离线与 provider 证据；设备侧未验 | 需要在浏览器真实流中断线并观察 UI 恢复提示 |

## 下一次真机复验步骤

1. 使用带摄像头、麦克风和扬声器的 Windows Edge/Chrome，打开 `http://127.0.0.1:8000/workbench` 并允许本地站点摄像头、麦克风权限。
2. 先打开“开麦对话”并确认音频流已连接，再点击“打开摄像头”或“共享屏幕给 AI”；记录 `hello.ok`、首个 `model.audio`/`model.delta`、`done` 和错误事件。
3. 说一句完整短句，停止说话后保持麦克风开启至少 1 秒；确认字幕收口、有 `done`、能听到回答。
4. 连续完成三轮；在 AI 播放中说话验证抢话；手动断网后恢复，确认 UI 报告重连而不是结束会话。
5. 若产品要求支持“先开摄像头后开麦”，先修复上面的音频就绪闸门，再重复全部步骤。

## 当前判定

R12 **设备侧未通过，阻塞于真实设备不可用和视频先行顺序错误**。provider 可靠性三条（静音尾、`stream_broken` 可恢复、`done` 严格成功）不在本报告重复实现；它们的离线/模型侧证据见 `docs/realtime-r12-acceptance-20260929.md` 与 `HANDOFF.md`。
