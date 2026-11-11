"""Multi-document isolation, explicit review, recovery and update privacy checks."""
from pathlib import Path
from io import BytesIO
import json
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import numpy as np
from PIL import Image
import app
from clearpage.pdf import engine
from clearpage import saved_work
from clearpage import workspace
from tests.test_quality import text_page, pdf


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old_root=app.WORK['data_root'];app.WORK['data_root']=self.root/'private'
        app.WORK.update(active=False,stop=False,zip_ready=False,saved_id='a'*24)
        self.client=app.app.test_client();self.client.get('/')
        self.headers={'X-ClearPage':'local'}

    def tearDown(self):
        app.WORK['stop']=True
        for job in app.JOBS.values():job['cancel']=True
        deadline=time.monotonic()+15
        while app.WORK['active'] and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(app.WORK['active'])
        for job in list(app.JOBS.values()):
            job['state']='ready';self.client.post('/api/clear/'+job['id'],json={},headers=self.headers)
        app.WORK['data_root']=self.old_root
        self.tmp.cleanup()

    def post(self,url,data):
        response=self.client.post(url,json=data,headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        return response.json

    def upload(self,negative=False):
        response=self.client.post('/api/upload',data=pdf([text_page(negative),text_page()]),
                                  headers=dict(self.headers,**{'X-Filename':'same-name.pdf'}))
        self.assertEqual(response.status_code,200,response.json)
        return app.JOBS[response.json['id']]

    def configure(self,job,**changes):
        data=dict(page=0,options={'method':'polarity'},keep=[],reviewed=[],confirmed=False)
        data.update(changes)
        return self.post('/api/view/'+job['id'],data)

    def wait_queue(self):
        deadline=time.monotonic()+20
        while app.WORK['active'] and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(app.WORK['active'],app.WORK['message'])

    def test_queue_isolation_explicit_review_and_complete_zip(self):
        first,second=self.upload(True),self.upload()
        for job in (first,second):self.configure(job)
        self.post('/api/queue',{'ids':[first['id'],second['id']]});self.wait_queue()
        self.assertEqual(first['state'],'review');self.assertEqual(second['state'],'review')
        self.assertEqual(first['ui']['keep'],[])
        self.assertTrue(first['results'][0]['uncertain'])
        blocked=self.client.post('/api/queue',json={'ids':[first['id']], 'export':True},headers=self.headers)
        self.assertEqual(blocked.status_code,400)
        blocked=self.client.post('/api/view/'+first['id'],json={'keep':[0],'reviewed':[],'confirmed':True},headers=self.headers)
        self.assertEqual(blocked.status_code,400)
        self.configure(first,keep=[0],reviewed=[0],confirmed=True)
        self.configure(second,keep=[],reviewed=[],confirmed=True)
        self.post('/api/queue',{'ids':[first['id'],second['id']], 'export':True});self.wait_queue()
        self.assertTrue(app.WORK['zip_ready'],app.WORK['message'])
        response=self.client.get('/api/queue/download')
        with zipfile.ZipFile(BytesIO(response.data)) as archive:
            self.assertEqual(len(set(archive.namelist())),2)
            for name in archive.namelist():
                target=self.root/name;target.write_bytes(archive.read(name))
                self.assertEqual(len(engine.inspect_pdf(target)[1]),2)
                for page in (0,1):self.assertTrue(np.array_equal(engine.render_page(target,page),text_page()))

    def test_save_resume_keeps_repairs_arrays_and_review_choices(self):
        job=self.upload(True);self.configure(job)
        self.post('/api/queue',{'ids':[job['id']]});self.wait_queue()
        self.configure(job,keep=[0],reviewed=[0],confirmed=True,page=1)
        patch_image=Image.new('RGB',(12,8),(12,30,45))
        job['repairs']={0:[{'box':(10,10,22,18),'patch':patch_image,'text':'checked','method':'text'}]}
        job['auto_background']={0:[dict(indices=np.array([10,12],dtype=np.int64),pixels=np.array([[1,2,3],[4,5,6]],dtype=np.uint8))]}
        job['repair_color_settings']=(True,(0,))
        job['inversion_model']={'template':np.array([[.2,.4]],dtype=np.float32),'count':6}
        before=(job['dir']/'0-after.png').read_bytes()
        saved=self.post('/api/save-work',{})
        self.post('/api/clear/'+job['id'],{})
        self.post('/api/resume/'+saved['id'],{})
        restored=app.JOBS[job['id']]
        self.assertEqual(restored['ui']['keep'],[0]);self.assertEqual(restored['ui']['page'],1)
        self.assertEqual(restored['repair_color_settings'],(True,(0,)))
        self.assertTrue(np.array_equal(restored['inversion_model']['template'],job['inversion_model']['template']))
        self.assertTrue(np.array_equal(restored['repairs'][0][0]['patch'],patch_image))
        self.assertTrue(np.array_equal(restored['auto_background'][0][0]['indices'],[10,12]))
        self.assertEqual((restored['dir']/'0-after.png').read_bytes(),before)
        self.assertNotIn('pending_repair',restored)
        self.assertEqual(len(self.client.get('/api/saved').json),1)

    def test_failed_save_preserves_previous_checkpoint(self):
        self.upload();saved=self.post('/api/save-work',{})
        path=app.WORK['data_root']/'saved-work'/(saved['id']+'.zip');before=path.read_bytes()
        with patch.object(saved_work,'MAX_BYTES',1):
            response=self.client.post('/api/save-work',json={},headers=self.headers)
        self.assertEqual(response.status_code,400);self.assertEqual(path.read_bytes(),before)

    def test_corrupt_or_traversal_snapshot_cannot_escape(self):
        store=self.root/'saved';store.mkdir();target=self.root/'restore';target.mkdir()
        with zipfile.ZipFile(store/('b'*24+'.zip'),'w') as archive:
            archive.writestr('../escape.txt','bad')
        with self.assertRaises(ValueError):saved_work.load(store,'b'*24,target)
        self.assertFalse((self.root/'escape.txt').exists());self.assertEqual(list(target.iterdir()),[])

    def test_queue_preflight_and_busy_guard(self):
        job=self.upload()
        response=self.client.post('/api/queue',json={'ids':[job['id'],'missing']},headers=self.headers)
        self.assertEqual(response.status_code,400);self.assertFalse(app.WORK['active']);self.assertEqual(job['state'],'ready')
        app.WORK['active']=True
        try:
            for url in ('/api/save-work','/api/upload','/api/clear/'+job['id']):
                self.assertEqual(self.client.post(url,json={},headers=self.headers).status_code,400)
            self.assertEqual(self.client.get('/api/workspace').status_code,200)
            self.post('/api/queue/stop',{});self.assertTrue(app.WORK['stop'])
        finally:app.WORK['active']=False

    def test_settings_change_invalidates_old_export_and_confirmation(self):
        job=self.upload();self.configure(job)
        self.post('/api/queue',{'ids':[job['id']]});self.wait_queue()
        self.configure(job,confirmed=True)
        self.configure(job,dirty=True,options={'method':'preset','auto_restore':False})
        self.assertEqual(job['state'],'ready');self.assertFalse(job['results']);self.assertFalse(job['ui']['confirmed'])
        self.assertEqual(self.client.post('/api/export/'+job['id'],json={'keep':[]},headers=self.headers).status_code,400)

    def test_presets_clear_document_specific_inversion_pages(self):
        self.post('/api/presets',{'name':'Negative pages','options':{'method':'polarity','polarity_pages':[1,2]}})
        item=self.client.get('/api/presets').json[0]
        self.assertEqual(item['options']['polarity_pages'],[])

    def test_word_changes_invalidate_review_and_zip(self):
        job=self.upload()
        job['ui']={'confirmed':True,'reviewed':[0,1]}
        app.WORK['zip_ready']=True
        app.require_new_review(job,0)
        self.assertFalse(job['ui']['confirmed']);self.assertEqual(job['ui']['reviewed'],[1])
        self.assertFalse(app.WORK['zip_ready'])

    def test_changes_overlay_only_colors_modified_pixels(self):
        job=self.upload();original=Image.open(app.original(job,0)).convert('RGB')
        after=np.array(original);after[10:20,10:20]=100;Image.fromarray(after).save(app.image_path(job,0,True))
        response=self.client.get('/api/changes/'+job['id']+'/0')
        image=np.array(Image.open(BytesIO(response.data)))
        self.assertTrue(np.array_equal(image[25:],after[25:]))
        self.assertFalse(np.array_equal(image[10:20,10:20],after[10:20,10:20]))

    def test_update_checker_waits_for_windows_asset_and_ignores_external_links(self):
        from clearpage import updates
        newer = str(int(updates.VERSION.split('.')[0])+1)+'.0'
        payload={'tag_name':'v'+newer,'draft':False,'prerelease':False,'html_url':'https://untrusted.example','assets':[]}
        for ready in (False,True):
            updates._cache.clear()
            if ready:payload['assets']=[{'name':f'ClearPage-{newer}-Windows-x64.zip','state':'uploaded','size':100,'browser_download_url':f'https://github.com/invicto-labs/Watermark-remover/releases/download/v{newer}/ClearPage-{newer}-Windows-x64.zip'}]
            with patch('urllib.request.urlopen',return_value=BytesIO(json.dumps(payload).encode())) as call:
                response=workspace.latest_release()
                self.assertEqual(response['available'],ready)
                self.assertTrue(response['url'].startswith(workspace.RELEASES))
                self.assertNotIn('untrusted.example',response['url'])
                self.assertIsNone(call.call_args.args[0].data)
        updates._cache.clear()


if __name__=='__main__':unittest.main()
