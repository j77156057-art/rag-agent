# 实时音视频兼容模式

## 当前行为

开发舱的 `/api/vision/live-stream` 会在握手时选择原生实时或兼容抽帧模式。浏览器继续播放摄像头、屏幕或项目预览；抽帧模式下网关只保留最新一帧交给视觉分析，因此模型变慢时不会把旧帧堆积起来。

兼容抽帧模式提供：

- 持续视频本地播放和最新帧发送；
- 项目级 WebSocket 会话、心跳、取消、重连和项目隔离；
- 画面观察、异常候选、时间戳、背压和前端自适应发送；
- 原生实时模型不可用时的可用降级路径。

## 当前限制

- 兼容抽帧模式不提供原生音频流输入和流式音频回复；`audio.chunk` 会返回 `audio_not_ready`。原生通道是否启用以 WebSocket `hello.ok.mode` 为准。
- 视觉理解按帧返回，结果可能晚于正在播放的画面。
- `DOCMIND_REALTIME_PROVIDER` 只表示原生 provider 的候选配置；最终会话模式以 WebSocket `hello.ok.mode` 为准，连接失败会自动降级。
- 原生实时模型接入完成后，需要重新执行真实设备联验，确认 session 字段、打断事件、首响应和音频输出。

## 状态接口

`GET /api/vision/realtime-status` 返回连接前的 provider 可用性和限制说明；前端开发舱在握手后以 `hello.ok` 的实际会话模式更新面板。`GET /api/vision/realtime/status` 另外返回时间线和性能指标。
