from __future__ import annotations
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from production_contract import (load_timeline, sha256, text_hash, body_geometry, plan_reading_frames,
                                 plan_voice_frames, fullchar_units, wav_info, lock, config)
from mix_timeline_audio import prominent_units
from validate_news_video import timestamp_checks, validate_timeline_schema, automatic_qa_frames, main as validate_main
from render_news_brief import geometry, still_motion_filter, draw_text, text_layer, shear, fitted, slide_frames, ease_out
from PIL import Image, ImageFont
import minimax_tts


def brief_plan(root: Path) -> dict:
    """Identity-only fixtures: no file here is real news media or a real editorial review."""
    video, cover = root/'src.mp4', root/'cover.png'
    video.write_bytes(b'identity-only fixture, no media QA')
    Image.new('RGB', (1200, 1600), 'navy').save(cover)
    timing = {'information_task': '说明事件与结果', 'reading_basis': '测试夹具，不是人工验收',
              'cut_reason': '下一个信息点'}
    return {'schema_version': 2, 'format': 'brief_video', 'fps': 30, 'total_frames': 286, 'cover_frames': 1,
            'audio_mode': 'bgm_only',
            'headline_lines': [{'text': '情侣同居生子未领证', 'style': 'yellow_tag'},
                               {'text': '分手后男方起诉索要100万元', 'style': 'black_tag'},
                               {'text': '还辩称两人只是同事', 'style': 'yellow_italic'}],
            'result_line': '法院：孩子由女方抚养 车归男方 房子各一半',
            'disclaimer_label': lock()['body']['disclaimer']['text'],
            'source_label': '测试媒体',
            'caption_blocks': [
                {'id': 'b1', 'start_frame': 1, 'end_frame': 97, 'lines': ['湖南一对情侣办酒席同居生子', '始终未领证 分手后男方起诉女方'],
                 'timing': dict(timing, reading_hold_seconds=3.0)},
                {'id': 'b2', 'start_frame': 97, 'end_frame': 189, 'lines': ['衡阳中院二审维持原判', '已满8岁的孩子自愿随母亲生活'],
                 'timing': dict(timing, reading_hold_seconds=3.0)},
                {'id': 'b3', 'start_frame': 189, 'end_frame': 286, 'lines': ['判定吉普车归男方 房产双方各占一半', '38万余元转账、彩礼不予返还'],
                 'timing': dict(timing, reading_hold_seconds=3.0)}],
            'clips': [
                {'media_type': 'video', 'path': 'src.mp4', 'source_sha256': sha256(video), 'source_in_seconds': 0,
                 'start_frame': 1, 'end_frame': 141, 'crop': [0, 0, 720, 1280], 'supports_claim': '说明当事人关系',
                 'footage_role': 'contextual_broll', 'keeps_source_subtitles': False, 'use_original_sound': False},
                {'media_type': 'video', 'path': 'src.mp4', 'source_sha256': sha256(video), 'source_in_seconds': 5,
                 'start_frame': 141, 'end_frame': 286, 'crop': [0, 0, 720, 1280], 'supports_claim': '说明判决结果',
                 'footage_role': 'contextual_broll', 'keeps_source_subtitles': False, 'use_original_sound': False}],
            'cover': {'source_kind': 'image', 'derived_from_video': False, 'path': 'cover.png', 'source_sha256': sha256(cover),
                      'crop': [0, 0, 1200, 1600], 'headline': '未婚情侣同居生子', 'subline': '分手后男方起诉100万',
                      'acquisition_method': 'user_provided', 'rights_basis': '测试夹具', 'selection_reason': '测试夹具',
                      'clean_image_reviewed': True}}


def write_wav(path: Path, seconds: float = 2.5) -> None:
    with wave.open(str(path), 'wb') as handle:
        handle.setparams((1, 2, 44100, 44100, 'NONE', 'not compressed'))
        handle.writeframes(b'\x10\x00'*round(44100*seconds))


class BriefContractRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.plan = brief_plan(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, plan=None):
        path = self.root/'timeline.json'
        path.write_text(json.dumps(plan or self.plan, ensure_ascii=False), encoding='utf-8')
        return load_timeline(path)

    def reject(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.load()
        self.plan = brief_plan(self.root)  # every later case starts from a valid plan

    def test_valid_brief_passes(self):
        self.assertEqual(self.load()['total_frames'], 286)

    def test_legacy_page_schema_is_rejected(self):
        self.plan['schema_version'] = 1
        self.reject('schema')

    def test_fps_must_match_the_active_layout(self):
        self.plan['fps'] = 25
        self.reject('FPS')

    def test_duration_floor_and_extension_authorization(self):
        for total, pattern in ((260, 'at least 9 seconds'), (400, 'exceeds 13 seconds'), (452, 'hard duration ceiling')):
            self.plan['total_frames'] = self.plan['caption_blocks'][-1]['end_frame'] = total
            if total == 452:
                self.plan.update(duration_extension_authorized=True, duration_extension_reason='用户同意延长')
            self.reject(pattern)
        self.plan['total_frames'] = self.plan['caption_blocks'][-1]['end_frame'] = self.plan['clips'][-1]['end_frame'] = 400
        self.plan.update(duration_extension_authorized=True, duration_extension_reason='用户同意延长')
        self.assertEqual(self.load()['total_frames'], 400)

    def test_headline_block_rules(self):
        self.plan['headline_lines'] = self.plan['headline_lines'][:1]
        self.reject('2–3 headline lines')
        self.plan['headline_lines'] = self.plan['headline_lines'][:2]
        self.reject('yellow_italic')
        self.plan['headline_lines'][1]['style'] = 'underline'
        self.reject('unknown style')
        self.plan['headline_lines'][1]['text'] = '这是一个明显超过十七个全角字宽度上限的标题行文案'
        self.reject('full-width units')

    def test_source_outlet_cannot_enter_headline_or_result_line(self):
        self.plan['headline_lines'][0]['text'] = '测试媒体报道事件'
        self.reject('must not contain news source outlets')
        self.plan = brief_plan(self.root)
        self.plan['result_line'] = '测试媒体：孩子由女方抚养'
        self.reject('must not contain news source outlets')

    def test_caption_line_count_and_width(self):
        self.plan['caption_blocks'][0]['lines'] = ['一']*5
        self.reject('caption lines')
        self.plan['caption_blocks'][0]['lines'] = ['这是一行明显超过二十个全角字宽度上限的字幕文案内容']
        self.reject('caption line exceeds')

    def test_reading_hold_floor_and_visible_time(self):
        self.plan['caption_blocks'][0]['timing']['reading_hold_seconds'] = 1.0
        self.reject('pacing floor')
        self.plan['caption_blocks'][0]['timing']['reading_hold_seconds'] = 3.4
        self.reject('shorter than editorial reading hold')

    def test_blocks_must_be_contiguous_and_cover_the_timeline(self):
        self.plan['caption_blocks'][1]['start_frame'] = 98
        self.reject('gap, overlap')
        self.plan['caption_blocks'][-1]['end_frame'] = 280
        self.reject('do not cover')

    def test_bgm_only_rejects_voice(self):
        self.plan['caption_blocks'][0]['narration'] = '任何配音文本'
        self.reject('only in tts_bgm')

    def add_voices(self):
        write_wav(self.root/'voice.wav')
        for block in self.plan['caption_blocks']:
            narration = ''.join(block['lines'])
            manifest = {'status': 'TTS_READY', 'page_id': block['id'],
                        'request': {'text': narration, 'text_sha256': text_hash(narration)},
                        'audio': {'path': 'voice.wav', 'sha256': sha256(self.root/'voice.wav'), 'duration_seconds': 2.5}}
            (self.root/f'{block["id"]}.json').write_text(json.dumps(manifest), encoding='utf-8')
            block.update(narration=narration, voice={
                'path': 'voice.wav', 'manifest': f'{block["id"]}.json',
                'start_frame': block['start_frame']+(9 if block['id'] == 'b1' else 6), 'duration_seconds': 2.5,
                'text_sha256': text_hash(narration), 'audio_sha256': sha256(self.root/'voice.wav')})
        self.plan['audio_mode'] = 'tts_bgm'

    def test_tts_mode_passes_with_measured_binding(self):
        self.add_voices()
        self.assertEqual(self.load()['audio_mode'], 'tts_bgm')

    def test_tts_mode_requires_narration_and_matching_copy(self):
        self.add_voices()
        del self.plan['caption_blocks'][0]['narration']
        self.reject('needs narration')
        self.add_voices()
        self.plan['caption_blocks'][0]['narration'] = '被改写的口播内容'
        self.reject('mismatch')

    def test_tts_manifest_text_and_wav_identity(self):
        self.add_voices()
        self.plan['caption_blocks'][0]['voice']['text_sha256'] = 'stale'
        self.reject('stale narration hash')
        self.add_voices()
        self.plan['caption_blocks'][0]['voice']['audio_sha256'] = 'swapped'
        self.reject('WAV replaced')
        self.add_voices()
        self.plan['caption_blocks'][0]['voice']['duration_seconds'] = 2.4
        self.reject('measured from current WAV')

    def test_tts_voice_cannot_overflow_or_start_before_the_slide(self):
        self.add_voices()
        self.plan['caption_blocks'][0]['voice']['start_frame'] = 9
        self.reject('after the caption slide-in')
        self.add_voices()
        self.plan['caption_blocks'][0]['voice']['duration_seconds'] = 3.2
        self.reject('voice overflow')

    def test_original_sound_needs_quote_clips_in_original_bgm(self):
        self.plan['clips'][0]['use_original_sound'] = True
        self.reject('only for first-hand quote')
        self.plan['audio_mode'] = 'original_bgm'
        self.plan['clips'][0].update(footage_role='first_hand_quote', use_original_sound=True, quote_reviewed=True,
                                     keeps_source_subtitles=True, claim_ids=['c1'])
        self.assertEqual(self.load()['audio_mode'], 'original_bgm')
        self.plan['disclaimer_label'] = None
        self.plan['clips'][1].update(footage_role='first_hand_quote', claim_ids=['c1'])
        self.assertEqual(self.load()['disclaimer_label'], None)
        self.plan['audio_mode'] = 'bgm_only'
        self.reject('original_bgm')

    def test_source_subtitles_stay_only_on_reviewed_quotes(self):
        self.plan['clips'][0]['keeps_source_subtitles'] = True
        self.reject('first-hand quote')

    def test_disclaimer_follows_contextual_footage(self):
        self.plan['disclaimer_label'] = None
        self.reject('disclaimer_label')
        self.plan['disclaimer_label'] = '资料画面'
        self.reject('disclaimer_label')

    def test_clips_must_be_continuous_and_unique(self):
        self.plan['clips'][1]['start_frame'] = 142
        self.reject('continuous half-open')
        self.plan['clips'][1]['source_in_seconds'] = 0
        self.reject('Same source interval repeated')

    def test_unknown_role_and_missing_claim_support(self):
        self.plan['clips'][0]['footage_role'] = 'broll'
        self.reject('footage_role')
        self.plan['clips'][0]['supports_claim'] = ''
        self.reject('claim it supports')

    def add_still(self, media_type='image'):
        Image.new('RGB', (1200, 1200), 'gray').save(self.root/'still.png')
        self.plan['caption_blocks'][-1]['end_frame'] = self.plan['total_frames'] = 346
        clip = {'media_type': media_type, 'path': 'still.png', 'source_sha256': sha256(self.root/'still.png'),
                'start_frame': 286, 'end_frame': 346, 'crop': [0, 0, 1200, 1200], 'supports_claim': '说明政策原文',
                'footage_role': 'direct_evidence', 'keeps_source_subtitles': False, 'use_original_sound': False,
                'acquisition_method': 'user_provided', 'rights_basis': '测试夹具',
                'motion': {'easing': 'linear', 'start_zoom': 1.0, 'end_zoom': 1.06,
                           'start_anchor': [0.5, 0.5], 'end_anchor': [0.55, 0.5]}}
        if media_type == 'screenshot':
            clip.update(intentional_text_reviewed=True, claim_ids=['c1'])
        self.plan['clips'].append(clip)

    def test_sourced_still_with_motion_is_allowed(self):
        self.add_still('screenshot')
        self.assertEqual(self.load()['clips'][-1]['media_type'], 'screenshot')

    def test_screenshot_requires_reviewed_claim_mapping(self):
        self.add_still('screenshot')
        del self.plan['clips'][-1]['claim_ids']
        self.reject('explicit review')

    def test_still_needs_visible_keyframe_motion(self):
        self.add_still()
        self.plan['clips'][-1]['motion']['end_zoom'] = 1.005
        self.reject('outside policy')

    def test_cover_rules(self):
        self.plan['cover']['source_kind'] = 'video_frame'
        self.reject('derived_from_video')
        self.plan['cover'].update(source_kind='video_frame', derived_from_video=True, source_in_seconds=1.5,
                                  acquisition_method='downloaded_video_frame', frame_sharpness_reviewed=True)
        self.assertEqual(self.load()['cover']['source_kind'], 'video_frame')
        self.plan['cover'].pop('frame_sharpness_reviewed')
        self.reject('clean-frame and sharpness')

    def test_long_cover_copy_is_rejected_not_shrunk(self):
        self.plan['cover']['headline'] = '超过八个全角字的封面主标题'
        self.reject('cover headline exceeds')
        self.plan = brief_plan(self.root)
        self.plan['cover']['subline'] = '这是一条超过十二个全角字的封面副标题文案'
        self.reject('cover subline exceeds')

    def test_overlay_must_stay_inside_the_footage_band(self):
        def overlay(box, reviewed=True):
            x, y, w, h = body_geometry(lock(), self.plan)['footage']
            return {'type': 'comment_screenshot', 'path': 'cover.png', 'source_sha256': sha256(self.root/'cover.png'),
                    'start_frame': 30, 'end_frame': 90, 'box': box or [x, y, 400, 200], 'supports_claim': '呈现网友讨论',
                    'intentional_text_reviewed': reviewed, 'claim_ids': ['c1'], 'acquisition_method': 'user_provided',
                    'rights_basis': '测试夹具'}
        self.plan['overlays'] = [overlay(None)]
        self.assertEqual(len(self.load()['overlays']), 1)
        self.plan['overlays'] = [overlay([0, 0, 400, 200])]
        self.reject('inside the footage band')
        self.plan['overlays'] = [overlay(None, False)]
        self.reject('explicit review')

    def test_body_geometry_stacks_header_then_footage(self):
        zones = body_geometry(lock(), self.plan)
        self.assertEqual(zones['footage'][1]+zones['footage'][3], lock()['body']['footage']['bottom_y'])
        self.assertEqual(zones['header'][3], zones['footage'][1])
        self.assertLess(zones['headline_block'][1]+zones['headline_block'][3], zones['result_line'][1])
        plan = brief_plan(self.root)
        plan.update(result_line=None, disclaimer_label=None)
        plan['clips'] = [dict(clip, footage_role='direct_evidence') for clip in plan['clips']]
        slim = body_geometry(lock(), plan)
        self.assertLess(slim['footage'][1], zones['footage'][1])
        text = dict(plan, format='brief_text', clips=[], headline_lines=plan['headline_lines'][:1])
        self.assertEqual(body_geometry(lock(), text)['panel'], lock()['brief_text']['panel'])

    def test_reading_pace_planner_matches_the_floor(self):
        blocks, total = plan_reading_frames([27.5, 22.55, 30.6], 30)
        self.assertEqual(blocks[0]['start_frame'], 1)
        self.assertEqual(blocks[-1]['end_frame'], total)
        self.assertGreaterEqual((blocks[1]['end_frame']-blocks[1]['start_frame'])/30, 2.61)
        self.assertGreaterEqual((total-blocks[-1]['start_frame'])/30, 2.98)
        with self.assertRaises(ValueError):
            plan_reading_frames([], 30)

    def test_voice_pacer_leaves_lead_gap_and_tail(self):
        blocks, total = plan_voice_frames([2.5, 2.5], 30)
        self.assertEqual(blocks[0]['voice_start_frame'], 10)
        self.assertEqual(blocks[1]['voice_start_frame']-blocks[1]['start_frame'], 9)
        self.assertEqual(total-(blocks[1]['voice_start_frame']+75), 12)

    def test_fullchar_units_count_digits_as_partial_width(self):
        self.assertEqual(fullchar_units('中文'), 2)
        self.assertEqual(fullchar_units('12306'), 2.75)
        self.assertEqual(fullchar_units('甲 乙'), 2.5)

    def test_wav_header_is_read_for_16_and_24_bit_and_extensible(self):
        write_wav(self.root/'a.wav', 2.5)
        self.assertAlmostEqual(wav_info(self.root/'a.wav')['duration_seconds'], 2.5)
        data = bytearray((self.root/'a.wav').read_bytes())
        data[20:22] = (0xFFFE).to_bytes(2, 'little')  # WAVE_FORMAT_EXTENSIBLE tag
        (self.root/'b.wav').write_bytes(bytes(data))
        self.assertAlmostEqual(wav_info(self.root/'b.wav')['duration_seconds'], 2.5)
        with self.assertRaises(ValueError):
            wav_info(self.root/'cover.png')

    def test_prominent_units_follow_the_audio_mode(self):
        base = self.root
        self.assertEqual(prominent_units(self.plan, base), [])
        self.plan['audio_mode'] = 'original_bgm'
        self.plan['clips'][0]['use_original_sound'] = True
        self.assertEqual(prominent_units(self.plan, base)[0]['duration_seconds'], 140/30)

    def test_pts_regular_passes_and_legacy_field_is_understood(self):
        self.assertTrue(all(x['passed'] for x in timestamp_checks([{'pts_time': str(i/30)} for i in range(420)], 30, '1/15360')))
        self.assertTrue(all(x['passed'] for x in timestamp_checks([{'pkt_pts_time': str(i/30)} for i in range(60)], 30, '1/15360')))

    def test_same_frame_count_timestamp_jump_fails(self):
        frames = [{'pts_time': str((i+(1 if i >= 100 else 0))/30)} for i in range(420)]
        self.assertFalse(all(x['passed'] for x in timestamp_checks(frames, 30, '1/15360')))

    def test_missing_pts_fails(self):
        self.assertFalse(all(x['passed'] for x in timestamp_checks([{}], 30, None)))

    def test_schema_validation_covers_caption_blocks(self):
        validate_timeline_schema(self.plan)
        broken = copy.deepcopy(self.plan)
        broken['caption_blocks'][1]['start_frame'] += 1
        with self.assertRaises(ValueError):
            validate_timeline_schema(broken)
        with self.assertRaisesRegex(ValueError, 'schema_version'):
            validate_timeline_schema(dict(self.plan, schema_version=1))

    def test_automatic_qa_frames_touch_every_caption_and_cut(self):
        frames = automatic_qa_frames(self.plan['caption_blocks'],
                                     [{'begin': 7, 'arrival': 13, 'exit': 97}, {'begin': 97, 'arrival': 103, 'exit': None}],
                                     self.plan['clips'], 1, 286)
        for wanted in (0, 1, 7, 12, 13, 97, 103, 140, 141, 188, 285):
            self.assertIn(wanted, frames)
        self.assertEqual(frames, sorted(set(frames)))

    def test_no_expectations_cannot_pass(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            validate_main(['not-needed.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_equal_scale_geometry(self):
        sw, sh = geometry([0, 0, 720, 960], (1080, 1440))
        self.assertEqual((sw, sh), (1080, 1440))
        self.assertEqual(geometry([0, 0, 720, 444], (1080, 642)), (1080, 666))

    def test_still_motion_filter_has_exact_frame_count_and_size(self):
        motion = {'start_zoom': 1.0, 'end_zoom': 1.06, 'start_anchor': [0.5, 0.5], 'end_anchor': [0.5, 0.5]}
        chain = still_motion_filter(motion, 60, 30, 1080, 642, 4)
        self.assertIn(':d=60:s=1080x642:fps=30', chain)
        self.assertIn('scale=4320:2568', chain)
        with self.assertRaises(ValueError):
            still_motion_filter(motion, 60, 30, 1080, 642, 1)

    def test_tts_cache_binds_text_settings_and_audio(self):
        write_wav(self.root/'synth.wav', 1.0)
        wav_hex = (self.root/'synth.wav').read_bytes().hex()
        response = ({'base_resp': {'status_code': 0}, 'data': {'status': 2, 'audio': wav_hex}}, 'unit-test')
        output = self.root/'out.wav'
        argv = ['tts', '--text', '回归文案。', '--page-id', 'b1', '--output', str(output)]
        with patch.dict(os.environ, {'MINIMAX_API_KEY': 'unit-test-not-a-key', 'MINIMAX_API_BASE_URL': 'https://api.minimax.cn'}), \
             patch.object(minimax_tts, 'post_json', return_value=response) as api, \
             patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
            minimax_tts.main(); minimax_tts.main()
            self.assertEqual(api.call_count, 1)
            self.assertEqual(api.call_args.args[2]['voice_setting']['emotion'], minimax_tts.VOICE_CONFIG['emotion'])
            argv[2] = '改过的文案。'
            minimax_tts.main()
            self.assertEqual(api.call_count, 2)
            with output.open('ab') as stream:
                stream.write(b'corrupt')
            minimax_tts.main()
            self.assertEqual(api.call_count, 3)


def find_test_font() -> Path | None:
    for path, index in ((Path('C:/Windows/Fonts/msyhbd.ttc'), 0),
                        (Path('/System/Library/Fonts/Hiragino Sans GB.ttc'), 2)):
        if path.is_file():
            return path
    return None


TEST_FONT = find_test_font()


@unittest.skipUnless(TEST_FONT is not None, 'no CJK bold test font on this machine')
class BriefRendererHelpers(unittest.TestCase):
    def font(self, size):
        return ImageFont.truetype(str(TEST_FONT), size, index=0 if os.name == 'nt' else 2)

    def test_text_layer_is_tight_and_stroke_grows_it(self):
        plain = text_layer('情侣同居生子', self.font(60), (255, 222, 0, 255))
        outlined = text_layer('情侣同居生子', self.font(60), (255, 222, 0, 255), outline=5, outline_fill=(0, 0, 0, 255))
        self.assertLess(plain.width, 420)
        self.assertGreater(outlined.width, plain.width)
        self.assertEqual(outlined.getchannel('A').getextrema()[1], 255)

    def test_shear_only_widens_and_keeps_height(self):
        layer = text_layer('还辩称两人只是同事', self.font(60), (255, 222, 0, 255))
        leaned = shear(layer, 0.17)
        self.assertEqual(leaned.height, layer.height)
        self.assertGreater(leaned.width, layer.width)
        self.assertIs(shear(layer, 0), layer)

    def test_fitted_stops_at_the_minimum_font_size(self):
        build = lambda size: text_layer('过长的标题行'*6, self.font(size), (255, 255, 255, 255))
        with self.assertRaisesRegex(ValueError, 'minimum font size'):
            fitted(build, 60, 40, 1020, 'headline')
        layer, size = fitted(lambda s: text_layer('短标题', self.font(s), (255, 255, 255, 255)), 60, 40, 1020, 'headline')
        self.assertEqual(size, 60)
        self.assertLessEqual(layer.width, 1020)

    def test_slide_frames_count_direction_and_ease(self):
        image = Image.new('RGBA', (300, 80), (255, 222, 0, 255))
        incoming = slide_frames(image, 200, 300, 1, (1080, 1920), 6, 8)
        outgoing = slide_frames(image, 200, 300, -1, (1080, 1920), 6, 8)
        self.assertEqual((len(incoming), len(outgoing)), (6, 6))
        self.assertEqual(incoming[-1].getbbox(), (200, 300, 500, 380))
        self.assertGreater(incoming[0].getbbox()[0], 200)
        self.assertTrue(outgoing[-1].getbbox() is None or outgoing[-1].getbbox()[2] <= 0)
        self.assertEqual(ease_out(1.0), 1.0)
        self.assertGreater(ease_out(0.5), 0.9)

    def test_long_cover_text_is_rejected_not_shrunk(self):
        with self.assertRaises(ValueError):
            draw_text(Image.new('RGBA', (1080, 1920)), '过长的封面标题一定不能强行缩小', self.font(144),
                      [80, 1100, 920, 180], 'white')


@unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows PowerShell 7 required')
class PublicationPacingGate(unittest.TestCase):
    """Synthetic files exercise only the publication gate, always with -WhatIf."""
    def setUp(self):
        from test_editorial_contract import EditorialContractTests
        fixture = EditorialContractTests(); fixture.setUp()
        fixture.p['presentation'].update(cover_headline='测试新闻', cover_subline='测试副标题')
        fixture.t['cover'].update(headline='测试新闻', subline='测试副标题')
        fixture.ready()
        self.tmp = fixture.tmp; self.root = fixture.root
        self.video = fixture.video; self.cover = fixture.cover; self.editorial = fixture.report
        machine = {'passed': True, 'diagnostic_only': False, 'validation_scope': 'production_machine_checks',
                   'sha256': sha256(self.video)}
        self.qa = self.root/'qa.json'; self.qa.write_text(json.dumps(machine), encoding='utf-8')
        self.acceptance = {'status': 'FINAL_READY', 'diagnostic': False, 'cover_title': '测试新闻',
                           'content_id': 'p1', 'editorial_report_sha256': sha256(self.editorial),
                           'cover_title_full': '测试新闻，测试副标题',
                           'video_sha256': sha256(self.video), 'cover_sha256': sha256(self.cover),
                           'machine_report': 'qa.json', 'machine_report_sha256': sha256(self.qa),
                           'review': {k: 'passed' for k in ('visual', 'audio', 'facts', 'muted_reading', 'voiced_playback')}}

    def tearDown(self): self.tmp.cleanup()

    def invoke(self):
        report = self.root/'acceptance.json'; report.write_text(json.dumps(self.acceptance), encoding='utf-8')
        command = ['pwsh', '-NoProfile', '-File', str(Path(__file__).with_name('publish_news_output.ps1')),
                   '-OutputRoot', str(self.root/'delivery'), '-WorkRoot', str(self.root/'work'),
                   '-Date', '2026-09-07', '-Sequence', '1', '-CoverTitle', '测试新闻', '-CoverSubtitle', '测试副标题',
                   '-FinalVideo', str(self.video), '-Cover', str(self.cover), '-AcceptanceReport', str(report),
                   '-EditorialReport', str(self.editorial), '-PythonExecutable', sys.executable, '-WhatIf']
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertFalse((self.root/'delivery').exists(), 'Dry-run gate must not create output directories')
        return result

    def test_missing_full_title_in_acceptance_is_rejected(self):
        del self.acceptance['cover_title_full']
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Acceptance hashes/titles', result.stderr)

    def test_both_pacing_reviews_are_required(self):
        for gate in ('muted_reading', 'voiced_playback'):
            self.acceptance['review'][gate] = 'not_checked'
            result = self.invoke()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing completed review: '+gate, result.stderr)
            self.acceptance['review'][gate] = 'passed'

    def test_complete_fixture_passes_dry_run_only(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_editorial_report_binding_is_rejected(self):
        del self.acceptance['editorial_report_sha256']
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Editorial record changed', result.stderr)

    def test_changed_editorial_input_is_rejected(self):
        package = self.root/'facts.json'
        data = json.loads(package.read_text(encoding='utf-8')); data['review']['reviewer'] = 'different'
        package.write_text(json.dumps(data), encoding='utf-8')
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Editorial binding verification failed', result.stderr)

    def test_diagnostic_is_rejected_even_with_reviews(self):
        self.acceptance['diagnostic'] = True
        self.assertNotEqual(self.invoke().returncode, 0)

    def test_changed_video_invalidates_reviews(self):
        self.video.write_bytes(b'changed-unit-fixture')
        self.assertNotEqual(self.invoke().returncode, 0)


if __name__ == '__main__':
    unittest.main()
