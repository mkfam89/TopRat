#!/usr/bin/env python3
"""tailor_local.py — instant, deterministic TEMPLATE tailoring inside the dashboard.

Processes a runNow=true to_process.json queue: builds resumes for the jobs whose skills already
cover the JD (jobpipe action=template): copy the classified base resume + a LibreOffice PDF, then
update the tracker — NO LLM, no tokens. AI-customize jobs are LEFT in the queue for the Cowork
job-tailor-queue-processor (which polls every couple of minutes). Processing order honors the
prioritization (local-first, best match).

The dashboard's per-row **Tailor** button queues one id via /api/queue *without* runNow, so those
are picked up by the hourly queue processor, not here. This path fires only for a runNow queue or
with --force (CLI/manual).

Cheap-exits immediately when there's nothing queued / runNow isn't set, so a 2-minute poll is free.

Run:  python src/resume/tailor_local.py           # process a runNow template queue
      python src/resume/tailor_local.py --force   # ignore the runNow gate
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, shutil, subprocess, time
from datetime import datetime

# NOTE: render_resume imports `find_soffice` from THIS module, so importing it is
# deferred until after find_soffice is defined below — importing it here would be a
# circular import (tailor_local -> render_resume -> tailor_local, half-initialized).
rr = None

HERE = _paths.ROOT
def P(*p): return os.path.join(HERE, *p)
from pipelib import TO_PROCESS, TAILOR_ERRORS   # DATA root, not the code dir
from pipelib import resume_prefix                # profile.json identity.resume_prefix


# ---------------------------------------------------------------- tailoring errors
# A failed job used to print one line to the console and stay queued. The board then kept
# showing its old resume state, so the only place the failure existed was a log nobody reads.
# Every failure path now records WHY here, keyed by job id, and every success clears its own
# key — so the file holds live problems only, never a history to prune.
#   { "<id>": {"code": "...", "message": "...", "detail": "...", "at": "<iso>", "stage": "..."} }
# `code` is the stable key the UI switches on; `message` is the sentence shown to the user.
# `template_none` and `template_missing` are deliberately separate: "the folder holds no base
# resume at all" and "the one file this job needs is not there" have different fixes, and the
# first must never be reported as a named file (see scoring._pick_base).
ERR_CODES = ('template_missing', 'template_none', 'render_rejected', 'copy_failed',
             'content_unreadable', 'pdf_failed', 'ai_failed')

def _rec_err(bucket, cid, code, message, detail='', stage=''):
    """Stage one job's failure. Written once, at the end of the run, by _flush_errors."""
    if not cid: return
    bucket[cid] = {'code': code, 'message': message, 'detail': str(detail)[:500],
                   'at': datetime.now().isoformat(timespec='seconds'), 'stage': stage}
    print('tailor_local: %s for %s -> %s' % (code, cid, detail or message))

def _flush_errors(bucket, cleared):
    """Merge this run's failures in and drop the keys that succeeded (or left the queue)."""
    try:
        cur = load_json(TAILOR_ERRORS, {}) or {}
        if not isinstance(cur, dict): cur = {}
        for cid in cleared: cur.pop(cid, None)
        cur.update(bucket)
        if cur: save_json(TAILOR_ERRORS, cur)
        else:
            try: os.remove(TAILOR_ERRORS)
            except OSError: pass
    except Exception as e:
        print('tailor_local: could not record tailoring errors:', e)


def _resolve_content(it, cid, safe_co, errs=None):
    """Return a content.json dict for this queued job, or None.

    Sources, in order: an inline `content` field on the queue item, or a
    <cid>.content.json file next to the output folder / in the repo root. When none
    exists (today's default) the caller falls back to the verbatim template copy, so
    behavior is unchanged until the customize step starts emitting content.json.
    """
    c = it.get('content')
    if isinstance(c, dict) and c:
        return c
    for path in (P('New', safe_co, cid + '.content.json'),
                 P('content', cid + '.json'),
                 P(cid + '.content.json')):
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                if errs is not None:
                    _rec_err(errs, cid, 'content_unreadable',
                             'The tailoring content file for this job could not be read, so no '
                             'resume was built. The file is %s.' % os.path.basename(path),
                             e, stage='content')
                else:
                    print('tailor_local: bad content.json for', cid, e)
                return None
    return None

def load_json(fp, default):
    try:
        with open(fp, encoding='utf-8') as f: return json.load(f)
    except Exception: return default

def save_json(fp, data):
    with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)

def find_soffice():
    for c in ('soffice', 'soffice.exe', 'soffice.com'):
        w = shutil.which(c)
        if w: return w
    for pth in (r'C:\Program Files\LibreOffice\program\soffice.exe',
                r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
                # macOS: LibreOffice.app never puts soffice on PATH, so the bundle
                # path is the only way to find it. Without this, Macs get the .docx
                # but silently no .pdf, which breaks ATS uploads.
                '/Applications/LibreOffice.app/Contents/MacOS/soffice',
                os.path.expanduser('~/Applications/LibreOffice.app/Contents/MacOS/soffice'),
                '/usr/bin/soffice', '/opt/libreoffice/program/soffice'):
        if os.path.exists(pth): return pth
    return None

# Safe now: find_soffice exists, so render_resume's `from tailor_local import
# find_soffice` resolves even though this module is still mid-import.
try:
    import render_resume as rr        # content.json -> lint-gated .docx/.pdf (formatting locked in code)
except Exception:
    rr = None

def _run(cmd, timeout=180):
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout)

def main():
    q = load_json(TO_PROCESS, {}) or {}
    ids = [i for i in q.get('ids', []) if i]
    if not ids or (not q.get('runNow') and '--force' not in sys.argv):
        print('tailor_local: nothing to do (no runNow queue)'); return
    # Prioritized classification (local-first, best-match; template vs customize).
    try:
        out = _run([sys.executable, _paths.script('jobpipe.py'), 'process-queue'], timeout=120).stdout
        data = json.loads(out)
    except Exception as e:
        print('tailor_local: process-queue failed:', e); return
    process = data.get('process', [])
    soffice = find_soffice()
    errs = {}                       # this run's failures, keyed by job id (see _rec_err)
    # Buildable now = template items (verbatim copy) PLUS any item that ships a
    # content.json (rendered via render_resume, lint-gated). A customize item with no
    # content.json stays in the queue for the Cowork tailor task, exactly as before.
    def _content_for(p):
        cid = p.get('trackerId') or p.get('id')
        company = p.get('company') or p.get('companyDisplay') or 'Company'
        # Folder = the id's short company slug so it matches the shortened filename
        # (<resume_prefix><cid>), not the full raw company name.
        safe_co = (cid.split('_', 1)[0] if cid else '') or ''.join(ch for ch in company if ch.isalnum()) or 'Company'
        content = _resolve_content(p, cid, safe_co, errs) if rr is not None else None
        # OPTIONAL Claude-API enhancement: a customize job with no pre-supplied content.json
        # gets one built here IFF an API key is configured. No key -> content stays None and
        # the job is left in the queue for the Cowork agent, exactly as before. The rendered
        # output is still lint-gated by render_resume, so a bad AI result never ships.
        if content is None and rr is not None and p.get('action') == 'customize':
            try:
                import llm_tailor
                if llm_tailor.llm_available():
                    content = llm_tailor.build_content(p)
            except Exception as e:
                # Not fatal on its own — the job can still fall back to a template copy — but
                # it IS the reason an AI-tailored resume did not appear, so it is recorded.
                _rec_err(errs, cid, 'ai_failed',
                         'Claude could not build a tailored resume for this job, so the queue '
                         'fell back to the plain template copy.', e, stage='ai')
        return cid, safe_co, content

    buildable = []
    for p in process:
        cid, safe_co, content = _content_for(p)
        if content is not None or p.get('action') == 'template':
            buildable.append((p, cid, safe_co, content))
    if not buildable:
        _flush_errors(errs, [])
        print('tailor_local: no template items; %d AI-customize wait for the tailor task'
              % sum(1 for p in process if p.get('action') == 'customize'))
        return
    details, processed = {}, []
    for it, cid, safe_co, content in buildable:
        outdir = P('New', safe_co); os.makedirs(outdir, exist_ok=True)
        docx = os.path.join(outdir, '%s%s.docx' % (resume_prefix(), cid))
        customized = False
        if content is not None and rr is not None:
            # RENDER path: formatting locked in code + lint gate. A lint/validation
            # failure means the job is NOT built (never write a broken resume) and it
            # stays queued — we do NOT silently fall back to an unvalidated copy.
            try:
                fd, fp = rr.render(content, outdir=outdir, no_pdf=(soffice is None))
                if os.path.abspath(fd) != os.path.abspath(docx):
                    os.replace(fd, docx)
                    if fp and os.path.exists(fp):
                        os.replace(fp, os.path.splitext(docx)[0] + '.pdf')
                customized = True
            except Exception as e:
                _rec_err(errs, cid, 'render_rejected',
                         'The resume was built but rejected by the formatting check, so nothing '
                         'was written. The job stays in the queue and is retried on the next run.',
                         e, stage='render')
                continue
        else:
            # TEMPLATE path: verbatim base-resume copy (unchanged behavior).
            base = it.get('baseResume')
            from pipelib import TEMPLATE_DIR
            src = os.path.join(TEMPLATE_DIR, os.path.basename(base)) if base else None
            if not base:
                # No file was chosen because there was nothing to choose from. Report the
                # folder, not a filename — naming one would invent a file nobody asked for.
                _rec_err(errs, cid, 'template_none',
                         'resume_template/ holds no base resume, so there is nothing to copy '
                         'for this job. Add a base resume to that folder and run the queue again.',
                         TEMPLATE_DIR, stage='template')
                continue
            if not os.path.isfile(src):
                _rec_err(errs, cid, 'template_missing',
                         'This job needs the base resume %s, but that file is not in '
                         'resume_template/. Add the file and run the queue again.'
                         % os.path.basename(base),
                         base, stage='template')
                continue
            try:
                shutil.copy2(src, docx)
            except Exception as e:
                _rec_err(errs, cid, 'copy_failed',
                         'The base resume could not be copied into the job folder, so no resume '
                         'file was created.', e, stage='copy')
                continue
            if soffice:
                try:
                    _run([soffice, '--headless', '--convert-to', 'pdf', '--outdir', outdir, docx], timeout=150)
                except Exception as e:
                    # The .docx exists, so this is a partial result, not a dead job: the PDF
                    # twin ATS uploads want is the piece missing. Recorded, not fatal.
                    _rec_err(errs, cid, 'pdf_failed',
                             'The resume was created, but LibreOffice could not produce the PDF '
                             'copy. The .docx file is there; the PDF twin is not.', e, stage='pdf')
            else:
                print('tailor_local: LibreOffice not found — .docx built, PDF skipped for', cid)
        details[cid] = {'foundDate': it.get('foundDate', ''), 'applyUrl': it.get('applyUrl', ''),
                        'salary': it.get('salary') or it.get('salaryEst', ''),
                        'location': it.get('location', ''), 'source': it.get('source', ''),
                        'flaggedSkills': it.get('flaggedSkills', []), 'skillMatch': it.get('skillMatch'),
                        'questionnaire': [], 'customized': customized}
        processed.append(cid)
    # A job that built is no longer failing — drop its old error. A pdf_failed job DID build,
    # so it is deliberately excluded from the clear list: its record is this run's.
    _flush_errors(errs, [c for c in processed if c not in errs])
    if not processed:
        print('tailor_local: nothing built'); return
    # Persist + fold into the tracker via jobpipe (update then rebuild).
    save_json(P('details.json'), details)
    save_json(P('processed.json'), processed)
    save_json(P('run.json'), {'timestamp': datetime.now().isoformat(timespec='seconds'),
                              'trigger': 'tailor_local', 'templated': len(processed)})
    _run([sys.executable, _paths.script('jobpipe.py'), 'update', '--details', 'details.json',
          '--processed', 'processed.json', '--run', 'run.json'], timeout=150)
    save_json(P('manual.json'), {})
    _run([sys.executable, _paths.script('jobpipe.py'), 'rebuild', '--manual', 'manual.json'], timeout=150)
    # Drop the built (template) ids from the queue; keep AI-customize ids for the Cowork task.
    remaining = [i for i in ids if i not in set(processed)]
    if remaining:
        # Carry the per-id tailoring notes across the rewrite. They are the user's own words
        # about what to emphasize; dropping them here silently emptied the instructions for
        # every job that was still waiting on the AI pass.
        left = {'requestedAt': q.get('requestedAt'), 'runNow': True, 'ids': remaining}
        notes = q.get('notes')
        if isinstance(notes, dict) and notes:
            left['notes'] = notes
        save_json(TO_PROCESS, left)
    else:
        try: os.remove(TO_PROCESS)
        except OSError: pass
    print('tailor_local: built %d template resume(s); %d AI-customize left for the tailor task'
          % (len(processed), len(remaining)))

if __name__ == '__main__':
    import runlog; runlog.run('tailor_local.py', main)
