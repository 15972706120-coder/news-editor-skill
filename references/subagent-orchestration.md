# 子智能体编排与轻量交接规范

本规范只优化执行组织，不改变来源、封面、三段式正文、MiniMax、BGM、时间轴或 G0–G8 的现行质量标准。默认由根 Agent 集中完成事实、文案、时间轴、声音与渲染；仅在资源允许且存在独立工作时派发素材/封面侦察，完整制作保留独立只读 QA。以下角色是职责与写入边界，不要求每题都启动全部角色。选题和台账按 [编辑与数据反馈闭环](editorial-feedback-loop.md) 执行。

## 1. 基本原则

- 根 Agent 是唯一编排者、用户沟通者、时间轴批准者和最终发布者。子智能体不直接承诺 `FINAL_READY`，不把草稿写入最终输出区。
- 一个用户请求等于一个 orchestrated run。根 Agent 只联网核对一次 GitHub；本次运行固定该 commit。子智能体用父运行清单做本地证明，不重复访问 GitHub。
- 一个文件只能有一个写入者。事实、文案、视频、封面、时间轴、声音、渲染和 QA 分目录写入；建档时明确哪些职责由根 Agent 保留、哪些交给子角色。同一职责不由根与子角色同时写，时间轴仅由根 Agent 或已派发的 `timeline_editor` 中的一方写，其他角色只提交建议或证据。
- 默认不向子智能体转发完整聊天历史。只传任务包、必要输入路径与 SHA-256、硬约束和本角色必读 reference。网页原文、日志、接触表、截图和媒体文件保留在工作区，交接只写路径与哈希。
- 并行只用于相互独立的工作。事实标题未锁定前，不启动精确视频检索、封面定稿和成片文案；`timeline.json` 未锁定前，不启动最终声音、渲染或 QA。
- 同时运行的子智能体不得超过 `config.json` 的 `orchestration.max_parallel_workers`。登录态浏览器只交给 `video_scout`，避免多个智能体争用同一标签页、Cookie 或下载会话。
- 并发上限不是必须用满的目标；先确认实际槽位、浏览器占用和可独立输入，串行资源下不把连续制作拆成大量子任务。每轮默认仅 1–2 条在制，候选刷新、数量上限与停止凑数见反馈闭环。

## 2. 版本门与父运行清单

根 Agent 为新用户请求生成一个新的 `run_id`，把清单写到本主题工作区之外的本次运行根目录：

```powershell
pwsh -NoProfile -File '<SkillRoot>\scripts\ensure_latest_skill.ps1' `
  -RunId '<run_id>' `
  -ManifestOut '<项目根>\.news-editor-work\<日期>\<run_id>\run-manifest.json'
```

只有 `LATEST_READY` 且结果同时返回 `manifest_path` 与 `manifest_sha256` 才能派发子任务。`UPDATED_READY_RELOAD` 时先重读新版 Skill，再用同一 `run_id` 和尚不存在的新清单路径复核；最终必须取得 `LATEST_READY`。清单路径已存在、位于 Skill 仓库内或写入失败都阻塞，不覆盖旧清单。

子智能体必须拿到父清单路径、父清单 SHA-256、同一 `run_id` 和唯一 `child_id`，先执行：

```powershell
pwsh -NoProfile -File '<SkillRoot>\scripts\ensure_latest_skill.ps1' `
  -RunId '<父 run_id>' `
  -ParentRunManifest '<run-manifest.json>' `
  -ParentRunManifestSha256 '<父清单 SHA-256>' `
  -ChildId '<role-topic-sequence>'
```

`CHILD_CONTEXT_READY` 表示已在不联网的情况下核对：可信仓库与分支、干净工作树、本地 HEAD、父清单哈希、有效期、父级 `LATEST_READY`、固定 commit 和核心规范文件哈希。其余状态全部为 `BLOCKED_SKILL_VERSION`。子模式不得静默回退为旧版执行；独立启动、缺少父清单、清单过期或无法确认同一运行时，改用完整联网版本门开启新运行。

父清单只在本次用户请求和其子任务中有效，最长时效从 config 读取。新用户请求、任务重启、阻塞后人工恢复或跨日继续，均重新联网核对并建立新清单。

版本门状态、运行 ID、固定 SHA、有效期和重新开启运行的原因留在工作区。`LATEST_READY` 不等于发生更新；只有 `UPDATED_READY_RELOAD` 才要求更新后的重读。不能将每次本地子证明当成新根运行、重复联网或全面重读；有效期校验和过期处理不得省略。

## 3. 阶段、屏障与角色

### Phase 0｜根任务建档

根 Agent 完成版本门、建立工作区、记录用户硬约束、在制主题与每个文件的唯一写入者。下一条开工前刷新台账、事实和素材可行性；数量 N 是合格交付上限，暂不制作的题目保留为可替换候选。交付：`run-manifest.json`、每个主题的 `project-brief.md`；只为实际派发的子任务创建角色任务包。

### Phase 1｜事实锁定

默认由根 Agent 承担 `facts` 职责，完成来源排除、双来源核验、原始信息标题、四类时间、事实增量、风险和待确认项；确有独立核验工作时可单独派发 `facts`。只有 `fact-pack.md` 与 `source-manifest.md` 通过后才开放 Phase 2，并保留锁定输入哈希。事实未锁定时不得让其他角色自行猜标题或正文。

### Phase 2｜文案与素材准备

- 根 Agent 承担 `copy_pacing`：依据事实包输出短封面标题、正文白字/红字、逐页配音稿、信息任务、阅读依据、最低停留与建议档位，核对标题/口播/字幕/媒体的主体、数字、单位、日期和条件。素材与独立封面前置探针通过前不启动 TTS。
- `video_scout`：以锁定的原始信息标题做首条精确检索，下载和筛选视频，必要时按事实包扩展场景词；输出素材清单、接触表、裁切预览和建议源时间码。
- `cover_scout`：按 config 数量独立检索高清干净图片，为全部候选生成无标题 3:4 底图、270×360 无标题缩略图和同尺寸接触表，逐一填写技术清晰、主体可辨、主题高度相关、视觉冲击四项人工评分与依据；只交接所有单项和总分均达门槛的候选，不得用任何视频帧，也不得把搜索排名或模型自报分数当作人工查看证据。

视频与封面侦察仅在有可用资源、互不争用浏览器且工作独立时派发，素材已齐时省略对应 scout；根自行执行时保留同等来源与预览证据。实际派发的输入必须引用同一份事实包哈希，并完整校验任务包与交接包。某一路阻塞不允许另一角色替它写文件；根 Agent 可从可靠节点重派或在明确结束旧任务、保存原证据并记录新写入者后接手，不扩展用户授权范围。

### Phase 3｜单写者时间轴

默认由根 Agent 承担 `timeline_editor` 职责并独占 `timeline.json`，读取锁定事实、文案与已验证素材/封面证据，按声音、阅读、镜头动作和真实素材共同决定 7–9 秒或标准档，分别确定页面边界与镜头边界，完成裁切、缩放、位移、字幕局部模糊、静态图关键帧和素材到文案的映射。若确需派发 `timeline_editor`，该角色独占文件，根 Agent 的批准记录另存，不直接覆盖角色证据。验收后由唯一写入者将时间轴状态置为 `TIMELINE_LOCKED`；其他角色不得修改。

### Phase 4｜集中制作声音与画面

- 根 Agent 承担 `audio`：只读取锁定时间轴及文本，生成逐页 MiniMax stem、记录文本/参数/WAV 哈希并混入 BGM。
- 根 Agent 承担 `render`：只读取锁定时间轴、封面和素材，渲染无最终音轨的视频草稿及分板证据。

连续制作默认在根会话完成，避免为每个短节点重建上下文。只有确有并行收益、资源可用且输入独立时才另派 `audio/render`，仍遵守各自输出目录、任务包、交接包和唯一写入者约束。任一输入哈希改变，已有结果立即失效；不得沿用旧 stem、旧渲染或手填延迟。

### Phase 5｜独立 QA 与本地交付

完整制作派发独立 `qa`，只读检查新合成的最终 MP4、独立封面、来源包、四类时间、表达映射、时间轴、声音报告和分板证据；不得修片或改时间轴，也不得将未试听/未查看标为通过。发现问题输出 `BLOCKED` 交接和精确回退节点，根 Agent 不得覆盖失败结论放行。所有硬门通过后只返回 `QA_READY`，由根 Agent复核并执行本地输出脚本，最终决定是否为 `FINAL_READY`。用户验收与平台发布另记；仅在已验收且明确发布授权后进入支付宝发布管线，生产代选或滚动调度不构成发布授权。

## 4. 任务包

每个子任务都保存一个 JSON 任务包，结构为：

```json
{
  "schema": "news-editor-task-packet/v1",
  "run_id": "同父运行",
  "skill_sha": "父清单固定的40位commit",
  "child_id": "facts-topic01-01",
  "role": "facts",
  "topic_id": "topic01",
  "inputs": [{"path": "shared/project-brief.md", "sha256": "..."}],
  "required_references": ["references/editorial-sop.md"],
  "allowed_outputs": ["lanes/facts/fact-pack.md", "lanes/facts/source-manifest.md"],
  "constraints": {"user_hard_constraints": [], "preserve": [], "forbid": []},
  "stop_conditions": ["核心事实无法双来源核验时返回BLOCKED"]
}
```

路径全部相对本次运行工作区；输入文件必须存在并匹配 SHA-256；输出只能位于 config 为该角色分配的目录。根 Agent 派发前运行：

```powershell
python '<SkillRoot>\scripts\orchestration_contract.py' validate-packet `
  --packet '<task-packet.json>' --work-root '<本次运行工作区>' `
  --run-manifest '<run-manifest.json>' `
  --run-manifest-sha256 '<父清单 SHA-256>'
```

任务消息只包含：角色目标、任务包路径与哈希、父清单路径与哈希、SkillRoot。用户原始附件或长网页内容不重复粘贴；需要查看时从任务包的输入路径读取。子智能体只读本角色所列 references，不预加载全部规范；遇到冲突以 SKILL、config、锁定 JSON 和任务包硬约束为准并返回根 Agent。

## 5. 交接包

子智能体结束时写一个 JSON 交接包：

```json
{
  "schema": "news-editor-handoff/v1",
  "run_id": "同父运行",
  "child_id": "facts-topic01-01",
  "role": "facts",
  "topic_id": "topic01",
  "status": "READY",
  "input_packet_sha256": "...",
  "outputs": [{"path": "lanes/facts/fact-pack.md", "sha256": "..."}],
  "decisions": ["采用两条独立来源锁定事件时间"],
  "risks": [],
  "next_stage": "copy_pacing"
}
```

`decisions` 只保留影响后续的结论，数量不得超过 config 限额；原始日志和详细证据留在输出文件。阻塞时使用 `status=BLOCKED`，`risks` 写清阻塞事实和最近可靠节点，输出可为空。根 Agent 接收前运行：

```powershell
python '<SkillRoot>\scripts\orchestration_contract.py' validate-handoff `
  --handoff '<handoff.json>' --packet '<task-packet.json>' `
  --work-root '<本次运行工作区>'
```

机器校验通过后，子智能体在对话中只需回报 `READY/BLOCKED`、交接包路径、最多五条决定和阻塞项；不回贴长日志、网页全文、完整脚本输出或媒体二进制描述。

## 6. 目录与单写者边界

本次运行的编排文件全部在工作区，不能进入 config 定义的最终输出根：

目录中的角色表示职责；未派发时由根 Agent 按建档记录承担该职责，已派发时由对应子角色独占。以下目录结构与现有 config 的角色输出根保持一致，不因精简子任务而取消哈希和证据边界。

```text
.news-editor-work/<日期>/<run_id>/
├─ run-manifest.json
├─ shared/                 # 根 Agent 写：brief、锁定事实输入、全局约束
├─ tasks/                  # 根 Agent 写：各角色任务包
├─ handoffs/               # 各角色只写自己的交接包
├─ lanes/
│  ├─ facts/               # facts 独占
│  ├─ copy-pacing/         # copy_pacing 独占
│  ├─ video/               # video_scout 独占
│  └─ cover/               # cover_scout 独占
├─ timeline/               # 根或已派发的 timeline_editor 独占
├─ audio/                  # 根或已派发的 audio 独占
├─ render/                 # 根或已派发的 render 独占
└─ qa/                     # qa 独占，只读其他目录
```

同角色重试使用新的 `child_id` 和新文件名，不覆盖上次证据。根 Agent 合并结论时只新增整合文件，不直接修改角色原始证据。

## 7. 提速与失败恢复

- 事实锁定是首个屏障，不能为了并行提前猜测标题。锁定后先复核素材与独立封面可行性，再由根完成连续制作；可独立的 scout 按实际资源并行。素材已齐或仅串行可用时不机械派发全套角色。
- 一个主题只生成一次接触表、一次目标裁切预览和一次最终分板；相同输入哈希命中时复用证据。输入改变才重算受影响节点。
- 视频登录、搜索和下载保持在一个 `video_scout` 会话；封面搜索使用独立普通网页会话。不要让多个子智能体同时控制同一个浏览器。
- 子智能体最多在自己节点内重试两次。相同故障连续两次，写 `BLOCKED` 交接；根 Agent 从最近可靠屏障重派，不从 Phase 0 重跑。
- 故障交接区分事实/来源、素材/封面、声音/渲染、访问/工具及版本错误，写清有效工件、输入哈希、已尝试步骤和恢复条件。根记录当前工作、有效工具等待、等待用户及最近有效进展，批次间隔或暂时没有新文件不自动视为挂死；不得靠新子任务无限清空重试计数。
- 任何角色发现事实、标题、素材身份或哈希不一致，立即停止下游写入。需要改变用户硬约束、来源范围、标题事实或最终版式时，必须回到根 Agent 决策，不能由子智能体自行扩权。

## 8. 最小使用选择

- 单条已有成片的简单音量或文字修正：根 Agent 单独执行，避免编排开销。
- 单条完整新闻：根完成事实与连续制作，仅为独立侦察工作启用必要 scout，最终交给独立只读 QA；每个实际派发的任务均有版本证明、任务包与交接包。
- 多主题批量：每个主题独立 `topic_id`，默认仅 1–2 条在制，下一条开工前刷新候选与台账；同一主题仍遵守阶段屏障。根按可用并发槽派发独立工作，绝不让多个角色共同写一个 `timeline.json`，也不为凑 N 条复用无增量事件。
