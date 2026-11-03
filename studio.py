"""Local production dashboard. No cloud login is needed to create videos."""
import argparse, functools, http.server, json, logging, mimetypes, os, pathlib, secrets, threading, time, urllib.parse
import pipeline
from catalog import TOPICS

SESSION=secrets.token_urlsafe(32)
BUSY=False
BUSY_LOCK=threading.Lock()
PORT=8765

def launch_job(function):
    global BUSY
    with BUSY_LOCK:
        if BUSY: raise RuntimeError('A production job is already running')
        BUSY=True
    def run():
        global BUSY
        try: function()
        except Exception as exc: pipeline.event('Job stopped: '+str(exc))
        finally:
            with BUSY_LOCK: BUSY=False
    threading.Thread(target=run,daemon=True).start()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def response_headers(self,status,content_type,length,extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(length))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        if extra:
            for key,value in extra.items(): self.send_header(key,value)
        self.end_headers()
    def send_json(self,value,status=200):
        data=json.dumps(value,ensure_ascii=False).encode(); self.response_headers(status,'application/json; charset=utf-8',len(data)); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get('Host') not in [f'127.0.0.1:{PORT}',f'localhost:{PORT}']:
            return self.send_json({'error':'Invalid host'},403)
        path=urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path=='/':
            page=(pipeline.ROOT/'index.html').read_text(encoding='utf-8').replace('__SESSION__',SESSION)
            data=page.encode(); self.response_headers(200,'text/html; charset=utf-8',len(data)); self.wfile.write(data)
        elif path=='/api/state':
            result=pipeline.status(); result['busy']=BUSY
            result['topics']=[{key:t[key] for key in ['id','title','problem','benefit','platform','source','reviewed']} for t in TOPICS]
            self.send_json(result)
        elif path.startswith('/videos/'):
            target=(pipeline.ROOT/path.lstrip('/')).resolve()
            base=pipeline.VIDEOS.resolve()
            if not target.is_relative_to(base) or not target.is_file() or target.suffix not in ['.mp4','.jpg','.srt','.txt','.json']:
                return self.send_json({'error':'File not found'},404)
            size=target.stat().st_size; start=0; end=size-1; status=200; extra={'Accept-Ranges':'bytes'}
            byte_range=self.headers.get('Range')
            if byte_range:
                import re
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',byte_range)
                if not match or not any(match.groups()): return self.send_json({'error':'Invalid byte range'},416)
                if not match[1]: start=max(0,size-int(match[2]))
                else: start=int(match[1]); end=min(end,int(match[2])) if match[2] else end
                if start>end or start>=size: return self.send_json({'error':'Invalid byte range'},416)
                status=206; extra['Content-Range']=f'bytes {start}-{end}/{size}'
            if target.suffix in ['.srt','.txt']: content_type='text/plain; charset=utf-8'
            else: content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            self.response_headers(status,content_type,end-start+1,extra)
            try:
                with target.open('rb') as file:
                    file.seek(start); remaining=end-start+1
                    while remaining:
                        data=file.read(min(65536,remaining))
                        if not data: break
                        self.wfile.write(data); remaining-=len(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
        else: self.send_json({'error':'Not found'},404)
    def do_POST(self):
        expected=f'http://127.0.0.1:{PORT}'
        if self.headers.get('Host') not in [f'127.0.0.1:{PORT}',f'localhost:{PORT}'] or self.headers.get('X-Studio-Token')!=SESSION or self.headers.get('Origin') not in [None,expected,f'http://localhost:{PORT}']:
            return self.send_json({'error':'This action must come from your local Studio page'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>65536: return self.send_json({'error':'Request too large'},413)
            data=json.loads(self.rfile.read(length) or b'{}')
            if self.path=='/api/produce':
                launch_job(lambda:pipeline.produce())
            elif self.path=='/api/batch':
                count=data.get('count',3)
                if not isinstance(count,int) or not 1<=count<=5: raise ValueError('Choose a batch of one to five videos')
                launch_job(lambda:pipeline.produce_batch(count))
            elif self.path=='/api/rebuild':
                job=pipeline.get_job(str(data['id']))
                if job['status'] not in ['ready','error'] or job['video_id']: raise ValueError('This local episode cannot be rebuilt')
                launch_job(lambda:pipeline.produce(job_id=job['id']))
            elif self.path=='/api/pause':
                config=pipeline.settings(); config['automatic']=False; pipeline.save_settings(config)
            else: return self.send_json({'error':'Not found'},404)
            self.send_json({'ok':True},202)
        except Exception as exc: self.send_json({'error':str(exc)},400)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    global PORT
    PORT=args.port
    pipeline.initialize()
    logging.basicConfig(filename=str(pipeline.PRIVATE/'studio.log'),level=logging.INFO,format='%(asctime)s %(message)s')
    # One port, one dashboard. No startup upload or automatic production by default.
    server=http.server.ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    pipeline.recover_local_jobs()
    print(f'Click & Chill Studio is ready at http://127.0.0.1:{PORT}',flush=True)
    try: server.serve_forever()
    finally: server.server_close()

if __name__=='__main__': main()
