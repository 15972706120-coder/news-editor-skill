# 可执行制作链路：时间轴、版式、声音与验收

这是现有 News-Editor 的执行补强，不是新样式。保留既有选题、下载、深海蓝三段式、封面字体与交付目录。标准静态新闻包装用随包的 `render_locked_news.py`；既有 Remotion 工程可以继续用，但必须读取同一配置/时间轴并提供等价的渲染来源记录，不得重新手填一套页长或坐标。

## 1. 一个时间轴，其他节点只读

`timeline.json` 使用 schema_version=1。帧号从零开始；所有区间为全局半开区间 `[start_frame,end_frame)`，不能把秒数、页内帧号和全局帧号混用。视频规格来自 config，固定版式来自其中引用的 lock。

必需字段：

- 顶层：`schema_version, fps, total_frames, cover_frames, duration_profile, audio_mode, headline, subtitle, pages`；渲染还需 `cover, clips`。`duration_profile=standard|compact`，对应范围只读 config；当前标准音频模式为 `tts_bgm`。
- 每页：`id, start_frame, end_frame, white_lines`（1–2行）、`red_emphasis`（单独1行）、`narration, voice`。
- 每页还需 `timing={information_task, reading_hold_seconds, reading_basis, cut_reason}`。三个文字字段（information_task、reading_basis、cut_reason）不得为空；reading_hold_seconds 是有限正数，含读完红字后的理解停留；不足其向上取整帧数时脚本直接拦截。初稿可记录读稿估计，不能把估计写成已经完成的手机播放复核。
- `voice`：`path, manifest, start_frame, duration_seconds, text_sha256, audio_sha256`；时长来自该 WAV 的采样帧数，哈希来自本次 TTS 报告，不能手填估算。
- 旁白默认与白字+红字一致，只忽略标点与排版差异；独立改写旁白时必须有 `narration_override_reason, claim_ids, narration_reviewed=true`，并逐页对照事实核验。否定词、数字和主体变化不能被“意思差不多”放行。
- 视频片段：`media_type=video, path, source_sha256, source_in_seconds, start_frame, end_frame, page_id, crop, supports_claim`。标准适配器 speed=1，不重复或变速凑时长。
- 静态片段：`media_type=image|screenshot`，没有 source_in_seconds 和 blur；除通用字段外需 `acquisition_method, rights_basis, source_url`（用户提供可免 URL）与 `motion={start_zoom,end_zoom,start_anchor,end_anchor,easing}`。截图另需 `intentional_text_reviewed=true, claim_ids`。数量、正文占比、缩放与锚点位移只读 config；视频帧占比必须大于零。
- 可选 `blur=[x,y,w,h]` 仅用于等比填满后**实拍板局部坐标**中的底部字幕，不能填成整张画布坐标；必须实际检查原字是否不可辨及边缘。移动字幕应拆段跟踪，不得扩大模糊区兜底。
- `cover`：`source_kind=image, derived_from_video=false, path, source_sha256, crop, headline, subline, acquisition_method, source_url, rights_basis, selection_reason, clean_image_reviewed`。禁止 source_in_seconds；渲染器直接读取独立图片，不调用视频抽帧。声明已查看不能自动证明清晰、美观或相关，仍需独立封面质量证据。

所有输入路径相对 timeline 文件目录，或使用显式绝对路径；不得依赖运行时 cwd。默认规格改动必须有用户依据并记录，不能为了容纳配音自动延长成片。

## 2. 页长由内容与实测共同决定

先根据真实动态素材和信息量选择时长档：素材充分使用 standard；不足时先精简为单一主要任务并选择 7–9 秒 compact，不重复、慢放、冻结或用静态图堆满。然后写短稿、合成并试听每页，再分配页长；最后锁定镜头目标帧数。`production_contract.allocate_pages(...)` 只给出配音与理解停留的初始下限，总帧不足立即报错。

编辑者在这个下限以上再检查：

1. 第一页交代一个值得关注的事件或矛盾；第二页交代回应、结果或影响。不用泛泛铺垫，也不把关键回应藏到最后一瞬间。
2. 每页一个信息任务，红字只承担该页最重要的结论。数字多、政策条件多、否定词多时增加理解停留；先减掉冗余文案，不能通过小字或明显加速强行容纳。
3. 页面和镜头独立：页面按完整语义/配音停顿切换，镜头按动作与场景变化切换。同页允许多个互补镜头，不要求每次换镜头都换文字。
4. 先看完整页和手机缩略效果，确认红字有足够时间读完，再调整页长。调整后必须重跑统一时间轴检查，配音仍须在自己的页内结束。
5. 不把某个首屏秒数写死为爆款公式；用同类选题的实际留存、完播和有效互动反馈调整，未经比较不能承诺观众更喜欢。

### 编辑决定换页点，而不是平均分剩余秒数

每页初步时长取 `max(实测完整配音+起止余量, 阅读理解最低停留)`。阅读理解停留已经包含读完重点后的消化时间，不要重复机械叠加；看字与听声音通常同时发生，也不能把二者时长直接相加。分配器平均分配剩余帧只是可运行的初稿，不能因此跳过编辑判断。

定稿时逐页执行以下步骤，在 `edit-plan.md` 保留简短理由：

1. 先写该页唯一的信息任务及观众读完应知道什么。事件类通常为“发生了什么 → 回应/结果”；民生政策类通常为“谁受影响/能得到什么 → 条件/时间/如何办理”。保持已确认双页三段式模板，不因这一分类自动加页。
2. 第一屏直接给出重要事实，避免把时间、来源、套话堆成前奏；但涉及生效日期、地域、条件时不得省略到改变含义。已知回应不得故意延迟到末尾制造误解或悬念。
3. 以实际音频的语句结束为候选换页点；该页文字、红字和重要限制都应已完整理解。不在“暂无法”“仅限”“不包括”和随后的对象之间切断，不在金额、年龄、日期尚未表达完整时换页。
4. 按手机尺寸正常速度静音看一遍，再带声音看一遍。静音来不及读完的页，先删重复信息，再给它更多时间；读完后明显空等的页，将余量转给信息更复杂的页。未做此检查须记录 not_checked，不能用计算值代替真实复核。
5. 每页记录 `信息任务、白字/红字、实测音长、阅读停留依据、最终起止帧、换页理由`。若镜头跨页，素材时间应连续，只更换包装；实现上可在页边界拆段，但后一段入点承接前段出点，不能重新从头播放。
6. 14 秒装不下时，不牺牲否定词、适用范围或重要条件，不截尾音、不用明显加速赶稿；先压缩文案，仍放不下则说明所需时长并取得延长授权。不同新闻可以有不同换页点，不把某次实测的 6.5/7.5 秒变成新默认。

首轮观众反馈只用于提出下一轮假设：开头流失可检查主题是否一眼可懂，换页附近流失可检查信息断层，反复观看既可能感兴趣也可能没读懂。不能凭一个指标断言原因；尽量在同类题材、相近长度下比较，每轮仅改变一个主要节奏变量，同时关注理解是否准确。

`load_timeline(path, verify_assets=True)` 是渲染、混音、验收共用的入口。任何页号、稿件、WAV、页起止、素材内容变化都使旧证据失效。当前检查能证明请求稿和实际使用的 WAV 对应，**不能证明 TTS 实际读音正确**；人名、数字、否定词仍须试听，ASR 可辅助但不独立放行。

## 3. 正常执行命令

先完成版本门、对应环境预检、事实与素材审核。标准渲染适配器需要 Python/Pillow、批准的字体与 FFmpeg/FFprobe；不需要临时重建浏览器工程。只有选择 Remotion 实现时才检查其完整 Skill、项目与 npm 依赖，缺工程不视为已验证。

```text
python scripts/minimax_tts.py --text-file <page.txt> --page-id <page-id> --output <page.wav> --report <page.wav.json>
python scripts/production_contract.py <timeline.json>
python scripts/render_locked_news.py --timeline <timeline.json> --out-dir <work/render> --font <approved-bold-font>
```

不带 `--render` 时先准备无损封面、两种缩略图、每页透明包装层和滤镜计划。透明包装层中间空白是待合成区，不是正文合格预览：还须按 N5 查看每段目标裁切后的首中尾帧。几何/文本检查失败时禁止进入编码。准备产物通过视觉复核后，用同一命令添加 `--render` 编码一次正文，封面仅占首帧。

```text
python scripts/mix_news_audio.py --timeline <timeline.json> --output <mix.wav> --report <mix.json>
python scripts/assemble_news_video.py --timeline <timeline.json> --render-report <render-report.json> --mix-report <mix.json> --output <candidate.mp4> --report <assembly.json>
python scripts/validate_news_video.py <candidate.mp4> --timeline <timeline.json> --assembly-report <assembly.json> --qa-dir <work/qa> --qa-frames auto --report <qa.json>
python scripts/extract_layout_proof.py <candidate.mp4> --timeline <timeline.json> --qa-report <qa.json> --out-dir <work/proof>
```

FFmpeg 相关命令均可传 `--ffmpeg <实际可执行文件>`，需要探针的命令也可传 `--ffprobe`。Windows 的链接入口与实际可执行文件、权限应先验证；权限错误不是安装损坏，不据此重装依赖。

## 4. 封面错位的具体防线

正文安全框与分板使用 XYWH；lock 中 `*_visible_bbox` 是参考样本的 XYXY 可见边界。参考文字长度不同，不能把样本的宽度强加给每个新标题。固定字号不变，保留参考视觉中心；测量新字符串的真实字体包围盒和描边，再在安全宽度内居中。绘制时扣除字体 ascent/left bearing，不能把“文字绘制原点”当成“肉眼可见文字左上角”。

`render_locked_news.py` 将每条字的可见 XYWH、字体哈希、lock 哈希写入 `render-report.json`；长标题直接报溢出，不缩字、不横向挤压。参考中心与板块没变；新字形的可见高度可能不同，最终仍与参考视觉复核。源视频旋转/SAR 非标准时先显式归一并留证，不能凭编码宽高猜坐标。

## 5. 快速但不降低验收

- TTS 缓存键包含实际文本、站点、模型、音色及全部生效声音参数；复用还检查页号、文件路径、WAV规格和哈希。只改封面不再付费合成同一文案。缓存命中仍不代表读音审核通过。
- 正文从源片段按入点解码，统一帧率后按目标帧数裁切，重建从零开始的 PTS；一次编码全部图文。已发现的源自带跳切/叠化须先避开，合法 PTS 不能证明动作连续。
- 混音先生成可测的独立声音轨，逐页同区间测声差；最终混音保持线性，整体响度与真峰值一起判断。最终 MP4 再测一次，不只检查 WAV。
- 只替换声音时视频流直接复制；只改变文案/镜头才重渲染对应画面。只有输入哈希全匹配才允许人工复用旧产物，不能仅凭文件存在跳过。当前自动缓存实现仅覆盖 TTS，不宣称全部节点已有自动增量缓存。
- 最终 QA 一次批量抽取封面、页中点、全部片段首中尾/切点和末帧；分板复用相同视频哈希及抽帧哈希的 QA 结果，避免重复全片扫描。
- 每节点记录实测时间，区分检索/下载、API、编码、QA、人工评审。局部提速不能冒充整条新闻总耗时提速。

## 6. 失败、内部测试与发布

普通命令遇到坏封面、长配音、错音频或来源不足时停止对应节点。维护回归才允许 `--diagnostic` 重现失败；产物固定标记 INTERNAL/DRAFT、diagnostic=true、final_ready=false。不得以该参数绕过正常制作门，更不能放到发布区。

机器验收返回 `MACHINE_CHECKS_PASSED`，从不自动返回 FINAL_READY。最终发布另需 `acceptance.json`：

```json
{
  "status": "FINAL_READY",
  "diagnostic": false,
  "cover_title": "已审核的封面主标题",
  "video_sha256": "实际成片哈希",
  "cover_sha256": "独立封面哈希",
  "machine_report": "qa.json",
  "machine_report_sha256": "验收报告哈希",
  "review": {"visual": "passed", "audio": "passed", "facts": "passed", "muted_reading": "passed", "voiced_playback": "passed"}
}
```

示例不是默认通过值；只有实际看过分板/切点/手机预览、听过每页原声及混音、核过事实并完成全部 G0–G8 才能填写。`muted_reading` 和 `voiced_playback` 分别对应手机尺寸正常速度的静音阅读与带声音播放；逐页证据需注明完整播放区间、是否读完红字、重要限制是否完整、是否空等及换页结论。未做写 not_checked 并停止发布。证据路径和结论保留原有 QA 清单中。`publish_news_output.ps1` 必须传 `-AcceptanceReport`，会核对标题、当前成片/封面哈希、机器检查范围、诊断标记和人工门，拒绝只凭“文件存在”交付。

维护开发与生产启动区分：先在实际安装版完成 GitHub 版本门，记录开发基线；隔离开发工作树上的修复、测试不会中途再次拉取覆盖代码。新生产任务仍严格从 GitHub 最新版启动；不能拿开发状态代替已发布版本。
