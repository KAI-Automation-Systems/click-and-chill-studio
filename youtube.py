"""Official OAuth/API integration. Credentials are encrypted with Windows DPAPI."""
import ctypes, ctypes.wintypes as wt, datetime as dt, json, os, pathlib, time
from pipeline import PRIVATE, ROOT, atomic_json, event, get_job, now, settings, update_job, production_lock

SCOPES=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly']

class Blob(ctypes.Structure):
    _fields_=[('cbData',wt.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def protect(data,decode=False):
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    buffer=ctypes.create_string_buffer(data); source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte))); out=Blob()
    function=crypt.CryptUnprotectData if decode else crypt.CryptProtectData
    function.restype=wt.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(out.pbData,out.cbData)
    finally:
        kernel.LocalFree.argtypes=[ctypes.c_void_p]; kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(out.pbData)

def save_secret(name,data):
    PRIVATE.mkdir(parents=True,exist_ok=True)
    path=PRIVATE/(name+'.dpapi'); temp=path.with_suffix('.tmp')
    temp.write_bytes(protect(json.dumps(data).encode())); os.replace(temp,path)

def read_secret(name):
    return json.loads(protect((PRIVATE/(name+'.dpapi')).read_bytes(),decode=True))

def import_client(data):
    installed=data.get('installed')
    if not isinstance(installed,dict): raise ValueError('Choose a Google OAuth Desktop app JSON file, not an API key or service account file.')
    required=['client_id','client_secret','auth_uri','token_uri']
    if any(not isinstance(installed.get(k),str) or not installed[k] for k in required): raise ValueError('The OAuth file is incomplete')
    if installed['token_uri']!='https://oauth2.googleapis.com/token' or not installed['auth_uri'].startswith('https://accounts.google.com/'):
        raise ValueError('Only official Google OAuth endpoints are accepted')
    if not installed['client_id'].endswith('.apps.googleusercontent.com'): raise ValueError('Invalid Google client ID')
    save_secret('client_secret',{'installed':installed})
    event('Google desktop app credentials imported and encrypted')

def credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not (PRIVATE/'token.dpapi').exists(): raise RuntimeError('Connect your new YouTube channel first')
    cred=Credentials.from_authorized_user_info(read_secret('token'),SCOPES)
    if cred.expired and cred.refresh_token:
        cred.refresh(Request()); save_secret('token',json.loads(cred.to_json()))
    if not cred.valid: raise RuntimeError('Google sign-in needs to be renewed')
    return cred

def service(cred=None):
    from googleapiclient.discovery import build
    return build('youtube','v3',credentials=cred or credentials(),cache_discovery=False)

def channel_identity(client):
    response=client.channels().list(part='snippet',mine=True).execute()
    items=response.get('items',[])
    if len(items)!=1: raise RuntimeError('Select exactly one YouTube channel during Google sign-in. Create your new channel if none exists.')
    return {'id':items[0]['id'],'title':items[0]['snippet']['title']}

def authorize():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not (PRIVATE/'client_secret.dpapi').exists(): raise RuntimeError('Import the Google Desktop app JSON first')
    flow=InstalledAppFlow.from_client_config(read_secret('client_secret'),SCOPES)
    cred=flow.run_local_server(host='127.0.0.1',port=0,open_browser=True,authorization_prompt_message='',success_message='YouTube is connected. Return to Click & Chill Studio to choose this channel.',access_type='offline',prompt='consent',timeout_seconds=300)
    identity=channel_identity(service(cred))
    save_secret('token',json.loads(cred.to_json()))
    return identity

def upload(job_id,publish_at=None,private_test=False):
    with production_lock('upload-'+job_id):
        return _upload(job_id,publish_at,private_test)

def _upload(job_id,publish_at=None,private_test=False):
    from googleapiclient.http import MediaFileUpload
    job=get_job(job_id); config=settings()
    if job['video_id']: return job['video_id']
    if job['status']!='ready': raise RuntimeError('Only a ready, checked video can be uploaded. Ambiguous uploads are never retried automatically.')
    if not config['channel_id']: raise RuntimeError('Choose and connect your new channel first')
    if config.get('expected_channel_id') and config['channel_id']!=config['expected_channel_id']: raise RuntimeError('Target channel is not the new channel specified by the owner')
    client=service(); identity=channel_identity(client)
    if identity['id']!=config['channel_id']: raise RuntimeError('Connected Google channel differs from the selected target; publication stopped')
    folder=pathlib.Path(job['folder']).resolve()
    if not folder.is_relative_to((ROOT/'videos').resolve()): raise RuntimeError('Invalid video folder')
    episode=json.loads((folder/'episode.json').read_text(encoding='utf-8'))
    if episode['checks']['youtube']['decode']!='passed': raise RuntimeError('Video has not passed decoding checks')
    status={'privacyStatus':'private' if private_test or publish_at else 'public','selfDeclaredMadeForKids':False,'containsSyntheticMedia':True}
    if publish_at and not private_test:
        target=dt.datetime.fromisoformat(publish_at.replace('Z','+00:00'))
        if target<=dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10): raise RuntimeError('The publication time must be in the future')
        status['publishAt']=publish_at
    body={'snippet':{'title':episode['title'],'description':episode['description'],'tags':episode['tags'],'categoryId':'27','defaultLanguage':'en','defaultAudioLanguage':'en'},'status':status}
    media=MediaFileUpload(str(folder/'youtube.mp4'),mimetype='video/mp4',chunksize=8*1024*1024,resumable=True)
    request=client.videos().insert(part='snippet,status',body=body,media_body=media)
    update_job(job_id,status='uploading',message='Uploading to '+identity['title'],publish_at=publish_at or '')
    try:
        response=None
        while response is None:
            progress,response=request.next_chunk(num_retries=3)
            if progress: update_job(job_id,message=f'Uploading: {round(progress.progress()*100)}%')
        video_id=response['id']
        # Persist remote identity before optional thumbnail/processing operations.
        update_job(job_id,video_id=video_id,status='uploaded',message='Private test uploaded' if private_test else 'Scheduled on YouTube' if publish_at else 'Published on YouTube')
        atomic_json(folder/'youtube-receipt.json',{'id':video_id,'channel':identity,'uploaded_at':now(),'requested_status':status,'returned_status':response.get('status')})
    except Exception as exc:
        update_job(job_id,status='needs_attention',message='Upload needs checking in YouTube Studio before any retry',error='An upload was started but its final result is uncertain: '+type(exc).__name__)
        event('Upload outcome needs checking; automatic retries stopped for '+job_id)
        raise
    finally:
        media.stream().close()
    thumbnail_media=MediaFileUpload(str(folder/'thumbnail.jpg'),mimetype='image/jpeg')
    try:
        client.thumbnails().set(videoId=video_id,media_body=thumbnail_media).execute()
    except Exception:
        update_job(job_id,message='Uploaded; custom thumbnail unavailable (channel eligibility or API error). YouTube will use a video frame.')
    finally:
        thumbnail_media.stream().close()
    event('YouTube upload completed: '+video_id)
    return video_id
