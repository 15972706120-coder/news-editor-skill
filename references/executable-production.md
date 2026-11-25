# 可执行制作链路：时间轴、版式、声音与验收

资讯快讯用随包的 `render_news_brief.py` 渲染，`mix_timeline_audio.py` 混音，`validate_news_video.py` 机器验收，`extract_layout_proof.py` 出分板证据。四者只读同一个 `timeline.json` 和 `config.json` 引用的锁定版式；不另行手填一套时长、字幕帧位或坐标。

## 1. 一个时间轴，其他节点只读

`timeline.json` 使用 `schema_version=2`。帧号从零开始；所有区间为全局半开区间 `[start_frame,end_frame)`，不能把秒数和全局帧号混用。视频规格来自 config，固定版式来自其中引用的 lock。

顶层字段：

- `schema_version, format, fps, total_frames, cover_frames, audio_mode, headline_lines, caption_blocks, source_label`；渲染还需 `cover, clips`（纯图文形态没有 `clips`）。
- `format`：`brief_video`（标题块＋实拍带＋字幕块）或 `brief_text`（蓝色背景＋面板＋逐行正文）。
- `audio_mode`：`bgm_only`（默认）、`original_bgm`、`tts_bgm`，含义见 [视觉与声音模板](visual-audio-template.md)。可选 `bgm` 指定内置曲库文件。
- `headline_lines`：2–3 个 `{text, style}`（纯图文为 1 个），`style` 取 `yellow_tag / black_tag / yellow_italic / white_italic`，视频形态末行必须是 `yellow_italic`；每行不超过 config 的标题行字宽。
- `result_line`（可选）：1 行红字结论，不超过 config 字宽。
- `disclaimer_label`：只要用了 `contextual_broll` 片段就必须等于锁定 JSON 的免责文案，否则必须没有这个字段。
- `source_label`：本条新闻的来源小字（如“界面新闻/财联社”），渲染在右下角来源安全框；标题块与结果行都不得包含其中的媒体名。
- `publish_title`（可选）：发布到平台时使用的标题；不填时取标题块各行以空格连接（渲染报告的 `derived_publish_title`）。
- `duration_extension_authorized, duration_extension_reason`：总时长超过 config 上限时必须同时给出，且不超过硬上限。

每个字幕块（`caption_blocks`）：

- `id, start_frame, end_frame, lines`：块必须从第 1 帧起连续覆盖到 `total_frames`；`lines` 为 1–4 行（纯图文每段最多 6 行），每行不超过 config 的字幕行字宽。
- `highlights`（仅纯图文）：要标黄的子串，每一条都必须是 `lines` 拼接文本的子串。
- `timing={information_task, reading_basis, cut_reason, reading_hold_seconds}`：三个文字字段不得为空；`reading_hold_seconds` 是有限正数且不低于阅读下限（视频形态按字宽计算，纯图文按行数×逐行节奏）；块的实际可见时长不得小于它。
- 仅 `tts_bgm`：`narration` 与 `voice`。旁白默认与字幕逐字一致（忽略标点与排版差异）；独立改写时须有 `narration_override_reason, claim_ids, narration_reviewed=true`。`voice={path, manifest, start_frame, duration_seconds, text_sha256, audio_sha256}`，时长来自该 WAV 的真实采样帧数，哈希来自本次 TTS 报告，不能手填估算。

视频片段（`clips`，仅视频形态）：

- `media_type=video`：`path, source_sha256, source_in_seconds, start_frame, end_frame, crop, supports_claim, footage_role, keeps_source_subtitles, use_original_sound`。片段必须从第 1 帧起连续覆盖到片尾；`crop` 是源像素坐标 `[x,y,w,h]`，渲染器把它等比铺满实拍带。速度固定为 1，不重复用同一时段（确需复用写 `replay_reason`）。
- `footage_role`：`direct_evidence`（事件现场、当事方、官方画面）、`contextual_broll`（资料画面，须配免责小字）、`first_hand_quote`（当事方或目击者原话）。只有 `first_hand_quote` 可以 `keeps_source_subtitles=true`，且必须有 `quote_reviewed=true` 与 `claim_ids`。
- `use_original_sound=true` 仅允许在 `original_bgm` 模式下的 `first_hand_quote` 片段；其他模式和其他角色都必须为 false。
- 可选 `blur=[x,y,w,h]`：仅用于等比铺满后**实拍带局部坐标**里的底部字幕，不能填整张画布坐标；必须实际检查原字不可辨及边缘。
- `media_type=image|screenshot`：没有时间码、原声和字幕字段；另需 `acquisition_method, rights_basis, source_url`（用户提供可免 URL）与 `motion={start_zoom,end_zoom,start_anchor,end_anchor,easing}`；截图另需 `intentional_text_reviewed=true, claim_ids`。数量、正文占比、缩放与锚点位移只读 config；视频帧占比必须大于零。静态段运动由渲染器按 config 的超采样倍数先放大再输出，render 报告记录 `still_motion_supersample`，机器验收会核对该字段。

浮层（`overlays`，可选）：`type=comment_screenshot`，含 `path, source_sha256, start_frame, end_frame, box, supports_claim, intentional_text_reviewed=true, claim_ids, acquisition_method, rights_basis, source_url`。`box` 必须完全落在实拍带内；数量与时长只读 config。

封面（`cover`）：

- `source_kind=image, derived_from_video=false, path, source_sha256, crop, headline, subline, acquisition_method, source_url, rights_basis, selection_reason, clean_image_reviewed=true`；图片封面不得含 `source_in_seconds`。
- 或 `source_kind=video_frame, derived_from_video=true, source_in_seconds, ..., frame_sharpness_reviewed=true`，取自已下载原片的清晰无字关键帧。
- `headline/subline` 不超过 config 的硬上限字宽；渲染器按真实字形宽度在字号上下限之间自适应，低于下限直接报错。

所有输入路径相对 timeline 文件目录，或使用显式绝对路径；不得依赖运行时 cwd。默认规格改动必须有用户依据并记录，不能为了容纳字幕或配音自动延长成片。

## 2. 节奏由阅读或实测声音决定

- **默认与原声模式按阅读排**：每块下限为 config 的阅读公式（基础秒数＋每字宽秒数×字宽，且不小于最小块时长）。`production_contract.plan_reading_frames(字宽列表, fps)` 给出可运行的初稿；编辑在此之上按理解难度加长。
- **配音模式按实测排**：先逐块合成并测量配音时长，再用 `plan_voice_frames(时长列表, fps)` 得到初稿；块长仍不得低于阅读下限。
- 编辑者还要检查：第一块是否直接给出最重要的事实；每块是否只有一个信息点；数字、否定、限定是否在同一块内表达完整；手机尺寸静音看一遍是否读得完。分配器的结果不是批准的剪辑。
- 配音的机器检查能证明请求稿与 WAV 对应，**不能证明读音正确**；人名、数字、否定词仍须试听。

`load_timeline(path, verify_assets=True)` 是渲染、混音、验收共用的入口。任何文案、素材、WAV、帧位变化都会使旧证据失效。

## 3. 正常执行命令

先完成版本门、环境预检、事实与素材审核。连续生产从 [编辑台账与效果反馈](editorial-feedback-loop.md) 的事件占位开始；每条事实包先通过 editorial_contract 的 facts 阶段，素材与封面探针通过 feasible 阶段后才渲染（配音模式才调用 TTS）。标准渲染适配器需要 Python/Pillow、批准的字体与 FFmpeg/FFprobe。

```text
python scripts/production_contract.py <timeline.json>
python scripts/render_news_brief.py --timeline <timeline.json> --out-dir <work/render> --font <approved-bold-font> [--font-index <n>]
python scripts/render_news_brief.py --timeline <timeline.json> --out-dir <work/render> --font <approved-bold-font> --render
python scripts/mix_timeline_audio.py --timeline <timeline.json> --output <mix.wav> --report <mix.json>
python scripts/assemble_news_video.py --timeline <timeline.json> --render-report <render-report.json> --mix-report <mix.json> --output <candidate.mp4> --report <assembly.json>
python scripts/validate_news_video.py <candidate.mp4> --timeline <timeline.json> --assembly-report <assembly.json> --qa-dir <work/qa> --qa-frames auto --report <qa.json>
python scripts/extract_layout_proof.py <candidate.mp4> --timeline <timeline.json> --qa-report <qa.json> --render-report <render-report.json> --out-dir <work/proof>
```

只有 `tts_bgm` 需要先逐块生成配音：

```text
python scripts/minimax_tts.py --text-file <block.txt> --page-id <block-id> --output <block.wav> --report <block.wav.json>
```

不带 `--render` 时渲染器先准备无损封面、3:4 上传图、两种缩略图、标题区包装层、各字幕块的定格图与滑入序列和滤镜计划；这些通过视觉复核后，用同一命令加 `--render` 编码一次，封面仅占第 0 帧。FFmpeg 相关命令均可传 `--ffmpeg`，需要探针的命令可传 `--ffprobe`；Windows 的链接入口与权限应先验证，权限错误不是安装损坏。

渲染器保证的帧数规则（任一不满足即视为渲染失败，不要手工补帧）：

- 每段素材经帧率转换后用尾帧克隆补足并裁到计划帧数，只覆盖转换损失的 1–2 帧；真实不足的素材由时间轴检查在渲染前拒绝。
- 所有叠加层都是只在自己帧区间存在的有限流，不使用无限循环图层，因此叠加不会改变主画面的帧数。
- 链尾以帧率滤镜为每帧写入真实时长。否则末帧时长为 0，MP4 会把媒体长度记成末帧起点，总时长落在整毫秒边界时编辑列表会把末帧标成丢弃。

## 4. 封面与文字的对齐防线

`render_news_brief.py` 把每条文字的可见 XYWH、字体哈希、锁定文件哈希写入 `render-report.json`。标题块、结果行、字幕行和封面两行文字都按真实字形测量宽度：字号从锁定上限起步，放不进锁定宽度时逐像素下调到下限为止，仍放不下直接报错——不缩字到下限以下、不横向压缩、不换行。锁定文件里的 `*_visible_bbox` 是参考样本的 XYXY 可见边界，不是固定文本框；新字符串保持参考视觉中心。源视频旋转或 SAR 非标准时先显式归一并留证。

## 5. 快速但不降低验收

- TTS 缓存键包含实际文本、站点、模型、音色及全部生效声音参数；复用还检查块号、文件路径、WAV 规格和哈希。只改封面不再付费合成同一文案。缓存命中仍不代表读音审核通过。
- 正文从源片段按入点解码，统一帧率后按目标帧数裁切，一次编码全部图文。已发现的源自带跳切/叠化须先避开。
- 混音先生成可测的独立音轨，逐区间测声差；最终混音保持线性，整体响度与真峰值一起判断，最终 MP4 再测一次。
- 只替换声音时视频流直接复制；只改文案/镜头才重渲染。只有输入哈希全匹配才允许复用旧产物。
- 最终 QA 一次批量抽取封面、第 1 帧、每个字幕块到达帧与滑入帧、全部片段首中尾/切点和末帧；分板复用相同视频哈希及抽帧哈希的 QA 结果。
- 每节点记录实测时间，区分检索/下载、API、编码、QA 与人工评审。

## 6. 失败、内部测试与发布

普通命令遇到坏封面、超长文案、错音频或来源不足时停止对应节点。维护回归才允许 `--diagnostic` 重现失败；产物固定标记 INTERNAL/DRAFT、diagnostic=true、final_ready=false，不得进入发布区。`smoke_test_brief_pipeline.py` 用合成素材跑通全链路，产物同样是内部诊断。

机器验收返回 `MACHINE_CHECKS_PASSED`，从不自动返回 FINAL_READY。最终发布另需 `acceptance.json`：

```json
{
  "status": "FINAL_READY",
  "diagnostic": false,
  "content_id": "与台账及editorial.json相同的稳定ID",
  "editorial_report_sha256": "本次editorial-report.json文件哈希",
  "cover_title": "已审核的封面主标题",
  "cover_title_full": "封面主标题，封面副标题（与最终 MP4 文件名逐字一致）",
  "video_sha256": "实际成片哈希",
  "cover_sha256": "独立封面哈希",
  "machine_report": "qa.json",
  "machine_report_sha256": "验收报告哈希",
  "review": {"visual": "passed", "audio": "passed", "facts": "passed", "muted_reading": "passed", "voiced_playback": "passed"}
}
```

示例不是默认通过值；只有实际看过分板/到达帧/切点/手机预览、听过最终音轨、核过事实并完成全部 G0–G8 才能填写。`muted_reading` 和 `voiced_playback` 分别对应手机尺寸正常速度的静音阅读与带声音播放；默认模式下前者就是观众的真实观看方式，必须确认每个字幕块读得完。未做写 not_checked 并停止发布。`publish_news_output.ps1` 必须同时传 `-CoverTitle <封面主标题>`、`-CoverSubtitle <封面副标题>`、`-AcceptanceReport` 和 `-EditorialReport`，脚本以“主标题，副标题”命名最终 MP4，并核对标题、当前成片/封面哈希、机器检查范围、诊断标记和人工门。先按 [编辑反馈](editorial-feedback-loop.md) 运行 ready 阶段检查，生成 editorial-report.json；acceptance.content_id 必须相同且 editorial_report_sha256 为当前报告哈希。

维护开发与生产启动区分：先在实际安装版完成 GitHub 版本门，记录开发基线；隔离开发工作树上的修复、测试不会中途再次拉取覆盖代码。新生产任务仍严格从 GitHub 最新版启动。
