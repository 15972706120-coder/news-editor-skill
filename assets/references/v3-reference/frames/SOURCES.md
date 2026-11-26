# 参考图所用视频帧的来源与许可

`scripts/build_layout_reference.py` 用这些帧渲染版式参考图。每一帧都取自维基共享资源（Wikimedia Commons）上开放授权的视频，缩放到 2560×1440 后以 JPEG 保存；仓库里不保存视频文件。

| 文件 | 取自 | 时间点 | 作者 | 许可 |
|---|---|---|---|---|
| anshan-west-t05.jpg | [Bullet train arriving in Anshan West (Anshanxi) station](https://commons.wikimedia.org/wiki/File:Bullet_train_arriving_in_Anshan_West_(Anshanxi)_station.webm) | 00:05 | Enming Yan / 闫恩铭 | CC BY-SA 4.0 |
| anshan-west-t06.jpg | 同上 | 00:06 | 同上 | 同上 |
| anshan-west-t13.jpg | 同上 | 00:13 | 同上 | 同上 |
| anshan-west-t32.jpg | 同上 | 00:32 | 同上 | 同上 |
| anshan-west-t40.jpg | 同上 | 00:40 | 同上 | 同上 |
| tongguanyi-t06.jpg | [202308 Train 5612 arriving at Tongguanyi Station, Chongqing](https://commons.wikimedia.org/wiki/File:202308_Train_5612_arriving_at_Tongguanyi_Station,_Chongqing.webm) | 00:06 | Jonashtand | CC BY-SA 4.0 |

许可文本见 <https://creativecommons.org/licenses/by-sa/4.0/>。帧里出现的人都是车站这一公开场合里的旅客背影或侧影。

由这些帧派生的六张参考图（`reference-contact-sheet.png`、`caption-slide-in-strip.png`、`typography-detail.png`、`cover-style-reference.png`、`cover-grid-good-bad-reference.jpg`、`platform-overlay-reference.webp`）同样按 CC BY-SA 4.0 分发。参考图里的文案是虚构的示例，不是新闻事实。

更换或新增帧时：从开放授权的视频里取清晰、无字幕、无水印的画面，在上表登记来源、作者、时间点和许可，然后在 Windows 上重跑脚本并提交重建的参考图。
