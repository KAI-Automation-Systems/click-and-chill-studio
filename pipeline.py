import contextlib, datetime as dt, hashlib, json, logging, msvcrt, os, pathlib, random, re, shutil, sqlite3, subprocess, sys, time, urllib.request, uuid

ROOT=pathlib.Path(__file__).resolve().parent
WORK=ROOT/'work'
sys.path.insert(0,str(WORK/'python-packages'))
from catalog import TOPICS
from render import render_video, speech_and_timing, thumbnail, verify_video

PRIVATE=WORK/'click-and-chill-private'
VIDEOS=ROOT/'videos'
RUNTIME=WORK/'runtime'
DB=ROOT/'studio.sqlite3'
CONFIG=ROOT/'settings.json'
DEFAULTS={'brand':'Click & Chill','voice':'am_michael','language':'en','timezone':'Europe/Amsterdam','publish_hour':19,'publish_days':[0,2,4],'automatic':False,'channel_id':'','channel_title':'','next_publish_at':'','publish_mode':'public','vertical_export':True,'model_url':'http://127.0.0.1:8766','source_max_age_days':90}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat()

def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,path)

def settings():
    if not CONFIG.exists(): return DEFAULTS.copy()
    return {**DEFAULTS,**json.loads(CONFIG.read_text(encoding='utf-8'))}

def save_settings(value): atomic_json(CONFIG,value)

@contextlib.contextmanager
def connect():
    con=sqlite3.connect(DB,timeout=20); con.row_factory=sqlite3.Row
    try:
        with con: yield con
    finally: con.close()

def initialize():
    PRIVATE.mkdir(parents=True,exist_ok=True); VIDEOS.mkdir(exist_ok=True)
    if not CONFIG.exists(): save_settings(DEFAULTS)
    with connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, topic_id TEXT UNIQUE, title TEXT, status TEXT, message TEXT, folder TEXT, created_at TEXT, updated_at TEXT, publish_at TEXT, video_id TEXT, error TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS events (at TEXT, message TEXT)')

def event(message):
    with connect() as con: con.execute('INSERT INTO events VALUES (?,?)',(now(),message))
    logging.info(message)

def recover_local_jobs():
    """Only call on Studio startup, after acquiring its listening port."""
    try:
        with production_lock():
            for job in jobs():
                if job['status'] in ['planning','rendering']:
                    update_job(job['id'],status='error',message='Previous production was interrupted; this episode can be rebuilt',error='Production stopped before the video was complete')
    except RuntimeError:
        # Another CLI producer still owns the lock; its work is not a crash.
        pass

def jobs():
    with connect() as con: return [dict(row) for row in con.execute('SELECT * FROM jobs ORDER BY created_at DESC')]

def get_job(job_id):
    with connect() as con: row=con.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if row is None: raise ValueError('Unknown video')
    return dict(row)

def update_job(job_id,**fields):
    allowed={'status','message','folder','publish_at','video_id','error','title'}
    if not fields.keys()<=allowed: raise ValueError('Invalid job update')
    fields['updated_at']=now()
    with connect() as con: con.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),job_id))

@contextlib.contextmanager
def production_lock(name='production'):
    if not re.fullmatch(r'[a-z0-9-]+',name): raise ValueError('Invalid lock name')
    lock=(PRIVATE/(name+'.lock')).open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock.close(); raise RuntimeError('Production is already running')
    try: yield
    finally:
        lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1); lock.close()

def reserve_topic():
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        used={r[0] for r in con.execute('SELECT topic_id FROM jobs')}
        available=[t for t in TOPICS if t['id'] not in used]
        if not available: raise RuntimeError('Reviewed topic library is exhausted. Add newly researched topics before continuing; no duplicate filler will be published.')
        # Balance browser and Windows topics after the first familiar problem.
        topic=available[0]
        job_id=uuid.uuid4().hex[:12]; folder=VIDEOS/(job_id+'-'+topic['id'])
        con.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',(job_id,topic['id'],topic['title'],'planning','Preparing an original episode',str(folder),now(),now(),'','',''))
    return job_id,topic,folder

def ffmpeg_path():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def start_model():
    url=settings()['model_url']
    try:
        with urllib.request.urlopen(url+'/health',timeout=2) as r:
            if r.status==200: return None
    except Exception: pass
    executables=list((RUNTIME/'llama').rglob('llama-server.exe'))
    model=RUNTIME/'Qwen3-4B-Q4_K_M.gguf'
    if not model.exists(): model=RUNTIME/'qwen2.5-1.5b-instruct-q4_k_m.gguf'
    if not executables or not model.exists(): raise RuntimeError('Local AI is not installed yet')
    log=(PRIVATE/'model.log').open('ab')
    process=subprocess.Popen([str(executables[0]),'-m',str(model),'--host','127.0.0.1','--port','8766','-c','2048','-t','4','--parallel','1','--jinja'],stdout=log,stderr=log,creationflags=0x08000000)
    log.close()
    for _ in range(60):
        if process.poll() is not None: raise RuntimeError('Local AI could not start; see model.log')
        try:
            with urllib.request.urlopen(url+'/health',timeout=2) as r:
                if r.status==200: return process
        except Exception: time.sleep(1)
    process.terminate(); raise RuntimeError('Local AI startup timed out')

def creative_hook(topic):
    process=start_model()
    try:
        schema={'type':'object','properties':{'hook':{'type':'string'},'angle':{'type':'string'}},'required':['hook','angle'],'additionalProperties':False}
        payload={'messages':[{'role':'system','content':'You write friendly English comedy for beginner computer tutorials. Make fun of computers, NEVER the viewer. Output JSON. Write an amusing metaphor about the problem, followed by reassurance. Do not simply repeat the problem. Do not give technical instructions, invent facts, promise earnings, mention AI, or use insults. The hook is two short spoken sentences, 18 to 32 words total. The angle is a short English description of the joke. Example style: Your browser has started a surprise concert. We can give it an indoor voice.'},{'role':'user','content':'Problem: '+topic['problem']+'\nWrite a fresh funny opening. No keys, clicks or instructions. No quotes inside the joke.'}], 'temperature':.7,'max_tokens':160,'response_format':{'type':'json_schema','json_schema':{'name':'opening','strict':True,'schema':schema}}}
        payload['chat_template_kwargs']={'enable_thinking':False}
        request=urllib.request.Request(settings()['model_url']+'/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=120) as response: result=json.load(response)
        content=result['choices'][0]['message']['content']; data=json.loads(content)
        hook=data['hook'].strip()
        if not 6<=len(hook.split())<=45 or hook.lower()==topic['problem'].lower() or any(term in hook.lower() for term in ['press ','click ','ctrl','control key','windows key','idiot','stupid','guarantee','subscribe','as an ai']):
            raise ValueError('AI opening failed editorial constraints')
        engine='Qwen3-4B local' if (RUNTIME/'Qwen3-4B-Q4_K_M.gguf').exists() else 'Qwen2.5-1.5B local'
        return hook,{'engine':engine,'angle':data['angle'],'accepted':True}
    finally:
        # Free laptop memory between productions; do not terminate someone else's server.
        if process is not None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill()

def build_scenes(topic,hook):
    scenes=[{'kind':'hook','heading':topic['title'],'body':topic['problem'],'voice':hook+' This tip is for '+topic['platform']+'.'}]
    for index,(heading,instruction) in enumerate(topic['steps']):
        voice=instruction
        if index==1: voice+=' '+topic['joke2']
        scenes.append({'kind':'step','heading':f'Step {index+1}: '+heading,'body':instruction,'voice':voice})
    scenes.append({'kind':'catch','heading':'One thing to remember','body':topic['caution'],'voice':topic['caution']})
    end_joke=topic['joke'] if topic['joke'] not in hook else 'Your next computer drama can now have a shorter episode.'
    scenes.append({'kind':'recap','heading':'Small fix. Less frustration.','body':topic['benefit'],'voice':'Quick recap. '+topic['benefit']+' '+end_joke+' Try it once with something unimportant. Save this tip for later.'})
    return scenes

def produce_batch(count=3):
    if not isinstance(count,int) or not 1<=count<=5: raise ValueError('Batch size must be between one and five')
    result=[]
    for _ in range(count):
        if len(jobs())>=len(TOPICS): break
        result.append(produce())
    return result

def validate_topic(topic):
    from zoneinfo import ZoneInfo
    age=(dt.datetime.now(ZoneInfo(settings()['timezone'])).date()-dt.date.fromisoformat(topic['reviewed'])).days
    if age>settings()['source_max_age_days']: raise RuntimeError('This topic needs a fresh source review before automatic publication')
    if not topic['source'].startswith('https://support.microsoft.com/'): raise ValueError('Unapproved factual source')
    if len(topic['steps'])!=3 or any(len(step)!=2 for step in topic['steps']): raise ValueError('Topic has invalid steps')

def produce(vertical=None, job_id=None):
    initialize()
    with production_lock():
        if job_id is None:
            job_id,topic,folder=reserve_topic()
        else:
            previous=get_job(job_id)
            if previous['video_id'] or previous['status'] not in ['ready','error']: raise ValueError('Only a local, unuploaded episode can be rebuilt')
            topic=next(t for t in TOPICS if t['id']==previous['topic_id']); folder=pathlib.Path(previous['folder'])
            update_job(job_id,status='planning',message='Rebuilding with the latest presentation',error='')
        folder.mkdir(exist_ok=True)
        progress=lambda message:update_job(job_id,message=message)
        try:
            validate_topic(topic); progress('Writing the English opening with local AI')
            try: hook,creative=creative_hook(topic)
            except Exception as exc:
                # A reviewed script can still be produced if AI is temporarily unavailable.
                hook=topic['problem']+' '+topic['joke']; creative={'engine':'reviewed fallback','accepted':False,'reason':str(exc)}
                event('AI opening unavailable; using the reviewed opening for '+topic['id'])
            scenes=build_scenes(topic,hook)
            update_job(job_id,status='rendering',message='Recording English narration')
            duration=speech_and_timing(scenes,folder,settings()['voice'],ROOT/'speech.py')
            if not 25<=duration<=180: raise RuntimeError('Episode duration is outside the 25–180 second quality range')
            progress('Drawing the thumbnail'); thumbnail(topic,folder)
            encoder=ffmpeg_path()
            video=render_video(scenes,topic,folder,encoder,progress=progress)
            checks={'youtube':verify_video(video,encoder)}
            do_vertical=settings()['vertical_export'] if vertical is None else vertical
            if do_vertical:
                vertical_video=render_video(scenes,topic,folder,encoder,vertical=True,progress=progress)
                checks['vertical']=verify_video(vertical_video,encoder)
            description=topic['benefit']+'\n\nFor: '+topic['platform']+'.\n\n'+ '\n'.join(f'{i+1}. {step[0]}' for i,step in enumerate(topic['steps']))+'\n\nRemember: '+topic['caution']+'\n\nIllustrated examples, not a recording of your device. English synthetic narration and AI-assisted opening.\nSource checked '+topic['reviewed']+': '+topic['source']+'\n\n#ComputerTips #Windows11 #BeginnerTech'
            metadata={'job_id':job_id,'topic':topic,'title':topic['title'],'description':description,'tags':['computer tips','beginner computer help','Windows 11',topic['id']],'language':'en','narrator':{'engine':'Kokoro ONNX','voice':settings()['voice'],'speed':1.0},'duration_seconds':round(duration,2),'creative':creative,'scenes':scenes,'checks':checks,'created_at':now(),'publish_format':'landscape YouTube video','vertical_export':'local export only; not auto-published to TikTok'}
            atomic_json(folder/'episode.json',metadata)
            (folder/'script.txt').write_text('\n\n'.join(s['heading']+'\n'+s['voice'] for s in scenes),encoding='utf-8')
            (folder/'description.txt').write_text(description,encoding='utf-8')
            progress('Checking subtitles and audio')
            if not (folder/'captions.srt').read_text().strip(): raise RuntimeError('Subtitles are missing')
            update_job(job_id,status='ready',message='Video, audio, subtitles and thumbnail are ready')
            event('Episode ready: '+topic['title'])
            return get_job(job_id)
        except BaseException as exc:
            update_job(job_id,status='error',message='Production stopped',error=str(exc)); event('Production stopped: '+str(exc)); raise

def next_slot(after=None):
    from zoneinfo import ZoneInfo
    config=settings(); timezone=ZoneInfo(config['timezone'])
    current=after or dt.datetime.now(dt.timezone.utc)
    local=current.astimezone(timezone)
    for day in range(9):
        date=local.date()+dt.timedelta(days=day)
        candidate=dt.datetime.combine(date,dt.time(config['publish_hour']),timezone)
        if candidate.weekday() in config['publish_days'] and candidate>local+dt.timedelta(minutes=15):
            return candidate.astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')
    raise RuntimeError('No publication slot found')

def tick():
    with production_lock('schedule'):
        return _tick()

def _tick():
    initialize(); config=settings()
    if not config['automatic'] or not config['channel_id']: return
    if any(j['status'] in ['uploading','needs_attention','error'] for j in jobs()): return
    slot=config['next_publish_at'] or next_slot()
    target=dt.datetime.fromisoformat(slot.replace('Z','+00:00'))
    if target<dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=15):
        slot=next_slot(); target=dt.datetime.fromisoformat(slot.replace('Z','+00:00'))
    # Keep one future publication ready, rather than flooding a brand-new channel.
    if target-dt.datetime.now(dt.timezone.utc)>dt.timedelta(hours=48): return
    ready=[j for j in reversed(jobs()) if j['status']=='ready']
    job=ready[0] if ready else produce()
    from youtube import upload
    upload(job['id'],publish_at=slot)
    config=settings(); config['next_publish_at']=next_slot(target); save_settings(config)

def status():
    config=settings(); items=jobs()
    with connect() as con: events=[dict(r) for r in con.execute('SELECT * FROM events ORDER BY rowid DESC LIMIT 12')]
    return {'settings':config,'jobs':items,'events':events,'topics_total':len(TOPICS),'topics_remaining':len(TOPICS)-len(items),'credentials_present':(PRIVATE/'client_secret.dpapi').exists(),'connected':bool(config['channel_id'] and (PRIVATE/'token.dpapi').exists()),'ai_installed':(RUNTIME/'qwen2.5-1.5b-instruct-q4_k_m.gguf').exists()}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['produce','tick','status']); parser.add_argument('--landscape-only',action='store_true')
    args=parser.parse_args(); initialize()
    if args.command=='produce': print(json.dumps(produce(vertical=False if args.landscape_only else None),indent=2))
    elif args.command=='tick': tick()
    else: print(json.dumps(status(),indent=2))
