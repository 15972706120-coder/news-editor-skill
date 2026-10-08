#!/usr/bin/env python3
"""Bind recorded editorial review to exact copy, sources, media and deliverables.

This checks records and identity, not the truth of a source or an actual viewing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

SCHEMA = 'news-editor-editorial/v1'
REPORT_SCHEMA = 'news-editor-editorial-report/v1'


def require(value, message):
    if not value:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(value, label, allow_date=True, nullable=False):
    if value is None and nullable:
        return
    require(text(value), f'{label}: timestamp required')
    if allow_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        dt.date.fromisoformat(value)
        return
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, f'{label}: timezone required')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    require(isinstance(value, dict), 'JSON root must be an object')
    return value


def resolve(base, value):
    require(text(value), 'file path required')
    p = Path(value)
    return (p if p.is_absolute() else base / p).resolve()


def web_url(value):
    return text(value) and urlparse(value).scheme in ('http', 'https') and bool(urlparse(value).netloc)


def write_report(output, result):
    output = Path(output).resolve()
    protected = {Path(v['path']).resolve() for v in result['files'].values()}
    package_path = Path(result['files']['package']['path'])
    package = read(package_path)
    for item in package.get('media', []):
        if isinstance(item, dict) and text(item.get('path')):
            protected.add(resolve(package_path.parent,item['path']))
    if 'timeline' in result['files']:
        timeline_path = Path(result['files']['timeline']['path'])
        timeline = read(timeline_path)
        for item in [timeline.get('cover', {}), *timeline.get('clips', [])]:
            if isinstance(item, dict) and text(item.get('path')):
                protected.add(resolve(timeline_path.parent,item['path']))
        for page in timeline.get('pages', []):
            for key in ('path','manifest'):
                value=page.get('voice',{}).get(key)
                if text(value): protected.add(resolve(timeline_path.parent,value))
    require(output not in protected and not any(output.exists() and p.exists() and output.samefile(p) for p in protected), 'report must not overwrite an input or referenced media')
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=output.parent,prefix='.editorial-',suffix='.tmp',delete=False) as f:
            temp_path=Path(f.name)
            f.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        os.replace(temp_path,output)
    finally:
        if temp_path and temp_path.exists():temp_path.unlink()


def validate_package(package, base, stage='facts', timeline=None):
    require(package.get('schema') == SCHEMA, 'unsupported editorial schema')
    for key in ('content_id', 'event_id', 'audience_value'):
        require(text(package.get(key)), f'{key} required')
    require(package.get('kind') in ('breaking', 'development', 'service', 'correction'), 'invalid kind')
    timestamp(package.get('event_occurred_at'), 'event_occurred_at', nullable=True)
    if package.get('event_occurred_at') is None:
        require(text(package.get('event_time_basis')), 'unknown event time needs event_time_basis')
    timestamp(package.get('latest_material_at'), 'latest_material_at')
    if package['kind'] == 'correction':
        require(text(package.get('corrects_content_id')), 'correction needs original content_id')
        require(text(package.get('corrected_claim')), 'correction needs corrected_claim')
    review = package.get('review')
    require(isinstance(review, dict), 'editorial review required')
    require(text(review.get('reviewer')) and text(review.get('evidence')), 'reviewer/evidence required')
    timestamp(review.get('reviewed_at'), 'review.reviewed_at', allow_date=False)
    for key in ('source_eligibility', 'source_independence', 'dates', 'subject_numbers_conditions', 'semantic_increment'):
        require(review.get(key) == 'passed', f'uncompleted editorial review: {key}')
    sources = package.get('sources')
    require(isinstance(sources, list) and len(sources) >= 2, 'at least two independent source records required')
    source_ids = {}
    for source in sources:
        require(isinstance(source, dict), 'invalid source record')
        sid = source.get('id')
        require(text(sid) and sid not in source_ids, 'missing/duplicate source id')
        for key in ('publisher', 'independence_group', 'title'):
            require(text(source.get(key)), f'{sid}: {key} required')
        require(web_url(source.get('url')), f'{sid}: source URL required')
        require(source.get('role') in ('primary', 'independent_reporting'), f'{sid}: source role required')
        timestamp(source.get('published_at'), f'{sid}.published_at', nullable=True)
        if source.get('published_at') is None:
            require(text(source.get('time_basis')), f'{sid}: unknown source time needs basis')
        timestamp(source.get('checked_at'), f'{sid}.checked_at', allow_date=False)
        source_ids[sid] = source
    claims = package.get('claims')
    require(isinstance(claims, list) and bool(claims), 'claims required')
    claim_ids = set()
    for claim in claims:
        require(isinstance(claim, dict), 'invalid claim')
        cid = claim.get('id')
        require(text(cid) and cid not in claim_ids, 'missing/duplicate claim id')
        claim_ids.add(cid)
        require(text(claim.get('text')) and text(claim.get('subject')), f'{cid}: text/subject required')
        require(isinstance(claim.get('conditions'), list) and all(text(v) for v in claim['conditions']), f'{cid}: explicit conditions list required')
        require(isinstance(claim.get('numbers'), list), f'{cid}: explicit numbers list required')
        for number in claim['numbers']:
            require(isinstance(number, dict) and all(text(number.get(k)) for k in ('value', 'unit', 'scope')), f'{cid}: number value/unit/scope required')
        refs = claim.get('source_ids')
        require(isinstance(refs, list) and len(set(refs)) >= 2 and all(s in source_ids for s in refs), f'{cid}: two known source ids required')
        require(len({source_ids[s]['independence_group'] for s in refs}) >= 2, f'{cid}: syndicated sources are not independent')
    if stage == 'facts':
        return
    require(stage in ('feasible', 'ready'), 'unsupported stage')
    feasibility = package.get('feasibility', {})
    for key in ('video', 'cover'):
        proof = feasibility.get(key, {})
        require(proof.get('status') == 'passed' and text(proof.get('evidence')), f'{key}: feasibility not recorded')
    media = package.get('media')
    require(isinstance(media, list) and bool(media), 'media time review required')
    media_by_path = {}
    for item in media:
        require(isinstance(item, dict), 'invalid media record')
        path = resolve(base, item.get('path'))
        require(path not in media_by_path, 'duplicate media path')
        require(path.is_file() and item.get('sha256') == digest(path), 'stale media hash')
        require(item.get('role') in ('direct_evidence', 'contextual_broll'), 'invalid media role')
        require(item.get('capture_time_status') in ('verified', 'unknown'), 'capture time status required')
        require(text(item.get('time_basis')), 'capture time basis required')
        timestamp(item.get('captured_at'), 'media.captured_at', nullable=True)
        if item['capture_time_status'] == 'verified':
            require(item.get('captured_at') is not None, 'verified capture time missing')
        require(type(item.get('used_as_current_scene')) is bool, 'current scene flag required')
        if item['used_as_current_scene']:
            require(item['role'] == 'direct_evidence' and item['capture_time_status'] == 'verified', 'unknown/old context cannot claim current scene')
            require(text(item.get('current_scene_basis')), 'current scene needs event/time matching evidence')
        refs = item.get('claim_ids')
        require(isinstance(refs, list) and bool(refs) and all(c in claim_ids for c in refs), 'media claim mapping required')
        require(item.get('source_id') in source_ids or web_url(item.get('source_url')), 'media provenance required')
        media_by_path[path] = item
    if stage == 'feasible':
        return
    require(isinstance(timeline, dict), 'ready stage needs timeline')
    require(review.get('copy_media_consistency') == 'passed', 'uncompleted editorial review: copy_media_consistency')
    presentation = package.get('presentation', {})
    for key in ('headline', 'subtitle'):
        require(text(presentation.get(key)) and presentation[key] == timeline.get(key), f'stale presentation.{key}')
    cover = timeline.get('cover', {})
    for field, target in (('cover_headline', 'headline'), ('cover_subline', 'subline')):
        require(text(presentation.get(field)) and presentation[field] == cover.get(target), f'stale presentation.{field}')
    planned = presentation.get('pages')
    actual = timeline.get('pages')
    require(isinstance(planned, list) and bool(planned) and isinstance(actual, list), 'presentation pages required')
    require(len(planned) == len(actual), 'stale page count')
    for p, a in zip(planned, actual):
        for field in ('id', 'narration', 'white_lines', 'red_emphasis'):
            require(field in p and p[field] == a.get(field), f'stale page {p.get("id")}.{field}')
        refs = p.get('claim_ids')
        require(isinstance(refs, list) and bool(refs) and all(c in claim_ids for c in refs), 'page claim mapping required')
    timeline_base = Path(timeline['_editorial_timeline_base'])
    for clip in timeline.get('clips', []):
        path = resolve(timeline_base, clip.get('path'))
        require(path in media_by_path and clip.get('source_sha256') == media_by_path[path]['sha256'], 'timeline clip lacks reviewed media identity')
    require(bool(timeline.get('clips')), 'timeline clips required')
    cover_path = resolve(timeline_base, cover.get('path'))
    require(cover_path in media_by_path and cover.get('source_sha256') == media_by_path[cover_path]['sha256'], 'cover lacks reviewed media identity')


def check(package_path, stage='facts', timeline_path=None, video_path=None, cover_path=None):
    package_path = Path(package_path).resolve()
    package = read(package_path)
    timeline = None
    paths = {'package': package_path}
    if stage == 'ready':
        require(all((timeline_path, video_path, cover_path)), 'ready needs --timeline --video --cover')
        timeline_path = Path(timeline_path).resolve()
        timeline = read(timeline_path)
        timeline['_editorial_timeline_base'] = str(timeline_path.parent)
        paths.update(timeline=timeline_path, video=Path(video_path).resolve(), cover=Path(cover_path).resolve())
    validate_package(package, package_path.parent, stage, timeline)
    statuses={'facts':'FACT_RECORD_READY','feasible':'FEASIBILITY_RECORD_READY','ready':'EDITORIAL_RECORD_READY'}
    return dict(schema=REPORT_SCHEMA, status=statuses[stage],
        stage=stage, content_id=package['content_id'], event_id=package['event_id'], kind=package['kind'],
        checked_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        cover_title=timeline['cover']['headline'] if timeline else None,
        cover_title_full=(timeline['cover']['headline']+'，'+timeline['cover']['subline']) if timeline else None,
        files={name:dict(path=str(path),sha256=digest(path)) for name,path in paths.items()},
        scope='Record completeness and file identity only; source truth and actual viewing require recorded independent editorial review.')


def verify(report_path, video_path, cover_path, title, subtitle):
    report = read(report_path)
    require(report.get('schema') == REPORT_SCHEMA and report.get('status') == 'EDITORIAL_RECORD_READY' and report.get('stage') == 'ready', 'ready editorial report required')
    files = report.get('files', {})
    require(set(files) == {'package','timeline','video','cover'}, 'incomplete editorial binding')
    for name, item in files.items():
        path = resolve(Path(report_path).resolve().parent, item.get('path'))
        require(path.is_file() and item.get('sha256') == digest(path), f'stale editorial {name}')
    require(digest(video_path) == files['video']['sha256'] and digest(cover_path) == files['cover']['sha256'], 'editorial deliverable mismatch')
    base = Path(report_path).resolve().parent
    rebuilt = check(resolve(base,files['package']['path']), 'ready', resolve(base,files['timeline']['path']), video_path, cover_path)
    for key in ('content_id','event_id','kind','cover_title','cover_title_full'):
        require(rebuilt[key] == report.get(key), f'stale editorial {key}')
    require(report['cover_title'] == title and report['cover_title_full'] == title+'，'+subtitle, 'editorial title mismatch')
    return dict(status='EDITORIAL_BINDING_VERIFIED',content_id=report['content_id'],video_sha256=files['video']['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('check')
    p.add_argument('--package', required=True); p.add_argument('--stage', choices=('facts','feasible','ready'), default='facts')
    for key in ('timeline','video','cover','report'): p.add_argument('--'+key)
    v = sub.add_parser('verify')
    for key in ('report','video','cover','title','subtitle'): v.add_argument('--'+key, required=True)
    a = parser.parse_args()
    try:
        result = check(a.package,a.stage,a.timeline,a.video,a.cover) if a.command=='check' else verify(a.report,a.video,a.cover,a.title,a.subtitle)
        if a.command=='check' and a.report:
            write_report(a.report,result)
        print(json.dumps(result,ensure_ascii=False)); return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(json.dumps(dict(status='BLOCKED_EDITORIAL_RECORD',error=str(error)),ensure_ascii=False)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
