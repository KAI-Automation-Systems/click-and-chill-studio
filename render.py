"""Original animated diagrams, neural speech, subtitles, and H.264 video."""
import functools, json, math, os, pathlib, re, shutil, subprocess, sys, tempfile, time, wave
from PIL import Image, ImageDraw, ImageFont

BG='#101723'; PANEL='#1c293a'; WHITE='#f4f6f9'; MUTED='#aab8c9'; ACCENT='#bce97b'; BLUE='#83c6ef'
FONT = pathlib.Path('C:/Windows/Fonts')

@functools.lru_cache(maxsize=80)
def font(size, bold=False):
    return ImageFont.truetype(str(FONT / ('segoeuib.ttf' if bold else 'segoeui.ttf')), max(10,int(size)))

def wrap(text, draw, face, width):
    lines=[]; line=''
    for word in text.split():
        trial=(line+' '+word).strip()
        if draw.textlength(trial,font=face)>width and line:
            lines.append(line); line=word
        else: line=trial
    if line: lines.append(line)
    return lines

def textblock(draw, xy, text, size, width, color=WHITE, bold=False, maxlines=8):
    face=font(size,bold); lines=wrap(text,draw,face,width)
    if len(lines)>maxlines:
        return textblock(draw,xy,text,size-2,width,color,bold,maxlines)
    for n,line in enumerate(lines):
        draw.text((xy[0],xy[1]+n*size*1.32),line,font=face,fill=color)
    return len(lines)*size*1.32

def keycaps(draw, keys, x, y, width, scale):
    size=int(27*scale); face=font(size,True)
    widths=[max(62*scale,draw.textlength(k,font=face)+36*scale) for k in keys]
    total=sum(widths)+14*scale*(len(keys)-1)
    ratio=min(1,width/total)
    if ratio<1: return keycaps(draw,keys,x,y,width,scale*ratio)
    for k,w in zip(keys,widths):
        draw.rounded_rectangle((x,y,x+w,y+64*scale),radius=11*scale,fill=ACCENT)
        draw.text((x+w/2,y+31*scale),k,anchor='mm',font=face,fill=BG)
        x+=w+14*scale

def computer(draw,x,y,s,t,happy=False):
    bob=math.sin(t*2.1)*5*s
    y+=bob
    draw.rounded_rectangle((x,y,x+180*s,y+130*s),radius=16*s,outline=MUTED,width=max(1,int(3*s)),fill=PANEL)
    draw.line((x+90*s,y+130*s,x+90*s,y+155*s),fill=MUTED,width=max(1,int(5*s)))
    draw.line((x+45*s,y+156*s,x+135*s,y+156*s),fill=MUTED,width=max(1,int(5*s)))
    blink=int(t*10)%43==0
    for ex in [x+58*s,x+123*s]:
        if blink: draw.line((ex-10*s,y+48*s,ex+10*s,y+48*s),fill=ACCENT,width=max(1,int(4*s)))
        else: draw.ellipse((ex-7*s,y+40*s,ex+7*s,y+57*s),fill=ACCENT)
    if happy: draw.arc((x+58*s,y+55*s,x+122*s,y+99*s),0,180,fill=ACCENT,width=max(1,int(4*s)))
    else: draw.line((x+65*s,y+89*s,x+115*s,y+89*s),fill=MUTED,width=max(1,int(3*s)))

def browser_demo(draw,box,kind,t,stage):
    x,y,w,h=box; s=w/650
    draw.rounded_rectangle((x,y,x+w,y+h),radius=12*s,fill='#f4f6f9')
    draw.rounded_rectangle((x,y,x+w,y+54*s),radius=12*s,fill='#ccd6e4')
    restored=stage>=3 or (stage==2 and t>=2)
    for i,label in enumerate(['Recipes','Useful page','Music']):
        if kind=='tabs' and i==1 and not restored: continue
        tx=x+12*s+i*180*s
        draw.rounded_rectangle((tx,y+10*s,tx+168*s,y+43*s),radius=7*s,fill='#ffffff' if i==1 else '#b9c6d9')
        draw.text((tx+10*s,y+16*s),label,font=font(14*s),fill=BG)
    draw.rounded_rectangle((x+16*s,y+65*s,x+w-16*s,y+96*s),radius=8*s,fill='#e2e8f0')
    draw.text((x+30*s,y+72*s),'example.test / practice page',font=font(13*s),fill='#526275')
    cy=y+118*s
    if kind=='downloads':
        draw.text((x+25*s,cy),'Downloads',font=font(25*s,True),fill=BG)
        draw.rounded_rectangle((x+25*s,cy+50*s,x+w-25*s,cy+115*s),radius=8*s,fill='#e2e8f0')
        draw.text((x+42*s,cy+65*s),'Holiday checklist.pdf',font=font(19*s),fill=BG)
        draw.text((x+42*s,cy+98*s),'Completed  /  Show in folder',font=font(12*s),fill='#526275')
    elif kind=='find':
        draw.text((x+25*s,cy),'Find: holiday',font=font(22*s,True),fill=BG)
        for i,line in enumerate(['A long article about everyday things.','The holiday checklist is right here.','More words you do not need to hunt through.']):
            if i==1: draw.rectangle((x+20*s,cy+(48+i*40)*s,x+w-20*s,cy+(81+i*40)*s),fill=ACCENT)
            draw.text((x+25*s,cy+(50+i*40)*s),line,font=font(16*s),fill=BG)
    elif kind=='zoom':
        zs=1 if stage<2 else 1.6
        draw.text((x+25*s,cy),'100%' if stage<2 else '150%',font=font(18*s),fill='#526275')
        draw.text((x+25*s,cy+52*s),'Much easier to read.',font=font(22*s*zs,True),fill=BG)
    elif kind=='mute':
        draw.text((x+25*s,cy),'Music tab',font=font(30*s,True),fill=BG)
        draw.text((x+25*s,cy+55*s),'Sound off' if stage>=2 else 'Unexpected concert...',font=font(22*s),fill='#526275')
        for i in range(14):
            bh=8*s if stage>=2 else (15+abs(math.sin(t*4+i)) * 65)*s
            bx=x+(30+i*23)*s
            draw.rectangle((bx,y+h-35*s-bh,bx+12*s,y+h-35*s),fill='#315b70')
    elif kind=='favorite':
        draw.text((x+25*s,cy),'Save this favorite',font=font(26*s,True),fill=BG)
        draw.text((x+25*s,cy+55*s),'Name: My useful page',font=font(20*s),fill='#526275')
    elif kind=='history':
        draw.text((x+25*s,cy),'History',font=font(26*s,True),fill=BG)
        for i,line in enumerate(['Today  /  Useful page','Yesterday  /  Another page']):
            draw.text((x+25*s,cy+(58+i*42)*s),line,font=font(19*s),fill='#526275')
    elif kind=='paste':
        draw.text((x+25*s,cy),'Paste words, leave the styling.',font=font(24*s,True),fill=BG)
        draw.text((x+25*s,cy+64*s),'Hello! This text fits right in.',font=font(21*s),fill='#526275')
    elif kind=='read':
        draw.text((x+25*s,cy),'Read Aloud',font=font(26*s,True),fill=BG)
        draw.text((x+25*s,cy+55*s),'Play / Pause / Voice options',font=font(20*s),fill='#526275')
    elif kind=='fullscreen':
        draw.text((x+25*s,cy),'More room for the page.',font=font(28*s,True),fill=BG)
    else:
        draw.text((x+25*s,cy),'Your useful page is back.' if restored else 'The useful tab is closed.',font=font(26*s,True),fill=BG)
        draw.text((x+25*s,cy+55*s),'No detective work required.' if restored else 'Bring it back with Ctrl + Shift + T.',font=font(20*s),fill='#526275')
    # Animated explanatory pointer, on fictional content only.
    px=x+w*(.65+.08*math.sin(t)); py=y+h*.65
    draw.polygon([(px,py),(px,py+28*s),(px+7*s,py+20*s),(px+16*s,py+30*s),(px+22*s,py+25*s),(px+12*s,py+15*s),(px+24*s,py+13*s)],fill='#15334b',outline='#ffffff')

def demo(draw,box,kind,t,stage):
    x,y,w,h=box; s=w/650
    if kind in ['tabs','downloads','zoom','find','mute','favorite','history','paste','read','fullscreen']:
        return browser_demo(draw,box,kind,t,stage)
    draw.rounded_rectangle((x,y,x+w,y+h),radius=12*s,fill='#f4f6f9')
    if kind=='snap':
        for i,label in enumerate(['Your notes','The useful page']):
            bx=x+(20+i*315)*s
            draw.rounded_rectangle((bx,y+45*s,bx+295*s,y+h-25*s),radius=8*s,fill='#dde6f0')
            draw.text((bx+14*s,y+65*s),label,font=font(18*s,True),fill=BG)
    elif kind=='snip':
        draw.text((x+25*s,y+25*s),'Share only the useful part',font=font(22*s,True),fill=BG)
        draw.rectangle((x+50*s,y+85*s,x+w-50*s,y+h-45*s),outline='#477727',width=max(1,int(4*s)))
        draw.text((x+70*s,y+115*s),'Example content',font=font(24*s),fill=BG)
    elif kind=='emoji':
        draw.text((x+25*s,y+25*s),'Emoji panel',font=font(25*s,True),fill=BG)
        for i in range(5):
            ex=x+(65+i*110)*s; ey=y+h*.57
            draw.ellipse((ex-30*s,ey-30*s,ex+30*s,ey+30*s),fill='#e6bf57')
            for dx in [-10,10]: draw.ellipse((ex+(dx-3)*s,ey-10*s,ex+(dx+3)*s,ey-4*s),fill=BG)
            draw.arc((ex-15*s,ey-7*s,ex+15*s,ey+18*s),0,180,fill=BG,width=max(1,int(2*s)))
    elif kind=='clipboard':
        draw.text((x+25*s,y+22*s),'Clipboard history',font=font(25*s,True),fill=BG)
        for i,line in enumerate(['Shopping list','A harmless note','An address you chose to copy']):
            draw.rounded_rectangle((x+25*s,y+(65+i*65)*s,x+w-25*s,y+(115+i*65)*s),radius=7*s,fill='#e2e8f0')
            draw.text((x+40*s,y+(80+i*65)*s),line,font=font(17*s),fill=BG)
    elif kind=='textsize':
        draw.text((x+25*s,y+25*s),'Accessibility > Text size',font=font(24*s,True),fill=BG)
        draw.text((x+25*s,y+92*s),'Readable text',font=font((22 if stage<2 else 35)*s),fill=BG)
        draw.line((x+35*s,y+185*s,x+w-35*s,y+185*s),fill='#526275',width=max(1,int(3*s)))
        kx=x+w*(.35 if stage<2 else .6)
        draw.ellipse((kx-12*s,y+173*s,kx+12*s,y+197*s),fill='#477727')
        draw.text((x+25*s,y+h-50*s),'Apply',font=font(20*s,True),fill=BG)
    elif kind=='notifications':
        draw.text((x+25*s,y+25*s),'System > Notifications',font=font(24*s,True),fill=BG)
        for i,label in enumerate(['Noisy example app','Important reminders']):
            draw.text((x+25*s,y+(90+i*60)*s),label,font=font(20*s),fill=BG)
            draw.rounded_rectangle((x+w-105*s,y+(90+i*60)*s,x+w-30*s,y+(118+i*60)*s),radius=14*s,fill='#93a7b8' if i==0 else '#477727')
    else:
        draw.text((x+25*s,y+25*s),'Your Windows desktop',font=font(25*s,True),fill=BG)
        for i,label in enumerate(['Notes','Pictures','Browser']):
            bx=x+(50+i*180)*s
            draw.rounded_rectangle((bx,y+95*s,bx+75*s,y+155*s),radius=8*s,fill='#b8cadb')
            draw.text((bx,y+165*s),label,font=font(16*s),fill=BG)

def frame(scene, topic, width, height, t, duration, index, total):
    vertical=height>width; s=width/(720 if vertical else 1280)
    image=Image.new('RGB',(width,height),BG); d=ImageDraw.Draw(image)
    pad=42*s if vertical else 55*s
    d.text((pad,28*s),'CLICK & CHILL',font=font(19*s,True),fill=ACCENT)
    d.text((width-pad,30*s),'ONE SMALL FIX',font=font(15*s),fill=MUTED,anchor='ra')
    if vertical:
        textblock(d,(pad,98*s),scene['heading'],43*s,width-2*pad,bold=True,maxlines=3)
        if scene['kind']=='hook':
            computer(d,width*.34,320*s,1.3*s,t)
            textblock(d,(pad,600*s),scene['body'],28*s,width-2*pad,color=MUTED,maxlines=5)
        else:
            keycaps(d,topic['keys'],pad,280*s,width-2*pad,s)
            demo(d,(pad,390*s,width-2*pad,300*s),topic['demo'],t,index)
            textblock(d,(pad,735*s),scene['body'],25*s,width-2*pad,color=MUTED,maxlines=6)
    else:
        textblock(d,(pad,97*s),scene['heading'],46*s,width-2*pad,bold=True,maxlines=2)
        if scene['kind']=='hook':
            textblock(d,(pad,260*s),scene['body'],29*s,width*.59,color=MUTED,maxlines=5)
            computer(d,width*.77,285*s,1.1*s,t)
        else:
            keycaps(d,topic['keys'],pad,220*s,width*.42,s)
            textblock(d,(pad,322*s),scene['body'],25*s,width*.42,color=MUTED,maxlines=6)
            demo(d,(width*.52,220*s,width*.43,325*s),topic['demo'],t,index)
    # Captions are short phrases, approximately timed to each recorded sentence.
    chunks=scene['captions']; ci=min(len(chunks)-1,int(max(0,t-.12)/max(.1,duration-.4)*len(chunks)))
    caption=chunks[ci]
    face=font(23*s if vertical else 25*s,True)
    lines=wrap(caption,d,face,width-2*pad-30*s)
    cy=height-(170*s if vertical else 118*s)
    d.rounded_rectangle((pad-8*s,cy-8*s,width-pad+8*s,cy+len(lines)*32*s+10*s),radius=8*s,fill=PANEL)
    for i,line in enumerate(lines): d.text((width/2,cy+i*32*s),line,font=face,fill=WHITE,anchor='ma')
    d.text((pad,height-45*s),topic['platform'],font=font(14*s),fill=MUTED)
    d.text((width-pad,height-45*s),'Illustrated demo',font=font(14*s),fill=MUTED,anchor='ra')
    progress=(index+t/duration)/total
    d.rectangle((0,height-6*s,width*progress,height),fill=ACCENT)
    return image

def caption_chunks(text, words=8):
    parts=[]
    for sentence in re.split(r'(?<=[.!?])\s+',text):
        tokens=sentence.split()
        parts += [' '.join(tokens[i:i+words]) for i in range(0,len(tokens),words)]
    return parts or ['']

def srt_time(seconds):
    ms=round(seconds*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); sec,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{sec:02},{ms:03}'

def render_video(scenes,topic,folder,ffmpeg,vertical=False,progress=lambda _:None):
    width,height=(720,1280) if vertical else (1280,720)
    output=folder/('vertical.mp4' if vertical else 'youtube.mp4')
    partial=output.with_name(output.stem+'.partial.mp4')
    audio=folder/'narration.wav'
    command=[ffmpeg,'-y','-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-r','12','-i','pipe:0','-i',str(audio),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-r','24','-c:a','aac','-b:a','160k','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-shortest','-movflags','+faststart',str(partial)]
    with (folder/('vertical-render.log' if vertical else 'render.log')).open('wb') as log:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,creationflags=0x08000000)
        try:
            for index,scene in enumerate(scenes):
                progress(f"Rendering {'vertical' if vertical else 'YouTube'} scene {index+1}/{len(scenes)}")
                count=round(scene['duration']*12)
                for n in range(count):
                    process.stdin.write(frame(scene,topic,width,height,n/12,scene['duration'],index,len(scenes)).tobytes())
            process.stdin.close()
            code=process.wait(timeout=180)
            if code: raise RuntimeError('Video encoding failed; see the render log.')
        except BaseException:
            process.kill(); process.wait(); raise
    partial.replace(output)
    return output

def speech_and_timing(scenes,folder,voice,script):
    for i,scene in enumerate(scenes): scene['audio']=str(folder/f'scene-{i:02}.wav')
    manifest=folder/'speech.json'
    manifest.write_text(json.dumps({'voice':voice,'scenes':scenes}),encoding='utf-8')
    # Windows can hold the phonemizer's copied DLL until process exit. The parent
    # owns a confined temporary directory and removes it after those handles close.
    temporary_root=pathlib.Path(__file__).resolve().parent/'work'/'voice-temp'
    temporary_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root,ignore_cleanup_errors=True) as temporary:
        environment={**os.environ,'TMP':temporary,'TEMP':temporary}
        result=subprocess.run([sys.executable,str(script),str(manifest)],capture_output=True,text=True,timeout=600,creationflags=0x08000000,env=environment)
    # Antivirus/emulation can briefly keep a DLL open after its process exits.
    # Verify confinement before retrying; cleanup must not discard valid audio.
    temporary_path=pathlib.Path(temporary).resolve()
    temporary_path.relative_to(temporary_root.resolve())
    for attempt in range(8):
        if not temporary_path.exists():break
        try:shutil.rmtree(temporary_path);break
        except PermissionError:time.sleep(.25*(attempt+1))
    if result.returncode: raise RuntimeError('Neural narration failed: '+result.stderr[-800:])
    params=None; chunks=[]; cursor=0; subtitles=[]; number=1
    for scene in scenes:
        with wave.open(scene['audio'],'rb') as source:
            if params is None: params=source.getparams()
            if (source.getnchannels(),source.getsampwidth(),source.getframerate())!=(params.nchannels,params.sampwidth,params.framerate): raise RuntimeError('Inconsistent speech audio formats')
            raw=source.readframes(source.getnframes()); speech_duration=source.getnframes()/source.getframerate()
        # Snap duration to the render frame grid so audio and pictures stay aligned.
        duration=math.ceil((speech_duration+.4)*12)/12
        padding=round((duration-speech_duration)*params.framerate)*params.nchannels*params.sampwidth
        chunks.append(raw+b'\0'*padding)
        scene['duration']=duration; scene['captions']=caption_chunks(scene['voice'])
        step=speech_duration/len(scene['captions'])
        for ci,caption in enumerate(scene['captions']):
            start=cursor+ci*step; end=cursor+(ci+1)*step
            subtitles.append(f'{number}\n{srt_time(start)} --> {srt_time(end)}\n{caption}\n'); number+=1
        cursor+=duration
    with wave.open(str(folder/'narration.wav'),'wb') as dest:
        dest.setparams(params); dest.writeframes(b''.join(chunks))
    (folder/'captions.srt').write_text('\n'.join(subtitles),encoding='utf-8')
    return cursor

def thumbnail(topic,folder):
    im=Image.new('RGB',(1280,720),BG); d=ImageDraw.Draw(im)
    d.text((60,45),'CLICK & CHILL',font=font(24,True),fill=ACCENT)
    textblock(d,(60,150),topic['title'],65,850,bold=True,maxlines=4)
    keycaps(d,topic['keys'],60,560,920,1.1)
    computer(d,1010,280,1.05,1,happy=True)
    im.save(folder/'thumbnail.jpg',quality=95)

def verify_video(path,ffmpeg):
    if path.stat().st_size<20000: raise RuntimeError('Video is unexpectedly small')
    result=subprocess.run([ffmpeg,'-v','error','-i',str(path),'-f','null','-'],capture_output=True,timeout=180,creationflags=0x08000000)
    if result.returncode: raise RuntimeError('Video failed decoding validation')
    # Extract a mid-video frame for local inspection and confirm there is audio.
    probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(path)],capture_output=True,text=True,creationflags=0x08000000)
    if 'Video:' not in probe.stderr or 'Audio:' not in probe.stderr: raise RuntimeError('Missing video or audio stream')
    return {'decode':'passed','video':'H.264','audio':'AAC','bytes':path.stat().st_size}
