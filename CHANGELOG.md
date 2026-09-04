# Changelog

## 1.8.0 — 2026-09-04

- 新增仓库根 `config.json` 作为全部制作 FACT（输出路径、音色与模型、混音参数、成片规格、坐标锁定文件引用）的唯一机器事实源；修改任何值只改一处。
- 文档全面瘦身去复述：SKILL.md、profile-v2、README 与 7 个 reference 中的输出路径、坐标数字、音色 ID、混音响度数值全部改为引用 config 或锁定 JSON；坐标的唯一人读镜像限定在 `locked-layout-validation.md`，音色 API 说明限定在 `minimax-tts.md`。
- 脚本直接消费 config：`minimax_tts.py` 的默认模型/音色、`publish_news_output.ps1` 与 `check_output_layout.ps1` 的输出根目录（`-OutputRoot` 变为可选参数）均从 config 读取，忘传/传错路径的空间归零。
- 新增 `scripts/mix_news_audio.py` 自动混音：按 config 把各页人声归一到目标段响度、BGM 自动计算 12dB 避让增益、限幅后实测综合响度并有限迭代校准；输出结构化指标 JSON。回归验证与 2026-09-04 手工版逐项一致（I -15.1 LUFS / TP -3.5 / 逐页差 11.9，且一次迭代命中）。
- 一致性门禁升级：新增 config 完整性检查（必需段、锁定文件与 BGM 资产存在性）与"文档复述 FACT"检查（输出路径/坐标模式/音色 ID 只允许出现在 config、CHANGELOG 与两份镜像文档）。

## 1.7.0 — 2026-09-04

- 规范去双轨化：删除 SKILL.md 的 V1 条款与 `10a/15a` 补丁覆盖结构，封面零模糊与 `layout-lock-v2.json` 坐标直接并入正文条款；V1 历史仅留 CHANGELOG。
- 全量清理 V1 残留：`locked-layout-validation`、`cover-platform-layout-v2`、`editorial-sop`、`quality-standards`、`delivery-gates`、`visual-audio-template` 中的 V1 分板与文字坐标全部更新为 V2；`visual-audio-template` 的配音节从 Edge TTS 规范重写为 MiniMax 定版规范；`environment-setup` 移除 Edge TTS 安装与冒烟步骤。
- TTS 脚本合并：`minimax_tts.py` 直接暴露 `--voice/--speed/--vol/--pitch/--emotion/--timbre`，删除 `minimax_tts_pro.py` 分身；新增 `voice_source` 输出字段（`explicit/env/default_fallback`），音色静默回退从此可见。
- 新增 `scripts/check_skill_consistency.py` 一致性门禁（禁用术语、V1 坐标、内部链接、资产引用、Python 语法、锁定文件状态），配 `hooks/pre-commit` 以 `core.hooksPath` 分发；首次运行即清除 11 FAIL / 29 WARN 的规范漂移。

## 1.6.0 — 2026-09-04

- 默认音色定版为 `Chinese (Mandarin)_News_Anchor`（用户五音色试听对比确认），`Reliable_Executive` 降为备用男声；两个 TTS 脚本默认值同步。
- 新增 `minimax_tts_pro.py`：暴露 speed/vol/pitch/emotion/timbre_weights 参数，并记录发音词典、停顿标记、账户音色查询接口（`POST /v1/get_voice`）等扩展能力。
- 写入实测混音定式：人声约 +2.4dB、BGM-01 0–14s 平切 -9.4dB、`alimiter=0.668`，并记录人声增益超过约 +4dB 触发限幅器"天花板泵"的陷阱。
- 发布流程整体改为输出流程：输出区从 `<项目根>/outputs/` 迁移到独立磁盘路径 `D:\每日新闻\YYYY-MM-DD\N.中文新闻短名\`，工作区仍为项目根 `.news-editor-work`；全部文档与目录检查提示同步更新。

## 1.5.0 — 2026-09-04

- 新增最高优先级 V2 制作配置：扩大中段实拍区，并按最新平台参考重排、下移正文文字组；机器坐标源切换为 `layout-lock-v2.json`。
- 封面改为严格使用清晰、干净、无字幕、无马赛克和无模糊的原始干净帧；正文保留仅限底部字幕的局部模糊例外。
- 增加素材有效裁切分辨率、放大倍率、单次高质量编码和强制人工清晰度检查。
- 默认配音迁移到 MiniMax 官方 T2A，同步加入凭据停点、安全环境变量、受限重试、逐页 WAV 和人工听检；真正调用 API 前才向用户请求本机配置。
- 最终 MP4 强制与封面主标题同名，移除“新闻女声”等变体后缀，并在目录检查中验证名称一致。

## 1.4.0 — 2026-09-02

- 从用户确认的 14 秒参考成片抽取封面、两页稳定帧、分页切点、末帧以及标题板、实拍板、正文板独立参考资产。
- 新增 `layout-lock-v1.json`，把封面、标题条、1054px 实拍区、白字、红字、来源和底线的像素坐标设为唯一坐标源。
- 新增 `extract_layout_proof.py`，对草稿与最终 MP4 自动抽取封面、缩略图、各页三块分板、切点和末帧，并输出结构化证据清单。
- 将全部视觉规范统一到确认成片实测布局，同时保留用户后续确认的深海蓝底板；板块边界偏差超过 2px 或未逐板查看时不得交付。

## 1.3.0 — 2026-09-01

- 将封面与正文底板统一为深海蓝渐变，新增透明 V3 四角框资产，禁止纯黑空封面和旧版不透明暗层遮住真实画面。
- 将正文实拍中段放大到 `1080×938`，正文固定为 1–2 行白色说明加 1 行红色重点，并定义平台 Logo 的可控遮挡阈值。
- 当精确标题结果全是主播或人脸近景时，要求从事实包推导职场、家庭、办事流程等场景关键词，并把素材区分为直接证据与场景 B-roll。
- 增加主播/AI 主播/对镜口播零容忍、普通人脸单段 1.5 秒与总占比 20%、至少 2 类场景等素材与时间轴硬门。
- 扩展素材清单、剪辑计划和 QA 报告字段，确保其他 Agent 必须提交场景类别、人脸占比、封面真实画面与红字遮挡证据。

## 1.2.0 — 2026-09-01

- 在原 N0–N11 流程内补齐节点状态、证据文件、失败回退和定向修改的复验要求。
- 细化抖音候选页确认、下载后完整解码、目标裁切首中末帧、有效时码与多素材互补剪辑标准。
- 封面改为先换帧与重构图、只对实际文字框精确局部模糊，禁止用大块模糊牺牲有效信息。
- 正文恢复用户确认的原版三段式布局，固定黄线、副标题、实拍层和上移后的平台安全文字区。
- 细化双页语义、逐页试音、帧数分配、人声/BGM stem 测量、最终抽帧与发布哈希验收。

## 1.1.0 — 2026-08-28

- 将固定盘符、用户名、旧版 `outputs/today-news.../project` 路径改为运行时项目变量。
- 统一使用 `<ProjectRoot>/outputs` 发布区与 `<ProjectRoot>/.news-editor-work` 内部工作区。
- 浏览器 profile、Cookie、下载元数据、截图和原片不再进入发布区。
- 增加稳定发布与目录检查脚本，强制日期、连续序号和中文主题命名。
- 更新封面字号、平台安全区、素材裁字、底部字幕模糊和最终验收规范。

## 1.0.0 — 2026-08-24

- 建立热点发现、事实核验、抖音精准检索、真实素材剪辑、新闻女声、BGM、封面与最终 QA 工作流。

