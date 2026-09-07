#!/usr/bin/env python3
"""Mux verified render and mix without another video encode; bind the final MP4 hash."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from production_contract import load_timeline, read_json, sha256, require


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline',required=True,type=Path)
    parser.add_argument('--render-report',required=True,type=Path)
    parser.add_argument('--mix-report',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--ffmpeg',default='ffmpeg')
    parser.add_argument('--diagnostic',action='store_true')
    args=parser.parse_args()
    load_timeline(args.timeline)
    config_path=Path(__file__).resolve().parents[1]/'config.json'
    config=read_json(config_path)
    lock_path=config_path.parent/config['layout']['active_lock_file']
    render,mix=read_json(args.render_report),read_json(args.mix_report)
    timeline_sha=sha256(args.timeline)
    require(render['timeline_sha256']==mix['timeline_sha256']==timeline_sha,'Stale render/mix timeline')
    require(render['lock_sha256']==sha256(lock_path),'Layout changed since render')
    require(mix['config_sha256']==sha256(config_path),'Mix configuration changed')
    require(mix['status']=='PASS','Mix quality not passed')
    diagnostic=bool(render.get('diagnostic')) or render['status']=='BLOCKED_VISUAL'
    require(not diagnostic or args.diagnostic,'Rejected/internal render cannot be assembled as production')
    video,audio=Path(render['video']),Path(mix['output'])
    require(sha256(video)==render['video_sha256'] and sha256(audio)==mix['output_sha256'],'Render/mix file changed')
    require(args.output.resolve() not in {video.resolve(),audio.resolve(),args.timeline.resolve()},'Never overwrite inputs')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fmt=config['mix']['final_format']
    started=time.perf_counter()
    subprocess.run([args.ffmpeg,'-v','error','-y','-i',str(video),'-i',str(audio),'-map','0:v:0','-map','1:a:0',
                    '-c:v','copy','-c:a',fmt['codec'],'-b:a',fmt['bitrate'],'-ar',str(fmt['sample_rate_hz']),
                    '-ac',str(fmt['channels']),'-movflags','+faststart',str(args.output)],check=True,timeout=120)
    report={'schema_version':1,'status':'ASSEMBLED_INTERNAL' if diagnostic else 'ASSEMBLED_NEEDS_QA',
            'diagnostic':diagnostic,'final_ready':False,'timeline_sha256':timeline_sha,
            'render_report':str(args.render_report.resolve()),'render_report_sha256':sha256(args.render_report),
            'mix_report':str(args.mix_report.resolve()),'mix_report_sha256':sha256(args.mix_report),
            'video_input_sha256':sha256(video),'audio_input_sha256':sha256(audio),
            'output':str(args.output.resolve()),'output_sha256':sha256(args.output),'elapsed_seconds':time.perf_counter()-started}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
