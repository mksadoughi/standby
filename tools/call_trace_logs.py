"""A call before trace.jsonl, from the logs (ui.log, the recorder's live log, chat.jsonl), as one HTML page.
call_trace.py uses it for a call that has no trace.jsonl. python call_trace_logs.py <talk-session> [out.html]"""
import html, json, os, re, sys, time

H = os.path.expanduser('~/.standby')
sid = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else '%s.html' % sid
meta = json.load(open(f'{H}/sessions/{sid}/meta.json'))
t0 = meta['started']
day = time.strftime('%Y-%m-%d', time.localtime(t0))
runs = [json.loads(l) for l in open(f'{H}/sessions/{sid}/runs.jsonl')]
t1 = max([r.get('end', 0) for r in runs] + [t0 + 60])
pat = re.compile(r'^\[standby(?:-app)?\] (\d\d:\d\d:\d\d) (.*)')


def at(hms):
    return time.mktime(time.strptime(day + ' ' + hms, '%Y-%m-%d %H:%M:%S'))


def kind(src, msg):
    m = msg
    if src == 'said':
        return 'you' if m.startswith('YOU') else 'voice'
    if 'voice timing' in m:
        return 'timing'
    if 'dropped while Claude has the floor' in m or 'someone else heard, ignored' in m:
        return 'dropped'
    if 'talked over' in m or 'interrupted' in m:
        return 'interrupt'
    if re.search(r'lite: \S+: (paused|resumed)', m):
        return 'pause'
    if re.search(r'lite: \S+: (handed|a hand-off|what came back|the hand-off|received)', m) or 'never reached' in m:
        return 'handoff'
    if m.startswith('ask ') or 'ask dropped' in m:
        return 'ask'
    if 'early' in m:
        return 'early'
    if re.match(r'voice: turn \d+', m):
        return 'turn'
    if m.startswith('utterance') or m.startswith('voice: ') and not m.startswith('voice: this answer'):
        return 'hearing'
    return 'system'


ev = []
for path, src in ((f'{H}/live/{sid}.log', 'rec'), (f'{H}/ui.log', 'app')):
    try:
        lines = open(path, errors='replace').read().splitlines()
    except OSError:
        continue
    for line in lines:
        m = pat.match(line)
        if not m:
            continue
        t = at(m.group(1))
        if not (t0 - 5 <= t <= t1 + 30):
            continue
        msg = m.group(2)
        if src == 'app' and not re.search(r'voice|ask |lite: ' + sid + r'|lite: call', msg):
            continue
        if 'heartbeat' in msg or 'not asked early' in msg:
            continue
        if src == 'rec' and re.match(r'voice: (?!turn|dropped|someone|this answer|Standby)', msg):
            continue                                   # the heard words: the chat has them, finished
        ev.append({'t': t, 'src': src, 'msg': msg})
for l in open(f'{H}/sessions/{sid}/chat.jsonl'):
    m = json.loads(l)
    if 'at' in m and m.get('text'):
        who = {'me': 'YOU', 'ai': 'VOICE'}.get(m.get('role'), str(m.get('role')))
        tag = (' [%s]' % m['reason']) if m.get('reason') else ''
        ev.append({'t': m['at'], 'src': 'said', 'msg': '%s%s: %s' % (who, tag, ' '.join(m['text'].split()))})
ev.sort(key=lambda e: e['t'])

rows, first_words, dropped = [], [], 0
paused = False
for e in ev:
    k = kind(e['src'], e['msg'])
    if k == 'pause':
        paused = 'paused' in e['msg'] and 'resumed' not in e['msg']
    if k == 'dropped' and paused:
        k = 'unheard'
    r = {'k': k, 'clock': time.strftime('%H:%M:%S', time.localtime(e['t'])), 'off': round(e['t'] - t0, 1),
         'text': e['msg']}
    if k == 'timing':
        g = re.search(r"turn over ([\d.]+|-)s?.*?first word ([\d.]+|-)s?.*?speaking ([\d.]+|-)s?", e['msg'])
        if g:
            r['bar'] = [None if x == '-' else float(x) for x in g.groups()]
            if r['bar'][1] is not None:
                first_words.append(r['bar'][1])
    if k in ('you', 'voice'):
        r['text'] = e['msg'].split(': ', 1)[1] if ': ' in e['msg'] else e['msg']
        r['tag'] = re.match(r'\w+(?: \[(\w+)\])?', e['msg']).group(1)
    if k in ('dropped', 'unheard'):
        dropped += k == 'dropped'
        r['text'] = re.sub(r'^voice: (dropped while Claude has the floor|someone else heard, ignored): ', '', e['msg'])
    rows.append(r)

fw = sorted(first_words)
summary = {
    'sid': sid, 'title': (open(f'{H}/sessions/{sid}/title.auto.txt').read().strip()
                          if os.path.exists(f'{H}/sessions/{sid}/title.auto.txt') else ''),
    'start': time.strftime('%b %d, %H:%M', time.localtime(t0)), 'minutes': round((t1 - t0) / 60),
    'you': sum(r['k'] == 'you' for r in rows), 'voice': sum(r['k'] == 'voice' for r in rows),
    'median': fw[len(fw) // 2] if fw else None, 'slow': sum(x > 3 for x in fw), 'dropped': dropped,
    'handoffs': sum(r['k'] == 'handoff' and 'handed to the session' in r['text'] for r in rows),
}

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Call trace</title>
<style>
:root{--bg:#f7f7f9;--card:#fff;--ink:#16161d;--dim:#6b6b78;--line:#e4e4ea;--you:#2f6fdf;--voice:#6b4fe0;
--turn:#8a8a99;--ask:#0f8b8d;--early:#a0a0ad;--hand:#c77700;--pause:#7a7a7a;--drop:#d9443b;--int:#e07b39;--time:#16161d;--sys:#b5b5c0}
@media (prefers-color-scheme:dark){:root{--bg:#121218;--card:#1b1b23;--ink:#ececf2;--dim:#9a9aa8;--line:#2b2b36;
--you:#6c9cff;--voice:#a28bff;--ask:#3cc1c3;--hand:#f0a53a;--drop:#ff6b62;--int:#ff9b5c;--time:#ececf2;--sys:#5b5b68}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px 10px}
h1{margin:0 0 2px;font-size:18px}.sub{color:var(--dim);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 8px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 10px}
.stat b{font-size:16px;display:block}.stat span{color:var(--dim);font-size:12px}
.filters{display:flex;flex-wrap:wrap;gap:6px}.filters label{display:flex;align-items:center;gap:5px;border:1px solid var(--line);
background:var(--card);border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer;user-select:none}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
main{max-width:980px;margin:0 auto;padding:12px 20px 60px}
.row{display:grid;grid-template-columns:74px 64px 96px 1fr;gap:10px;align-items:start;padding:5px 8px;border-radius:8px}
.row:hover{background:var(--card)}.clock{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px;padding-top:2px}
.off{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px;text-align:right;padding-top:2px}
.lane{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding-top:3px}
.msg{word-break:break-word}.small{color:var(--dim);font-size:12.5px}
.bubble{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 11px}
.you .bubble{border-left:3px solid var(--you)}.voice .bubble{border-left:3px solid var(--voice)}
.drop .msg{color:var(--drop);text-decoration:line-through;text-decoration-color:color-mix(in srgb,var(--drop) 45%,transparent)}
.bar{display:flex;height:12px;border-radius:4px;overflow:hidden;margin:4px 0 2px;max-width:520px;background:var(--line)}
.bar i{display:block;height:100%}.legend{font-size:12px;color:var(--dim)}
.gap{margin:10px 0;border-top:1px dashed var(--line);color:var(--dim);font-size:11px;text-align:center}
.gap span{position:relative;top:-9px;background:var(--bg);padding:0 8px}
.hidden{display:none}
</style></head><body>
<header><h1 id="title"></h1><div class="sub" id="sub"></div><div class="stats" id="stats"></div><div class="filters" id="filters"></div></header>
<main id="list"></main>
<script>
const S=__SUMMARY__, R=__ROWS__;
const L={you:['You','--you',1],voice:['Voice','--voice',1],timing:['Timing','--time',1],handoff:['Session','--hand',1],
pause:['Pause','--pause',1],dropped:['Dropped','--drop',1],unheard:['While paused','--pause',0],interrupt:['Talk-over','--int',1],ask:['Asked','--ask',0],
turn:['Turn end','--turn',0],early:['Early ask','--early',0],hearing:['Heard','--turn',0],system:['System','--sys',0]};
document.getElementById('title').textContent='Call trace'+(S.title?' · '+S.title:'');
document.getElementById('sub').textContent=S.sid+' · '+S.start+' · '+S.minutes+' min';
const st=[[S.you,'things you said'],[S.voice,'voice replies'],[S.median!=null?S.median.toFixed(2)+' s':'–','median first word'],
[S.slow,'replies slower than 3 s'],[S.dropped,'lines dropped while it spoke (not paused)'],[S.handoffs,'hand-offs']];
document.getElementById('stats').innerHTML=st.map(s=>`<div class="stat"><b>${s[0]}</b><span>${s[1]}</span></div>`).join('');
let on={};try{on=JSON.parse(localStorage.getItem('trace-lanes')||'{}')}catch(e){}
for(const k in L) if(!(k in on)) on[k]=!!L[k][2];
const f=document.getElementById('filters');
f.innerHTML=Object.entries(L).map(([k,v])=>{const n=R.filter(r=>r.k===k).length;return n?`<label><input type=checkbox data-k="${k}" ${on[k]?'checked':''}><span class=dot style="background:var(${v[1]})"></span>${v[0]} <span class=small>${n}</span></label>`:''}).join('');
f.addEventListener('change',e=>{on[e.target.dataset.k]=e.target.checked;try{localStorage.setItem('trace-lanes',JSON.stringify(on))}catch(x){};draw()});
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function body(r){
 if(r.k==='you'||r.k==='voice') return `<div class=bubble>${r.tag?`<span class=small>[${esc(r.tag)}] </span>`:''}${esc(r.text)}</div>`;
 if(r.k==='timing'&&r.bar){const[a,b,c]=r.bar,sc=v=>v==null?0:Math.min(v,12)/12*100;
  return `<div class=bar><i style="width:${sc(a)}%;background:var(--turn)"></i><i style="width:${sc(b)}%;background:var(--voice)"></i><i style="width:${sc(c)}%;background:var(--ask)"></i></div>
  <div class=legend>turn over ${a??'–'} s · first word ${b??'–'} s · speaking starts ${c??'–'} s</div>`}
 return `<div class=small>${esc(r.text)}</div>`}
function draw(){let h='',last=null;for(const r of R){if(!on[r.k])continue;
 if(last!=null&&r.off-last>20)h+=`<div class=gap><span>${Math.round(r.off-last)} s later</span></div>`;last=r.off;
 const v=L[r.k];h+=`<div class="row ${r.k==='dropped'||r.k==='unheard'?'drop':r.k}"><div class=clock>${r.clock}</div><div class=off>+${r.off.toFixed(1)}s</div>
 <div class=lane style="color:var(${v[1]})">${v[0]}</div><div class=msg>${body(r)}</div></div>`}
 document.getElementById('list').innerHTML=h||'<p class=small>Nothing to show: turn a lane on above.</p>'}
draw();
</script></body></html>"""
open(out, 'w').write(PAGE.replace('__SUMMARY__', json.dumps(summary)).replace('__ROWS__', json.dumps(rows)))
print(out, len(rows), 'events')
