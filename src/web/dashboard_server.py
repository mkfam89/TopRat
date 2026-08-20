#!/usr/bin/env python3
"""dashboard_server.py - tiny local server for the job tracker dashboard.

Serves job_tracker.html + your tracking data on http://127.0.0.1:8765 so the dashboard
becomes a real little app: it auto-loads tracking.csv / candidates.csv, saves your skip/apply
marks straight back to disk, lets you set the skill-match threshold, and queues extra jobs for
resume tailoring. Standard-library only - no pip installs. Listens on localhost only (not the network).

Run:  python src/web/dashboard_server.py     (or double-click "Start Here.bat")
Stop: close this window.
Dev:  http://127.0.0.1:8765/dev — hidden page: restart this process after a .py edit, and read
      the port / data root / scheduler state / last log lines without leaving the browser.
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401  -- src/_paths.py: puts every src/ folder on sys.path; _paths.ROOT = project folder
import os, sys, json, csv, subprocess, webbrowser, threading, time, io, zipfile
import http.client                       # port_is_ours(): ask a busy port whether it is us
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
try:
    import profile_lib as pl          # profile schema + geocode + URL generators
except Exception:
    pl = None
try:
    import scheduler as sched          # in-app job scheduler (replaces Windows Task Scheduler)
except Exception:
    sched = None
try:
    import make_autostart as mau       # LEGACY: Startup-folder .vbs, logon only (superseded by mwd)
except Exception:
    mau = None
try:
    import make_watchdog as mwd        # watchdog switch: restarts the dashboard if it stops
                                      # (Task Scheduler on Windows, launchd on macOS)
except Exception:
    mwd = None
try:
    import blocklist as bl             # deterministic employer ban list (config/employer_blocklist.json)
except Exception:
    bl = None
try:
    import ghost_risk as gr            # heuristic ghost-job risk scorer (ADVISORY ONLY)
except Exception:
    gr = None
try:
    import ghostflags as gf            # the USER's own ghost flags (recorded, not guessed)
except Exception:
    gf = None
try:
    import reposts as rp               # advisory repost detector (never excludes)
except Exception:
    rp = None
try:
    import scoring as scr              # classify() → suggested base-resume family (template)
except Exception:
    scr = None
try:
    import pipelib as plib             # shared helpers (employment-type detection, tracker id)
except Exception:
    plib = None
try:
    import jobdesc as jdmod            # on-demand job-description fetcher (script-only, cached)
except Exception:
    jdmod = None
try:
    import locality as loc_mod         # geo-aware "is this job local?" gate (offline, cache-only)
except Exception:
    loc_mod = None
try:
    import backlog as bk               # pending-job cap: the count, and "are the searches paused?"
except Exception:
    bk = None
AUTOSTART_ERR = ''                      # last autostart install/uninstall error (for the UI warning)
WATCHDOG_ERR = ''                       # last watchdog install/uninstall error (for the UI warning)

HERE = _paths.ROOT
PORT = 8765
BOOT_TS = time.time()                   # when THIS process started — /dev tells restarts apart by it
SERVER_OBJ = None                       # the live ThreadingHTTPServer, so /api/dev/restart can free the port
def P(*p): return os.path.join(HERE, *p)          # project folder: config/, logs/, New/ ...

WEB = os.path.dirname(os.path.abspath(__file__))  # src/web/ — the pages this server serves
def W(name): return os.path.join(WEB, name)       # html + css assets beside this file

# Per-user DATA (tracker state + private config) resolves off pipelib's DATA root,
# It falls back to HERE when the data repo is not separate.
from pipelib import (DATA as _DATA, cfg, cfg as _pl_cfg, cfg_write, INSTANCE_JSON, JSON_PATH,
                     HTML_PATH, TRACKING_CSV, CANDIDATES_CSV, ARCHIVE_CSV, TO_PROCESS,
                     TAILOR_ERRORS, env as _env, env_truthy as _env_truthy_pl)
def D(*p): return os.path.join(_DATA, *p)         # DATA: state files the pipeline writes

def read_bytes(fp, default=b''):
    try:
        with open(fp, 'rb') as f: return f.read()
    except Exception: return default

# ---- shared top navigation (injected into every page so all tabs share one bar) ----
# Styling lives in ui.css (.ui-nav*), not inline here, so the bar matches the pages it sits
# on. There is deliberately NO separate "Table" tab: the card and table boards are two views
# of the same page now. The toolbar toggle switches them, so a second tab is a second way
# to reach the same board. /table still routes there directly for old bookmarks.
# "Tailoring queue" sits next to Jobs because it is the other half of the same loop: the board
# is what you decide on, the queue is what you asked for and has not been built yet. It used to
# be the board wearing a ?filter=queued in a second tab, which could not offer a Run-now button.
NAV_ITEMS = [('/', 'Jobs', 'tracker'), ('/queue', 'Tailoring queue', 'queue'),
             ('/setup', 'Settings', 'settings'),
             # "Scheduled tasks", not "Schedule": the tab sits two places away from a "Jobs" tab
             # that means job POSTINGS, and the page behind it used to call its runs jobs too.
             ('/schedule', 'Scheduled tasks', 'schedule'), ('/archived', 'Archived', 'archived')]

def nav_html(active=''):
    links = ''.join(
        '<a href="{href}" class="{cls}">{label}</a>'
        .format(href=h, label=l, cls=('active' if k == active else ''))
        for h, l, k in NAV_ITEMS)
    label = instance_label()
    badge = '<span class="ui-nav-badge">' + label + '</span>' if label else ''
    return ('<div class="ui-nav%s">'
            '<span class="ui-nav-brand">'
            '<img class="ui-nav-mark" src="/assets/icon/job-agent-icon-64.png" alt="">'
            '<span class="brand-text">Top Rat</span></span>'
            % (' is-labeled' if label else '')
            + badge + '<div class="ui-nav-links">' + links + '</div>'
            # Live scheduler pill — filled in by SCHED_PILL_JS, hidden until the first poll
            # answers, so a scheduler-less build shows nothing rather than an empty chip.
            # The chip and its bubble share a positioned wrapper, so the bubble can hang off
            # the chip's own right edge on every page without measuring anything.
            + '<span class="ui-sched-wrap" id="uiNavSchedWrap">'
            + '<a class="ui-nav-sched" id="uiNavSched" href="/schedule" aria-haspopup="true"'
              ' aria-expanded="false" hidden></a>'
            + '<div class="ui-sched-pop" id="uiNavSchedPop" hidden></div>'
            + '</span>'
            + '</div>')

# ---- first-run onboarding nudge (injected into every non-settings page) ----
# config/profile.json does not exist yet: the first visit goes to the wizard
# (/setup?welcome=1). If the user chose "Skip for now" there, we instead show a
# dismissible reminder banner at most once per 24h (localStorage-timed).
ONBOARD_JS = """
<script>(async()=>{try{
  var r=await fetch('/api/profile-status'); var d=await r.json();
  if(d.configured){ localStorage.removeItem('setupSkippedAt'); localStorage.removeItem('setupRemindAt'); return; }
  if(!localStorage.getItem('setupSkippedAt')){ location.href='/setup?welcome=1'; return; }
  var last=+localStorage.getItem('setupRemindAt')||0;
  if(Date.now()-last < 24*3600*1000) return;
  localStorage.setItem('setupRemindAt', String(Date.now()));
  var b=document.createElement('div');
  b.style.cssText='position:sticky;top:0;z-index:99998;display:flex;gap:12px;align-items:center;'+
    'flex-wrap:wrap;padding:10px 16px;background:var(--accent-soft,#eef3fc);'+
    'border-bottom:1px solid var(--accent-line,#c2d4f4);color:var(--ink,#2b3440);'+
    'font:14px system-ui,Segoe UI,sans-serif;';
  b.innerHTML='<span>\\ud83d\\udc4b Make your user profile. It makes the job searches more accurate.</span>'+
    '<a href="/setup?welcome=1" class="ui-btn primary sm" style="text-decoration:none;">'+
    'Make it now (2 min)</a>'+
    '<button style="margin-left:auto;background:none;border:none;color:var(--muted,#5f6b7a);'+
    'cursor:pointer;font-size:16px;" title="Remind me tomorrow">\\u2715</button>';
  b.querySelector('button').onclick=()=>b.remove();
  document.body.insertBefore(b, document.body.firstChild);
}catch(e){}})();</script>
"""

# ---- live scheduler pill (injected into every page's nav) ----
# ONE place the scheduler talks to the user. It used to be five: this chip, plus four warning
# banners stacked down /schedule — a missed-runs list, a "why did the chip send me here"
# explainer, a paused-searches notice and a watchdog-is-off warning. Four warnings on one page
# is not four times the signal; it is a wall the reader learns to scroll past. Worse, three of
# them could only be seen from /schedule, which is the page you go to BECAUSE something is
# wrong — so the warning arrived after the user had already worked out they had a problem.
#
# Now the chip states ONE number — how many scheduled runs were missed — and the bubble behind
# it carries every message: each missed task on its own line with the reason and a Run now,
# then the failures, the paused searches and the watchdog, each with the button that fixes it.
# /schedule keeps the settings (times, on/off switches) and no warnings at all.
#
# NO COUNTDOWN. An earlier build ticked "in 5h 47m" down every second; it drew the eye on
# every page for a number that changes nothing, and a per-second timer to say "still 5 hours"
# is motion without information. The chip shows the next run TIME instead, which is what a
# person actually checks against.
#
# The chip never decides anything on its own: the per-task facts come from
# scheduler.status_all() and the watchdog facts from watchdog_state_cached(), so the bubble and
# the /schedule cards cannot disagree about what is wrong.
SCHED_PILL_JS = """
<script>(function(){
  var el = document.getElementById('uiNavSched'); if(!el) return;
  var S = null, timer = null;

  // Next-run time, phrased the way a person reads a clock: today is just the time, tomorrow
  // and later carry the day so "9:00 AM" is never ambiguous about which morning.
  function when(iso, nowIso){
    if(!iso) return '';
    var d = new Date(iso), now = new Date(nowIso), t = d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    var days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate())
                         - new Date(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000);
    if(days <= 0) return t;
    if(days === 1) return 'tomorrow ' + t;
    if(days < 7) return d.toLocaleDateString([], {weekday:'short'}) + ' ' + t;
    return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + t;
  }
  function mins(s){ if(s == null) return ''; return s < 90 ? Math.round(s) + 's' : Math.round(s/60) + 'm'; }
  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : String(s);
    return d.innerHTML; }

  // The chip is the ONE place scheduler status is reported, so it has to say what it is a
  // status OF. Unlabelled, a lone "2 missed" in a nav bar full of job-posting counts reads as
  // being about the postings.
  var LABEL = '<span class="slbl">Scheduled tasks:</span>';

  // ---------------- what the chip counts ----------------
  // Missed runs, and ONLY missed runs. Every other thing that can be wrong lives in the bubble.
  // A chip that cycles through five different sentences ("stuck?", "failed", "searches
  // paused", "2 missed", "next 9:00") teaches the user to re-read it every time instead of
  // recognising it; one number in one place is a thing you can glance at.
  function missedList(){
    var all = (S && S.jobs) || {}, out = [];
    for(var k in all){
      if(Object.prototype.hasOwnProperty.call(all, k) && all[k].missed) out.push([k, all[k]]);
    }
    return out;
  }
  function runningList(){
    var all = (S && S.jobs) || {}, out = [];
    for(var k in all){
      if(Object.prototype.hasOwnProperty.call(all, k) && all[k].running) out.push([k, all[k]]);
    }
    return out;
  }

  // Everything that is wrong but is NOT a missed run — the contents of the four banners that
  // used to sit on /schedule, in one list. Each entry carries the words AND the button, because
  // a warning that only states a problem makes the reader go hunting for the control that
  // fixes it. `sev` decides the chip's colour: 'bad' is a failure, 'warn' is a nudge, 'info'
  // never colours the chip at all (it is context, not a fault).
  function problems(){
    var out = [], all = (S && S.jobs) || {}, k, j;
    for(k in all){
      if(!Object.prototype.hasOwnProperty.call(all, k)) continue;
      j = all[k]; if(!j.enabled) continue;
      if(j.stuck){
        out.push({sev:'bad', head:(j.label || k) + ' is possibly stuck',
          body:'It runs for ' + mins(j.elapsedSec) + ' now. That is much more than the usual '
             + Math.round(j.lastDurationSec || 0) + 's. The app cancelled nothing. A slow network '
             + 'looks the same from here. A step stops by itself after 30 minutes. If the task is '
             + 'still here after that time, close the dashboard, open it again, then run the task.'});
      } else if(j.failed){
        out.push({sev:'bad', head:(j.label || k) + ' did not finish', run:k,
          body:'The last run stopped with: <code>' + esc(j.lastResult || 'unknown error') + '</code>. '
             + (String(j.lastResult || '').indexOf('interrupted') === 0
                ? 'The dashboard closed while the task was still at work, so the task never reached the end. '
                : 'The message names the step that failed. The full output is in '
                  + '<code>schedule_log.txt</code>. ')
             + 'Nothing is broken permanently.'});
      }
    }
    // The pending-job cap. This one exists because the pause is otherwise invisible: no error,
    // no empty board, just postings that stop arriving — indistinguishable from a quiet week.
    var B = (S && S.backlog) || {};
    if(B.paused){
      out.push({sev:'warn', head:'The searches are paused',
        body:'<b>' + esc(String(B.count || 0)) + ' postings</b> wait for a decision, and the limit '
           + 'is <b>' + esc(String(B.cap || 0)) + '</b>. The searching tasks are still on. They wait, '
           + 'and they start again by themselves when the count decreases.',
        acts:[{act:'board', label:'Open the board', primary:true}]});
    }
    // The watchdog. Without it, "a scheduled task runs only while the dashboard is open" is a
    // trap rather than a rule, so its OFF state belongs beside the missed runs it causes.
    var W = (S && S.watchdog) || null;
    if(W){
      if(!W.supported){
        out.push({sev:'info', head:'This computer cannot restart the dashboard by itself',
          body:'The watchdog needs a scheduler this computer does not have. Here, the tasks run '
             + 'only while this dashboard is open.'});
      } else if(W.desired && !W.enabled){
        out.push({sev:'bad', head:'The watchdog did not turn on',
          body:(W.error ? esc(W.error) + '. ' : '') + 'The switch shows on, but this computer has '
             + 'no scheduled job for it. Nothing restarts the dashboard if it stops.',
          acts:[{act:'wd-on', label:'Try again', primary:true},
                {act:'wd-go', label:'Show me the setting'}]});
      } else if(!W.desired){
        out.push({sev:'warn', head:'The watchdog is off',
          body:'The tasks run only while this dashboard window is open. If you turn it on, this '
             + 'computer starts the dashboard again, minimized, each time it is closed.',
          acts:[{act:'wd-on', label:'Turn it on', primary:true},
                {act:'wd-go', label:'Show me the setting'}]});
      } else if(W.pauseMin){
        out.push({sev:'info', head:'The watchdog is paused',
          body:'For the next ' + esc(String(W.pauseMin)) + ' minutes the dashboard stays closed if you '
             + 'close it. After that, this computer starts it again.'});
      }
    }
    return out;
  }
  function worstSev(probs){
    var w = '';
    for(var i = 0; i < probs.length; i++){
      if(probs[i].sev === 'bad') return 'bad';
      if(probs[i].sev === 'warn') w = 'warn';
    }
    return w;
  }

  function paint(){
    if(!S || !S.available){ el.hidden = true; setPop(false); return; }
    var n = missedList().length, probs = problems(), worst = worstSev(probs);
    var running = runningList().length;
    var cls, html, tip;
    if(n){
      cls = (worst === 'bad') ? 'is-error' : 'is-missed';
      html = '<span class="sdot"></span><span class="smiss">' + n + ' missed</span>';
      tip = n + (n === 1 ? ' scheduled task was' : ' scheduled tasks were') + ' due while the dashboard '
          + 'was closed. Click to see which, and to catch them up.';
    } else {
      // Calm, and still useful: the resting question is "when does the next one go?".
      cls = (worst === 'bad') ? 'is-error' : (worst === 'warn' ? 'is-missed' : 'is-idle');
      html = '<span class="sdot' + (running ? ' spin' : '') + '"></span>Nothing missed'
           + (S.nextRun ? ' <span class="seta">next ' + esc(when(S.nextRun, S.now)) + '</span>' : '');
      tip = 'No scheduled run has been skipped.'
          + (S.nextRun ? ' Next: ' + (S.nextLabel || S.nextJob) + ' at ' + when(S.nextRun, S.now) + '.' : '');
    }
    // The count stays a count of MISSED runs. Anything else that needs a look is one quiet mark
    // — enough to make the chip worth opening, without turning it into a second sentence.
    if(worst) html += ' <span class="swarn" title="Something else needs a look">⚠</span>';
    if(!n && worst) tip += ' ' + probs.length + (probs.length === 1 ? ' thing needs' : ' things need')
                         + ' a look — click to read them.';
    el.className = 'ui-nav-sched ' + cls;
    el.innerHTML = LABEL + html;
    el.title = tip;
    // A modifier-click still opens the settings page; naming the job scrolls it into view there.
    el.href = '/schedule' + (S.stateJob ? '?job=' + encodeURIComponent(S.stateJob) : '');
    el.hidden = false;
    if(open) renderPop();          // a poll landed while the bubble is up: keep them in step
  }

  // ---------------- the bubble ----------------
  // The chip has room for one number ("2 missed") and the question that follows is always WHICH
  // tasks, and why. That answer used to be a page away — click the chip, land on /schedule, read
  // four stacked banners and six cards to find the one it meant. The bubble expands out of the
  // chip and holds all of it: the missed tasks with a Run now on each, then every other warning
  // the app has, each with its own button. The Schedule page stays one click down in the footer,
  // for what a bubble should not do: edit a time, switch a task off.
  var pop = document.getElementById('uiNavSchedPop'), open = false;
  // A page served from an older cached nav has the chip but not the bubble. Build it rather
  // than dying on the first click.
  if(!pop){
    pop = document.createElement('div');
    pop.className = 'ui-sched-pop'; pop.id = 'uiNavSchedPop'; pop.hidden = true;
    (el.parentNode || document.body).appendChild(pop);
  }

  function stamp(iso){
    if(!iso) return '';
    var d = new Date(iso);
    return d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' '
         + d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
  }
  // One row per missed task. The sub-line answers the question the tag raises: when it last
  // ran, and when it will next go if it is simply left alone.
  function missRow(name, j){
    return '<div class="sjob"><div class="sjmain">'
         + '<div class="sjname">' + esc(j.label || name) + '<span class="stag miss">missed</span></div>'
         + '<div class="swhen">' + (j.lastRunAt ? 'Last ran ' + esc(stamp(j.lastRunAt)) : 'Has never run')
         + ' · next ' + (esc(when(j.nextRun, S.now)) || 'not scheduled') + '</div></div>'
         + '<button class="srun" data-job="' + esc(name) + '">▶ Run now</button></div>';
  }
  // A running task is not a warning, so it gets a plain line with no button — but leaving it
  // out entirely would make the bubble contradict the pulsing dot on the chip.
  function runRow(name, j){
    return '<div class="sjob"><div class="sjmain">'
         + '<div class="sjname">' + esc(j.label || name) + '<span class="stag run">running</span></div>'
         + '<div class="swhen">Going for ' + mins(j.elapsedSec) + '</div></div></div>';
  }
  // One warning, its explanation, and the button that resolves it. `head` is escaped here;
  // `body` is built with esc() around every value in problems(), so it is trusted HTML.
  function probRow(p){
    var acts = (p.acts || []).map(function(a){
      return '<button class="sact' + (a.primary ? ' primary' : '') + '" data-act="' + esc(a.act) + '">'
           + esc(a.label) + '</button>';
    });
    if(p.run) acts.unshift('<button class="sact primary srun" data-job="' + esc(p.run) + '">▶ Run it now</button>');
    return '<div class="sprob ' + esc(p.sev) + '">'
         + '<div class="spname">' + esc(p.head) + '</div>'
         + '<div class="spbody">' + p.body + '</div>'
         + (acts.length ? '<div class="spacts">' + acts.join('') + '</div>' : '')
         + '</div>';
  }
  function renderPop(){
    if(!S || !S.available){ pop.innerHTML = ''; return; }
    var miss = missedList(), running = runningList(), probs = problems(), n = miss.length;
    var html = '<h4>' + (n ? n + (n === 1 ? ' missed run' : ' missed runs') : 'Nothing missed') + '</h4>';
    if(n){
      html += miss.map(function(r){ return missRow(r[0], r[1]); }).join('');
      // The cause of a missed run is almost always this one fact, and it is not obvious.
      html += '<div class="snote">A scheduled task runs only while this dashboard is open. These '
            + 'tasks were due while it was closed. The app never starts a missed task by itself, '
            + 'because that task starts at the moment you open the app. Run one task to catch up, '
            + 'or leave it for the next scheduled run.</div>';
    } else {
      html += '<div class="sempty">No scheduled run has been skipped. Next up is <b>'
            + esc(S.nextLabel || S.nextJob || 'nothing scheduled') + '</b>'
            + (S.nextRun ? ' at ' + esc(when(S.nextRun, S.now)) : '') + '.</div>';
    }
    // A STUCK task is also a running task, and it already gets a full block below saying how
    // long it has gone and what to do. Printing the plain "Going for 40m" line as well would be
    // the same fact twice, three lines apart.
    html += running.filter(function(r){ return !r[1].stuck; })
                   .map(function(r){ return runRow(r[0], r[1]); }).join('');
    // The other three banners, in the order a reader needs them: what is broken, then what is
    // held back, then the setting that would have prevented the missed runs above.
    if(probs.length){
      html += '<div class="ssec">' + (n ? 'Also worth a look' : 'Worth a look') + '</div>'
            + probs.map(probRow).join('');
    }
    pop.innerHTML = html
      + '<div class="sfoot"><a href="' + (el.getAttribute('href') || '/schedule')
      + '">Open the Scheduled tasks page →</a></div>';
  }
  function setPop(o){
    open = !!o && !!(S && S.available);
    pop.hidden = !open;
    el.setAttribute('aria-expanded', open ? 'true' : 'false');
    if(open) renderPop();
  }
  el.addEventListener('click', function(e){
    if(e.metaKey || e.ctrlKey || e.shiftKey) return;   // let a modifier-click open /schedule
    e.preventDefault();
    setPop(!open);
  });
  // Outside click and Escape close it. A click INSIDE never does — the run buttons live there.
  document.addEventListener('click', function(e){
    if(!open || !e.target.closest) return;
    if(e.target.closest('#uiNavSchedPop') || e.target.closest('#uiNavSched')) return;
    setPop(false);
  });
  document.addEventListener('keydown', function(e){ if(e.key === 'Escape') setPop(false); });
  // The buttons the four banners used to carry. Each calls the SAME endpoint the /schedule
  // control calls, so a press here and a press there are one code path — the bubble can never
  // put the app in a state the settings page would not.
  pop.addEventListener('click', async function(e){
    var a = e.target.closest && e.target.closest('.sact[data-act]');
    if(a){
      var act = a.getAttribute('data-act');
      if(act === 'board'){ location.href = '/'; return; }
      if(act === 'wd-go'){ location.href = '/schedule#watchdog'; return; }
      if(act === 'wd-on'){
        a.disabled = true; a.textContent = 'turning on…';
        try{
          var wv = (S && S.watchdog) || {};
          var wr = await fetch('/api/watchdog', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({enable:true, everyMin: wv.everyMin || 5})});
          var wd = await wr.json();
          if(!wd || !wd.ok){ a.textContent = 'Windows refused'; a.title = (wd && wd.message) || ''; return; }
          poll();
        }catch(err){ a.textContent = 'did not turn on'; }
        return;
      }
    }
    var b = e.target.closest && e.target.closest('.srun'); if(!b) return;
    b.disabled = true; b.textContent = 'starting…';
    try{
      var r = await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'},
                                       body: JSON.stringify({job: b.getAttribute('data-job')})});
      var d = await r.json();
      if(!d || !d.ok){ b.textContent = (d && d.error) || 'did not start'; return; }
      // Running a SEARCH by hand re-shows the board's paused bubble if it was dismissed this
      // visit — the pause is the reason that run will not stick, and it is worth saying twice.
      // `group` comes from status_all, so no task name is hardcoded here.
      var jn = b.getAttribute('data-job'), jd = (S && S.jobs && S.jobs[jn]) || {};
      if(jd.group === 'search' && window.backlogRemind) window.backlogRemind();
      poll();                       // repaints the chip AND this bubble
    }catch(err){ b.textContent = 'did not start'; }
  });

  async function poll(){
    try{
      var r = await fetch('/api/sched-status', {cache:'no-store'});
      S = await r.json(); paint();
    }catch(e){ /* server restarting: keep the last painted value */ }
    clearTimeout(timer);
    // Fast only while something is actually happening; otherwise a minute is plenty, since
    // the chip now shows a clock time rather than a ticking number.
    timer = setTimeout(poll, (S && (S.state === 'running' || S.state === 'stuck')) ? 5000 : 60000);
  }
  poll();
  // A background tab throttles timers, so the chip can be minutes stale on return. Re-poll
  // the moment the page is looked at again.
  document.addEventListener('visibilitychange', function(){ if(!document.hidden) poll(); });
})();</script>
"""

# Shared design system. Injected into <head> on serve so no page file has to remember to link
# it, and so a page opened straight off disk (not through the server) still renders readably
# with its own fallback styles.
UI_CSS_LINK = '<link rel="stylesheet" href="/ui.css">'

# Tab icon, injected the same way and for the same reason: no page file has to remember it,
# and a page opened straight off disk simply shows no icon instead of a broken one.
ICON_LINK = ('<link rel="icon" href="/assets/icon/job-agent-icon-32.png" sizes="32x32">'
             '<link rel="icon" href="/assets/icon/job-agent-icon-256.png" sizes="256x256">'
             '<link rel="apple-touch-icon" href="/assets/icon/job-agent-icon-256.png">')

def inject_nav(html_bytes, active):
    """Insert the shared stylesheet into <head> and the nav bar right after <body>, without
    editing the page files. Also appends the first-run/reminder onboarding script on
    non-settings pages."""
    try: html = html_bytes.decode('utf-8')
    except Exception: return html_bytes
    low = html.lower()
    # 1) stylesheet + tab icon — first thing in <head>, so page-level rules can still
    #    override the sheet. Each is skipped if the page already declares its own.
    head_add = ('' if '/ui.css' in low else UI_CSS_LINK) + ('' if 'rel="icon"' in low else ICON_LINK)
    if head_add:
        h = low.find('<head')
        if h != -1:
            he = html.find('>', h)
            if he != -1:
                html = html[:he+1] + head_add + html[he+1:]
                low = html.lower()
        else:
            html = head_add + html
            low = html.lower()
    # 2) nav bar + onboarding nudge — immediately after <body>
    # The pill goes on every page including /schedule — it is the live "is it running now"
    # readout there too, and the page's own 4s status refresh does not touch the nav.
    # 'dev' is not a tab; it is here only to opt the hidden /dev page out of the first-run
    # redirect. A developer typing /dev on a fresh install wants the dev page, not the wizard.
    extra = nav_html(active) + SCHED_PILL_JS + ('' if active in ('settings', 'dev') else ONBOARD_JS)
    i = low.find('<body')
    if i != -1:
        j = html.find('>', i)
        if j != -1:
            return (html[:j+1] + extra + html[j+1:]).encode('utf-8')
    return (extra + html).encode('utf-8')

# ----------------------------------------------------------------- agent prompts
# The two Claude scheduled tasks that run the pipeline automatically are driven by prompts
# that live in Claude's own Scheduled folder, OUTSIDE this repo — so a new user cloning the
# code had no way to get them and no way to know they existed. The text now ships in
# agent_prompts/ and this renders it with the user's own name and folder path filled in, for
# the Copy button on Settings. We deliberately do NOT write into Claude's Scheduled folder:
# that is another app's storage, the layout is not ours to depend on, and pasting into the
# Claude window is a step a non-technical user can see working.
AGENT_PROMPTS_DIR = os.path.join(HERE, 'agent_prompts')

AGENT_PROMPTS = [
    {'id': 'job-alert-resume', 'file': 'daily-discovery.md',
     'title': 'Find new jobs every weekday morning',
     'when': 'Weekdays at 6am',
     'blurb': 'Searches the job boards, scores everything onto your board, and tailors a '
              'resume for the local ones. Jobs further away wait for you to press Tailor.'},
    {'id': 'job-tailor-queue-processor', 'file': 'tailor-queue.md',
     'title': 'Tailor the resumes you asked for',
     'when': 'Every hour, 7am to 6pm',
     'blurb': 'This task does nothing until you press Tailor on a job. After that, it '
              'writes those resumes in the background, so they are ready when you look.'},
]


def render_agent_prompt(name):
    """The prompt text for one scheduled task, personalized. '' if the file is missing.

    Substitution is a plain string replace on {{TOKEN}} — no template engine, because the
    body is a prompt full of braces, backslashes and JSON and anything cleverer would start
    interpreting them."""
    path = os.path.join(AGENT_PROMPTS_DIR, name)
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return ''
    prof = load_json(cfg('profile.json'), {}) or {}
    ident = prof.get('identity') if isinstance(prof.get('identity'), dict) else {}
    loc = ((prof.get('search') or {}).get('location') or {}) if isinstance(prof.get('search'), dict) else {}
    try:
        from pipelib import resume_prefix as _rp
        prefix = _rp()
    except Exception:
        prefix = 'Resume_'
    # Script paths are built with os.path.join, not written into the template with literal
    # backslashes: this app ships a macOS launcher too, and a hardcoded 'src\pipeline\...'
    # is simply wrong there. to_process.json comes from the DATA root, which is not always
    # the project folder once the code/data split is on.
    for token, value in (
        ('{{JOBPIPE}}', os.path.join(HERE, 'src', 'pipeline', 'jobpipe.py')),
        ('{{GIT_DAILY}}', os.path.join(HERE, 'src', 'ops', 'git_daily.py')),
        ('{{SCRAPE}}', os.path.join(HERE, 'src', 'pipeline', 'scrape.py')),
        ('{{TO_PROCESS}}', TO_PROCESS),
        ('{{PROJECT_DIR}}', HERE),
        ('{{OWNER}}', str(ident.get('full_name') or '').strip() or 'the user'),
        ('{{RESUME_PREFIX}}', prefix),
        ('{{LOCAL_AREA}}', str(loc.get('formatted_address') or loc.get('query') or '').strip()
         or 'your local area'),
    ):
        text = text.replace(token, value)
    return text


def load_json(fp, default):
    try:
        with open(fp, encoding='utf-8') as f: return json.load(f)
    except Exception: return default

# ---- per-instance overrides (git-ignored config/instance.json) ----
# Lets a staging copy neutralize itself (no notifications, no scheduler, alt port, a label)
# WITHOUT committing anything — the file is git-ignored, so it never leaks to the live copy
# on a merge. Env vars win over the file. Live has no instance.json, so all defaults apply.
def instance_cfg():
    return load_json(INSTANCE_JSON, {}) or {}

def _env_truthy(name):
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')

def resolve_port(default=8765):
    """--port N  >  TOP_RAT_PORT env  >  instance.json "port"  >  default."""
    argv = sys.argv
    for a in argv:
        if a.startswith('--port='):
            try: return int(a.split('=', 1)[1])
            except ValueError: pass
    if '--port' in argv:
        i = argv.index('--port')
        if i + 1 < len(argv):
            try: return int(argv[i + 1])
            except ValueError: pass
    env = _env('PORT')
    if env.isdigit(): return int(env)
    ic = instance_cfg().get('port')
    return ic if isinstance(ic, int) else default

# ---- the port actually bound (config/runtime.json) ------------------------------
# resolve_port() says which port this copy WANTS. When that port is taken by a foreign
# process we bind the next free one instead (bind_port below) — and at that moment the
# wanted port and the real port stop agreeing. watchdog.py and stop_board.py resolve the
# port independently, so without this file the watchdog would probe an empty socket,
# conclude the board is down, and start a second server every tick.
#
# So: the process that owns the socket writes where it is. The file lives beside the CODE
# (not in the data root) because two worktrees sharing one data root are two servers, and
# it is git-ignored per-copy state, never committed and never shipped.
RUNTIME_JSON = os.path.join(os.path.dirname(INSTANCE_JSON), 'runtime.json')
PORT_SCAN = 10                          # how many ports to try past the wanted one

def runtime_write(port):
    try:
        os.makedirs(os.path.dirname(RUNTIME_JSON), exist_ok=True)
        with open(RUNTIME_JSON, 'w', encoding='utf-8') as f:
            json.dump({'port': port, 'pid': os.getpid(), 'boot': int(BOOT_TS),
                       'codeDir': HERE,
                       '_readme': 'Written by the running dashboard so the watchdog and'
                                  ' stop_board find the socket it actually bound. Deleted on'
                                  ' a clean stop. Safe to delete; do not edit.'}, f, indent=2)
    except OSError:
        pass                            # a read-only config dir must not stop the server

def runtime_read():
    d = load_json(RUNTIME_JSON, {})
    return d if isinstance(d, dict) else {}

def runtime_clear():
    try:
        if runtime_read().get('pid') == os.getpid():
            os.remove(RUNTIME_JSON)     # only OUR record — never another instance's
    except OSError:
        pass

def port_is_ours(port, timeout=1.0):
    """Is the server on `port` THIS copy of the app? (None = nothing/not us answering.)

    /api/dev/ping reports codeDir, so we can tell "I am already running" (do not start a
    second one) apart from "some other program, or another copy, holds my port" (move over).
    """
    try:
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
        conn.request('GET', '/api/dev/ping')
        body = json.loads(conn.getresponse().read().decode('utf-8', 'replace'))
        conn.close()
    except Exception:
        return None
    cd = body.get('codeDir')
    return None if not cd else (os.path.normcase(os.path.abspath(cd)) ==
                                os.path.normcase(os.path.abspath(HERE)))

class AlreadyRunning(Exception):
    def __init__(self, port):
        super().__init__('this copy of the dashboard is already running on %d' % port)
        self.port = port

def bind_port(preferred, wait=2.5):
    """Bind `preferred`, or the next free port after it. Returns (server, port, moved_from).

    Raises AlreadyRunning when the wanted port is held by THIS copy — a second dashboard on
    the same data root is never what the user meant by double-clicking Start Here twice.

    Moving over is a LAST resort, hence `wait`: /dev's restart closes the old socket and
    launches the replacement immediately, so for a moment the port it was told to take is
    still in teardown. Falling forward on that half-second would silently strand the
    restarted server on 8766 while the browser polls 8765. So we insist on the wanted port
    for a couple of seconds first, and only scan when something is genuinely camped on it.
    """
    if port_is_ours(preferred):
        raise AlreadyRunning(preferred)
    last, deadline = None, time.time() + max(0.0, wait)
    while True:
        try:
            return ThreadingHTTPServer(('127.0.0.1', preferred), Handler), preferred, 0
        except OSError as e:
            last = e
            if time.time() >= deadline:
                break
            time.sleep(0.25)
    for n in range(1, PORT_SCAN + 1):
        try:
            return ThreadingHTTPServer(('127.0.0.1', preferred + n), Handler), preferred + n, preferred
        except OSError as e:
            last = e
    raise last

def live_port():
    """The port a running instance of THIS copy is on, else None.

    Used by --print-port so the launchers open the window that exists rather than the port
    the config asked for. Verified against the socket: a stale file from a crashed run must
    not send Start Here.bat to a dead port.
    """
    d = runtime_read()
    p = d.get('port')
    if not isinstance(p, int) or d.get('pid') == os.getpid():
        return None
    return p if port_is_ours(p) else None

def scheduler_disabled():
    return _env_truthy_pl('DISABLE_SCHEDULER') or bool(instance_cfg().get('disable_scheduler'))

def instance_label():
    return (instance_cfg().get('label') or '').strip()

# ---- data root (where THIS user's settings + tracker data live) ----------------
# pipelib.resolve_data_root() owns the resolution order; we only REPORT which rule
# won (so the Settings card can explain itself) and WRITE the instance.json override.
# instance.json stays beside the CODE by design. The app resolves the data root from it.
def suggested_data_root(full_name=''):
    """<app>/user_data_<INITIALS> — the self-contained default the wizard offers.

    The app folder holds everything, so the whole install is one folder to copy,
    back up, or hand over. Falls back to a generic name until a name is typed;
    the wizard recomputes it live in the browser as the user types, so this is
    only the value the page STARTS with."""
    import pipelib as _pl
    ini = ''
    if pl is not None:
        try:
            if not full_name:
                prof = pl.load_profile() or {}
                full_name = ((prof.get('identity') or {}).get('full_name') or '')
            ini = pl.initials(full_name)
        except Exception:
            ini = ''
    return os.path.join(HERE, _pl.suggest_user_data_dirname(ini))


def data_root_info():
    """{path, source, sourceLabel, writable, split, effectiveAfterRestart, override, default,
    appDir, sep, suggestion} — suggestion/appDir/sep drive the wizard's folder hints."""
    import pipelib as _pl
    override = str((instance_cfg().get('data_root') or '')).strip()
    env = _env('DATA')
    nested_user = _pl.nested_user_data()
    if env:                  src, label = 'env', 'the TOP_RAT_DATA environment variable (it overrides this setting)'
    elif override:           src, label = 'instance', 'the folder you chose here (config/instance.json)'
    elif nested_user and os.path.normcase(_pl.DATA) == os.path.normcase(os.path.abspath(nested_user)):
        src, label = 'nested', 'the data folder inside the app folder (%s)' % os.path.basename(nested_user)
    elif os.path.isdir(_pl.DEFAULT_DATA) and os.path.normcase(_pl.DATA) == os.path.normcase(_pl.DEFAULT_DATA):
        src, label = 'default', 'the legacy default location (~/Documents/my_job_agent)'
    elif _pl.SPLIT:          src, label = 'sibling', 'a data folder found next to the app folder, or inside it'
    else:                    src, label = 'code', 'the app folder itself (there is no separate data folder yet)'
    # The folder that a saved override points to after the dashboard restarts.
    pending = ''
    if override:
        pend = os.path.expanduser(override)
        pend = os.path.abspath(pend if os.path.isabs(pend) else os.path.join(HERE, pend))
        if os.path.normcase(pend) != os.path.normcase(os.path.abspath(_pl.DATA)):
            pending = pend
    return {'path': os.path.abspath(_pl.DATA), 'source': src, 'sourceLabel': label,
            'writable': os.access(_pl.DATA, os.W_OK), 'split': bool(_pl.SPLIT),
            'override': override, 'pending': pending,
            'default': os.path.abspath(_pl.DEFAULT_DATA), 'codeDir': HERE,
            # The wizard builds its own live suggestion as the name is typed; it needs
            # the app folder and this machine's separator to do that in the browser.
            'appDir': HERE, 'sep': os.sep,
            'userDataPrefix': _pl.USER_DATA_PREFIX,
            'suggestion': suggested_data_root(),
            'instanceFile': INSTANCE_JSON}

def check_data_root(raw):
    """Validate a candidate data folder WITHOUT moving anything.
    Returns (abs_path, error_or_empty, warning_or_empty). It creates the folder if the
    parent exists. An empty root is valid. pipelib makes the state files again on the next run."""
    raw = (raw or '').strip()
    if not raw:
        return '', 'Enter a folder path.', ''
    path = os.path.expanduser(raw)
    path = os.path.abspath(path if os.path.isabs(path) else os.path.join(HERE, path))
    if os.path.isfile(path):
        return path, 'That path is a file, not a folder.', ''
    if not os.path.isdir(path):
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            return path, 'The parent folder does not exist: ' + parent, ''
        try: os.makedirs(path, exist_ok=True)
        except Exception as e: return path, 'The app did not create the folder: %s' % e, ''
    if not os.access(path, os.W_OK):
        return path, 'The app cannot write to this folder.', ''
    try:
        probe = os.path.join(path, '.write_probe')
        with open(probe, 'w', encoding='utf-8') as f: f.write('ok')
        os.remove(probe)
    except Exception as e:
        return path, 'The app cannot write to this folder: %s' % e, ''
    warn = ''
    if not os.path.exists(os.path.join(path, 'job_tracker.json')):
        warn = ('This folder has no job_tracker.json yet. It starts as an empty tracker. '
                'To keep your history, copy your existing data files here first.')
    if os.path.normcase(path) == os.path.normcase(os.path.abspath(HERE)):
        warn = 'That is the app folder itself. The settings and the data then stay with the code.'
    return path, '', warn

# ------------------------------------------------------ native folder picker --
# The dashboard is a LOCAL server: the browser and the filesystem are the same machine,
# so a real OS folder dialog is possible — and it is the only way to get a real absolute
# path. A web <input type=file webkitdirectory> hands back relative names, never a path,
# which is why the Settings folder fields were type-it-yourself until now.
# Tkinter must own the main thread and this runs on a request thread, so the dialog runs
# in a short-lived CHILD process that prints the chosen path on stdout. No new dependency:
# tkinter ships with CPython, and if it is missing the user just types the path as before.
_PICKER_SRC = (
    "import sys, tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw()\n"
    "try: r.attributes('-topmost', True)\n"      # otherwise it opens BEHIND the browser
    "except Exception: pass\n"
    # parent= is deliberately DROPPED on macOS: Tk attaches the chooser to its parent as
    # a document-modal SHEET, and our parent is withdrawn, so the sheet lands on an
    # unmapped window - off the visible screen, with no title bar to drag it back by
    # (reported on macOS 2026-08-18). Without a parent Tk shows a free-floating panel the
    # window server places itself. Elsewhere parent= is what keeps it above the browser.
    "kw = {} if sys.platform == 'darwin' else {'parent': r}\n"
    "p = filedialog.askdirectory(title=sys.argv[1],\n"
    "                            initialdir=(sys.argv[2] or None), mustexist=False, **kw)\n"
    "r.destroy()\n"
    "sys.stdout.write(p or '')\n")


def _pick_folder_macos(title, init):
    """macOS folder panel via osascript. (path, err), or None to fall through to tkinter.

    Preferred over tkinter on macOS because this is the same Cocoa panel Finder opens:
    the window server positions it, and `tell me to activate` raises osascript above the
    browser without touching System Events (so no accessibility permission prompt).
    Returns None - never an error - when osascript is unavailable or fails for a reason
    other than the user cancelling, so the tkinter path still gets its turn.
    """
    def esc(v):
        return v.replace('\\', '\\\\').replace('"', '\\"')
    # Parenthesised: `default location` wants a file specifier, and the parens keep the
    # POSIX file coercion from being parsed as part of the following clause.
    loc = ('default location (POSIX file "%s")' % esc(init)) if init else ''
    script = ('tell me to activate\n'
              'set _f to choose folder with prompt "%s" %s\n'
              'return POSIX path of _f\n'
              % (esc(title or 'Choose a folder'), loc))
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return '', 'The folder window stayed open too long. Type the path instead.'
    except Exception:
        return None
    if r.returncode != 0:
        err = r.stderr or ''
        # -128 is userCanceledErr. A cancel is a RESULT, not a failure: falling through
        # would pop a second dialog at someone who just dismissed the first one.
        if '-128' in err or 'canceled' in err.lower() or 'cancelled' in err.lower():
            return '', ''
        return None
    out = (r.stdout or '').strip()
    return (os.path.normpath(os.path.abspath(out)) if out else ''), ''


def pick_folder(title='', initial=''):
    """Open the OS folder dialog. Returns (path, error); ('', '') = the user cancelled."""
    init = os.path.expanduser((initial or '').strip())
    if init and not os.path.isdir(init):
        parent = os.path.dirname(init)
        init = parent if os.path.isdir(parent) else ''
    if sys.platform == 'darwin':
        got = _pick_folder_macos(title, init)
        if got is not None:
            return got
    try:
        r = subprocess.run([sys.executable or 'python', '-c', _PICKER_SRC,
                            title or 'Choose a folder', init],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return '', 'The folder window stayed open too long. Type the path instead.'
    except Exception as e:
        return '', '%s: %s' % (type(e).__name__, e)
    if r.returncode != 0:
        tail = [l for l in (r.stderr or '').strip().splitlines() if l.strip()]
        return '', ('The folder window did not open (%s). Type the path instead.'
                    % (tail[-1] if tail else 'no error output'))
    out = (r.stdout or '').strip()
    # tkinter returns forward slashes even on Windows; normpath makes it look like a
    # path the user recognizes before it lands in the text field.
    return (os.path.normpath(os.path.abspath(out)) if out else ''), ''


def load_json_strict(fp):
    """Return (data, ok). A missing file gives ({}, True). A fresh start is correct there.
    Existing-but-unparseable -> (None, False) so callers can ABORT instead of
    clobbering good data with an empty tracker. Retries transient read truncation."""
    if not os.path.exists(fp) or os.path.getsize(fp) <= 2:
        return {}, True
    for _ in range(4):
        try:
            with open(fp, encoding='utf-8') as f: return json.load(f), True
        except Exception:
            time.sleep(0.5)
    return None, False

def atomic_write(path, text, verify_json=False):
    """Write via temp file + fsync + os.replace, then read back and verify.
    Raises on mismatch rather than leaving a truncated file."""
    for _ in range(5):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text); f.flush()
            try: os.fsync(f.fileno())
            except OSError: pass
        os.replace(tmp, path)
        try:
            # newline='' on the READ as well as the write. Without it the read-back gets
            # universal-newline translation, so a csv.DictWriter payload (which terminates
            # every line with \r\n) came back with \n and never compared equal — the verify
            # loop then spun five times and raised on every CSV this function was given.
            # job_tracker.json was written first and survived, so the board still showed the
            # new status and the failure looked like nothing at all, while tracking.csv
            # silently stopped being updated.
            with open(path, encoding='utf-8', newline='') as f: back = f.read()
            if back == text and (not verify_json or json.loads(back) is not None):
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise IOError('atomic_write: the app cannot verify ' + os.path.basename(path))

def load_csv(fp):
    try:
        with open(fp, newline='', encoding='utf-8') as f: return list(csv.DictReader(f))
    except Exception: return []

def gui_settings():
    return load_json(cfg('gui_settings.json'), {'threshold': 0.6, 'localEnabled': True, 'localThreshold': 0.2,
                     'autostart': True, 'dailyAutoTailorCap': 20, 'hourlyAutoTailorCap': 10,
                     'watchdog': False, 'watchdogEveryMin': 5})

# ---- is the Claude integration available on this machine? ----
# One source of truth for "Claude is set up": the Claude Code CLI is on this machine. There is
# no saved on/off preference any more — the notes field simply appears when the CLI that reads
# the notes exists. Cached, because /api/jobs is polled and find_claude() walks the PATH; the
# cache is short enough that a fresh `npm i -g` is picked up on the next board refresh.
_CLAUDE_CLI_CACHE = {'at': 0.0, 'ok': False}
def _claude_cli_present(ttl=60.0):
    now = time.time()
    if now - _CLAUDE_CLI_CACHE['at'] < ttl:
        return _CLAUDE_CLI_CACHE['ok']
    try:
        import llm_tailor as lt
        ok = bool(lt.find_claude())
    except Exception:
        ok = False
    _CLAUDE_CLI_CACHE.update(at=now, ok=ok)
    return ok

# ---- autostart (launch dashboard at login) ----
def autostart_state():
    if mau is None:
        return {'desired': False, 'enabled': False, 'supported': False, 'error': 'autostart module unavailable'}
    return {'desired': bool(gui_settings().get('autostart', True)),
            'enabled': mau.is_enabled(), 'supported': mau.supported(), 'error': AUTOSTART_ERR}

def reconcile_autostart():
    """Make the OS launcher match the wanted setting. It defaults to on. It records any failure.

    The watchdog supersedes this launcher: it covers logon AND every N minutes after. When the
    watchdog is on, this function does nothing, so the two never both start a server and race
    for the port.
    """
    global AUTOSTART_ERR
    if mau is None or not mau.supported():
        return
    if bool(gui_settings().get('watchdog', False)):
        return
    desired = bool(gui_settings().get('autostart', True))
    try:
        if desired and not mau.is_enabled():
            ok, msg = mau.install(); AUTOSTART_ERR = '' if ok else msg
        elif not desired and mau.is_enabled():
            ok, msg = mau.uninstall(); AUTOSTART_ERR = '' if ok else msg
    except Exception as e:
        AUTOSTART_ERR = str(e)

# ---- watchdog (the OS restarts the dashboard, minimized, when it is not running) ----
# The dashboard only owns the SWITCH. All the OS work lives in make_watchdog.py - which picks
# Task Scheduler on Windows and launchd on macOS - and the check itself in watchdog.py, so the
# feature keeps working with no dashboard and no Claude. Nothing here branches on the platform:
# the state below carries 'supported' and 'mechanism', and the page reads those.
def watchdog_state():
    if mwd is None:
        return {'desired': False, 'enabled': False, 'supported': False, 'everyMin': 5,
                'pauseMin': 0, 'task': '', 'mechanism': '',
                'error': 'watchdog module unavailable'}
    s = gui_settings()
    try:    every = int(s.get('watchdogEveryMin', 5) or 5)
    except (TypeError, ValueError): every = 5
    try:    pause = max(0, int((float(s.get('watchdogPauseUntil') or 0) - time.time()) / 60))
    except (TypeError, ValueError): pause = 0
    return {'desired': bool(s.get('watchdog', False)), 'enabled': mwd.is_enabled(),
            'supported': mwd.supported(), 'everyMin': every, 'pauseMin': pause,
            'task': mwd.TASK_NAME, 'mechanism': mwd.MECHANISM, 'error': WATCHDOG_ERR}

# The watchdog moved into the nav chip's bubble (it is the setting that CAUSES the missed runs
# the chip counts), so /api/sched-status now carries it — and that endpoint is polled once a
# minute by every open tab. mwd.is_enabled() shells out to schtasks.exe (or launchctl on a
# Mac), which is far too much process-spawning for a fact that only changes when the user flips
# the switch. Cached briefly;
# the toggle clears the cache, so the bubble never lags behind the switch it points at.
_WD_CACHE = {'at': 0.0, 'val': None}
_WD_TTL = 45.0

def watchdog_state_cached():
    now = time.time()
    if _WD_CACHE['val'] is None or (now - _WD_CACHE['at']) > _WD_TTL:
        _WD_CACHE['val'] = watchdog_state()
        _WD_CACHE['at'] = now
    return _WD_CACHE['val']

def watchdog_cache_clear():
    _WD_CACHE['at'] = 0.0

def reconcile_watchdog():
    """Make the scheduled job match the wanted setting. It defaults to OFF: registering a
    task with someone's operating system is not something to do unless they asked."""
    global WATCHDOG_ERR
    if mwd is None or not mwd.supported():
        return
    s = gui_settings()
    desired = bool(s.get('watchdog', False))
    try:
        if desired and not mwd.is_enabled():
            ok, msg = mwd.install(int(s.get('watchdogEveryMin', 5) or 5)); WATCHDOG_ERR = '' if ok else msg
        elif not desired and mwd.is_enabled():
            ok, msg = mwd.uninstall(); WATCHDOG_ERR = '' if ok else msg
    except Exception as e:
        WATCHDOG_ERR = str(e)

# ---- /dev: developer page (restart + read-only diagnostics) --------------------
# Why this exists: the HTML/CSS pages are re-read from disk on every request and every response
# is sent no-store, so a browser refresh already picks up a .html or .css edit. A .py edit does
# NOT — that code is loaded into this process. Before /dev the only way to pick one up was to
# leave the browser, kill the process and start it again. /dev does that from the page.
#
# READ-ONLY except for the restart. Nothing here writes to the tracker, to notes/answers, or to
# any other file the user owns: this page answers questions, it does not change state.
LOG_FILE = os.path.join(HERE, 'logs', 'execution.log')   # runlog.py writes here (src/lib/runlog.py)

def log_tail(lines=20):
    """Last N lines of logs/execution.log, oldest first. Cheap: reads the tail, not the file.

    Bounded read (64 KB) because this log grows for the life of the install and the /dev page
    polls it; slurping the whole file to show 20 lines would get slower every week."""
    try:
        size = os.path.getsize(LOG_FILE)
        with open(LOG_FILE, 'rb') as f:
            f.seek(max(0, size - 64 * 1024))
            raw = f.read().decode('utf-8', 'replace')
        if size > 64 * 1024:
            raw = raw.split('\n', 1)[-1]        # drop the partial first line the seek cut in half
        return [ln for ln in raw.splitlines() if ln.strip()][-lines:]
    except Exception as e:
        return ['(no log yet: %s)' % e]

def dev_info():
    """Everything you ask when the app is behaving oddly, in one payload. All best-effort:
    a broken sub-answer must not take the whole page down, so each block has its own try."""
    try:    droot = data_root_info()
    except Exception as e: droot = {'path': '(unresolved: %s)' % e, 'sourceLabel': '', 'split': False}
    try:    wdog = watchdog_state()
    except Exception: wdog = {}
    return {
        'pid': os.getpid(),
        'boot': BOOT_TS,
        'bootLabel': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(BOOT_TS)),
        'uptimeSec': int(time.time() - BOOT_TS),
        'port': PORT,
        'label': instance_label(),
        'python': sys.executable or '(embedded)',
        'pyVersion': sys.version.split()[0],
        'argv': ' '.join(sys.argv[1:]) or '(none)',
        'codeDir': HERE,
        'webDir': WEB,
        'dataRoot': droot.get('path', ''),
        'dataRootWhy': droot.get('sourceLabel', ''),
        'dataSplit': bool(droot.get('split')),
        'schedulerDisabled': scheduler_disabled(),
        'schedulerLoaded': sched is not None,
        'watchdogOn': bool(wdog.get('desired')) and bool(wdog.get('enabled')),
        'watchdogEveryMin': wdog.get('everyMin', 0),
        'logFile': LOG_FILE,
        'log': log_tail(20),
    }

def dev_restart(delay=0.4):
    """Replace this process with a fresh one. Returns nothing — it does not come back.

    Order matters and is the whole trick:
      1. the caller has ALREADY been answered (see the route). Once step 3 runs there is no
         socket left to reply on, so anything not said by now is never said.
      2. wait a moment so that response actually reaches the browser.
      3. server_close() releases the listening socket. The child cannot bind the port while
         this process still holds it, so this must happen BEFORE the launch, not after.
      4. launch the replacement, carrying --port forward explicitly: a staging copy started
         with `--port=8766` must not come back on 8765 (resolve_port reads argv first, and
         the child gets a fresh argv).
      5. os._exit — not sys.exit. The scheduler and any in-flight worker threads are still
         running; a normal exit waits on them and a raised SystemExit in this thread would
         just end the thread and leave a half-dead server holding nothing.
    """
    def go():
        time.sleep(delay)
        try:
            import runlog
            runlog.log('dashboard_server', 'OK', 'restart requested from /dev (pid %d, port %d)'
                       % (os.getpid(), PORT))
        except Exception:
            pass
        try:
            if SERVER_OBJ is not None:
                SERVER_OBJ.server_close()
        except Exception:
            pass
        # Reuse watchdog.py's launcher helpers so there is ONE answer to "how is this server
        # started" (pythonw so no console flashes; detached so it outlives us). If the import
        # fails the restart still happens, just in a plainer way.
        exe, flags = (sys.executable or 'python'), 0
        try:
            import watchdog as _wd
            exe, flags = _wd.python_exe(), _wd._no_window_flags()
        except Exception:
            pass
        try:
            kwargs = dict(cwd=HERE, close_fds=True, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.name == 'nt': kwargs['creationflags'] = flags
            else:               kwargs['start_new_session'] = True
            subprocess.Popen([exe, os.path.abspath(__file__), '--no-browser',
                              '--port=%d' % PORT], **kwargs)
        except Exception as e:
            try:
                import runlog
                runlog.log('dashboard_server', 'ERROR', 'restart failed to relaunch: %s' % e)
            except Exception:
                pass
        os._exit(0)
    threading.Thread(target=go, daemon=True).start()

# ---- persistence: apply the dashboard's marks to job_tracker.json + tracking.csv ----
def apply_marks(rows):
    """rows: [{id,status,statusDate,bookmarked,notes,answers}]. Update the durable stores."""
    tracker, ok = load_json_strict(JSON_PATH)
    if not ok:
        raise IOError('CAUTION: job_tracker.json is unreadable or corrupt. The app refuses to '
                      'save. A save replaces your applied, skipped, and bookmark data with '
                      'an empty file. Restore the file from git or from Backups first.')
    applied = tracker.setdefault('applied', {})
    skip = set(tracker.get('skipped_jobs', []))
    book = set(tracker.get('bookmarked', []))
    notes = tracker.setdefault('notes', {})
    answers = tracker.setdefault('answers', {})
    for r in rows:
        jid = (r.get('id') or '').strip()
        if not jid: continue
        st = (r.get('status') or '').strip().lower()
        if st == 'applied':
            applied[jid] = (r.get('statusDate') or '').strip() or time.strftime('%Y-%m-%d'); skip.discard(jid)
        elif st == 'skipped':
            skip.add(jid); applied.pop(jid, None)
        else:
            applied.pop(jid, None); skip.discard(jid)
        if str(r.get('bookmarked', '')).strip().lower() in ('yes', 'true', '1'): book.add(jid)
        else: book.discard(jid)
        if 'notes' in r: (notes.__setitem__(jid, r['notes']) if r['notes'] else notes.pop(jid, None))
        if 'answers' in r: (answers.__setitem__(jid, r['answers']) if r['answers'] else answers.pop(jid, None))
    tracker['applied'] = applied; tracker['skipped_jobs'] = sorted(skip); tracker['bookmarked'] = sorted(book)
    atomic_write(JSON_PATH, json.dumps(tracker, indent=2), verify_json=True)
    # keep tracking.csv current (it is the source of truth) by re-merging status into it
    _rewrite_tracking_csv(tracker)

# ---- the pending-job cap: state, and the two ways to clear a full board ----
# All the counting lives in backlog.py so the scheduler and this server read one implementation.
# A missing module reports an OFF cap, which pauses nothing and shows no banner.
BACKLOG_OFF = {'count': 0, 'cap': 0, 'enabled': False, 'over': False, 'paused': False, 'groups': []}

def _hourly_search_on():
    """Is the hourly search (the `radar` task) switched on?

    The board needs this for the second half of its paused bubble: an EMPTY board with the
    hourly search switched off is the other way new postings stop arriving, and it is just as
    silent as the cap — nothing errors, and the board simply stays empty. Defaults to True
    (= "on", so no bubble) whenever the answer is unknowable: a scheduler-less build must not
    accuse the user of switching off a task that does not exist here.
    """
    if sched is None:
        return True
    try:
        job = (sched.load_schedule().get('jobs') or {}).get('radar')
        return True if job is None else bool(job.get('enabled', True))
    except Exception:
        return True

def backlog_state():
    # `hourlyOn` rides along with the cap because the board's one bubble speaks for both
    # reasons the searches can go quiet, and reads them out of a single response.
    if bk is None:
        return dict(BACKLOG_OFF, hourlyOn=_hourly_search_on())
    try:
        return dict(bk.state(), hourlyOn=_hourly_search_on())
    except Exception:
        return dict(BACKLOG_OFF, hourlyOn=_hourly_search_on())

def _snapshot_before(tag):
    """Copy the state files a bulk action is about to rewrite, into Backups/snapshots/.

    Same convention as backup.py, but rooted in the DATA dir — backup.py still snapshots
    beside the CODE, which since the code/data split is not where job_tracker.json lives.
    This is a ROLLBACK net, not a record of the jobs: 'delete all' means the postings are
    gone from the board and the next scrape may find them again, exactly as chosen. It
    exists so one mis-click is undoable. Never fatal — a failed snapshot is logged and the
    caller decides, because refusing to act on an unwritable Backups folder would leave the
    user stuck with a full board and no way to clear it.
    """
    import shutil
    dest = D('Backups', 'snapshots', time.strftime('%Y%m%d_%H%M%S') + '_' + tag)
    os.makedirs(dest, exist_ok=True)
    for src in (JSON_PATH, TRACKING_CSV, CANDIDATES_CSV, TO_PROCESS):
        try:
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
        except Exception:
            pass
    return dest

def _filter_csv(path, drop):
    """Rewrite a CSV without the given ids. Returns how many rows went. Leaves an EMPTY file
    with its header intact rather than deleting it — every reader here treats a missing file
    and an empty one the same, but a header-only file keeps the column order for the next
    writer, and it makes 'this was emptied' distinguishable from 'this vanished'."""
    rows = load_csv(path)
    if not rows:
        return 0
    keep = [r for r in rows if (r.get('id') or '').strip() not in drop]
    if len(keep) == len(rows):
        return 0
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols); w.writeheader(); w.writerows(keep)
    atomic_write(path, buf.getvalue())
    return len(rows) - len(keep)

def delete_jobs(ids):
    """Remove jobs from the board outright — the 'I do not need these' half of the banner.

    PURE DELETE, no memory (the user's choice): nothing records that these ids existed, so a
    later scrape is free to find the same postings again. That is the trade against 'Skip all',
    which keeps every posting and its details and is the option to reach for when the record
    matters. Everything keyed by job id goes with the row — details, notes, answers, bookmark,
    queue entry — or the next rebuild would re-materialise a half-job from the leftovers.
    """
    drop = {str(i).strip() for i in (ids or []) if str(i).strip()}
    if not drop:
        return {'deleted': 0, 'snapshot': ''}
    tracker, ok = load_json_strict(JSON_PATH)
    if not ok:
        raise IOError('CAUTION: job_tracker.json is unreadable or corrupt. The app refuses to '
                      'delete. Restore the file from git or from Backups first.')
    snap = _snapshot_before('delete')
    n = _filter_csv(TRACKING_CSV, drop) + _filter_csv(CANDIDATES_CSV, drop)
    for key in ('job_details', 'notes', 'answers', 'applied'):
        d = tracker.get(key)
        if isinstance(d, dict):
            for jid in drop:
                d.pop(jid, None)
    for key in ('bookmarked', 'skipped_jobs'):
        lst = tracker.get(key)
        if isinstance(lst, list):
            tracker[key] = sorted(set(lst) - drop)
    atomic_write(JSON_PATH, json.dumps(tracker, indent=2), verify_json=True)
    # The tailoring queue is a list of ids; a deleted job left in it would be built into a
    # resume for a posting that is no longer on the board.
    try:
        q = load_json(TO_PROCESS, {}) or {}
        if isinstance(q, dict):
            before = list(q.get('ids', []) or [])
            q['ids'] = [i for i in before if i not in drop]
            if isinstance(q.get('notes'), dict):
                for jid in drop:
                    q['notes'].pop(jid, None)
            if len(q['ids']) != len(before):
                atomic_write(TO_PROCESS, json.dumps(q, indent=2), verify_json=True)
    except Exception:
        pass
    return {'deleted': n, 'snapshot': os.path.basename(snap)}

def _rewrite_tracking_csv(tracker):
    rows = load_csv(TRACKING_CSV)
    if not rows: return
    applied = tracker.get('applied', {}); skip = set(tracker.get('skipped_jobs', []))
    book = set(tracker.get('bookmarked', [])); notes = tracker.get('notes', {}); answers = tracker.get('answers', {})
    for r in rows:
        jid = r.get('id', '')
        r['status'] = 'applied' if jid in applied else 'skipped' if jid in skip else 'pending'
        r['statusDate'] = applied.get(jid, '') if jid in applied else ''
        r['bookmarked'] = 'yes' if jid in book else 'no'
        if jid in notes: r['notes'] = notes[jid]
        if jid in answers: r['answers'] = answers[jid]
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols); w.writeheader(); w.writerows(rows)
    atomic_write(TRACKING_CSV, buf.getvalue())

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json', extra=None):
        if isinstance(body, str): body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass
    def log_message(self, *a): pass

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path
        if p == '/ui.css':
            # Shared design system for every page (see ui.css). Served from the CODE dir.
            return self._send(200, read_bytes(W('ui.css')), 'text/css; charset=utf-8')
        if p == '/favicon.ico' or p.startswith('/assets/icon/'):
            # The Top Rat mark, served from the CODE dir (assets/icon/) so the browser tab,
            # the nav brand and the desktop window (app.py ICON) all show the same drawing.
            # basename() only — this is the one route reading a caller-named file out of
            # assets/, so it must not accept a path.
            name = 'job-agent-icon.ico' if p == '/favicon.ico' else os.path.basename(p)
            data = read_bytes(P('assets', 'icon', name))
            if not data:
                return self._send(404, '{"error":"no such icon"}')
            ct = ('image/x-icon' if name.endswith('.ico') else
                  'image/svg+xml' if name.endswith('.svg') else 'image/png')
            return self._send(200, data, ct)
        if p in ('/', '/index.html', '/cards', '/cards.html', '/table', '/job_tracker.html'):
            # ONE board page, TWO views. cards.html renders either the card column or the
            # table off the same /api/jobs data and the same filter/sort state, so switching
            # is a client-side toggle with no reload and no way for the two to disagree.
            #
            # /table and /job_tracker.html are kept as direct entry points for old bookmarks;
            # the page reads its starting view from the path. The classic GENERATED
            # job_tracker.html (HTML_PATH, in the data root) is no longer served — it baked
            # the job rows into the file as a JS array and only refreshed on `jobpipe rebuild`,
            # so it can disagree with the card board between rebuilds. It is still written
            # by tracker.py/dashboard_build.py (the Simplify sync reads SIMPLIFY_APPLIED back
            # out of it), so nothing downstream changes.
            return self._send(200, inject_nav(read_bytes(W('cards.html')), 'tracker'),
                              'text/html; charset=utf-8')
        if p in ('/queue', '/queue.html'):
            # The Tailoring queue is the BOARD in queue mode, not a second page: cards.html reads
            # location.pathname, locks itself to table view + queued jobs, hides the controls that
            # could unlock that, and shows the Run-now bar. One implementation of the job list, the
            # row expander and the detail card — nothing here to drift from the board.
            return self._send(200, inject_nav(read_bytes(W('cards.html')), 'queue'),
                              'text/html; charset=utf-8')
        if p in ('/setup', '/setup.html'):
            return self._send(200, inject_nav(read_bytes(W('setup.html')), 'settings'), 'text/html; charset=utf-8')
        if p in ('/schedule', '/schedule.html'):
            return self._send(200, inject_nav(read_bytes(W('schedule.html')), 'schedule'), 'text/html; charset=utf-8')
        if p in ('/archived', '/archived.html'):
            return self._send(200, inject_nav(read_bytes(W('archived.html')), 'archived'), 'text/html; charset=utf-8')
        if p in ('/help/adzuna', '/help/adzuna.html'):
            # Static "how to set up Adzuna" page linked from the salary hover hint.
            return self._send(200, inject_nav(read_bytes(W('help_adzuna.html')), 'settings'), 'text/html; charset=utf-8')
        if p in ('/help/claude-code', '/help/claude_code.html', '/help/claude-key'):
            # Static "set up Claude Code for tailoring" page, linked from Settings and from
            # the not-available notice in the tracker's tailoring block. The old
            # /help/claude-key path is kept as an alias so a bookmark still lands somewhere.
            return self._send(200, inject_nav(read_bytes(W('help_claude_code.html')), 'settings'), 'text/html; charset=utf-8')
        if p in ('/help/ntfy', '/help/ntfy.html'):
            # Static "what ntfy is / how to set it up" page linked from Settings step 4.
            return self._send(200, inject_nav(read_bytes(W('help_ntfy.html')), 'settings'), 'text/html; charset=utf-8')
        if p in ('/dev', '/dev.html'):
            # Hidden developer page. Deliberately NOT in NAV_ITEMS — you reach it by typing the
            # URL. It is a testing tool, not a feature of the app, and a tab for it would be one
            # more thing a non-technical user can land on and worry about.
            # 'dev' as the active key suppresses the first-run onboarding redirect (inject_nav)
            # without lighting up the Settings tab.
            return self._send(200, inject_nav(read_bytes(W('dev.html')), 'dev'),
                              'text/html; charset=utf-8')
        if p == '/api/dev/ping':
            # Liveness + process identity. The restart flow polls this and waits for a pid/boot
            # DIFFERENT from the one it started with: "the port answers" on its own would match
            # the old process in the moment before it exits, and the page would reload too early.
            # codeDir identifies WHICH copy answered, so a starting server can tell "I am already
            # running here" from "another copy (or another program) is sitting on my port".
            return self._send(200, json.dumps({'ok': True, 'pid': os.getpid(), 'boot': BOOT_TS,
                                               'codeDir': HERE, 'port': PORT}))
        if p == '/api/dev/info':
            return self._send(200, json.dumps(dev_info()))
        if p == '/api/archived':
            return self._send(200, json.dumps({'served': True, 'archived': load_csv(D('archived.csv'))}))
        if p == '/archived-resume':
            fname = (parse_qs(parsed.query).get('file') or [''])[0]
            row = next((r for r in load_csv(ARCHIVE_CSV) if r.get('resumeFile') == fname), None)
            if not row:
                return self._send(404, '{"error":"The archive index has no such resume."}')
            zp = P(*[s for s in (row.get('zipFile') or '').split('/') if s not in ('..', '')])
            inner = row.get('pathInZip') or fname
            try:
                with zipfile.ZipFile(zp) as z:
                    data = z.read(inner)
                ct = 'application/pdf' if inner.lower().endswith('.pdf') else \
                     ('application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                      if inner.lower().endswith('.docx') else 'application/octet-stream')
                return self._send(200, data, ct)
            except Exception as e:
                return self._send(404, json.dumps({'error': 'The app did not extract the file from the archive: ' + str(e)}))
        if p == '/api/schedule':
            if sched is None:
                return self._send(500, '{"error":"scheduler not available"}')
            return self._send(200, json.dumps({'config': sched.load_schedule(), 'status': sched.status_all()}))
        if p == '/api/sched-status':
            # Status only — no config. The nav pill on EVERY page polls this on a timer, so it
            # stays cheap; /api/schedule additionally serializes the whole job config, which the
            # pill never reads. 200 with available:false (not 500) so a build with the scheduler
            # disabled just hides the pill instead of logging an error every few seconds.
            if sched is None:
                return self._send(200, json.dumps({'available': False}))
            st = sched.status_all()
            st['available'] = True
            # Rides along because the chip's bubble is now the only place the watchdog warning
            # appears, and a second fetch per poll per tab to say "still off" is not worth it.
            st['watchdog'] = watchdog_state_cached()
            return self._send(200, json.dumps(st))
        if p == '/api/autostart':
            return self._send(200, json.dumps(autostart_state()))
        if p == '/api/watchdog':
            return self._send(200, json.dumps(watchdog_state()))
        if p == '/api/salary-quota':
            # Read-only Adzuna quota state so the tracker can warn on load (not just after a click).
            try:
                out = subprocess.run([sys.executable, _paths.script('salary_probe.py'), '--quota', '--json'],
                                     cwd=HERE, capture_output=True, text=True, timeout=30).stdout
                return self._send(200, out or json.dumps({'blocked': False, 'note': ''}))
            except Exception as e:
                return self._send(200, json.dumps({'blocked': False, 'note': '', 'error': str(e)}))
        if p == '/api/adzuna':
            # Adzuna creds status for the Settings card. Never returns the raw app_key
            # (secret) — only whether one is set + a last-4 hint for the user to confirm.
            # NOTE: never name this `cfg` — a local assignment anywhere in do_GET makes
            # pipelib's module-level cfg() local to the WHOLE method, which silently broke
            # /api/profile-status below (UnboundLocalError -> swallowed -> configured:false
            # forever -> every page bounced back to the wizard).
            az = load_json(_pl_cfg('adzuna.json'), {}) or {}
            aid = (az.get('app_id') or '').strip()
            akey = (az.get('app_key') or '').strip()
            return self._send(200, json.dumps({
                'app_id': aid, 'keySet': bool(akey),
                'keyHint': ('…' + akey[-4:]) if len(akey) >= 4 else '',
                'configured': bool(aid and akey)}))
        if p == '/api/claude-cli':
            # Is unattended tailoring available? Tailoring runs through the Claude Code CLI
            # (`claude -p`), which bills against the user's Claude PLAN — there is no API key
            # in this app and nothing metered to buy. This route only reports whether the
            # binary can be found; a logged-out CLI is discovered at call time and falls back
            # to the template copy. Locals here are `lt`/`binp`, never `cfg` — see the rule
            # in the Adzuna block above.
            try:
                import llm_tailor as lt
                binp = lt.find_claude()
                envkey = bool((os.environ.get('ANTHROPIC_API_KEY') or '').strip())
                return self._send(200, json.dumps({
                    'available': bool(binp), 'binary': binp or '',
                    # Reported so the UI can reassure rather than alarm: llm_tailor strips
                    # this variable from the child process, so the plan is still what pays.
                    'apiKeyInEnv': envkey}))
            except Exception as e:
                return self._send(200, json.dumps({'available': False, 'binary': '',
                                                   'apiKeyInEnv': False, 'error': str(e)}))
        if p == '/api/agent-prompts':
            # The two scheduled-task prompts, personalized, for the Copy buttons on Settings.
            # Always 200 with a list: a missing agent_prompts/ file yields an empty `prompt`
            # and the UI disables that one card, rather than the whole section erroring out.
            try:
                out = [dict(a, prompt=render_agent_prompt(a['file'])) for a in AGENT_PROMPTS]
                return self._send(200, json.dumps({'prompts': out, 'projectDir': HERE}))
            except Exception as e:
                return self._send(200, json.dumps({'prompts': [], 'projectDir': HERE,
                                                   'error': str(e)}))
        if p == '/api/data-root':
            # Where this user's settings + tracker data live, and how that was decided.
            # Read-only here; POST writes the override into config/instance.json.
            try:
                return self._send(200, json.dumps(data_root_info()))
            except Exception as e:
                return self._send(200, json.dumps({'error': str(e), 'path': _DATA}))
        if p == '/api/queue':
            # Read side of to_process.json for the Tailoring queue page: the ORDER the ids were
            # queued in and the notes the user typed. Deliberately does NOT join in job data —
            # the page reads /api/jobs for that, so a queue row and a board card can never show
            # two different versions of the same job. Read-only; the POST below writes.
            q = load_json(TO_PROCESS, {}) or {}
            ids = [i for i in q.get('ids', []) if i]
            notes = q.get('notes') if isinstance(q.get('notes'), dict) else {}
            return self._send(200, json.dumps({
                'ok': True, 'ids': ids, 'count': len(ids),
                # Only notes for ids still queued: a note survives an unqueue in the file (by
                # design, so a re-queue keeps it), but showing one for a job that is not in the
                # list would be a row the page cannot render.
                'notes': {k: v for k, v in notes.items() if k in set(ids)},
                'requestedAt': q.get('requestedAt', ''), 'runNow': bool(q.get('runNow'))}))
        if p == '/api/settings':
            return self._send(200, json.dumps(gui_settings()))
        if p == '/api/backlog':
            # "How full is the board, and are the searches paused?" Read-only, cheap (three
            # files and some arithmetic), and the SAME backlog.state() the scheduler gates on —
            # so the banner can never claim the searches are paused while they are still firing.
            return self._send(200, json.dumps({'ok': True, **backlog_state()}))
        if p == '/api/profile-status':
            # Deterministic first-run check for the onboarding nudge: "configured" means
            # config/profile.json exists AND has a non-empty identity.full_name (the
            # example-fallback profile in load_profile() does not count).
            configured = False
            try:
                prof = load_json(cfg('profile.json'), None)
                configured = bool(prof and (prof.get('identity') or {}).get('full_name', '').strip())
            except Exception:
                configured = False
            active = pl.get_active_profile() if pl else ''
            return self._send(200, json.dumps({'configured': configured, 'activeProfile': active}))
        if p == '/api/profile':
            if pl is None:
                return self._send(500, '{"error":"profile_lib not available"}')
            return self._send(200, json.dumps(pl.load_profile(clean=True)))
        if p == '/api/geocode':
            if pl is None:
                return self._send(500, '{"error":"profile_lib not available"}')
            q = (parse_qs(parsed.query).get('q') or [''])[0]
            try:
                return self._send(200, json.dumps(pl.geocode_city(q)))
            except Exception as e:
                return self._send(400, json.dumps({'error': str(e)}))
        if p == '/tracking.csv':
            return self._send(200, read_bytes(TRACKING_CSV), 'text/csv; charset=utf-8')
        if p == '/candidates.csv':
            return self._send(200, read_bytes(CANDIDATES_CSV), 'text/csv; charset=utf-8')
        if p == '/api/data':
            tracking = load_csv(TRACKING_CSV)
            candidates = load_csv(CANDIDATES_CSV)
            tj = load_json(JSON_PATH, {}) or {}
            # Ghost-risk is ADVISORY. Computed here for display only; nothing downstream
            # reads it. Scored over both views so every visible row can get a badge.
            ghost = {}
            if gr is not None:
                try:
                    ghost = gr.score_all(candidates + tracking, tj.get('job_details', {}))
                except Exception:
                    ghost = {}
            # Repost recognition is ADVISORY too — display-only, never excludes a job.
            repost = {}
            if rp is not None:
                try:
                    repost = rp.annotate(candidates + tracking, tj.get('job_details', {}),
                                         tj.get('applied', {}), set(tj.get('skipped_jobs', [])),
                                         tj.get('notes', {}))
                except Exception:
                    repost = {}
            # Resolved salary + source for every job (job_details → posted → cache → band),
            # so a value shows for all rows and a probed salary survives refresh (it's read
            # from job_details here, not from the stale embedded/CSV salary). Script path.
            salaries = {}
            try:
                so = subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salaries'],
                                    cwd=HERE, capture_output=True, text=True, timeout=30)
                salaries = json.loads(so.stdout or '{}')
            except Exception:
                salaries = {}
            return self._send(200, json.dumps({
                'served': True,
                'tracking': tracking,
                'candidates': candidates,
                'queued': (load_json(TO_PROCESS, {}) or {}).get('ids', []),
                'skipped': tj.get('skipped_jobs', []),
                'applied': tj.get('applied', {}),
                'ghostRisk': ghost,
                'ghostCaveat': (gr.CAVEAT if gr is not None else ''),
                'repost': repost,
                'repostCaveat': (rp.CAVEAT if rp is not None else ''),
                'salaries': salaries,
                'blocklist': (bl.load_blocklist().get('employers', {}) if bl is not None else {}),
                'settings': gui_settings()}))
        if p == '/api/jobs':
            # Merged, display-ready job list for the card UI (cards.html). ADDITIVE / read-only:
            # reuses the same live stores as /api/data but also surfaces location (from
            # job_details) and a derived Remote/Hybrid/Onsite tag. Touches no CSV schema and
            # leaves the table board (job_tracker.html) untouched.
            tj = load_json(JSON_PATH, {}) or {}          # DATA root, not the code dir
            jd = tj.get('job_details', {})
            applied = tj.get('applied', {})
            skipped = set(tj.get('skipped_jobs', []))
            book = set(tj.get('bookmarked', []))
            notes = tj.get('notes', {})
            # Per-job questionnaire (written by the LLM tailoring pass into job_details) + the
            # user's saved answers (durable in job_tracker.json, exported to tracking.csv).
            # These were only ever rendered on the classic table; the card board reads them here.
            answers = tj.get('answers', {})
            def _quest(det):
                q = det.get('questionnaire', []) if isinstance(det, dict) else []
                if not isinstance(q, list):
                    return []
                # Entries are plain strings. Older, hand-written entries can be {q:…} or {question:…}.
                out_q = []
                for item in q:
                    t = item if isinstance(item, str) else (
                        (item.get('q') or item.get('question') or '') if isinstance(item, dict) else '')
                    t = str(t).strip()
                    if t: out_q.append(t)
                return out_q
            tracking = load_csv(TRACKING_CSV)
            candidates = load_csv(CANDIDATES_CSV)
            # Resolved salary per job (same script path as /api/data): {id:{value,source,kind}}
            salaries = {}
            try:
                so = subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salaries'],
                                    cwd=HERE, capture_output=True, text=True, timeout=30)
                salaries = json.loads(so.stdout or '{}')
            except Exception:
                salaries = {}
            # Advisory repost annotation (display-only, never excludes) — mirrors /api/data.
            repost = {}
            if rp is not None:
                try:
                    repost = rp.annotate(tracking, jd, applied, skipped, notes)
                except Exception:
                    repost = {}
            # Blocklist is keyed by a NORMALIZED company name — match via norm_employer, not a raw
            # `company in dict` check (which silently misses every banned employer).
            blocked_keys = set()
            if bl is not None:
                try:
                    blocked_keys = set(bl.load_blocklist().get('employers', {}).keys())
                except Exception:
                    blocked_keys = set()
            def _is_blocked(co):
                if bl is None or not co:
                    return False
                try:
                    return bl.norm_employer(co) in blocked_keys
                except Exception:
                    return False
            # Advisory ghost-job risk (display-only, nothing downstream reads it) — mirrors /api/data.
            ghost = {}
            if gr is not None:
                try:
                    ghost = gr.score_all(tracking + candidates, jd)
                except Exception:
                    ghost = {}
            # The USER's OWN ghost flags — recorded, not guessed, so they are a separate field from
            # `ghost` above. Counted per employer (normalized the same way a ban is) because the
            # board offers a ban once a second posting from one employer gets flagged.
            gflags, gcounts, gdeclined = set(), {}, set()
            if gf is not None:
                try:
                    gflags = gf.flagged_ids()
                    gcounts = gf.employer_counts()
                    gdeclined = gf.declined_employers()
                except Exception:
                    gflags, gcounts, gdeclined = set(), {}, set()

            def _gkey(co):
                if not co:
                    return ''
                try:
                    return (gf or bl).norm_employer(co)
                except Exception:
                    return ''
            # Ids currently sitting in the tailoring queue → the card's "queued" badge, plus the
            # per-job CUSTOMIZATION notes typed in the Tailor dialog. The notes are kept in
            # to_process.json even after a job is unqueued, so the card can show them back for
            # editing / clearing before a re-queue (see /api/queue).
            _q = load_json(TO_PROCESS, {}) or {}
            queued_ids = set(_q.get('ids', []) or [])
            queue_notes = _q.get('notes', {}) if isinstance(_q.get('notes'), dict) else {}

            # Why the last tailoring run produced no resume for a job (tailor_local writes it).
            # See _tailor_err below for the two suppression rules.
            _terrs = load_json(TAILOR_ERRORS, {}) or {}
            if not isinstance(_terrs, dict): _terrs = {}

            def _tailor_err(jid, has_resume, tname, texists, sm):
                """One error record per job, or None — the board's single error tag reads this.

                Two sources, in this order:
                1. A RECORDED failure from the last queue run. Suppressed once the job has a
                   resume, because the file on disk is the newer fact — the Cowork agent may
                   have built what the script path could not. `pdf_failed` is the exception:
                   the .docx exists, so the record IS about a job that has a resume.
                2. A PREDICTED failure: no resume yet, the job is template-bound, and the base
                   file it needs is not in resume_template/. This one is knowable before the
                   queue runs, and saying so early is the whole point — the fix (add the file)
                   takes longer than the run does.
                """
                rec = _terrs.get(jid)
                if isinstance(rec, dict) and rec.get('code'):
                    if not has_resume or rec.get('code') == 'pdf_failed':
                        return {'code': rec.get('code', ''), 'message': rec.get('message', ''),
                                'detail': rec.get('detail', ''), 'at': rec.get('at', ''),
                                'stage': rec.get('stage', ''), 'predicted': False}
                if has_resume or texists:
                    return None
                # SAME number the card UI gets as CUSTOMIZE_THR (see /api/settings), so the tag
                # and the board's Templated/None split can never disagree about this job.
                try: thr = float(gui_settings().get('resumeCustomizeThreshold', 0.8) or 0.8)
                except Exception: thr = 0.8
                if (sm or 0) < thr:
                    return None          # below the threshold this job gets an AI resume, not a template
                # Two different predictions. An empty (or unresolvable) resume_template/ means no
                # file was ever chosen, so there is no name to print — say the folder is empty
                # instead of inventing one, which is what this used to do.
                if not tname:
                    return {'code': 'template_none',
                            'message': 'resume_template/ holds no base resume, so the tailoring '
                                       'queue has nothing to copy for this job. Add a base resume '
                                       'to that folder.',
                            'detail': '', 'at': '', 'stage': 'template', 'predicted': True}
                return {'code': 'template_missing',
                        'message': 'This job needs the base resume %s, but that file is not in '
                                   'resume_template/. Add the file, or the tailoring queue cannot '
                                   'build this resume.' % tname,
                        'detail': tname, 'at': '', 'stage': 'template', 'predicted': True}

            def _res_exists(rel):
                # Résumés are written beside the CODE (tailor_local writes New/<Co>/), but a
                # relocated data root can hold them instead. Test both before you name a file.
                parts = [s for s in str(rel).replace('\\', '/').split('/') if s not in ('..', '')]
                if not parts:
                    return False
                return os.path.isfile(P(*parts)) or os.path.isfile(D(*parts))

            def _resume_view(rp_path):
                # Preview target for the card's résumé overlay: the PDF twin is the only thing a
                # browser renders inline, so a docx-only job gets a download link and no preview.
                if not rp_path:
                    return '', ''
                stem = rp_path.rsplit('.', 1)[0]
                pdf, docx = stem + '.pdf', stem + '.docx'
                return (pdf if _res_exists(pdf) else ''), (docx if _res_exists(docx) else '')
            def _loc_type(loc):
                l = (loc or '').lower()
                if 'remote' in l: return 'Remote'
                if 'hybrid' in l: return 'Hybrid'
                return 'Onsite' if l.strip() else ''
            _strat = None
            if loc_mod is not None:
                try:
                    _strat = loc_mod._merged_strategy()
                except Exception:
                    _strat = None
            def _is_local(loc):
                # Offline/cache-only locality verdict (keyword + cached geo) for the card
                # board's local filter. Never networks; False when the module is missing.
                if loc_mod is None or not loc:
                    return False
                try:
                    return bool(loc_mod.is_local(loc, _strat))
                except Exception:
                    return False
            def _emp_type(det, role, loc, sal):
                # A stored label (persisted when a description was fetched) wins; otherwise
                # detect deterministically from the title/location/description on hand. Empty
                # for a normal full-time W2 role, so only non-default arrangements get a badge.
                stored = (det.get('employmentType') or '').strip() if isinstance(det, dict) else ''
                if stored:
                    return stored
                if plib is None:
                    return ''
                try:
                    return plib.detect_employment_type(role, loc, det.get('description', '') if isinstance(det, dict) else '', sal)
                except Exception:
                    return ''
            def _readable_desc(det):
                # The board ships stored descriptions to the drawer, which renders them
                # without asking /api/jobdesc. Anything that isn't a real posting (sign-in
                # wall, JS shell, redirect blob) is dropped here, so the drawer falls back to
                # the gated fetch and shows a reason instead of the page's metadata.
                s = (det.get('description', '') or '') if isinstance(det, dict) else ''
                if not s or jdmod is None:
                    return s
                try:
                    return '' if jdmod.assess(s, det.get('applyUrl', ''))[0] else s
                except Exception:
                    return s
            def _split_skills(s):
                return [x.strip() for x in (s or '').replace(';', ',').split(',') if x.strip()]
            def _template_for(role, skills):
                # The exact base-resume file that the pipeline picks from resume_template/, plus
                # the state of that file on disk. The folder can hold no base resume at all.
                if scr is None:
                    return '', False
                try:
                    c = scr.classify(role or '', skills or '')
                    base = c.get('baseResume', '')      # e.g. 'resume_template/<base>.docx'
                    name = os.path.basename(base)
                    # Resolve against pipelib.TEMPLATE_DIR — the SAME folder tailor_local copies
                    # from, which follows the DATA root. This used to test
                    # P(*base.split('/')), i.e. `resume_template/` beside the CODE: since the
                    # code/data split the templates live under the data root, so every job read
                    # as NO TEMPLATE and the board predicted template_missing for files that
                    # were on disk the whole time. (The split('/') was wrong twice over on
                    # Windows, where classify returns a backslash path and the split yields one
                    # unusable segment.) `_res_exists` below already knew to check both roots.
                    exists = bool(base) and plib is not None and os.path.isfile(
                        os.path.join(plib.TEMPLATE_DIR, name))
                    return name, exists
                except Exception:
                    return '', False
            out = []
            for r in tracking:
                jid = r.get('id', '')
                d = jd.get(jid, {})
                loc = d.get('location', '') or r.get('location', '')
                srec = salaries.get(jid) if isinstance(salaries, dict) else None
                sal = (srec.get('value', '') if isinstance(srec, dict) else (srec or '')) \
                    or r.get('salary', '') or d.get('salary', '')
                sal_est = bool((isinstance(srec, dict) and srec.get('kind') == 'rough')
                               or d.get('salaryEstimated')
                               or ('(est' in (sal or '').lower()))
                sal_src = (srec.get('source', '') if isinstance(srec, dict) else '') or d.get('salarySource', '')
                grec = ghost.get(jid) if isinstance(ghost, dict) else None
                tname, texists = _template_for(r.get('role', ''), '')
                missing = _split_skills(r.get('flaggedSkills', ''))
                if not missing and isinstance(d.get('flaggedSkills'), list):
                    missing = d.get('flaggedSkills')
                try:
                    sm = float(r.get('skillMatch') or d.get('skillMatch') or 0)
                except Exception:
                    sm = 0.0
                rpi = repost.get(jid) if isinstance(repost, dict) else None
                r_pdf, r_docx = _resume_view(r.get('resumePath', ''))
                out.append({
                    'id': jid, 'company': r.get('company', ''), 'role': r.get('role', ''),
                    'salary': sal, 'salaryEstimated': sal_est, 'salarySource': sal_src,
                    'employmentType': _emp_type(d, r.get('role', ''), loc, sal),
                    'description': _readable_desc(d),
                    'location': loc or 'Location', 'locationType': _loc_type(loc) or 'Location',
                    'isLocal': _is_local(loc),
                    'queued': jid in queued_ids,
                    'foundDate': r.get('foundDate', '') or d.get('foundDate', ''),
                    'source': r.get('source', '') or d.get('source', ''),
                    'applyUrl': r.get('applyUrl', '') or d.get('applyUrl', ''),
                    'skillMatch': sm, 'missingSkills': missing,
                    'status': ('applied' if jid in applied else 'skipped' if jid in skipped else 'pending'),
                    'statusDate': applied.get(jid, ''),
                    'bookmarked': jid in book, 'note': notes.get(jid, ''),
                    'questionnaire': _quest(d), 'answers': answers.get(jid, ''),
                    'resumePath': r.get('resumePath', ''), 'hasResume': bool(r.get('resumePath', '')),
                    'resumePdf': r_pdf, 'resumeDocx': r_docx,
                    'tailorNotes': queue_notes.get(jid, ''),
                    'candidate': False,
                    'templateName': tname, 'templateExists': texists,
                    'tailorError': _tailor_err(jid, bool(r.get('resumePath', '')), tname, texists, sm),
                    'ghost': (grec.get('level', '') if isinstance(grec, dict) else ''),
                    'ghostReasons': (grec.get('reasons', []) if isinstance(grec, dict) else []),
                    'ghostFlagged': jid in gflags,
                    'ghostFlagCount': gcounts.get(_gkey(r.get('company', '')), 0),
                    'ghostBanDeclined': _gkey(r.get('company', '')) in gdeclined,
                    'repost': bool(rpi),
                    'repostNote': (rpi.get('note', '') if isinstance(rpi, dict) else ''),
                    'blocked': _is_blocked(r.get('company', '')),
                })
            # Merge the fresh candidate pool (candidates.csv = not-yet-tailored jobs) so they show
            # as cards too. Deduped against the tracked board above (a candidate that's been tailored
            # already appears as a tracked row). Candidates carry their own location/repost columns.
            have = {j['id'] for j in out}
            for r in candidates:
                jid = r.get('id', '')
                if not jid or jid in have:
                    continue
                have.add(jid)
                d = jd.get(jid, {})
                loc = r.get('location', '') or d.get('location', '')
                srec = salaries.get(jid) if isinstance(salaries, dict) else None
                sal = (srec.get('value', '') if isinstance(srec, dict) else (srec or '')) \
                    or r.get('salary', '') or d.get('salary', '')
                sal_est = bool((isinstance(srec, dict) and srec.get('kind') == 'rough')
                               or d.get('salaryEstimated') or ('(est' in (sal or '').lower()))
                sal_src = (srec.get('source', '') if isinstance(srec, dict) else '') or d.get('salarySource', '')
                grec = ghost.get(jid) if isinstance(ghost, dict) else None
                tname, texists = _template_for(r.get('role', ''), r.get('requiredSkills', ''))
                try:
                    sm = float(r.get('skillMatch') or 0)
                except Exception:
                    sm = 0.0
                out.append({
                    'id': jid, 'company': r.get('company', ''), 'role': r.get('role', ''),
                    'salary': sal, 'salaryEstimated': sal_est, 'salarySource': sal_src,
                    'employmentType': _emp_type(d, r.get('role', ''), loc, sal),
                    'description': _readable_desc(d),
                    'location': loc or 'Location', 'locationType': _loc_type(loc) or 'Location',
                    'isLocal': _is_local(loc),
                    'queued': jid in queued_ids,
                    'foundDate': r.get('foundDate', '') or d.get('foundDate', ''),
                    'source': r.get('source', '') or d.get('source', ''),
                    'applyUrl': r.get('applyUrl', '') or d.get('applyUrl', ''),
                    'skillMatch': sm, 'missingSkills': _split_skills(r.get('flaggedSkills', '')),
                    'status': ('applied' if jid in applied else 'skipped' if jid in skipped else 'pending'),
                    'statusDate': applied.get(jid, ''),
                    'bookmarked': jid in book, 'note': notes.get(jid, ''),
                    'questionnaire': _quest(d), 'answers': answers.get(jid, ''),
                    'resumePath': '', 'hasResume': str(r.get('hasResume', '')).strip().lower() == 'yes',
                    'resumePdf': '', 'resumeDocx': '',
                    'tailorNotes': queue_notes.get(jid, ''),
                    'candidate': True,
                    'templateName': tname, 'templateExists': texists,
                    # Candidate rows carry no resumePath — hasResume is the yes/no column here.
                    'tailorError': _tailor_err(jid, str(r.get('hasResume', '')).strip().lower() == 'yes',
                                               tname, texists, sm),
                    'ghost': (grec.get('level', '') if isinstance(grec, dict) else ''),
                    'ghostReasons': (grec.get('reasons', []) if isinstance(grec, dict) else []),
                    'ghostFlagged': jid in gflags,
                    'ghostFlagCount': gcounts.get(_gkey(r.get('company', '')), 0),
                    'ghostBanDeclined': _gkey(r.get('company', '')) in gdeclined,
                    'repost': str(r.get('repost', '')).strip().lower() in ('yes', 'true', '1') or bool(r.get('repostNote')),
                    'repostNote': r.get('repostNote', ''),
                    'blocked': _is_blocked(r.get('company', '')),
                })
            _gs = gui_settings()
            return self._send(200, json.dumps({'served': True, 'jobs': out,
                               'repostCaveat': (rp.CAVEAT if rp is not None else ''),
                               'ghostCaveat': (gr.CAVEAT if gr is not None else ''),
                               # Is the Claude integration present on this machine? Derived from the
                               # Claude Code CLI, NOT from a saved preference: a separate opt-in
                               # switch could only ever be wrong — off with the CLI installed hid a
                               # working feature, on without it offered notes nothing would read.
                               # True => the card's Tailor button shows the notes field, since the
                               # notes are instructions for the LLM pass and are meaningless on the
                               # deterministic template-copy path.
                               'claudeEnabled': _claude_cli_present(),
                               # The pending-job cap rides along with the list it describes, so
                               # the banner's number and the board's Pending list come out of ONE
                               # response and cannot drift apart between two requests.
                               'backlog': backlog_state(),
                               'customizeThreshold': _gs.get('resumeCustomizeThreshold', 0.8)}))
        if p == '/api/jobdesc':
            # On-demand job-description fetch for the card drawer. Resolves the job's apply URL
            # and delegates to jobdesc.fetch (per-host JSON → JSON-LD → generic strip, cached).
            # A stored description in job_details wins. A future scrape can persist one. Script
            # path: zero tokens, graceful ok=False when a site is JS-only / blocks the fetch.
            if jdmod is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'jobdesc module unavailable'}))
            qs = parse_qs(parsed.query)
            jid = (qs.get('id') or [''])[0].strip()
            refresh = (qs.get('refresh') or ['0'])[0] in ('1', 'true', 'yes')
            tj = load_json(JSON_PATH, {}) or {}          # DATA root, not the code dir
            d = (tj.get('job_details', {}) or {}).get(jid, {})
            if d.get('description'):
                # A stored description can itself be a sign-in wall or a JS shell captured by
                # an older scrape. Run it through the same gate: renderable text is served as
                # before, junk falls through to a live fetch rather than being shown as the job.
                try:
                    _why, _note = jdmod.assess(d['description'], d.get('applyUrl', ''))
                except Exception:
                    _why, _note = '', ''
                if not _why:
                    return self._send(200, json.dumps({'ok': True, 'text': d['description'],
                        'source': 'stored', 'cached': True, 'url': d.get('applyUrl', ''),
                        'reason': '', 'message': _note, 'error': ''}))
            url = d.get('applyUrl', '')
            role = d.get('title', '') or d.get('role', '')
            loc = d.get('location', '')
            if not url or not role:
                for row in load_csv(TRACKING_CSV) + load_csv(CANDIDATES_CSV):
                    if row.get('id') == jid:
                        url = url or row.get('applyUrl', '')
                        role = role or row.get('role', '')
                        loc = loc or row.get('location', '')
                        if url:
                            break
            try:
                res = jdmod.fetch(url, refresh=refresh)
                # Persist a detected employment-type label (Contract/Part-time/…) into the
                # flexible job_details store so the card badge shows on the board without
                # re-opening. Deterministic + tiny (a short string, never the description blob),
                # so the zero-token script path is preserved.
                if res.get('ok') and res.get('text') and plib is not None and jid:
                    try:
                        label = plib.detect_employment_type(role, loc, res['text'], '')
                    except Exception:
                        label = ''
                    if label and label != d.get('employmentType'):
                        try:
                            tj2, ok2 = load_json_strict(JSON_PATH)
                            if ok2 and isinstance(tj2, dict):
                                jd2 = tj2.setdefault('job_details', {}).setdefault(jid, {})
                                jd2['employmentType'] = label
                                atomic_write(JSON_PATH, json.dumps(tj2, indent=2), verify_json=True)
                        except Exception:
                            pass
                return self._send(200, json.dumps(res))
            except Exception as e:
                return self._send(200, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e), 'url': url}))
        if p == '/api/blocklist':
            if bl is None:
                return self._send(500, json.dumps({'error': 'blocklist module unavailable'}))
            return self._send(200, json.dumps({
                'employers': [r for _, r in bl.blocked_employers()],
                'tactics': sorted(bl.TACTICS)}))
        # serve resume files so the dashboard's resume links open
        if any(p.startswith('/' + d + '/') for d in ('New', 'Applied', 'Skipped', 'Backups')):
            segs = [seg for seg in unquote(p).lstrip('/').split('/') if seg not in ('..', '')]
            # The app writes résumés beside the CODE. A relocated data root can hold them, so
            # try both roots (code first, matching where tailor_local writes).
            fp = next((c for c in (P(*segs), D(*segs)) if os.path.isfile(c)), '')
            if fp:
                ext = fp.rsplit('.', 1)[-1].lower()
                ct = {'pdf': 'application/pdf',
                      'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                      'csv': 'text/csv'}.get(ext, 'application/octet-stream')
                # inline so the card board's PDF preview renders in its iframe instead of
                # triggering a download
                extra = {'Content-Disposition': 'inline; filename="%s"' % segs[-1]} if ext == 'pdf' else None
                return self._send(200, read_bytes(fp), ct, extra)
        return self._send(404, '{"error":"not found"}')

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get('Content-Length', 0) or 0)
        try: body = json.loads(self.rfile.read(n) or b'{}')
        except Exception: body = {}
        if p == '/api/dev/restart':
            # Answer FIRST, act after — dev_restart() closes the socket this reply travels on.
            # The response carries the CURRENT pid so the page knows which process it is
            # waiting to see replaced.
            self._send(200, json.dumps({'ok': True, 'pid': os.getpid(), 'boot': BOOT_TS,
                                        'port': PORT}))
            dev_restart()
            return
        if p == '/api/save':
            try:
                apply_marks(body.get('rows', []))
                return self._send(200, '{"ok":true}')
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/backlog':
            # The two ways out of a full board, both driven entirely by the user pressing a
            # button on the banner. Nothing here runs on its own.
            #
            #   skip-all    -> mark every pending job skipped. The postings, their details and
            #                  their notes all stay; they move to the Skipped list, where they
            #                  remain searchable and can be set back to Pending one at a time.
            #   delete-all  -> remove them outright. No record is kept (see delete_jobs).
            #
            # The ids are resolved HERE from backlog.pending_ids(), not taken from the request:
            # the board may have been open for an hour, and a stale list from the page could
            # delete a job the user marked applied in the meantime. `expect` is the count the
            # banner showed; a mismatch means the board moved underneath the user, so the
            # action is refused and the page re-reads rather than acting on a stale number.
            if bk is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'backlog module unavailable'}))
            action = (body.get('action') or '').strip().lower()
            if action not in ('skip-all', 'delete-all'):
                return self._send(400, json.dumps({'ok': False,
                                                   'error': 'action must be skip-all or delete-all'}))
            try:
                ids = bk.pending_ids()
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': str(e)}))
            expect = body.get('expect')
            if isinstance(expect, int) and expect != len(ids):
                return self._send(409, json.dumps({
                    'ok': False, 'stale': True, 'count': len(ids),
                    'error': 'The board changed. There are now %d pending jobs, and not %d. '
                             'The app changed nothing. Reload the page and try again.' % (len(ids), expect)}))
            if not ids:
                return self._send(200, json.dumps({'ok': True, 'affected': 0, **backlog_state()}))
            try:
                if action == 'skip-all':
                    # `bookmarked` MUST be carried through. apply_marks reads that field off
                    # every row it is given and treats an absent one as "not bookmarked", so a
                    # bare {id,status} row silently un-bookmarks the job. Skipping is a bulk
                    # decision about the queue; a bookmark is a separate judgement the user made
                    # about one posting, and clearing 50 of them as a side effect would be
                    # unrecoverable and invisible.
                    book = set((load_json(JSON_PATH, {}) or {}).get('bookmarked', []) or [])
                    apply_marks([{'id': i, 'status': 'skipped',
                                  'bookmarked': 'yes' if i in book else 'no'} for i in ids])
                    out = {'affected': len(ids)}
                else:
                    res = delete_jobs(ids)
                    # `affected` counts JOBS, matching the number the button offered. `rows` is
                    # the CSV row count, which is larger whenever a job appears in both
                    # tracking.csv and candidates.csv — a detail, not the user's number.
                    out = {'affected': len(ids), 'rows': res['deleted'],
                           'snapshot': res['snapshot']}
                return self._send(200, json.dumps({'ok': True, 'action': action,
                                                   **out, **backlog_state()}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/block-employer':
            if bl is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'blocklist module unavailable'}))
            try:
                rec = bl.block_employer(body.get('company') or body.get('name', ''),
                                        reason=body.get('reason', ''),
                                        tactic=body.get('tactic', 'other'),
                                        example=body.get('example', ''))
                return self._send(200, json.dumps({'ok': True, 'record': rec}))
            except Exception as e:
                return self._send(400, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/ghost-flag':
            # The user's own "this looks like a ghost job" mark. Deliberately does NOT touch
            # status, the queue or any filter — it records the judgement and counts it against
            # the employer, so the board can offer a ban on the second flagged posting.
            # {decline:true} records a turned-down ban offer so we stop asking for that employer.
            if gf is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'ghostflags module unavailable'}))
            try:
                co = body.get('company', '')
                if body.get('decline'):
                    gf.decline_ban(co)
                    return self._send(200, json.dumps({'ok': True, 'declined': True,
                                                       'count': gf.flag_count(co)}))
                rec = gf.set_flag(body.get('id', ''), co, role=body.get('role', ''),
                                  flagged=bool(body.get('flagged', True)))
                return self._send(200, json.dumps({'ok': True, **rec}))
            except Exception as e:
                return self._send(400, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/unblock-employer':
            if bl is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'blocklist module unavailable'}))
            rec = bl.unblock_employer(body.get('name') or body.get('company', ''))
            return self._send(200, json.dumps({'ok': True, 'removed': bool(rec)}))
        if p == '/api/adzuna':
            # Save Adzuna creds from the Settings GUI into config/adzuna.json (git-ignored) —
            # the SAME file salary_probe.py reads, so the zero-Claude script path is unchanged.
            # app_key is only overwritten when a new value is supplied, so editing the app_id
            # alone never wipes a stored key; {clear:true} removes both.
            try:
                path = cfg_write('adzuna.json')
                az = load_json(path, {}) or {}          # not `cfg` — see the note in do_GET
                if body.get('clear'):
                    az['app_id'] = ''
                    az['app_key'] = ''
                else:
                    if 'app_id' in body:
                        az['app_id'] = (body.get('app_id') or '').strip()
                    new_key = (body.get('app_key') or '').strip()
                    if new_key:
                        az['app_key'] = new_key
                az['_readme'] = ('Adzuna API creds for salary_probe.py stage 2 (Jobsworth salary '
                                  'predictor). Managed from the dashboard Settings tab; free signup at '
                                  'https://developer.adzuna.com/signup. Git-ignored.')
                tmp = path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(az, f, indent=2); f.flush(); os.fsync(f.fileno())
                os.replace(tmp, path)
                aid = (az.get('app_id') or '').strip(); akey = (az.get('app_key') or '').strip()
                return self._send(200, json.dumps({'ok': True, 'configured': bool(aid and akey),
                                                   'app_id': aid, 'keySet': bool(akey)}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e)}))
        if p == '/api/claude-cli-test':
            # Live "can this machine tailor?" check. Delegates to llm_tailor.py --check,
            # which locates the CLI and asks it one tiny question, so the Settings verdict
            # and the terminal verdict come from the same code. There is nothing to save
            # here — authentication is the user's own `claude login`, not a key this app holds.
            try:
                r = subprocess.run([sys.executable, _paths.script('llm_tailor.py'), '--check'],
                                   cwd=HERE, capture_output=True, text=True, timeout=300)
                msg = (r.stdout or r.stderr or '').strip().splitlines()
                msg = msg[-1] if msg else 'No output from llm_tailor.py --check.'
                for pre in ('OK  ', 'FAIL  '):
                    if msg.startswith(pre):
                        msg = msg[len(pre):]
                return self._send(200, json.dumps({'ok': r.returncode == 0, 'message': msg}))
            except subprocess.TimeoutExpired:
                return self._send(200, json.dumps({'ok': False,
                                                   'message': 'The check timed out after 300s.'}))
            except Exception as e:
                return self._send(200, json.dumps({'ok': False, 'message': '%s: %s'
                                                   % (type(e).__name__, e)}))
        if p == '/api/pick-folder':
            # "Browse…" next to a folder field on /setup. Opens the OS folder dialog on the
            # machine RUNNING the server — localhost-only, so that machine is the user's own —
            # and hands the absolute path back for the text box. It picks, nothing more: the
            # existing Save / "Use this folder" buttons still own validating and storing it,
            # so a failed or cancelled dialog leaves the settings exactly as they were.
            path, err = pick_folder(str(body.get('title') or 'Choose a folder'),
                                    str(body.get('initial') or ''))
            if err:
                return self._send(200, json.dumps({'ok': False, 'error': err}))
            return self._send(200, json.dumps({'ok': True, 'path': path,
                                               'canceled': not path}))
        if p == '/api/data-root':
            # Point the app at a different data folder. We only VALIDATE + record the
            # choice in config/instance.json ("data_root"); no files are moved, and the
            # change takes effect when the dashboard restarts (pipelib resolves the root
            # at import time). {check:true} validates without saving; {clear:true} reverts
            # to the automatic default. instance.json is git-ignored and lives beside the
            # CODE. The app resolves the data root FROM it, so it cannot live inside it.
            try:
                if body.get('clear'):
                    ic = load_json(INSTANCE_JSON, {}) or {}
                    ic.pop('data_root', None)
                    os.makedirs(os.path.dirname(INSTANCE_JSON), exist_ok=True)
                    atomic_write(INSTANCE_JSON, json.dumps(ic, indent=2), verify_json=True)
                    info = data_root_info()
                    return self._send(200, json.dumps({'ok': True, 'cleared': True, 'info': info}))
                path, err, warn = check_data_root(body.get('path', ''))
                if err:
                    return self._send(200, json.dumps({'ok': False, 'error': err, 'path': path}))
                if body.get('check'):
                    return self._send(200, json.dumps({'ok': True, 'checked': True,
                                                       'path': path, 'warning': warn}))
                ic = load_json(INSTANCE_JSON, {}) or {}
                ic['data_root'] = path
                ic.setdefault('_readme', 'Per-instance overrides for this copy of the app '
                              '(git-ignored). "data_root" = folder holding your settings, '
                              'tracker data, and resume templates.')
                os.makedirs(os.path.dirname(INSTANCE_JSON), exist_ok=True)
                atomic_write(INSTANCE_JSON, json.dumps(ic, indent=2), verify_json=True)
                return self._send(200, json.dumps({'ok': True, 'path': path, 'warning': warn,
                                                   'restartRequired': True,
                                                   'info': data_root_info()}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e)}))
        if p == '/api/notify-test':
            # Fire one ntfy push so the user can confirm their phone is subscribed.
            # Delegates to the script path (notify.py --test) — no logic duplicated here.
            try:
                r = subprocess.run([sys.executable, _paths.script('notify.py'), '--test'],
                                   cwd=HERE, capture_output=True, text=True, timeout=30)
                out = ((r.stdout or '') + (r.stderr or '')).strip()
                ok = r.returncode == 0
                # notify.py exits 0 when it has nothing to send. That is not a successful alert.
                if 'disabled or no topic' in out:
                    ok, out = False, 'No saved topic yet, or the alerts are off. Save the wizard first.'
                return self._send(200, json.dumps({'ok': ok, 'output': out[-400:]}))
            except Exception as e:
                return self._send(200, json.dumps({'ok': False, 'output': '%s: %s'
                                                   % (type(e).__name__, e)}))
        if p == '/api/threshold':
            s = gui_settings()
            for k, dflt in (('globalFloor', 0.1), ('remoteFloor', 0.3), ('localFloor', 0.2),
                            ('localEnabled', True), ('threshold', 0.6), ('resumeCustomizeThreshold', 0.8),
                            ('excludeManyApplicants', True), ('maxApplicants', 50),
                            # Pending jobs that pause the searches (backlog.py). 0 = no cap.
                            ('backlogCap', 50),
                            # NOTE: no 'claudeEnabled' — the notes field is gated on the Claude
                            # Code CLI being present (_claude_cli_present), not on a stored
                            # preference. A leftover key in an old gui_settings.json is ignored.
                            ('dailyAutoTailorCap', 20), ('hourlyAutoTailorCap', 10)):
                if k in body: s[k] = body[k]
                else: s.setdefault(k, dflt)
            with open(cfg_write('gui_settings.json'), 'w', encoding='utf-8') as f: json.dump(s, f, indent=2)
            return self._send(200, json.dumps({'ok': True, 'settings': s}))
        if p == '/api/profile':
            if pl is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'profile_lib not available'}))
            prof = body.get('profile', body)
            try:
                loc = prof.get('search', {}).get('location', {})
                # re-geocode when coordinates are missing or the caller asks for it
                if loc.get('query') and (body.get('regeocode') or not loc.get('lat')):
                    prof['search']['location'] = pl.geocode_city(loc['query'])
                # Always persist the ACTIVE profile the pipeline reads + regenerate configs.
                pl.save_profile(prof)
                written = pl.regenerate_all(prof)
                # Then keep a per-user NAMED copy (config/profiles/<first>_<last>_profile.json).
                full = (prof.get('identity') or {}).get('full_name', '')
                # profileName = re-saving a known profile; chosenName = user's answer to a prompt.
                requested = body.get('profileName') or body.get('chosenName')
                resp = {'ok': True, 'written': written,
                        'location': prof['search'].get('location', {})}
                if requested:
                    resp['profileName'] = pl.save_named_profile(prof, requested)
                else:
                    base = pl.profile_filename(full)
                    if pl.named_profile_exists(base):
                        # A different profile already owns this name → ask the user to name it.
                        resp['needName'] = True
                        resp['base'] = base
                        resp['suggestion'] = pl.random_profile_filename(full)
                    else:
                        resp['profileName'] = pl.save_named_profile(prof, base)
                return self._send(200, json.dumps(resp))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/queue':
            # Merge (union) with anything already queued so single-job clicks accumulate
            # across multiple presses instead of overwriting each other.
            # `remove` UNQUEUES ids (the card board's "✕ Remove from queue"); it's applied AFTER
            # the merge so a single call can never both add and keep the same id.
            new_ids = [i for i in body.get('ids', []) if i]
            drop = {i for i in body.get('remove', []) if i}
            prev = load_json(TO_PROCESS, {}) or {}
            existing = prev.get('ids', [])
            merged, seen = [], set()
            for i in list(existing) + new_ids:
                if i not in seen: seen.add(i); merged.append(i)
            if drop:
                merged = [i for i in merged if i not in drop]
                seen -= drop
            # Optional per-id customization notes for the LLM tailoring pass ({id: text}).
            # ADDITIVE: consumers that only read `ids` (tailor_local.py, process-queue) are
            # unaffected; an empty note clears any previous one. Capped so the queue file
            # cannot grow without a limit from the UI.
            notes = prev.get('notes', {})
            if not isinstance(notes, dict):
                notes = {}
            incoming = body.get('notes', {})
            if isinstance(incoming, dict):
                for k, v in incoming.items():
                    if not k:
                        continue
                    txt = str(v or '').strip()[:2000]
                    if txt: notes[k] = txt
                    else: notes.pop(k, None)
            # Notes SURVIVE an unqueue (and the queue being processed) so the user can come back
            # to a job, edit what they typed, and re-queue — clearing one is an explicit empty
            # note, above. Only readers keyed off `ids` consume them, so a note for an unqueued
            # id is inert; the cap keeps the file from growing without bound.
            if len(notes) > 400:
                keep = {k: v for k, v in notes.items() if k in seen}
                for k, v in list(notes.items())[-400 + len(keep):]:
                    keep.setdefault(k, v)
                notes = keep
            q = {'requestedAt': time.strftime('%Y-%m-%d %H:%M:%S'), 'ids': merged}
            if notes: q['notes'] = notes
            with open(TO_PROCESS, 'w', encoding='utf-8') as f: json.dump(q, f, indent=2)
            return self._send(200, json.dumps({'ok': True, 'added': len(new_ids),
                'removed': len(drop), 'queued': len(merged),
                'ids': merged, 'noted': len(notes),
                'note': 'Saved to_process.json. The hourly auto-processor will tailor these (or ask Claude to "process the queue").'}))
        if p == '/api/queue-run':
            # "Run tailoring now" on the /queue page. Two steps, both deliberate:
            #  1. mark the queue file runNow — tailor_local.py refuses to touch a queue without
            #     it (or --force), which is what keeps the 2-minute poll free. Setting the flag
            #     here means the manual press uses the SAME gate the automatic path uses.
            #  2. start the scheduler's existing `tailor_local` job rather than spawning
            #     tailor_local.py directly, so a manual run is serialized against scheduled runs
            #     (never two pipelines at once), lands in schedule_log.txt, and lights up the nav
            #     pill exactly like an hourly run.
            # Zero tokens: this is the script path. Jobs that need AI tailoring and have no
            # Claude Code on the machine stay queued — the page says so rather than pretending.
            try:
                q = load_json(TO_PROCESS, {}) or {}
                ids = [i for i in q.get('ids', []) if i]
                if not ids:
                    return self._send(400, json.dumps({'ok': False,
                        'error': 'The queue is empty. Press Tailor resume on a job first.'}))
                if sched is None:
                    return self._send(500, json.dumps({'ok': False, 'error': 'scheduler not available'}))
                q['runNow'] = True
                tmp = TO_PROCESS + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(q, f, indent=2); f.flush(); os.fsync(f.fileno())
                os.replace(tmp, TO_PROCESS)
                res = sched.run_job('tailor_local', manual=True)
                if not res.get('ok'):
                    return self._send(409, json.dumps(res))
                return self._send(200, json.dumps({'ok': True, 'started': 'tailor_local',
                                                   'queued': len(ids)}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e)}))
        if p == '/api/salary-edit':
            # Manual salary override from the card's Salary row. Persists via the same
            # jobpipe salary-apply path the auto-probe uses, tagged source='manual' so it
            # reads as confirmed pay and always wins the resolve chain (job_details is read
            # first). An empty value clears the override, dropping the job back to the
            # auto-resolved figure. Deterministic, zero-token — no Claude, no scrape.
            jid = (body.get('id') or '').strip()
            if not jid:
                return self._send(400, json.dumps({'ok': False, 'error': 'missing id'}))
            value = (body.get('value') or '').strip()[:60]
            try:
                out = subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salary-apply',
                                      '--id', jid, '--value', value, '--source', 'manual'],
                                     cwd=HERE, capture_output=True, text=True, timeout=60)
                if out.returncode != 0:
                    return self._send(500, json.dumps({'ok': False,
                        'error': (out.stderr or 'salary-apply failed').strip()[:300]}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e)}))
            return self._send(200, json.dumps({'ok': True, 'id': jid,
                'salary': value, 'source': 'manual' if value else '',
                'msg': ('Saved %s' % value) if value else 'The manual salary is cleared. The app resolves it again.'}))
        if p == '/api/salary-probe':
            # Per-job "find salary" button. Runs salary_probe.py for ONE id (posting scrape ->
            # Adzuna Jobsworth), persists the hit via jobpipe salary-apply (cmd_update cannot:
            # it never overwrites existing job_details), and reports the Adzuna quota state so
            # the UI can say when estimates have dropped back to the band fallback.
            jid = (body.get('id') or '').strip()
            if not jid:
                return self._send(400, json.dumps({'ok': False, 'error': 'missing id'}))
            try:
                out = subprocess.run([sys.executable, _paths.script('salary_probe.py'),
                                      '--ids', jid, '--refresh', '--json'],
                                     cwd=HERE, capture_output=True, text=True, timeout=120)
                data = json.loads(out.stdout or '{}')
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': '%s: %s'
                                                   % (type(e).__name__, e)}))
            hit = (data.get('results') or {}).get(jid) or {}
            salary, source = hit.get('salary', ''), hit.get('source', '')
            if salary:
                try:
                    subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salary-apply',
                                    '--id', jid, '--value', salary, '--source', source],
                                   cwd=HERE, capture_output=True, text=True, timeout=60)
                except Exception:
                    pass
                msg = 'Found %s (%s).' % (salary, source)
            else:
                why = hit.get('jobsworthStatus') or hit.get('postingStatus') or 'no result'
                # ACTUALLY fall back to the band (the message used to promise this without
                # doing it, leaving the cell blank): look up the family range for the job's
                # title and persist it as source 'band' so the tracker shows something.
                band = ''
                title = hit.get('title', '') or jid
                try:
                    bout = subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salary',
                                           '--title', title],
                                          cwd=HERE, capture_output=True, text=True, timeout=30)
                    band = (json.loads(bout.stdout or '{}') or {}).get('range', '')
                except Exception:
                    band = ''
                if band:
                    salary, source = band, 'band'
                    try:
                        subprocess.run([sys.executable, _paths.script('jobpipe.py'), 'salary-apply',
                                        '--id', jid, '--value', salary, '--source', source],
                                       cwd=HERE, capture_output=True, text=True, timeout=60)
                    except Exception:
                        pass
                    msg = 'No exact salary (%s). The app applied the band estimate %s.' % (why, band)
                else:
                    msg = 'No salary found: %s. This title also matches no band.' % why
            return self._send(200, json.dumps({
                'ok': True, 'id': jid, 'salary': salary, 'source': source,
                'postingStatus': hit.get('postingStatus', ''),
                'jobsworthStatus': hit.get('jobsworthStatus', ''),
                'quotaBlocked': bool(data.get('quotaBlocked')),
                'quotaNote': data.get('quotaNote', ''),
                'message': msg}))
        if p == '/api/schedule':
            if sched is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'scheduler not available'}))
            try:
                sc = sched.load_schedule()             # not `cfg` — see the note in do_GET
                incoming = body.get('config', body)
                jobs = sc.setdefault('jobs', {})
                for name, patch in (incoming.get('jobs') or {}).items():
                    if name in jobs and isinstance(patch, dict):
                        for k in ('enabled', 'interval_minutes', 'window_start', 'window_end',
                                  'at', 'weekday', 'day', 'days',
                                  'archive_age', 'archive_age_unit'):
                            if k in patch: jobs[name][k] = patch[k]
                for k in ('tick_seconds', 'grace_seconds'):
                    if k in incoming: sc[k] = incoming[k]
                sched.save_schedule(sc)
                return self._send(200, json.dumps({'ok': True, 'config': sc, 'status': sched.status_all()}))
            except Exception as e:
                return self._send(500, json.dumps({'ok': False, 'error': str(e)}))
        if p == '/api/run':
            if sched is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'scheduler not available'}))
            res = sched.run_job((body.get('job') or '').strip(), manual=True)
            return self._send(200 if res.get('ok') else 400, json.dumps(res))
        if p == '/api/autostart':
            global AUTOSTART_ERR
            if mau is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'autostart not available'}))
            enable = bool(body.get('enable', True))
            s = gui_settings(); s['autostart'] = enable
            with open(cfg_write('gui_settings.json'), 'w', encoding='utf-8') as f: json.dump(s, f, indent=2)
            ok, msg = (mau.install() if enable else mau.uninstall())
            AUTOSTART_ERR = '' if ok else msg
            return self._send(200, json.dumps({'ok': ok, 'message': msg, 'state': autostart_state()}))
        if p == '/api/watchdog':
            global WATCHDOG_ERR
            if mwd is None:
                return self._send(500, json.dumps({'ok': False, 'error': 'watchdog not available'}))
            s = gui_settings()
            if 'everyMin' in body:                 # interval edit, with or without a toggle change
                try: s['watchdogEveryMin'] = max(1, min(1440, int(body.get('everyMin') or 5)))
                except (TypeError, ValueError): pass
            # "pauseMin" buys the user a window to close the dashboard and have it STAY closed.
            # Without it, an enabled watchdog reopens the board minutes after every quit.
            if 'pauseMin' in body:
                try: mins = max(0, min(24 * 60, int(body.get('pauseMin') or 0)))
                except (TypeError, ValueError): mins = 0
                s['watchdogPauseUntil'] = (time.time() + mins * 60) if mins else 0
            enable = bool(body.get('enable', s.get('watchdog', False)))
            s['watchdog'] = enable
            if enable:
                s['autostart'] = False             # the task covers logon; retire the legacy .vbs
            with open(cfg_write('gui_settings.json'), 'w', encoding='utf-8') as f: json.dump(s, f, indent=2)
            every = int(s.get('watchdogEveryMin', 5) or 5)
            ok, msg = (mwd.install(every) if enable else mwd.uninstall())
            WATCHDOG_ERR = '' if ok else msg
            # The nav chip's bubble reads a cached copy; drop it so the warning it shows agrees
            # with the switch the user just moved, on the very next poll rather than 45s later.
            watchdog_cache_clear()
            return self._send(200, json.dumps({'ok': ok, 'message': msg, 'state': watchdog_state()}))
        return self._send(404, '{"error":"not found"}')

def main():
    os.chdir(HERE)
    global PORT, SERVER_OBJ
    PORT = resolve_port(PORT)
    # Let launchers (Start Here.bat, Install Watchdog.bat) ask the authoritative resolver for this
    # instance's port instead of hardcoding it: `python src/web/dashboard_server.py --print-port`
    # prints the number and exits without binding a socket or starting the scheduler.
    if '--print-port' in sys.argv:
        # A RUNNING instance wins over the configured number: if it had to move over, the
        # launcher must open the window that exists, not the port the config asked for.
        print(live_port() or PORT); return
    staging = scheduler_disabled()     # staging instances disable the scheduler (and autostart)
    label = instance_label()
    try:
        srv, PORT, moved_from = bind_port(PORT)
    except AlreadyRunning as e:
        url = f'http://127.0.0.1:{e.port}/'
        print(f'The dashboard is already running in another window on {url}.')
        print('Open that address in your browser. (The app started nothing. A second copy on the'
              ' same data competes with the first copy for every file that it writes.)')
        input('Press Enter to close.'); return
    except OSError as e:
        print(f'The dashboard did not start ({e}).')
        print(f'Ports {PORT}-{PORT + PORT_SCAN} are all busy. Free one, or set a different'
              ' "port" in config/instance.json.')
        input('Press Enter to close.'); return
    url = f'http://127.0.0.1:{PORT}/'
    SERVER_OBJ = srv          # /api/dev/restart needs to release this socket before relaunching
    runtime_write(PORT)       # tell watchdog.py / stop_board.py where we actually landed
    print('=' * 60)
    print(f'  Job tracker dashboard is running:  {url}' + (f'   [{label}]' if label else ''))
    print('  Keep this window OPEN. Close it to stop the dashboard.')
    if moved_from:
        print(f'  NOTE: port {moved_from} was busy (not this app), so this window took {PORT}.')
    print('=' * 60)
    if staging:
        print('  Scheduler DISABLED for this instance (config/instance.json disable_scheduler'
              ' or TOP_RAT_DISABLE_SCHEDULER). No timed scrape, tailor, git, or notify run happens here.')
    elif sched is not None:
        try:
            sched.start()
            print(f'  Scheduler is running jobs on a timer. Manage them at {url}schedule')
        except Exception as e:
            print('  scheduler failed to start:', e)
    if mwd is not None and not staging:
        reconcile_watchdog()           # obey the watchdog setting, which defaults to OFF
        wd = watchdog_state()
        if wd['desired'] and wd['enabled']:
            print(f"  Watchdog is ON: \"{wd['task']}\" restarts this dashboard (minimized)"
                  f" within {wd['everyMin']} min if it stops.")
            if wd['pauseMin']:
                print(f"  Watchdog is PAUSED for {wd['pauseMin']} more minutes.")
        elif wd['desired'] and not wd['enabled']:
            print('  WARNING: the watchdog did NOT turn on'
                  + (': ' + wd['error'] if wd['error'] else '')
                  + '\n           Nothing restarts the dashboard if it stops.')
    if mau is not None and not staging:
        reconcile_autostart()          # skipped entirely when the watchdog is on
        st = autostart_state()
        wd_on = bool(gui_settings().get('watchdog', False))
        if wd_on:
            pass                       # the watchdog covers logon; the .vbs is retired
        elif st['desired'] and not st['enabled']:
            print('  WARNING: autostart did NOT turn on' + (': ' + st['error'] if st['error'] else '')
                  + '\n           The schedule runs only while this window is open.')
        elif not st['desired']:
            print('  NOTE: autostart is OFF. The schedule runs only while this window is open.')
    if '--no-browser' not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        srv.server_close()
        runtime_clear()       # a stale port record would send the watchdog at a dead socket

if __name__ == '__main__':
    main()
# profile/setup endpoints: GET /setup (guided wizard, ?welcome=1 = first-run mode),
# GET/POST /api/profile, GET /api/geocode (see profile_lib.py),
# GET /api/profile-status (drives the first-run redirect + daily reminder banner in ONBOARD_JS)
# uptime endpoints: GET/POST /api/watchdog (Task Scheduler watchdog — see make_watchdog.py and
# watchdog.py; the Schedule page owns the switch), GET/POST /api/autostart (LEGACY Startup-folder
# .vbs, no longer offered in the UI; turning the watchdog on removes the .vbs)
# dev endpoints (hidden page GET /dev — see dev.html; NOT in the nav): GET /api/dev/info
# (read-only diagnostics + log tail), GET /api/dev/ping (pid/boot identity, polled during a
# restart), POST /api/dev/restart (replace this process so a .py edit takes effect)