# 编辑台账与效果反馈

用于连续生产、同事件跟进、纠错、发布恢复和后台明细复盘。先执行 SKILL 的版本/环境门；本参考不改变真实素材、封面、时长档、MiniMax、G0–G8 或发布授权要求。参数与观察窗口从 config `editorial_feedback` 读取，属于试运行配置，不是传播最优值。

## 1. 开工前做出编辑决定

候选先过来源与事实门，再判断受众关联、最新事实、用户成本/风险/行动价值和素材可行性。品牌、热榜名次与旧爆款仅提供检索线索。用户要求 N 条是合格交付上限；没有足够合格题时少交付并说明，不回捞排除题凑数。

从台账读取相同事件已表达的事实和在制状态，确认选题就事务占位，不能等发布成功才登记。事件ID描述具体事件、主体与发生范围，不用标题、品牌或每日序号代替。编辑者核同义标题、等价金额、同源转载、事故身份、统计截止及回应是否首次出现；程序仅兜底检查规范化相同文本，不能自动判断语义增量。

- 无新增事实的同事件改标题重包装：阻断，不新建；未发布草稿可按原content_id修订与恢复，PUBLISHED条目只关联，不恢复为在制或复制重发。
- 新回应、适用条件变化、新统计：记录具体变化、口径与核验来源，再继续。
- 纠错：关联原稿及具体错误，允许突破冷却；回查原稿是否需纠正，不以辟谣版已发表视为旧误导消除。平台修改/删除/发布按用户既有处置授权范围执行。
- 不同事故、去程/返程等：先核事件身份与场景，不以题材相同自动合并。
- 低浏览稿修改：已有草稿按原content_id修订与恢复，保存改善假设及版本；已发布稿不得仅凭“换标题/改善呈现/试验假设”复制重发，公开再报仍需新增事实或必要纠错。另行用户授权的实验须先明确试验规则与范围，不以偶然晚版更高作为机械复制理由。

素材/封面不可行时尽早阻塞或切换合格候选，保留可靠事实与工件。完成一条后再刷新下一条；在制上限从 config 读取，等待用户验收与发布授权不算仍在制作。

## 2. 三个阶段的事实记录

每条工作目录维护 `editorial.json`（`news-editor-editorial/v1`），与台账使用同一 `content_id/event_id`。

| 字段 | 要求 |
|---|---|
| `content_id, event_id, kind, audience_value` | 稳定身份；kind 为 breaking/development/service/correction；说明受众具体价值 |
| `event_occurred_at, latest_material_at` | 事件发生与最新实质进展。支持真实 YYYY-MM-DD 精度或带时区ISO时间，不编造小时；事件时间未知填null并写 `event_time_basis` |
| `sources[]` | 每条有 id、publisher、independence_group、title、url、role、published_at、checked_at。role 为 primary/independent_reporting；未知发布时间填null并写 time_basis |
| `claims[]` | id、text、subject、conditions数组、numbers数组、source_ids；数量项有字符串 value/unit/scope。核心事实至少两个不同独立来源组，转载原公告不能算第二独立来源 |
| `review` | reviewer、reviewed_at、evidence及 source_eligibility/source_independence/dates/subject_numbers_conditions/semantic_increment。实际核查后写passed，未做写not_checked |
| 纠错字段 | kind=correction 时必需 corrects_content_id、corrected_claim；若未取得平台ID先用稳定内部作品ID关联 |

`checked_at/reviewed_at` 必须为实际核查时刻并带时区。条件/数量确实没有时显式空数组；不能通过留空跳过数字、地区、型号或否定词。source_eligibility 按既有国家级媒体排除与原始发布规则核查；已知存在原始公告/当事方发布时纳入，不只互引转载。

```text
python <SkillRoot>/scripts/editorial_contract.py check --stage facts --package <work/editorial.json>
```

事实通过后，在调用TTS之前补 `feasibility.video/cover={status,evidence}` 和 `media[]`。每条媒体有 path、sha256、role、claim_ids、source_id或source_url、captured_at、capture_time_status、time_basis、used_as_current_scene；role 为 direct_evidence/contextual_broll，capture_time_status 为 verified/unknown。

四类时间分别是事件发生、媒体拍摄、原始发布、最新实质进展。上传/转载时间不能代替拍摄时间。未知拍摄日期可保留用于背景，但不能宣称当前现场；used_as_current_scene=true 时必须是直接证据、拍摄时间已核且有 current_scene_basis 说明如何与事件时间匹配。旧背景画面与信息截图要明确用途，不能只靠“上传比事件晚”放行。

纠错须限定已证实的命题：同一视频在目标事件前已上传的证据经核验后，可纠正“今日现场”误导，并明示拍摄日期未知；不能据此断言某日拍摄。若纠错结论本身依赖尚未核实的拍摄日期或视频身份，则保持BLOCKED_SOURCE。与纠错结论无关的未知拍摄日期不自动阻塞，只能按背景用途使用。

```text
python <SkillRoot>/scripts/editorial_contract.py check --stage feasible --package <work/editorial.json>
```

这一步检查已取得的媒体哈希与探针记录，封面仍执行几何和四项实际看图门。探针失败不进入TTS/全片渲染。定稿后补 `presentation`：headline、subtitle、cover_headline、cover_subline、pages；每页包括 id/narration/white_lines/red_emphasis/claim_ids，与真实时间轴文字逐字对应。补 `review.copy_media_consistency=passed`，逐一对照事实、标题、口播、字幕、画面中的主体、数字单位、条件与因果边界。

```text
python <SkillRoot>/scripts/editorial_contract.py check --stage ready --package <work/editorial.json> --timeline <work/timeline.json> --video <work/final.mp4> --cover <work/封面.png> --report <work/editorial-report.json>
```

输出报告绑定事实包、时间轴、当前MP4和独立封面哈希。它只证明记录完备及文件身份，**不证明来源真实、实际看过/听过或已获用户授权**。事实/媒体/文案变化使旧报告失效；重新核受影响项并重建报告。独立QA和原有机器/视听门继续执行。

## 3. 跨日持久台账

生产项目与Skill源码分离。跨日DB放生产项目 `.news-editor-work` 根目录，不放日期/单次run目录，也不放技能仓库或最终输出区；每次运行共享同一DB。脚本使用标准库SQLite与事务占位，可在Windows或macOS运行。

统一命令前缀：

```text
python <SkillRoot>/scripts/editorial_ledger.py --workspace <项目根/.news-editor-work> --db <项目根/.news-editor-work/editorial-ledger.sqlite> <command>
```

`claim --item <claim.json>` 输入 content_id/run_id/event_id、claims[{id,text,kind,source}]、latest_material_at。claim.kind 为 fact/new/correction；同事件续报还需 editorial_review 记录语义增量/纠错判断。事实ID不能换含义，新ID同文本仍会被拦；已占位包括BLOCKED。新时间本身不等于新事实。

| 命令 | 状态与证据 |
|---|---|
| `claim` | IN_PROGRESS；按config在同事务检查制作容量 |
| `draft --content-id C --final <MP4>` | DRAFT；绑定当前成片，清除旧QA/用户验收/授权 |
| `qa-start --content-id C` | QA；独立只读审查 |
| `qa --content-id C --evidence <qa-ledger.json>` | WAITING_ACCEPTANCE；记录 result=PASS、reviewer、final_sha256 和证据文件哈希 |
| `accept --content-id C --record <user-acceptance.json>` | PENDING_AUTHORIZATION；必须来自真实用户验收 |
| `authorize --content-id C --record <publish-authorization.json>` | PENDING_PUBLISH；必须来自明确发布授权，制作/代选授权不能替代 |
| `prepare-publish --content-id C` | PUBLISHING；重核当前成片及QA、验收、授权同一哈希；这只是登记上传尝试，不会上传 |
| `publish --content-id C --platform-content-id P --published-at <实际ISO时间>` | PUBLISHED；平台已发布列表核验后记录，平台ID不可复用 |
| `block --content-id C --reason <具体原因>` | BLOCKED；保留占位与最近可靠状态 |
| `resume --content-id C --run-id <新run_id> --reason <恢复条件>` | 新版本门之后恢复同content_id，不重新claim；工件变化使旧审核失效 |
| `progress --content-id C --node <完成节点> [--artifact <工件>]` | 只有新节点/新工件才推进last_progress_at，重复心跳不能冒充进展 |
| `show [--content-id C]` | 只读状态与审计 |
| `reconcile --platform-json <平台盘点.json>` | 输入平台观测数组；按platform_content_id/content_id/final_sha256关联，显式身份、哈希或可比时间冲突输出conflicts；禁止自动补发或按标题强配 |

用户记录的公共字段是 actor、evidence（真实指令引用/路径）、at（实际带时区时间）、final_sha256，并分别有 accepted=true / authorized=true。QA摘要引用本次独立视听/事实验收；摘要本身不能替代机器报告和实际视听。脚本检查所供记录，不能认证用户身份或证明Agent真正执行过QA，不得自行伪造这些记录。

`production_started_at/production_completed_at` 是制作状态时刻，不等于平台时间；用户验收、发布授权分别保留自己的at。只到日的来源时间保留date精度。不要用文件mtime补造published_at；平台仅显示分钟时保留published_at_original和published_at_precision，不将归一化补出的:00当真实秒观测。

## 4. 等待、故障与上传不确定性

等待用户、授权、正常异步上传或平台审核属于合法等待。失去进展应结合最近完成节点、实际工件、工具/进程状态判断；last_progress_at是辅助证据，不是自动杀任务的阈值。现有TTS/媒体进程超时继续使用，检索按失败原因限制尝试，不新增统一75分钟强杀。

来源不足、视觉不可行、网络、登录/权限、资源和脚本错误分别记原因及恢复条件；保存可复用的事实/TTS/素材，只重跑受变化影响的节点。本次仅实现持久状态和进展记录，**没有常驻外部看门狗、自动重启或全节点缓存**；需监督时在被监督会话之外读取状态并结合工具事实，不以根会话自报心跳宣称可靠监督。

处于PUBLISHING或中途打断时，先盘点平台已发布、审核中、未通过、待优化和草稿，并记录实际作品身份；列表短暂空白不等于丢失。`reconcile`只读。只有确认缺失、原授权文件未变且授权范围仍有效，才执行 `retry-publish --content-id C --evidence <确认缺失.json>`，再重新prepare-publish。缺失记录含 confirmed_absent=true、reviewer、evidence、checked_at、final_sha256；未知结果不得写确认缺失。若先BLOCKED，须新run版本门及resume再处置。

已确认发布不能整批重传；不确定结果下禁止替换原授权工件。网页不可可靠盘点或登录无法恢复时保持阻塞并说明具体条件，不能无证据认定补发安全。

## 5. 效果数据：先关联身份，再比较等龄快照

后台导出只读保存在工作区，运行：

```text
python <SkillRoot>/scripts/performance_review.py --csv <后台导出.csv> --output <work/performance-review.json>
```

只有知道实际观察时刻才加 `--observed-at <带时区ISO时间>`；CSV取得时间不自动等于后台统计截止。没有就保留unknown。工具支持UTF-8 BOM和GB18030，逐条保留物理行号，标题内换行也可追溯。

当前工具要求平台完整16列导出（以脚本必需字段校验为准）。只有日期、观看时长、累计浏览等少量字段时，应重新导出完整明细，或人工报告这些字段支持的记录数、浏览总量、中位数和集中度；不能补零、伪造缺失列以通过工具。

关联文件用 `news-editor-performance-mapping/v1`：必填原CSV字节的csv_sha256、records数组；每条指定 csv_line_start，可有csv_line_end、platform_content_id、production_content_id（台账content_id）、真实published_at、video_duration_seconds。哈希与行范围核对后才关联，不按相似标题强配；同为60条不证明是同一批工件。

```text
python <SkillRoot>/scripts/performance_review.py --csv <后台导出.csv> --mapping <work/mapping.json> --observed-at <实际快照ISO时间> --output <work/performance-review.json>
```

按config观察窗采集真实快照并保留原件；仅一份累计导出不能拆出24/72小时表现。已知发布与观察时刻只计算实际年龄，仍标记为累计快照。没有真实小时不评价最佳发布时间，不把发布日期分组的累计浏览当账号当日新增流量。

- 浏览总量、中位数、头部集中度与收藏/分享/关注同时看，不用条均掩盖长尾，也不把点击次数/浏览比叫独立用户转化率。
- 人均浏览时长不是视频总长；平均观看占比只有实际片长已知才计算。条均留存、浏览加权描述值与平台播放分母汇总分开。
- 用原点赞/评论/收藏/分享次数重算每千浏览互动；舍入为0.0的互动指数不代表没有互动。
- 完播高于5秒先核总片长、分母与窗口，不自动改原数；点击全零先查挂载与埋点，不断言商业无效。
- 后台周看板与新稿明细的浏览/吸粉范围未核平时分别列示，不把差额自动归为历史长尾。
- 缺版本、人工改稿、活跃耗时和对照条件时，不把流量变化归因于skill或代理架构；更长、固定小时、品牌配额和BGM数量都保留为待验证假设。

下一轮先回看同题材的高前段指标/低完播样片，核口径与声画、翻页和信息兑现，再小范围改变一个变量。复杂事件装不下可按既有延长授权流程处理；不在本轮新增长稿日配额或削弱验收。新闻、服务图文与商品承接分别定义目标，不因新闻点击零就裁撤其他流程。

## 6. 从旧项目迁移

新制作从选题占位开始使用台账与editorial.json。旧成片先逐项回填事实和真实媒体时间、重跑受影响审核，生成绑定当前文件的editorial-report；禁止补造已验收或已发布状态。旧生产报告与后台导出用工件指纹/作品ID人工核对，未匹配项保留。

`acceptance.json` 新增content_id及editorial_report_sha256，本地输出命令必传-EditorialReport；旧验收不能静默放行。用户验收/授权记录仍按真实已有指令范围保存，不能从已交付或文件名推断授权。
