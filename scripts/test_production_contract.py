import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from production_contract import allocate_pages,load_timeline,sha256,text_hash
from mix_timeline_audio import quality_ok
from validate_news_video import timestamp_checks,validate_timeline_schema,main as validate_main
from render_locked_news import draw_text,geometry,still_motion_filter
from PIL import Image
import minimax_tts


class ProductionRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.wav=self.root/'voice.wav'
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,2,44100,44100,'NONE','not compressed'))
            f.writeframes(b'\x10\x00'*44100)
        self.still=self.root/'still.png'
        Image.new('RGB',(1200,1200),'navy').save(self.still)
        narration='暂无法保障亲子相邻。'
        manifest={'status':'TTS_READY','page_id':'p1','request':{'text':narration,'text_sha256':text_hash(narration)},
                  'audio':{'path':'voice.wav','sha256':sha256(self.wav),'duration_seconds':1}}
        (self.root/'voice.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.plan={'schema_version':1,'fps':30,'total_frames':60,'cover_frames':1,'pages':[
            {'id':'p1','start_frame':1,'end_frame':60,'white_lines':['暂无法保障'],'red_emphasis':'亲子相邻',
             'timing':{'information_task':'说明相邻座位的保障限制','reading_hold_seconds':1.8,
                       'reading_basis':'测试夹具，不是人工验收','cut_reason':'完整限制表达后切页'},
             'narration':narration,'voice':{'path':'voice.wav','manifest':'voice.json','start_frame':6,
                                         'duration_seconds':1,'text_sha256':text_hash(narration),'audio_sha256':sha256(self.wav)}}]}

    def tearDown(self):self.tmp.cleanup()

    def load(self,plan=None):
        path=self.root/'timeline.json'
        path.write_text(json.dumps(plan or self.plan),encoding='utf-8')
        return load_timeline(path)

    def test_valid_measured_binding(self):self.assertEqual(self.load()['total_frames'],60)

    def test_missing_red_is_rejected(self):
        self.plan['pages'][0]['red_emphasis']=''
        with self.assertRaises(ValueError):self.load()

    def test_negation_cannot_be_ignored(self):
        self.plan['pages'][0]['white_lines']=['可保障']
        with self.assertRaises(ValueError):self.load()

    def test_edited_narration_needs_new_audio(self):
        p=self.plan['pages'][0];p['narration']='暂无法保障其他座位。'
        p.update(narration_override_reason='测试',claim_ids=['c1'],narration_reviewed=True)
        p['voice']['text_sha256']=text_hash(p['narration'])
        with self.assertRaises(ValueError):self.load()

    def test_swapped_page_is_rejected(self):
        self.plan['pages'][0]['id']='p2'
        with self.assertRaises(ValueError):self.load()

    def test_changed_wav_is_rejected(self):
        with self.wav.open('ab') as f:f.write(b'changed')
        with self.assertRaises(ValueError):self.load()

    def test_voice_overflow_is_rejected(self):
        self.plan['pages'][0]['voice']['start_frame']=30
        with self.assertRaises(ValueError):self.load()

    def test_page_gap_is_rejected(self):
        self.plan['pages'][0]['start_frame']=2
        with self.assertRaises(ValueError):self.load()

    def test_false_duration_is_rejected(self):
        self.plan['pages'][0]['voice']['duration_seconds']=.5
        with self.assertRaises(ValueError):self.load()

    def test_allocation_uses_measured_voice(self):
        p=allocate_pages([5.7,6.6873469387755105],30,420)
        self.assertEqual([(i['start_frame'],i['end_frame']) for i in p],[(1,196),(196,420)])

    def test_long_voice_cannot_be_truncated(self):
        with self.assertRaises(ValueError):allocate_pages([8,8],30,420)

    def test_editorial_read_time_can_exceed_voice_time(self):
        pages=allocate_pages([2,2],30,420,minimum_hold_seconds=[8,4])
        self.assertGreaterEqual(pages[0]['end_frame']-pages[0]['start_frame'],240)

    def test_missing_timing_plan_is_rejected(self):
        del self.plan['pages'][0]['timing']
        with self.assertRaisesRegex(ValueError,'editorial timing'):self.load()

    def test_reading_hold_longer_than_page_is_rejected(self):
        self.plan['pages'][0]['timing']['reading_hold_seconds']=2
        with self.assertRaisesRegex(ValueError,'shorter than'):self.load()

    def test_reading_hold_must_be_positive_finite_number(self):
        for bad in (True,0,-1,float('nan'),float('inf'),'1.8'):
            with self.subTest(value=bad):
                self.plan['pages'][0]['timing']['reading_hold_seconds']=bad
                with self.assertRaisesRegex(ValueError,'invalid reading'):self.load()

    def test_editorial_task_basis_and_cut_reason_are_required(self):
        for key in ('information_task','reading_basis','cut_reason'):
            plan=copy.deepcopy(self.plan)
            plan['pages'][0]['timing'][key]=' '
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'missing timing'):self.load(plan)

    def test_reading_floor_uses_ceiling_frames(self):
        self.plan['pages'][0]['timing']['reading_hold_seconds']=59/30
        self.assertEqual(self.load()['total_frames'],60)
        self.plan['pages'][0]['timing']['reading_hold_seconds']=59/30+.001
        with self.assertRaisesRegex(ValueError,'shorter than'):self.load()

    def test_reading_budget_cannot_be_silently_compressed(self):
        with self.assertRaisesRegex(ValueError,'Voice/reading need'):
            allocate_pages([2,2],30,420,minimum_hold_seconds=[8,7])

    def test_same_voice_lengths_can_have_different_editorial_holds(self):
        pages=allocate_pages([2,2],30,420,minimum_hold_seconds=[4,8])
        lengths=[p['end_frame']-p['start_frame'] for p in pages]
        self.assertGreater(lengths[1],lengths[0])
        self.assertEqual(sum(lengths),419)

    def test_invalid_allocation_frame_budget_is_rejected(self):
        for fps,total in ((0,420),(30,1),(29.97,420),(30,420.5)):
            with self.subTest(fps=fps,total=total),self.assertRaises(ValueError):
                allocate_pages([2,2],fps,total)

    def test_compact_duration_profile_accepts_only_seven_to_nine_seconds(self):
        plan=copy.deepcopy(self.plan);plan['duration_profile']='compact'
        for frames,valid in ((210,True),(270,True),(207,False),(273,False)):
            plan['total_frames']=frames;plan['pages'][0]['end_frame']=frames
            with self.subTest(frames=frames):
                if valid:self.load(plan)
                else:
                    with self.assertRaisesRegex(ValueError,'Compact profile'):self.load(plan)

    def test_standard_duration_profile_is_fourteen_seconds(self):
        plan=copy.deepcopy(self.plan);plan['duration_profile']='standard';plan['total_frames']=420;plan['pages'][0]['end_frame']=420
        self.assertEqual(self.load(plan)['duration_profile'],'standard')
        plan['total_frames']=419;plan['pages'][0]['end_frame']=419
        with self.assertRaisesRegex(ValueError,'Standard profile'):self.load(plan)

    def test_still_motion_filter_has_exact_frame_count_and_size(self):
        motion=self.valid_still_clip()['motion']
        value=still_motion_filter(motion,60,30,1080,1024)
        self.assertIn('d=60:s=1080x1024:fps=30',value)
        self.assertIn('on/59',value)

    def test_restarting_same_clip_at_page_cut_is_rejected(self):
        self.plan['clips']=[{'media_type':'video','path':str(self.wav),'source_in_seconds':0,'start_frame':1,'end_frame':30,'page_id':'p1','supports_claim':'测试'},
                            {'media_type':'video','path':str(self.wav),'source_in_seconds':0,'start_frame':30,'end_frame':60,'page_id':'p1','supports_claim':'测试'}]
        path=self.root/'timeline.json';path.write_text(json.dumps(self.plan),encoding='utf-8')
        with self.assertRaises(ValueError):load_timeline(path,verify_assets=False)

    def cover(self):
        return {'source_kind':'image','derived_from_video':False,'path':'still.png','source_sha256':sha256(self.still),
                'acquisition_method':'user_provided','rights_basis':'用户提供用于本项目','selection_reason':'主体清晰并支持主题',
                'clean_image_reviewed':True,'crop':[0,0,1200,1200],'headline':'测试新闻','subline':'测试副标题'}

    def valid_still_clip(self,start=43):
        return {'media_type':'image','path':'still.png','source_sha256':sha256(self.still),'start_frame':start,'end_frame':60,
                'page_id':'p1','supports_claim':'用于说明新闻相关地点','acquisition_method':'user_provided',
                'rights_basis':'用户提供用于本项目','crop':[0,0,1200,1200],
                'motion':{'start_zoom':1.0,'end_zoom':1.05,'start_anchor':[.48,.5],'end_anchor':[.52,.5],'easing':'linear'}}

    def video_clip(self,end=43):
        return {'media_type':'video','path':str(self.wav),'source_sha256':sha256(self.wav),'source_in_seconds':0,'start_frame':1,'end_frame':end,
                'page_id':'p1','supports_claim':'用于测试视频主体','speed':1}

    def test_cover_must_be_separate_image_not_video_frame(self):
        self.plan['cover']=self.cover();self.assertEqual(self.load()['cover']['source_kind'],'image')
        for field,value in (('source_kind','video'),('derived_from_video',True)):
            plan=copy.deepcopy(self.plan);plan['cover'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'Cover'):
                self.load(plan)

    def test_small_sourced_still_with_motion_is_allowed(self):
        self.plan['clips']=[self.video_clip(),self.valid_still_clip()]
        self.assertEqual(len(self.load()['clips']),2)

    def test_still_cannot_replace_all_video(self):
        clip=self.valid_still_clip(start=1)
        self.plan['clips']=[clip]
        with self.assertRaisesRegex(ValueError,'not replace all video'):self.load()

    def test_still_ratio_is_limited(self):
        self.plan['clips']=[self.video_clip(end=42),self.valid_still_clip(start=42)]
        with self.assertRaisesRegex(ValueError,'exceed'):self.load()

    def test_still_requires_visible_keyframe_motion(self):
        clip=self.valid_still_clip();clip['motion']['end_zoom']=1.01
        self.plan['clips']=[self.video_clip(),clip]
        with self.assertRaisesRegex(ValueError,'zoom'):self.load()

    def test_screenshot_requires_reviewed_claim_mapping(self):
        clip=self.valid_still_clip();clip['media_type']='screenshot'
        self.plan['clips']=[self.video_clip(),clip]
        with self.assertRaisesRegex(ValueError,'screenshot text'):self.load()
        clip.update(intentional_text_reviewed=True,claim_ids=['claim-1'])
        self.assertEqual(self.load()['clips'][1]['media_type'],'screenshot')

    def test_pts_regular_passes(self):
        checks=timestamp_checks([{'pts_time':str(i/30)} for i in range(420)],30,'1/15360')
        self.assertTrue(all(x['passed'] for x in checks))

    def test_same_frame_count_timestamp_jump_fails(self):
        frames=[{'pts_time':str((i+(1 if i>=100 else 0))/30)} for i in range(420)]
        self.assertFalse(all(x['passed'] for x in timestamp_checks(frames,30,'1/15360')))

    def test_missing_pts_fails(self):
        self.assertFalse(all(x['passed'] for x in timestamp_checks([{}],30,None)))

    def test_no_expectations_cannot_pass(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as caught:
            validate_main(['not-needed.mp4'])
        self.assertEqual(caught.exception.code,2)

    def test_high_true_peak_cannot_pass(self):
        cfg={'integrated_lufs_range':[-17,-15],'max_true_peak_dbtp':-3,'voice_bgm_delta_db':12,'delta_tolerance_db':1}
        self.assertTrue(quality_ok(-16,-4,[12,12],cfg))
        self.assertFalse(quality_ok(-16,-1,[12,12],cfg))
        self.assertFalse(quality_ok(-16,float('nan'),[12,12],cfg))

    @unittest.skipUnless(Path('C:/Windows/Fonts/msyhbd.ttc').is_file(),'Windows reference font unavailable')
    def test_long_cover_text_is_rejected_not_shrunk(self):
        with self.assertRaises(ValueError):
            draw_text(Image.new('RGBA',(1080,1920)),'过长的封面标题一定不能强行缩小',Path('C:/Windows/Fonts/msyhbd.ttc'),144,[80,1100,920,180],'white')

    def test_equal_scale_geometry(self):
        sw,sh=geometry([0,0,720,960],(1080,1440))
        self.assertEqual((sw,sh),(1080,1440))

    def test_tts_cache_binds_text_settings_and_audio(self):
        wav_hex=self.wav.read_bytes().hex()
        response=({'base_resp':{'status_code':0},'data':{'status':2,'audio':wav_hex}},'unit-test')
        output=self.root/'synth.wav'
        argv=['tts','--text','回归文案。','--page-id','p1','--output',str(output)]
        with patch.dict(os.environ,{'MINIMAX_API_KEY':'unit-test-not-a-key','MINIMAX_API_BASE_URL':'https://api.minimax.cn'}), \
             patch.object(minimax_tts,'post_json',return_value=response) as api, \
             patch('sys.argv',argv),contextlib.redirect_stdout(io.StringIO()):
            minimax_tts.main();minimax_tts.main()
            self.assertEqual(api.call_count,1)
            self.assertEqual(api.call_args.args[2]['voice_setting']['emotion'],minimax_tts.VOICE_CONFIG['emotion'])
            argv[2]='改过的文案。'
            minimax_tts.main()
            self.assertEqual(api.call_count,2)
            with output.open('ab') as stream:stream.write(b'corrupt')
            minimax_tts.main()
            self.assertEqual(api.call_count,3)


@unittest.skipUnless(os.name=='nt' and shutil.which('pwsh'),'Windows PowerShell 7 required')
class PublicationPacingGate(unittest.TestCase):
    """Synthetic files exercise only the publication gate, always with -WhatIf."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.video=self.root/'fixture.mp4';self.video.write_bytes(b'not-a-video-unit-fixture')
        self.cover=self.root/'fixture.png';self.cover.write_bytes(b'not-an-image-unit-fixture')
        machine={'passed':True,'diagnostic_only':False,'validation_scope':'production_machine_checks',
                 'sha256':sha256(self.video)}
        self.qa=self.root/'qa.json';self.qa.write_text(json.dumps(machine),encoding='utf-8')
        self.acceptance={'status':'FINAL_READY','diagnostic':False,'cover_title':'测试新闻',
                         'video_sha256':sha256(self.video),'cover_sha256':sha256(self.cover),
                         'machine_report':'qa.json','machine_report_sha256':sha256(self.qa),
                         'review':{k:'passed' for k in ('visual','audio','facts','muted_reading','voiced_playback')}}

    def tearDown(self):self.tmp.cleanup()

    def invoke(self):
        report=self.root/'acceptance.json';report.write_text(json.dumps(self.acceptance),encoding='utf-8')
        command=['pwsh','-NoProfile','-File',str(Path(__file__).with_name('publish_news_output.ps1')),
                 '-OutputRoot',str(self.root/'delivery'),'-WorkRoot',str(self.root/'work'),
                 '-Date','2026-09-07','-Sequence','1','-CoverTitle','测试新闻',
                 '-FinalVideo',str(self.video),'-Cover',str(self.cover),'-AcceptanceReport',str(report),'-WhatIf']
        result=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=30)
        self.assertFalse((self.root/'delivery').exists(),'Dry-run gate must not create output directories')
        return result

    def test_both_pacing_reviews_are_required(self):
        for gate in ('muted_reading','voiced_playback'):
            self.acceptance['review'][gate]='not_checked'
            result=self.invoke()
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Missing completed review: '+gate,result.stderr)
            self.acceptance['review'][gate]='passed'

    def test_complete_fixture_passes_dry_run_only(self):
        result=self.invoke()
        self.assertEqual(result.returncode,0,result.stderr)

    def test_diagnostic_is_rejected_even_with_reviews(self):
        self.acceptance['diagnostic']=True
        self.assertNotEqual(self.invoke().returncode,0)

    def test_changed_video_invalidates_reviews(self):
        self.video.write_bytes(b'changed-unit-fixture')
        self.assertNotEqual(self.invoke().returncode,0)


if __name__=='__main__':unittest.main()
