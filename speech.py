"""Offline male neural narration. Runs in a separate process to release model RAM."""
import json, pathlib, re, sys, wave

ROOT=pathlib.Path(__file__).resolve().parent
WORK=ROOT/'work'
sys.path.insert(0,str(WORK/'python-packages'))

def spoken_text(text):
    text=text.replace('Ctrl','Control').replace('Win +','Windows plus')
    text=text.replace('…','...').replace('—',', ').replace('–',', ')
    text=re.sub(r'\bF11\b','F eleven',text)
    return re.sub(r'\s+',' ',text).strip()

def synthesize(manifest):
    import numpy as np
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    data=json.loads(pathlib.Path(manifest).read_text(encoding='utf-8'))
    if data['voice']!='am_michael': raise ValueError('Unsupported narrator; use am_michael')
    runtime=WORK/'runtime'/'voice'
    if not all((runtime/file).exists() for file in ['kokoro-v1.0.onnx','voices-v1.0.bin']):
        raise RuntimeError('Neural voice files are missing; restore the voice runtime first')
    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(runtime/'kokoro-v1.0.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    engine=Kokoro.from_session(session,str(runtime/'voices-v1.0.bin'))
    for scene in data['scenes']:
        samples,rate=engine.create(spoken_text(scene['voice']),voice=data['voice'],speed=1.0,lang='en-us')
        samples=np.asarray(samples).ravel()
        if len(samples)<rate*.15 or not np.isfinite(samples).all():raise RuntimeError('Narration is empty or invalid')
        pcm=(np.clip(samples,-1,1)*32767).astype('<i2')
        with wave.open(scene['audio'],'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate);output.writeframes(pcm.tobytes())
    print('Neural male narration complete',flush=True)

if __name__=='__main__':synthesize(sys.argv[1])
