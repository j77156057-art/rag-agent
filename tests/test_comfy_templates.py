import unittest, tempfile, os, json
from unittest.mock import patch
import game_workbench as gw
class T(unittest.TestCase):
 def test_z(self): self.assertTrue(gw.comfy_template_workflow('z-image-turbo')['ok'])
 def test_unknown(self): self.assertFalse(gw.comfy_template_workflow('x')['ok'])
 def test_apply_keeps_negative_prompt_separate(self):
  w=gw.comfy_template_workflow('z-image-turbo')['workflow']
  r=gw.comfy_apply_parameters(w, {'prompt':'good','negative_prompt':'bad'})
  self.assertEqual(r['workflow']['3']['inputs']['prompt'],'good')
  self.assertEqual(r['workflow']['4']['inputs']['prompt'],'bad')
 def test_template_exposes_provenance(self):
  t=gw.comfy_templates()['templates'][0]
  self.assertTrue(t['author']); self.assertTrue(t['source_url']); self.assertTrue(t['license'])
 def test_3d_extension_policy_is_documented_in_import_code(self):
  import inspect
  src=inspect.getsource(gw.comfy_import)
  self.assertIn("asset_kind", src); self.assertIn(".glb", src)
 def test_provenance_validation_requires_review_without_license(self):
  self.assertTrue(gw.comfy_validate_provenance({'author':'a','source_url':'https://example.com'})['review_required'])
  self.assertFalse(gw.comfy_validate_provenance({'source_url':'javascript:bad'})['ok'])
 def test_retry_limits_and_requires_failed_status(self):
  gw._COMFY_JOBS['old']={'status':'completed','workflow':{}}
  self.assertFalse(gw.comfy_retry('old')['ok'])
  gw._COMFY_JOBS['bad']={'status':'failed','workflow':{},'retry_count':2}
  self.assertFalse(gw.comfy_retry('bad')['ok'])
  gw._COMFY_JOBS.pop('old',None); gw._COMFY_JOBS.pop('bad',None)
 def test_queue_rejects_ui_workflow_with_actionable_error(self):
  r=gw.comfy_ui_to_api_workflow({'nodes': [], 'links': []})
  self.assertFalse(r['ok']); self.assertIn('可提交节点', r['error'])
 def test_ui_workflow_conversion_maps_links_and_widgets(self):
  ui={'nodes':[{'id':1,'type':'Source','inputs':[],'outputs':[{'name':'OUT'}]}, {'id':2,'type':'Sink','inputs':[{'name':'src','link':7},{'name':'value','link':None}],'widgets_values':[42]}], 'links':[[7,1,0,2,0,'X']]}
  r=gw.comfy_ui_to_api_workflow(ui)
  self.assertTrue(r['ok']); self.assertEqual(r['workflow']['2']['inputs']['src'],['1',0]); self.assertEqual(r['workflow']['2']['inputs']['value'],42)
 def test_h3_legacy_uuid_maps_to_installed_plugin_name(self):
  ui={'nodes':[{'id':105,'type':'4c314f31-ecda-4b08-ae98-faaba1bf613f','inputs':[],'widgets_values':[]}], 'links':[]}
  r=gw.comfy_ui_to_api_workflow(ui)
  self.assertEqual(r['workflow']['105']['class_type'],'TESpeedMiniMaxH3')
 def test_ui_conversion_skips_markdown_annotations(self):
  ui={'nodes':[{'id':1,'type':'MarkdownNote','inputs':[],'widgets_values':['help']},
               {'id':2,'type':'RealNode','inputs':[],'widgets_values':[]}], 'links':[]}
  r=gw.comfy_ui_to_api_workflow(ui)
  self.assertTrue(r['ok']); self.assertNotIn('1', r['workflow']); self.assertIn('2', r['workflow'])
 def test_ui_conversion_accepts_union_input_types(self):
  ui={'nodes':[{'id':1,'type':'Source','inputs':[],'outputs':[{'type':'FLOAT'}]}, {'id':2,'type':'Sink','inputs':[{'name':'value','type':'FLOAT,INT','link':7}],'widgets_values':[]}], 'links':[[7,1,0,2,0,'FLOAT']]}
  self.assertTrue(gw.comfy_ui_to_api_workflow(ui)['ok'])
 def test_ui_conversion_skips_missing_load_images(self):
  ui={'nodes':[{'id':1,'type':'LoadImage','inputs':[],'widgets_values':['missing.png']}, {'id':2,'type':'Sink','inputs':[{'name':'image','link':7}],'widgets_values':[]}], 'links':[[7,1,0,2,0,'IMAGE']]}
  r=gw.comfy_ui_to_api_workflow(ui)
  self.assertTrue(r['ok']); self.assertNotIn('1',r['workflow']); self.assertNotIn('2',r['workflow'])
 def test_project_workflow_path_precedes_environment(self):
  with tempfile.TemporaryDirectory() as d:
   project=os.path.join(d,'project'); os.makedirs(project)
   selected=os.path.join(project,'project.json'); alternate=os.path.join(d,'env.json')
   payload={'nodes':[]}
   for path in (selected, alternate):
    with open(path,'w',encoding='utf-8') as f: json.dump(payload,f)
   with open(os.path.join(project,'.docmind_comfy.json'),'w',encoding='utf-8') as f:
    json.dump({'h3_workflow':'project.json'},f)
   with patch.dict(os.environ, {'DOCMIND_PROJECT_ROOT':project,'DOCMIND_COMFY_WORKFLOW_H3':alternate}, clear=False):
    self.assertEqual(os.path.normcase(gw._comfy_workflow_path()), os.path.normcase(selected))
if __name__=='__main__': unittest.main()
