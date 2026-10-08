import copy
import json
import tempfile
import unittest
from pathlib import Path
from editorial_contract import check, verify, digest, validate_package, write_report


class EditorialContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.video=self.root/'final.mp4'; self.video.write_bytes(b'identity-only fixture, no media QA')
        self.cover=self.root/'cover.png'; self.cover.write_bytes(b'cover identity fixture')
        self.source=self.root/'source.mp4'; self.source.write_bytes(b'source identity fixture')
        self.package=self.root/'facts.json'; self.timeline=self.root/'timeline.json'; self.report=self.root/'editorial.json'
        self.p=dict(schema='news-editor-editorial/v1',content_id='p1',event_id='event1',kind='breaking',
            audience_value='判断适用范围',event_occurred_at='2026-10-08',latest_material_at='2026-10-08T08:00:00+08:00',
            sources=[dict(id='s1',publisher='机构',independence_group='机构',title='原始说明',url='https://example.org/a',role='primary',published_at='2026-10-08',checked_at='2026-10-08T09:00:00+08:00'),
                dict(id='s2',publisher='地方媒体',independence_group='地方媒体',title='独立采访',url='https://example.net/b',role='independent_reporting',published_at='2026-10-08',checked_at='2026-10-08T09:00:00+08:00')],
            claims=[dict(id='c1',text='暂无法保障亲子相邻。',subject='亲子座位',conditions=['暂无法保障'],numbers=[],source_ids=['s1','s2'])],
            review=dict(reviewer='测试审查记录',reviewed_at='2026-10-08T09:30:00+08:00',evidence='测试记录，不是新闻事实核验',
                **{k:'passed' for k in ['source_eligibility','source_independence','dates','subject_numbers_conditions','copy_media_consistency','semantic_increment']}),
            feasibility={k:dict(status='passed',evidence='测试记录') for k in ['video','cover']},
            presentation=dict(headline='座位安排',subtitle='范围说明',cover_headline='亲子座位',cover_subline='暂无法保障',pages=[dict(id='page1',narration='暂无法保障亲子相邻。',white_lines=['暂无法保障'],red_emphasis='亲子相邻',claim_ids=['c1'])]),
            media=[dict(path='source.mp4',sha256=digest(self.source),role='direct_evidence',capture_time_status='verified',captured_at='2026-10-08',time_basis='记录当事方拍摄说明',used_as_current_scene=True,current_scene_basis='本事件本日拍摄',claim_ids=['c1'],source_id='s1'),
                dict(path='cover.png',sha256=digest(self.cover),role='contextual_broll',capture_time_status='unknown',captured_at=None,time_basis='背景示意图，不宣称现场',used_as_current_scene=False,claim_ids=['c1'],source_id='s2')])
        self.t=dict(headline='座位安排',subtitle='范围说明',pages=[dict(id='page1',narration='暂无法保障亲子相邻。',white_lines=['暂无法保障'],red_emphasis='亲子相邻')],
            cover=dict(path='cover.png',source_sha256=digest(self.cover),headline='亲子座位',subline='暂无法保障'),
            clips=[dict(path='source.mp4',source_sha256=digest(self.source))])
        self.save()

    def tearDown(self): self.tmp.cleanup()

    def save(self):
        self.package.write_text(json.dumps(self.p),encoding='utf-8')
        self.timeline.write_text(json.dumps(self.t),encoding='utf-8')

    def ready(self):
        self.save(); r=check(self.package,'ready',self.timeline,self.video,self.cover)
        self.report.write_text(json.dumps(r),encoding='utf-8'); return r

    def test_ready_binds_exact_files_without_claiming_actual_qa(self):
        r=self.ready()
        self.assertIn('Record completeness',r['scope'])
        self.assertEqual(verify(self.report,self.video,self.cover,'亲子座位','暂无法保障')['status'],'EDITORIAL_BINDING_VERIFIED')

    def test_report_cannot_overwrite_source_media_or_hardlink_alias(self):
        r=self.ready();before=self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'overwrite'):write_report(self.source,r)
        alias=self.root/'alias.json';alias.hardlink_to(self.source)
        with self.assertRaisesRegex(ValueError,'overwrite'):write_report(alias,r)
        self.assertEqual(self.source.read_bytes(),before)

    def test_report_atomic_write_remains_verifiable(self):
        r=self.ready();write_report(self.report,r)
        self.assertEqual(verify(self.report,self.video,self.cover,'亲子座位','暂无法保障')['status'],'EDITORIAL_BINDING_VERIFIED')

    def test_fact_stage_does_not_require_unmade_media(self):
        self.p.pop('media');self.p.pop('presentation');self.p.pop('feasibility');self.save()
        self.assertEqual(check(self.package)['status'],'FACT_RECORD_READY')
        with self.assertRaises(ValueError):check(self.package,'ready',self.timeline,self.video,self.cover)

    def test_feasibility_stage_precedes_copy_and_final_render(self):
        self.p.pop('presentation');self.p['review']['copy_media_consistency']='not_checked';self.save()
        self.assertEqual(check(self.package,'feasible')['status'],'FEASIBILITY_RECORD_READY')
        self.p['feasibility']['cover']['status']='not_checked';self.save()
        with self.assertRaisesRegex(ValueError,'feasibility'):check(self.package,'feasible')

    def test_changed_copy_invalidates_editorial_review(self):
        self.t['pages'][0]['red_emphasis']='保障相邻'
        with self.assertRaisesRegex(ValueError,'stale page'):self.ready()

    def test_changed_cover_subtitle_invalidates_review(self):
        self.t['cover']['subline']='可以保障'
        with self.assertRaisesRegex(ValueError,'stale presentation'):self.ready()

    def test_repost_of_same_primary_source_is_not_independent(self):
        self.p['sources'][1]['independence_group']='机构'
        with self.assertRaisesRegex(ValueError,'not independent'):self.ready()

    def test_claims_cannot_reference_unknown_source(self):
        self.p['claims'][0]['source_ids'][1]='absent'
        with self.assertRaisesRegex(ValueError,'known source'):self.ready()

    def test_missing_conditions_or_units_is_rejected(self):
        del self.p['claims'][0]['conditions']
        with self.assertRaises(ValueError):self.ready()
        self.p['claims'][0]['conditions']=[]
        self.p['claims'][0]['numbers']=[dict(value='99',scope='个案')]
        with self.assertRaisesRegex(ValueError,'unit'):self.ready()

    def test_unreviewed_dates_block(self):
        self.p['review']['dates']='not_checked'
        with self.assertRaisesRegex(ValueError,'uncompleted'):self.ready()

    def test_unknown_capture_time_cannot_be_current_scene(self):
        m=self.p['media'][0];m['capture_time_status']='unknown';m['captured_at']=None
        with self.assertRaisesRegex(ValueError,'current scene'):self.ready()

    def test_dated_background_can_be_used_without_claiming_current_scene(self):
        m=self.p['media'][0];m.update(role='contextual_broll',capture_time_status='unknown',captured_at=None,used_as_current_scene=False)
        self.assertEqual(self.ready()['status'],'EDITORIAL_RECORD_READY')

    def test_correction_requires_original_and_specific_corrected_claim(self):
        self.p['kind']='correction'
        with self.assertRaisesRegex(ValueError,'original'):self.ready()
        self.p.update(corrects_content_id='old1',corrected_claim='旧画面被误写成当天现场')
        self.assertEqual(self.ready()['kind'],'correction')

    def test_old_source_or_media_hash_is_rejected(self):
        self.source.write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError,'media hash'):self.ready()

    def test_final_file_edit_invalidates_report(self):
        self.ready();self.video.write_bytes(b'new version')
        with self.assertRaisesRegex(ValueError,'stale editorial video'):verify(self.report,self.video,self.cover,'亲子座位','暂无法保障')

    def test_fact_package_edit_invalidates_report(self):
        self.ready();self.p['review']['reviewer']='other';self.save()
        with self.assertRaisesRegex(ValueError,'stale editorial package'):verify(self.report,self.video,self.cover,'亲子座位','暂无法保障')

    def test_reused_report_cannot_bind_another_title(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'title mismatch'):verify(self.report,self.video,self.cover,'其他标题','暂无法保障')

    def test_day_precision_not_falsely_converted_to_hour(self):
        self.p['latest_material_at']='2026-10-08';self.save()
        check(self.package)
        self.p['latest_material_at']='2026-10-08T09:00:00';self.save()
        with self.assertRaisesRegex(ValueError,'timezone'):check(self.package)


if __name__=='__main__':unittest.main()
