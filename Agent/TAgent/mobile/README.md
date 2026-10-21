# mobile/ —— 移动端（预留）

> 与 `core/` 平级的第 N 个界面实现。尚未实现，目录仅为占位与契约说明。

移动端通常不直接跑 Python，推荐形态：

- `mobile/` 放**BFF / 接口层**（例如 FastAPI），把 core 的事件流转成 HTTP SSE 或 WebSocket；
- `mobile/app/` 放 Flutter / React Native 工程。

若坚持用 Python 实现界面，做法与 `web/`、`desktop/` 完全一致：
实现 `Frontend` 子类并声明 `name = "mobile"`，即可被 `run.py --ui mobile` 发现。
