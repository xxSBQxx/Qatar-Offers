"""Offline API smoke tests for authentication, visibility, management, and scheduler settings."""
import os,tempfile,json,datetime as dt
os.environ['DATABASE_PATH']=tempfile.mktemp(suffix='.sqlite3')
os.environ['ADMIN_USERNAME']='admin-test';os.environ['ADMIN_PASSWORD']='ThisIsATestAdminPassword123!';os.environ['SESSION_SECRET']='integration-test-secret-long-enough';os.environ['SESSION_SECURE']='0'
from fastapi.testclient import TestClient
import api
with TestClient(api.app) as client:
 assert client.get('/api/health').status_code==200
 assert client.get('/').status_code==200 and client.get('/admin').status_code==200
 for route in ('/privacy','/terms','/cookies','/report','/browse.html'):
  assert client.get(route).status_code==200,route
 assert client.post('/api/auth/register',json={'username':'no-consent','password':'UserPasswordLong123!'}).status_code==400
 runtime=json.loads(api.build_runtime_sources().read_text(encoding='utf-8'))
 assert len(runtime['malls'])>=3 and len(runtime['discovery_queries'])>=5 and len(runtime['social_discovery_queries'])>=3,runtime
 r=client.post('/api/admin/login',json={'username':'admin-test','password':'ThisIsATestAdminPassword123!'})
 assert r.status_code==200,r.text
 # Reports require explicit privacy acknowledgement and are manageable only by admins.
 bad=client.post('/api/reports',json={'report_type':'expired_offer','message':'This offer has expired and the source page no longer lists it.','consent':False})
 assert bad.status_code==400,bad.text
 rep=client.post('/api/reports',json={'report_type':'expired_offer','offer_url':'https://8.8.8.8/offers/old','source_url':'https://8.8.8.8/original','message':'This offer has expired and the source page no longer lists it.','consent':True})
 assert rep.status_code==200,rep.text
 rid=rep.json()['report_id']
 assert client.get('/api/admin/reports?status=all').status_code==200
 assert client.patch(f'/api/admin/reports/{rid}',json={'status':'resolved'}).status_code==200
 # Discovery leads are review-only until an administrator promotes them.
 state_path=api.ROOT/'data'/'state.json';saved_state=state_path.read_text(encoding='utf-8')
 try:
  state_path.write_text(json.dumps({'offers':{},'discovered_sources':{'disc-test':{'id':'disc-test','url':'https://8.8.8.8/retail-offers','title':'Retail offers lead','status':'pending','discovered_via':'Google Programmable Search'}}}),encoding='utf-8')
  leads=client.get('/api/admin/discoveries?status=pending').json();assert any(x['id']=='disc-test' for x in leads)
  promoted=client.post('/api/admin/discoveries/disc-test/promote',json={});assert promoted.status_code==200,promoted.text
  assert any(x['url']=='https://8.8.8.8/retail-offers' for x in client.get('/api/admin/sources').json())
 finally:state_path.write_text(saved_state,encoding='utf-8')
 # Seed a pending offer, verify it is hidden, then manually approve it.
 with api.connect() as c:
  c.execute("INSERT INTO offers(id,store_name,title,description,category,discount_text,source_url,verification_status,verification_reason,last_checked_at,first_seen,last_seen,is_active,manual_status,evidence,confidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('test-offer','Test Merchant','40% off test item','Source text','fashion','40% off','https://example.qa/offers','pending','Needs review',api.now(),api.today(),api.today(),0,'auto','Test source evidence',0.8,api.now()))
 assert client.get('/api/offers').json()['total']==0
 r=client.post('/api/admin/offers/test-offer/review',json={'decision':'approve','reason':'Verified in source'})
 assert r.status_code==200,r.text
 assert client.get('/api/offers').json()['total']==1
 assert client.get('/api/offers/test-offer').status_code==200
 assert client.get('/api/offers').json()['offers'][0]['views']==1
 # Expired and stale-but-not-yet-reprocessed offers must never appear in public listings.
 stale_time=(dt.datetime.now(api.QATAR)-dt.timedelta(days=12)).isoformat(timespec='seconds')
 with api.connect() as c:
  c.execute("INSERT INTO offers(id,store_name,title,category,source_url,verification_status,verified_at,last_checked_at,first_seen,last_seen,is_active,manual_status,evidence,confidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('stale-offer','Stale Merchant','Stale 30% offer','fashion','https://stale.example.qa/offer','verified',stale_time,stale_time,api.today(),api.today(),1,'auto','30% offer',0.8,api.now()))
  c.execute("INSERT INTO offers(id,store_name,title,category,source_url,verification_status,verified_at,last_checked_at,first_seen,last_seen,end_date,is_active,manual_status,evidence,confidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('expired-offer','Expired Merchant','Expired 30% offer','fashion','https://expired.example.qa/offer','verified',api.now(),api.now(),api.today(),api.today(),(dt.date.fromisoformat(api.today())-dt.timedelta(days=1)).isoformat(),1,'auto','30% offer',0.8,api.now()))
 assert client.get('/api/offers').json()['total']==1
 assert client.get('/api/offers/stale-offer').status_code==404
 assert client.get('/api/offers/expired-offer').status_code==404
 # Any substantive edit removes the public listing until a second verification.
 r=client.patch('/api/admin/offers/test-offer',json={'description':'Edited details'})
 assert r.status_code==200 and r.json()['verification_status']=='pending',r.text
 assert client.get('/api/offers').json()['total']==0
 # Simulate the next collector sync: edited fields and the pending state must survive.
 with api.connect() as c:
  api._upsert_offer(c,{'id':'test-offer','store_name':'Test Merchant','title':'40% off test item','description':'Original source description','category':'fashion','discount_text':'40% off','source_url':'https://example.qa/offers','verification_status':'verified','verification_reason':'Source rechecked','verified_at':api.now(),'last_checked_at':api.now(),'first_seen':api.today(),'last_seen':api.today(),'is_active':True,'manual_status':'auto','evidence':'Test source evidence','confidence':0.9})
  preserved=c.execute('SELECT description,verification_status,is_active,manual_fields FROM offers WHERE id=?',('test-offer',)).fetchone()
  assert preserved['description']=='Edited details' and preserved['verification_status']=='pending' and not preserved['is_active'],dict(preserved)
 assert client.post('/api/admin/offers/test-offer/review',json={'decision':'approve'}).status_code==200
 assert client.get('/api/offers').json()['total']==1
 # Source management refuses local/private network targets.
 r=client.post('/api/admin/sources',json={'name':'Local target','url':'http://127.0.0.1/admin','source_type':'text'})
 assert r.status_code==400,r.text
 # Scheduling is stored in the database and bounded to a safe interval.
 r=client.patch('/api/admin/settings',json={'discovery_interval_hours':12})
 assert r.status_code==200 and r.json()['discovery_interval_hours']==12,r.text
 assert client.patch('/api/admin/settings',json={'discovery_interval_hours':0}).status_code==400
 # Merchant and category management are database-backed.
 r=client.post('/api/admin/merchants',json={'name':'Extra Merchant','name_ar':'متجر إضافي'})
 assert r.status_code==200,r.text
 assert any(m['name']=='Extra Merchant' for m in client.get('/api/admin/merchants').json())
 merchant=next(m for m in client.get('/api/admin/merchants').json() if m['name']=='Test Merchant')
 assert client.patch('/api/admin/merchants/'+merchant['id'],json={'name':'Test Merchant Group'}).status_code==200
 assert client.get('/api/offers').json()['total']==1
 assert client.patch('/api/admin/merchants/'+merchant['id'],json={'active':False}).status_code==200
 assert client.get('/api/offers').json()['total']==0
 assert client.patch('/api/admin/merchants/'+merchant['id'],json={'active':True}).status_code==200
 assert client.get('/api/offers').json()['total']==1
 r=client.post('/api/admin/categories',json={'slug':'test-category','name':'Test Category','name_ar':'فئة تجريبية','parent_slug':'fashion'})
 assert r.status_code==200,r.text
 cat=next(c for c in client.get('/api/admin/categories').json() if c['slug']=='test-category')
 assert cat['parent_slug']=='fashion'
 assert client.patch(f"/api/admin/categories/{cat['id']}",json={'name':'Updated Category'}).status_code==200
 # A regular user can register, but cannot access admin APIs.
 u=client.post('/api/auth/register',json={'username':'test-user','password':'UserPasswordLong123!','terms_accepted':True})
 assert u.status_code==200,u.text
 assert client.get('/api/admin/overview').status_code==403
 deletion=client.delete('/api/auth/account');assert deletion.status_code==200,deletion.text
 assert client.get('/api/auth/me').json().get('user') is None
print('all API authentication, verification, management, and scheduling smoke tests passed')
