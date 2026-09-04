# MiniMax TTS 集成

本文件只用于真正生成配音、变更 MiniMax 模型/音色，或处理接口错误。接口字段可能变化；执行前核对 [MiniMax 中国大陆 T2A 官方文档](https://platform.minimaxi.com/docs/api-reference/speech-t2a-http) 或 [MiniMax 国际站 T2A 官方文档](https://platform.minimax.io/docs/api-reference/speech-t2a-http)。

## 调用前停点

先完成事实、文案、素材、版式和不联网的脚本测试。第一次需要实际调用 API 时：

1. 检查环境变量 `MINIMAX_API_KEY` 与 `MINIMAX_API_BASE_URL`。
2. 若缺失，停在声音节点并向用户说明：请在本机配置 `MINIMAX_API_KEY`，并告知账号属于中国大陆站还是国际站；请勿把密钥粘贴到聊天中。配置完成后只需回复“已配置”。
3. 禁止把密钥写入命令行参数、Skill、源码、`.env` 示例、日志、QA 报告或 Git。若密钥曾进入聊天或仓库，提示立即轮换。

官方基址只允许：

- 中国大陆：`https://api.minimax.cn`；备用 `https://api-bj.minimaxi.com`。
- 国际站：`https://api.minimax.io`；备用 `https://api-uw.minimax.io`。

同一密钥不得自动跨站点尝试。

## 短视频同步方案

运行：

```powershell
python scripts/minimax_tts.py --text-file <page-01.txt> --output <音频/page-01.wav>
```

客户端调用 `POST <base>/v1/t2a_v2`，Bearer 鉴权，非流式、hex 输出。默认：

- model：`speech-2.8-hd`；成本或速度优先时才显式改为 `speech-2.8-turbo`。
- voice：`Chinese (Mandarin)_News_Anchor`（2026-09-04 用户五音色试听对比后定版）；`Chinese (Mandarin)_Reliable_Executive` 为备用男声，仅在用户点名时使用。
- `language_boost=Chinese`，`speed=1.0`，`vol=1.0`，`pitch=0`，`emotion=calm`。
- 原始 stem：WAV、44.1kHz、单声道；最终混音时高质量重采样为 AAC、48kHz、立体声。

### 参数扩展（`scripts/minimax_tts_pro.py`）

需要调整语速、语调、情绪或混合音色时使用 `scripts/minimax_tts_pro.py`（凭据仍只从环境变量读取）。T2A v2 支持范围：

- `speed` 0.5–2.0（值越大越快）；`vol` (0,10]；`pitch` -12~+12 半音（±3~5 内自然，可微调年龄感）。
- `emotion`：`happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper`；`speech-2.8-hd/turbo` 不支持 `whisper`，`fluent`/`whisper` 仅 2.6 系列。没有情绪强度参数。
- `timbre_weights`：最多 4 个音色按权重混合（int 1–100，越高越接近该音色）；使用时 `voice_setting.voice_id` 置空。
- `pronunciation_dict.tone`：不改稿纠正读音，如 `Cybercab/赛博卡布`（文本展开）、`郑栅洁/(zheng4)(shan1)杰`（拼音 1-4 声、5 轻声）、`resume/(rɪˈzjuːm)`（IPA）。
- 停顿标记 `<#x#>`：两段可发音文本之间插入 0.01–99.99 秒停顿，不可连续使用；不支持 SSML。
- `voice_setting.text_normalization`：开启后优化数字朗读，略增延迟。

### 音色查询

账户可用音色用 `POST <base>/v1/get_voice`、body `{"voice_type":"all"}` 查询（大陆站实测路径，国际站路径以官方文档为准）。返回 `system_voice[]` 含 `voice_id/voice_name/description`，本机账户 303 个系统音色。

成功必须同时满足 HTTP 成功、`base_resp.status_code==0`、`data.status==2`、`data.audio` 非空且 hex 可解码；保留 `trace_id`，不记录鉴权头。文本少于 10000 字符；超过 3000 字符时应拆页或改流式，不把超长稿硬塞入同步接口。

## 错误和试听

- 最多重试 3 次并指数退避：`1000/1001/1002/1024/1033`；`2045` 降速后再试。
- 鉴权、余额、敏感内容、非法字符、参数、音色和套餐错误不盲目重试：`1004/2049/1008/1026/1027/1042/2013/20132/2056`。完整定义见[官方错误码](https://platform.minimaxi.com/docs/api-reference/errorcode)。
- `20132` 时使用[官方音色查询接口](https://platform.minimax.io/docs/api-reference/voice-management-get)或音色表核对账户可用音色，不静默换音色。
- 每页原始 stem 必须完整试听并对照稿件，检查人名、地名、型号、数字、否定词、多音字、停顿、漏读和重读；不合格时改稿或参数并重新生成。
- 最终混音检查 48kHz/双声道、-17 至 -15 LUFS、真峰值不高于 -3 dBTP，播报区 BGM 比人声低 12±1dB；用耳机与手机外放分别试听。

模型与速率限制以[官方模型发布记录](https://platform.minimaxi.com/docs/release-notes/models)和[官方限流说明](https://platform.minimaxi.com/docs/guides/rate-limits)为准，Skill 不写死账户级并发值。
