"""Offline regression checks. No real YouTube calls or credentials are used."""
import datetime as dt, json, pathlib, tempfile, unittest, urllib.error, urllib.request, wave
from unittest.mock import patch, MagicMock
import pipeline, render, youtube
from catalog import TOPICS

class NeuralNarration(unittest.TestCase):
    def test_neural_audio_drives_scene_lengths_and_subtitles(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pathlib.Path(directory)
            scenes=[{'voice':'Hold Control and tap M.'},{'voice':'The browser is quiet again.'}]
            def generate(command,**kwargs):
                self.assertEqual(pathlib.Path(command[1]).name,'speech.py')
                manifest=json.loads(pathlib.Path(command[2]).read_text())
                self.assertEqual(manifest['voice'],'am_michael')
                for scene in manifest['scenes']:
                    with wave.open(scene['audio'],'wb') as audio:
                        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000)
                        audio.writeframes(b'\0\0'*24000)
                return type('Result',(),{'returncode':0,'stderr':''})()
            with patch.object(render.subprocess,'run',side_effect=generate):
                duration=render.speech_and_timing(scenes,folder,'am_michael',pipeline.ROOT/'speech.py')
            self.assertAlmostEqual(duration,34/12)
            self.assertIn('Hold Control',(folder/'captions.srt').read_text())
            with wave.open(str(folder/'narration.wav')) as audio:
                self.assertEqual(audio.getframerate(),24000)
                self.assertAlmostEqual(audio.getnframes()/24000,duration)
    def test_neural_failure_is_reported_without_legacy_voice_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(render.subprocess,'run',return_value=type('Result',(),{'returncode':1,'stderr':'Model missing'})()) as run:
                with self.assertRaisesRegex(RuntimeError,'Neural narration failed: Model missing'):
                    render.speech_and_timing([{'voice':'Test narration.'}],pathlib.Path(directory),'am_michael',pipeline.ROOT/'speech.py')
                self.assertEqual(run.call_count,1)

class StudioFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.temp.name)
        self.patches=[patch.object(pipeline,'DB',self.root/'db.sqlite3'),patch.object(pipeline,'CONFIG',self.root/'settings.json'),patch.object(pipeline,'PRIVATE',self.root/'private'),patch.object(pipeline,'VIDEOS',self.root/'videos')]
        for item in self.patches:item.start()
        pipeline.initialize()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

class IsolatedStudio(StudioFixture):
    def test_catalog_facts_have_distinct_ids_and_official_sources(self):
        self.assertEqual(len({t['id'] for t in TOPICS}),len(TOPICS))
        for t in TOPICS:
            self.assertTrue(t['source'].startswith('https://support.microsoft.com/'))
            self.assertEqual(len(t['steps']),3)
    def test_reservation_does_not_repeat_topics(self):
        ids=[pipeline.reserve_topic()[1]['id'] for _ in TOPICS]
        self.assertEqual(len(set(ids)),len(TOPICS))
        with self.assertRaisesRegex(RuntimeError,'exhausted'):pipeline.reserve_topic()
    def test_production_lock_blocks_concurrent_work(self):
        with pipeline.production_lock():
            with self.assertRaisesRegex(RuntimeError,'already running'):
                with pipeline.production_lock():pass
    def test_stale_facts_block_production(self):
        topic={**TOPICS[0],'reviewed':'2020-01-01'}
        with self.assertRaisesRegex(RuntimeError,'fresh source'):pipeline.validate_topic(topic)
    def test_unapproved_source_is_rejected(self):
        topic={**TOPICS[0],'source':'https://example.test/fix'}
        with self.assertRaises(ValueError):pipeline.validate_topic(topic)
    def test_monday_slot_and_summer_timezone(self):
        date=dt.datetime(2026,10,12,10,tzinfo=dt.timezone.utc)
        self.assertEqual(pipeline.next_slot(date),'2026-10-12T17:00:00Z')
    def test_winter_timezone(self):
        date=dt.datetime(2026,10,26,10,tzinfo=dt.timezone.utc)
        self.assertEqual(pipeline.next_slot(date),'2026-10-26T18:00:00Z')
    def test_missed_slot_is_not_scheduled_in_past(self):
        date=dt.datetime(2026,10,12,17,tzinfo=dt.timezone.utc)
        self.assertEqual(pipeline.next_slot(date),'2026-10-14T17:00:00Z')
    def test_no_account_means_no_automatic_upload(self):
        with patch.object(pipeline,'produce') as produce:
            pipeline.tick(); produce.assert_not_called()
    def test_failed_production_is_persisted_and_can_be_diagnosed(self):
        with patch.object(pipeline,'validate_topic',side_effect=RuntimeError('Source needs checking')):
            with self.assertRaises(RuntimeError):pipeline.produce()
        job=pipeline.jobs()[0]; self.assertEqual(job['status'],'error'); self.assertEqual(job['error'],'Source needs checking')
    def test_ready_local_episode_can_be_rebuilt_without_reserving_new_topic(self):
        id,topic,folder=pipeline.reserve_topic(); pipeline.update_job(id,status='ready')
        with patch.object(pipeline,'validate_topic',side_effect=RuntimeError('Expected stop')):
            with self.assertRaises(RuntimeError):pipeline.produce(job_id=id)
        self.assertEqual(len(pipeline.jobs()),1)
    def test_uploaded_episode_cannot_be_rebuilt(self):
        id,_,_=pipeline.reserve_topic();pipeline.update_job(id,status='uploaded',video_id='already-uploaded')
        with self.assertRaises(ValueError):pipeline.produce(job_id=id)
    def test_interrupted_local_production_can_be_rebuilt_after_restart(self):
        id,_,_=pipeline.reserve_topic();pipeline.update_job(id,status='rendering')
        pipeline.recover_local_jobs()
        self.assertEqual(pipeline.get_job(id)['status'],'error')
    def test_running_producer_is_not_mistaken_for_a_crash(self):
        id,_,_=pipeline.reserve_topic();pipeline.update_job(id,status='rendering')
        with pipeline.production_lock():pipeline.recover_local_jobs()
        self.assertEqual(pipeline.get_job(id)['status'],'rendering')
    def test_all_topics_render_both_aspect_ratios(self):
        for topic in TOPICS:
            scenes=pipeline.build_scenes(topic,topic['problem']+' '+topic['joke'])
            for scene in scenes:scene['captions']=render.caption_chunks(scene['voice'])
            for w,h in [(1280,720),(720,1280)]:
                for i,scene in enumerate(scenes):
                    image=render.frame(scene,topic,w,h,1,15,i,len(scenes))
                    self.assertEqual(image.size,(w,h))
    def test_subtitle_time_rollover(self):
        self.assertEqual(render.srt_time(59.9996),'00:01:00,000')
        self.assertEqual(render.srt_time(3601.25),'01:00:01,250')
    def test_caption_chunks_retain_all_words(self):
        text='This is a small test. It has two sentences and no missing words.'
        self.assertEqual(' '.join(render.caption_chunks(text)).split(),text.split())
    def test_recovered_tab_changes_the_demo(self):
        topic=TOPICS[0];scene=pipeline.build_scenes(topic,topic['problem'])[1];scene['captions']=['Example']
        before=render.frame(scene,topic,1280,720,1,10,1,6)
        after=render.frame(scene,topic,1280,720,3,10,2,6)
        self.assertNotEqual(before.tobytes(),after.tobytes())

class UploadProtection(StudioFixture):
    def make_job(self):
        id,topic,folder=pipeline.reserve_topic();folder.mkdir()
        (folder/'youtube.mp4').write_bytes(b'local-test-file')
        (folder/'thumbnail.jpg').write_bytes(b'local-thumbnail')
        (folder/'episode.json').write_text(json.dumps({'checks':{'youtube':{'decode':'passed'}},'title':topic['title'],'description':'Example','tags':[]}))
        pipeline.update_job(id,status='ready')
        config=pipeline.settings();config['channel_id']='test-channel';config['expected_channel_id']='test-channel';pipeline.save_settings(config)
        return id,folder
    def test_no_second_upload_for_an_existing_remote_id(self):
        id,_=self.make_job();pipeline.update_job(id,video_id='remote-id')
        with patch.object(youtube,'service') as service:
            self.assertEqual(youtube._upload(id),'remote-id');service.assert_not_called()
    def test_different_channel_stops_before_upload(self):
        id,_=self.make_job()
        with patch.object(youtube,'service',return_value=MagicMock()),patch.object(youtube,'channel_identity',return_value={'id':'wrong-channel','title':'Wrong'}):
            with self.assertRaisesRegex(RuntimeError,'differs'):youtube._upload(id)
        self.assertEqual(pipeline.get_job(id)['status'],'ready')
    def test_ambiguous_upload_blocks_automatic_retry(self):
        id,_=self.make_job(); client=MagicMock();client.videos.return_value.insert.return_value.next_chunk.side_effect=TimeoutError()
        with patch.object(youtube,'ROOT',self.root),patch.object(youtube,'service',return_value=client),patch.object(youtube,'channel_identity',return_value={'id':'test-channel','title':'Test'}):
            with self.assertRaises(TimeoutError):youtube._upload(id)
            self.assertEqual(pipeline.get_job(id)['status'],'needs_attention')
            with self.assertRaisesRegex(RuntimeError,'Only a ready'):youtube._upload(id)
        self.assertEqual(client.videos.return_value.insert.call_count,1)
    def test_google_client_rejects_other_token_destinations(self):
        with self.assertRaises(ValueError):youtube.import_client({'installed':{'client_id':'x.apps.googleusercontent.com','client_secret':'fake','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://example.test/token'}})
    def test_windows_secret_encryption_round_trip(self):
        encrypted=youtube.protect(b'local-test-secret')
        self.assertNotIn(b'local-test-secret',encrypted)
        self.assertEqual(youtube.protect(encrypted,decode=True),b'local-test-secret')

class DashboardRoutes(StudioFixture):
    def setUp(self):
        super().setUp()
        import studio, http.server, threading
        self.studio=studio
        (self.root/'index.html').write_text('<html>__SESSION__</html>')
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),studio.Handler)
        self.port=self.server.server_address[1]
        self.extra_patches=[patch.object(studio,'PORT',self.port),patch.object(pipeline,'ROOT',self.root)]
        for p in self.extra_patches:p.start()
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close()
        for p in reversed(self.extra_patches):p.stop()
        super().tearDown()
    def request(self,path,data=None,headers=None):
        r=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=data,headers=headers or {})
        return urllib.request.urlopen(r,timeout=5)
    def test_dashboard_replaces_the_session_marker(self):
        with self.request('/') as r: text=r.read().decode()
        self.assertIn(self.studio.SESSION,text);self.assertNotIn('__SESSION__',text)
    def test_foreign_origin_cannot_start_production(self):
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.request('/api/produce',b'{}',{'Origin':'https://example.test','X-Studio-Token':self.studio.SESSION})
        self.assertEqual(result.exception.code,403)
    def test_missing_session_cannot_start_production(self):
        with self.assertRaises(urllib.error.HTTPError) as result:self.request('/api/produce',b'{}')
        self.assertEqual(result.exception.code,403)
    def test_valid_button_request_queues_one_local_job(self):
        with patch.object(self.studio,'launch_job') as launch:
            with self.request('/api/produce',b'{}',{'X-Studio-Token':self.studio.SESSION}) as r:self.assertEqual(r.status,202)
            self.assertEqual(launch.call_count,1)
    def test_batch_limits_are_enforced(self):
        with patch.object(self.studio,'launch_job') as launch:
            with self.assertRaises(urllib.error.HTTPError) as result:
                self.request('/api/batch',b'{"count":999}',{'X-Studio-Token':self.studio.SESSION})
            self.assertEqual(result.exception.code,400);launch.assert_not_called()
    def test_media_byte_ranges_support_video_seeking(self):
        path=self.root/'videos'/'test.mp4';path.write_bytes(b'0123456789')
        with self.request('/videos/test.mp4',headers={'Range':'bytes=2-5'}) as r:
            self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345')
    def test_invalid_range_has_an_explicit_error(self):
        (self.root/'videos'/'test.mp4').write_bytes(b'0123456789')
        with self.assertRaises(urllib.error.HTTPError) as result:self.request('/videos/test.mp4',headers={'Range':'bytes=-'})
        self.assertEqual(result.exception.code,416)
    def test_file_traversal_cannot_read_other_files(self):
        (self.root/'private.txt').write_text('not video content')
        with self.assertRaises(urllib.error.HTTPError) as result:self.request('/videos/%2e%2e/private.txt')
        self.assertEqual(result.exception.code,404)

if __name__=='__main__':unittest.main(verbosity=2)
