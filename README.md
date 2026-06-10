# 会议实时转写系统

第一版本目标是跑通浏览器麦克风采集、WebSocket 音频传输、实时转写展示与文本编辑闭环。

## 项目结构

```text
frontend/  React + JavaScript 前端
backend/   FastAPI WebSocket 后端
```

## 当前能力

- 浏览器麦克风设备选择
- 开始、暂停、继续和结束会议
- AudioWorklet 采集音频并重采样为 16 kHz PCM16
- WebSocket 二进制音频传输
- 实时音量显示
- 预览文本和确认文本协议
- 已确认文本段落编辑
- MongoDB会议与转写持久化
- 完整会议WAV音频保存
- 历史会议查看、回放与文字时间跳转
- Word 文档导出，包含 AI 纪要、待办事项、完整原文及纪要到原文的内部跳转
- 本地或 OpenAI 兼容 LLM 生成会议摘要、核心要点、结论、未决问题与待办
- 6秒积木、1秒重叠识别与文字去重

后端使用 sherpa-onnx 加载 FunASR Nano INT8 模型进行真实转写。

## 后端配置

后端启动时会自动读取 `backend/.env`。可在其中配置 MongoDB、ASR、VAD 和
OpenAI 兼容 LLM 服务。项目提供了 `backend/.env.example` 作为模板。

本地 Ollama 默认配置：

```text
HUIYI_LLM_ENABLED=true
HUIYI_LLM_BASE_URL=http://127.0.0.1:11434/v1
HUIYI_LLM_API_KEY=ollama
HUIYI_LLM_MODEL=qwen3.5:4b
HUIYI_LLM_REASONING_EFFORT=none
```

切换第三方 OpenAI 兼容 API 时，修改地址、API Key、模型名即可。若第三方接口
不支持 `reasoning_effort`，将 `HUIYI_LLM_REASONING_EFFORT` 留空。

会议结束后系统会异步生成 AI 纪要。也可以在历史会议页面手动重新生成。

## 启动前端

```powershell
cd frontend
npm install
npm run dev
```

## 启动后端

```powershell
conda create -n huiyi-asr python=3.10
conda activate huiyi-asr
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

访问 `http://localhost:5173`。浏览器麦克风通常要求安全上下文，localhost 可直接使用。

## MongoDB

默认连接本地无认证MongoDB，并在首次会议时自动创建 `huiyi` 数据库：

```text
mongodb://127.0.0.1:27017
```
