"""Portable FastAPI + SQLite backend for Qatar Offers. No hosted AI API is used."""
from __future__ import annotations
import os, re, json, hmac, base64, hashlib, secrets, sqlite3, subprocess, sys, time, asyncio, datetime as dt, socket, ipaddress
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, Request, HTTPException, Response, Depends
from fastapi.responses import FileResponse, JSONResponse

ROOT=Path(__file__).resolve().parent
DB_PATH=Path(os.environ.get('DATABASE_PATH',ROOT/'data/qatar_offers.sqlite3'))
QATAR=ZoneInfo('Asia/Qatar');SESSION_COOKIE='qo_session';SESSION_HOURS=int(os.environ.get('SESSION_HOURS','12'))
MAX_BODY=1_000_000;RATE_WINDOW=60;RATE_MAX=int(os.environ.get('API_RATE_LIMIT','120'))
_RATE:dict[str,list[float]]={}

def now():return dt.datetime.now(QATAR).isoformat(timespec='seconds')
def today():return dt.datetime.now(QATAR).date().isoformat()
def fresh_cutoff():return (dt.datetime.now(QATAR)-dt.timedelta(days=7)).isoformat(timespec='seconds')
def connect():
 DB_PATH.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(DB_PATH,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL');return c

def password_hash(password,salt=None):
 salt=salt or secrets.token_bytes(16);return 'pbkdf2_sha256$240000$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(hashlib.pbkdf2_hmac('sha256',password.encode(),salt,240000)).decode()
def password_ok(password,stored):
 try:
  _,rounds,salt,expected=stored.split('$');got=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(salt),int(rounds));return hmac.compare_digest(base64.b64encode(got).decode(),expected)
 except Exception:return False

def init_db():
 with connect() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,last_login TEXT);
  CREATE TABLE IF NOT EXISTS malls(id TEXT PRIMARY KEY,name TEXT NOT NULL,name_ar TEXT,area TEXT,website TEXT,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY,store_name TEXT NOT NULL,title TEXT NOT NULL,title_ar TEXT,description TEXT,category TEXT NOT NULL DEFAULT 'other',discount_text TEXT,original_price TEXT,sale_price TEXT,promo_code TEXT,terms TEXT,redemption_instructions TEXT,start_date TEXT,end_date TEXT,image_url TEXT,source_url TEXT NOT NULL,mall_id TEXT,mall_name TEXT,language TEXT,origin TEXT,verification_status TEXT NOT NULL DEFAULT 'pending',verification_reason TEXT,verified_at TEXT,last_checked_at TEXT,first_seen TEXT,last_seen TEXT,is_active INTEGER NOT NULL DEFAULT 0,manual_status TEXT NOT NULL DEFAULT 'auto',manual_fields TEXT NOT NULL DEFAULT '[]',evidence TEXT,confidence REAL NOT NULL DEFAULT 0.0,updated_at TEXT NOT NULL,FOREIGN KEY(mall_id) REFERENCES malls(id) ON DELETE SET NULL);
  CREATE INDEX IF NOT EXISTS idx_offer_active ON offers(is_active,verification_status,end_date);
  CREATE INDEX IF NOT EXISTS idx_offer_category ON offers(category);
  CREATE INDEX IF NOT EXISTS idx_offer_store ON offers(store_name);
  CREATE INDEX IF NOT EXISTS idx_offer_mall ON offers(mall_id);
  CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,url TEXT UNIQUE NOT NULL,source_type TEXT NOT NULL DEFAULT 'text',mall_id TEXT,settings TEXT,enabled INTEGER NOT NULL DEFAULT 1,last_checked_at TEXT,last_status TEXT,last_error TEXT,created_at TEXT NOT NULL,deleted_at TEXT,FOREIGN KEY(mall_id) REFERENCES malls(id) ON DELETE SET NULL);
  CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE NOT NULL,name TEXT NOT NULL,name_ar TEXT,parent_slug TEXT,active INTEGER NOT NULL DEFAULT 1);
  CREATE TABLE IF NOT EXISTS merchants(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,name_ar TEXT,website TEXT,logo_url TEXT,active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS merchant_aliases(alias TEXT PRIMARY KEY,merchant_id TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(merchant_id) REFERENCES merchants(id) ON DELETE CASCADE);
  CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL,offer_id TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,offer_id),FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE);
  CREATE TABLE IF NOT EXISTS offer_metrics(offer_id TEXT PRIMARY KEY,views INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE);
  CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,details TEXT,created_at TEXT NOT NULL,FOREIGN KEY(actor_id) REFERENCES users(id) ON DELETE SET NULL);
  CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT,finished_at TEXT,summary TEXT,error TEXT,created_by INTEGER,created_at TEXT NOT NULL,FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL);
  CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
  CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
  ''')
  c.execute('INSERT OR IGNORE INTO merchant_aliases(alias,merchant_id,created_at) SELECT name,id,updated_at FROM merchants')
  source_columns={r['name'] for r in c.execute('PRAGMA table_info(sources)').fetchall()}
  if 'settings' not in source_columns:c.execute('ALTER TABLE sources ADD COLUMN settings TEXT')
  if 'deleted_at' not in source_columns:c.execute('ALTER TABLE sources ADD COLUMN deleted_at TEXT')
  offer_columns={r['name'] for r in c.execute('PRAGMA table_info(offers)').fetchall()}
  if 'redemption_instructions' not in offer_columns:c.execute('ALTER TABLE offers ADD COLUMN redemption_instructions TEXT')
  if 'manual_fields' not in offer_columns:c.execute("ALTER TABLE offers ADD COLUMN manual_fields TEXT NOT NULL DEFAULT '[]'")
  category_columns={r['name'] for r in c.execute('PRAGMA table_info(categories)').fetchall()}
  if 'parent_slug' not in category_columns:c.execute('ALTER TABLE categories ADD COLUMN parent_slug TEXT')
  c.execute('INSERT OR IGNORE INTO app_settings(key,value,updated_at) VALUES(?,?,?)',('discovery_interval_hours',str(max(1,int(os.environ.get('DISCOVERY_INTERVAL_HOURS','6')))),now()))
  defaults=[('fashion','Fashion','الأزياء'),('food','Food & Dining','الطعام والمطاعم'),('electronics','Electronics','الإلكترونيات'),('beauty','Beauty','الجمال'),('home','Home','المنزل'),('grocery','Grocery','البقالة'),('travel','Travel','السفر'),('entertainment','Entertainment','الترفيه'),('other','Other','أخرى')]
  c.executemany('INSERT OR IGNORE INTO categories(slug,name,name_ar) VALUES(?,?,?)',defaults)
  admin=os.environ.get('ADMIN_USERNAME','').strip();pw=os.environ.get('ADMIN_PASSWORD','')
  if admin and pw and len(pw)>=12:
   c.execute('INSERT OR IGNORE INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)',(admin,password_hash(pw),'admin',now()))
  _import_json(c)

def _import_json(c):
 malls_path=ROOT/'web/malls.json';offers_path=ROOT/'data/state.json'
 try:malls=json.loads(malls_path.read_text(encoding='utf-8'))
 except Exception:malls=[]
 for m in malls if isinstance(malls,list) else []:
  c.execute('INSERT OR IGNORE INTO malls(id,name,name_ar,area,updated_at) VALUES(?,?,?,?,?)',(m.get('id'),m.get('name') or m.get('id'),m.get('name_ar'),m.get('area'),now()))
 try:state=json.loads(offers_path.read_text(encoding='utf-8'))
 except Exception:state={}
 for o in state.get('offers',{}).values() if isinstance(state.get('offers'),dict) else []:
  if o.get('mall_id') and not c.execute('SELECT 1 FROM malls WHERE id=?',(o['mall_id'],)).fetchone():c.execute('INSERT OR IGNORE INTO malls(id,name,updated_at) VALUES(?,?,?)',(o['mall_id'],o.get('mall_name') or o['mall_id'],now()))
  _upsert_offer(c,o)
 try:cfg=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
 except Exception:cfg={}
 for m in cfg.get('malls',[]):
  c.execute('INSERT OR IGNORE INTO malls(id,name,name_ar,area,updated_at) VALUES(?,?,?,?,?)',(m.get('id'),m.get('name') or m.get('id'),m.get('name_ar'),m.get('area'),now()))
  for p in m.get('pages',[]):
   if p.get('url'):
    c.execute('INSERT OR IGNORE INTO sources(name,url,source_type,mall_id,settings,created_at) VALUES(?,?,?,?,?,?)',(m.get('name','Source'),p['url'],p.get('type','text'),m.get('id'),json.dumps(p,ensure_ascii=False),now()))
  if m.get('sitemap',{}).get('url'):
   c.execute('INSERT OR IGNORE INTO sources(name,url,source_type,mall_id,settings,created_at) VALUES(?,?,?,?,?,?)',(m.get('name','Sitemap')+' sitemap',m['sitemap']['url'],'sitemap',m.get('id'),json.dumps(m['sitemap'],ensure_ascii=False),now()))
 for f in cfg.get('feeds',[]):
  if f.get('url'):c.execute('INSERT OR IGNORE INTO sources(name,url,source_type,settings,created_at) VALUES(?,?,?,?,?)',(f.get('name','RSS feed'),f['url'],'rss',json.dumps(f,ensure_ascii=False),now()))

def _upsert_offer(c,o):
 if not isinstance(o,dict) or not o.get('id') or not o.get('title') or not o.get('store_name') or not o.get('source_url'):return
 merchant_name=str(o.get('store_name')).strip()[:200]
 alias=c.execute('SELECT merchant_id FROM merchant_aliases WHERE alias=?',(merchant_name,)).fetchone()
 if alias:merchant_id=alias['merchant_id']
 else:
  merchant_id=re.sub(r'[^a-z0-9-]','-',merchant_name.casefold()).strip('-')[:60] or hashlib.sha1(merchant_name.encode()).hexdigest()[:12]
  c.execute('INSERT OR IGNORE INTO merchants(id,name,updated_at) VALUES(?,?,?)',(merchant_id,merchant_name,now()))
  c.execute('INSERT OR IGNORE INTO merchant_aliases(alias,merchant_id,created_at) VALUES(?,?,?)',(merchant_name,merchant_id,now()))
 existing=c.execute('SELECT manual_status,manual_fields FROM offers WHERE id=?',(o.get('id'),)).fetchone()
 manual_status=existing['manual_status'] if existing else o.get('manual_status','auto')
 try:manual_fields=set(json.loads(existing['manual_fields'] or '[]')) if existing else set(o.get('manual_fields',[]))
 except (ValueError,TypeError):manual_fields=set()
 status=o.get('verification_status','pending');status=status if status in ('pending','verified','rejected','expired') else 'pending'
 end=o.get('end_date');active=bool(o.get('is_active',status=='verified') and status=='verified' and (not end or end>=today()) and (not o.get('start_date') or o.get('start_date')<=today()))
 fields=('id','store_name','title','title_ar','description','category','discount_text','original_price','sale_price','promo_code','terms','redemption_instructions','start_date','end_date','image_url','source_url','mall_id','mall_name','language','origin','verification_status','verification_reason','verified_at','last_checked_at','first_seen','last_seen')
 vals=[o.get(k) for k in fields]+[int(active),manual_status,json.dumps(sorted(manual_fields)),o.get('evidence'),float(o.get('confidence') or 0),now()]
 placeholders=','.join('?' for _ in vals)
 manualizable=set(fields[1:])-{'verification_status','verification_reason','verified_at','last_checked_at','first_seen','last_seen','origin','language','mall_id'}
 update_parts=[]
 for key in fields[1:]:
  if key in manualizable:update_parts.append(f"{key}=CASE WHEN instr(offers.manual_fields, '\"{key}\"')>0 THEN offers.{key} ELSE excluded.{key} END")
  elif key=='verification_status':update_parts.append("verification_status=CASE WHEN offers.manual_status='approved' THEN 'verified' WHEN offers.manual_status='rejected' THEN 'rejected' WHEN offers.manual_status='edited' THEN 'pending' ELSE excluded.verification_status END")
  else:update_parts.append(f'{key}=excluded.{key}')
 update_parts += ["is_active=CASE WHEN offers.manual_status IN ('rejected','edited') THEN 0 WHEN offers.manual_status='approved' THEN excluded.is_active ELSE excluded.is_active END","manual_status=CASE WHEN offers.manual_status IN ('approved','rejected','edited') THEN offers.manual_status ELSE excluded.manual_status END","manual_fields=CASE WHEN offers.manual_fields='[]' THEN excluded.manual_fields ELSE offers.manual_fields END",'evidence=excluded.evidence','confidence=excluded.confidence','updated_at=excluded.updated_at']
 updates=','.join(update_parts)
 columns=','.join(fields)+',is_active,manual_status,manual_fields,evidence,confidence,updated_at'
 c.execute(f'INSERT INTO offers({columns}) VALUES({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}',vals)

def sync_state_to_db():
 try:state=json.loads((ROOT/'data/state.json').read_text(encoding='utf-8'))
 except Exception:return 0
 n=0
 with connect() as c:
  for url,info in state.get('sources',{}).items():
   c.execute('UPDATE sources SET last_checked_at=?,last_status=?,last_error=? WHERE url=?',(info.get('checked_at'),info.get('status'),info.get('error'),url))
  for o in state.get('offers',{}).values():
   if o.get('mall_id') and not c.execute('SELECT 1 FROM malls WHERE id=?',(o['mall_id'],)).fetchone():c.execute('INSERT OR IGNORE INTO malls(id,name,updated_at) VALUES(?,?,?)',(o['mall_id'],o.get('mall_name') or o['mall_id'],now()))
   existing=c.execute('SELECT manual_status FROM offers WHERE id=?',(o.get('id'),)).fetchone()
   if existing and existing['manual_status']=='approved' and o.get('manual_status')=='auto' and o.get('verification_status')=='pending' and 'source changed or became unavailable' in str(o.get('verification_reason','')):c.execute("UPDATE offers SET manual_status='auto' WHERE id=?",(o['id'],))
   _upsert_offer(c,o);n+=1
 return n

def audit(c,user,action,entity_type,entity_id=None,details=None):c.execute('INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',(user['id'] if user else None,action,entity_type,str(entity_id) if entity_id is not None else None,json.dumps(details or {},ensure_ascii=False),now()))
def user_obj(row):return {'id':row['id'],'username':row['username'],'role':row['role'],'active':bool(row['active']),'created_at':row['created_at'],'last_login':row['last_login']}
def offer_obj(row):
 o=dict(row);o['is_active']=bool(o.get('is_active'));return o

def session_sign(token):return hmac.new(os.environ.get('SESSION_SECRET','').encode() or b'local-dev-secret-change-me',token.encode(),hashlib.sha256).hexdigest()
def issue_session(c,user_id):
 token=secrets.token_urlsafe(32);hashed=hashlib.sha256(token.encode()).hexdigest();expiry=dt.datetime.now(QATAR)+dt.timedelta(hours=SESSION_HOURS)
 c.execute('INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)',(hashed,user_id,expiry.isoformat(),now()));return token+'.'+session_sign(token)

def rate_limit(request):
 ip=request.client.host if request.client else 'unknown';ts=time.monotonic();arr=[x for x in _RATE.get(ip,[]) if ts-x<RATE_WINDOW]
 if len(arr)>=RATE_MAX:raise HTTPException(429,'Rate limit exceeded. Try again shortly.')
 arr.append(ts);_RATE[ip]=arr
async def body_json(request):
 raw=await request.body()
 if len(raw)>MAX_BODY:raise HTTPException(413,'Request body too large')
 try:return json.loads(raw or b'{}')
 except Exception:raise HTTPException(400,'Invalid JSON')

def csrf_check(request):
 if request.method in ('GET','HEAD','OPTIONS'):return
 origin=request.headers.get('origin')
 if origin:
  try:
   own=f'{request.url.scheme}://{request.headers.get("host","")}'
   if urlparse(origin).netloc.lower()!=urlparse(own).netloc.lower():raise HTTPException(403,'Cross-origin request blocked')
  except ValueError:raise HTTPException(403,'Invalid origin')

async def current_user(request:Request,required=False,admin=False):
 raw=request.cookies.get(SESSION_COOKIE)
 if not raw:
  if required:raise HTTPException(401,'Authentication required')
  return None
 if '.' not in raw:
  if required:raise HTTPException(401,'Invalid session')
  return None
 token,signature=raw.rsplit('.',1)
 if not hmac.compare_digest(signature,session_sign(token)):
  if required:raise HTTPException(401,'Invalid session signature')
  return None
 with connect() as c:
  row=c.execute('SELECT u.*,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
  if not row or not row['active'] or row['expires_at']<now():
   if row:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
   if required:raise HTTPException(401,'Session expired')
   return None
  u=user_obj(row)
 if admin and u['role']!='admin':raise HTTPException(403,'Administrator role required')
 return u

@asynccontextmanager
async def lifespan(app):
 init_db()
 async def scheduler():
  while True:
   with connect() as c:row=c.execute("SELECT value FROM app_settings WHERE key='discovery_interval_hours'").fetchone()
   interval=max(1,min(168,int(row['value']) if row else int(os.environ.get('DISCOVERY_INTERVAL_HOURS','6'))))*3600
   await asyncio.sleep(interval)
   try:await asyncio.to_thread(run_collection,None)
   except Exception as e:print('scheduled collector failed:',str(e)[:300])
 task=asyncio.create_task(scheduler())
 try:yield
 finally:task.cancel()
app=FastAPI(title='Qatar Offers API',version='2.0.0',lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def security_middleware(request,call_next):
 if request.url.path.startswith('/api/'): rate_limit(request)
 csrf_check(request)
 response=await call_next(request)
 response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='strict-origin-when-cross-origin';response.headers['X-Frame-Options']='DENY';response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
 response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
 return response

@app.get('/api/health')
def health():return {'ok':True,'time':now(),'timezone':'Asia/Qatar'}
@app.get('/api/offers')
def list_offers(q:str|None=None,category:str|None=None,mall_id:str|None=None,merchant:str|None=None,sort:str='ending',page:int=1,limit:int=24):
 page=max(1,page);limit=min(100,max(1,limit));where=["is_active=1","verification_status='verified'","(end_date IS NULL OR end_date>=?)","(start_date IS NULL OR start_date<=?)","COALESCE(last_checked_at,verified_at)>=?","NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0)"];args=[today(),today(),fresh_cutoff()]
 if q:where.append('(title LIKE ? OR title_ar LIKE ? OR description LIKE ? OR store_name LIKE ?)');args += [f'%{q[:100]}%']*4
 if category and category!='all':where.append('category=?');args.append(category)
 if mall_id:where.append('mall_id=?');args.append(mall_id)
 if merchant:where.append('store_name=?');args.append(merchant[:200])
 order={'ending':'COALESCE(end_date,\'9999-12-31\') ASC','newest':'COALESCE(last_checked_at,first_seen) DESC','discount':'store_name ASC','merchant':'store_name COLLATE NOCASE ASC','trending':'COALESCE((SELECT views FROM offer_metrics WHERE offer_id=offers.id),0) DESC, COALESCE(end_date,\'9999-12-31\') ASC'}.get(sort,'COALESCE(end_date,\'9999-12-31\') ASC')
 with connect() as c:
  clause=' AND '.join(where);total=c.execute(f'SELECT COUNT(*) FROM offers WHERE {clause}',args).fetchone()[0]
  rows=c.execute(f'SELECT offers.*,COALESCE((SELECT views FROM offer_metrics WHERE offer_id=offers.id),0) AS views FROM offers WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?',args+[limit,(page-1)*limit]).fetchall()
 return {'offers':[offer_obj(r) for r in rows],'page':page,'limit':limit,'total':total,'pages':(total+limit-1)//limit}
@app.get('/api/offers/{offer_id}')
def get_offer(offer_id:str):
 with connect() as c:
  c.execute("INSERT INTO offer_metrics(offer_id,views,updated_at) SELECT id,1,? FROM offers WHERE id=? AND is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0) ON CONFLICT(offer_id) DO UPDATE SET views=offer_metrics.views+1,updated_at=excluded.updated_at",(now(),offer_id,today(),today(),fresh_cutoff()))
  row=c.execute("SELECT offers.*,COALESCE((SELECT views FROM offer_metrics WHERE offer_id=offers.id),0) AS views FROM offers WHERE id=? AND is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0)",(offer_id,today(),today(),fresh_cutoff())).fetchone()
  if not row:raise HTTPException(404,'Offer not found')
  o=offer_obj(row);related=c.execute("SELECT offers.*,COALESCE((SELECT views FROM offer_metrics WHERE offer_id=offers.id),0) AS views FROM offers WHERE is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0) AND id<>? AND (category=? OR store_name=?) ORDER BY COALESCE(end_date,'9999-12-31') LIMIT 6",(today(),today(),fresh_cutoff(),offer_id,o['category'],o['store_name'])).fetchall()
 return {'offer':o,'related':[offer_obj(r) for r in related]}
@app.get('/api/malls')
def malls():
 sql="SELECT m.*,COUNT(o.id) AS offer_count FROM malls m LEFT JOIN offers o ON o.mall_id=m.id AND o.is_active=1 AND o.verification_status='verified' AND (o.end_date IS NULL OR o.end_date>=?) AND (o.start_date IS NULL OR o.start_date<=?) AND COALESCE(o.last_checked_at,o.verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=o.store_name AND hidden.active=0) WHERE m.active=1 GROUP BY m.id ORDER BY m.name"
 with connect() as c:return [dict(r) for r in c.execute(sql,(today(),today(),fresh_cutoff())).fetchall()]
@app.get('/api/mall/{mall_id}/offers')
def mall_offers(mall_id:str,category:str='all',page:int=1,limit:int=24):
 result=list_offers(category=category,mall_id=mall_id,page=page,limit=limit)
 with connect() as c:m=c.execute('SELECT * FROM malls WHERE id=? AND active=1',(mall_id,)).fetchone()
 if not m:raise HTTPException(404,'Mall not found')
 return {'mall':dict(m),'offers':result['offers'],'total':result['total'],'page':page,'pages':result['pages']}
@app.get('/api/shops')
def shops(q:str|None=None):
 with connect() as c:
  rows=c.execute("SELECT store_name AS name,COUNT(*) AS n FROM offers WHERE is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0) GROUP BY store_name ORDER BY store_name COLLATE NOCASE",(today(),today(),fresh_cutoff())).fetchall()
 return [dict(r) for r in rows if not q or q.casefold() in r['name'].casefold()]
@app.get('/api/shop')
def shop(name:str):
 with connect() as c:
  rows=c.execute("SELECT offers.* FROM offers WHERE store_name=? AND is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0) ORDER BY COALESCE(end_date,'9999-12-31')",(name,today(),today(),fresh_cutoff())).fetchall()
 if not rows:raise HTTPException(404,'Merchant not found')
 return {'name':name,'offers':[offer_obj(r) for r in rows]}
@app.get('/api/merchants')
def public_merchants(q:str|None=None):
 sql="SELECT m.*,COUNT(o.id) AS offer_count FROM merchants m LEFT JOIN merchant_aliases a ON a.merchant_id=m.id LEFT JOIN offers o ON o.store_name=a.alias AND o.is_active=1 AND o.verification_status='verified' AND (o.end_date IS NULL OR o.end_date>=?) AND (o.start_date IS NULL OR o.start_date<=?) AND COALESCE(o.last_checked_at,o.verified_at)>=? WHERE m.active=1 GROUP BY m.id ORDER BY m.name COLLATE NOCASE"
 with connect() as c:
  rows=c.execute(sql,(today(),today(),fresh_cutoff())).fetchall();result=[]
  for r in rows:
   item=dict(r);item['aliases']=[x['alias'] for x in c.execute('SELECT alias FROM merchant_aliases WHERE merchant_id=? ORDER BY alias',(r['id'],)).fetchall()]
   if not q or q.casefold() in item['name'].casefold() or any(q.casefold() in a.casefold() for a in item['aliases']):result.append(item)
  return result
@app.get('/api/categories')
def categories():
 sql="SELECT c.*,COUNT(o.id) AS offer_count FROM categories c LEFT JOIN offers o ON o.category=c.slug AND o.is_active=1 AND o.verification_status='verified' AND (o.end_date IS NULL OR o.end_date>=?) AND (o.start_date IS NULL OR o.start_date<=?) AND COALESCE(o.last_checked_at,o.verified_at)>=? WHERE c.active=1 GROUP BY c.id ORDER BY c.name"
 with connect() as c:return [dict(r) for r in c.execute(sql,(today(),today(),fresh_cutoff())).fetchall()]
@app.get('/api/analytics/public')
def public_analytics():
 with connect() as c:
  active_sql="is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0)"
  active=c.execute('SELECT COUNT(*) FROM offers WHERE '+active_sql,(today(),today(),fresh_cutoff())).fetchone()[0]
  cats=[dict(r) for r in c.execute('SELECT category,COUNT(*) AS count FROM offers WHERE '+active_sql+' GROUP BY category',(today(),today(),fresh_cutoff())).fetchall()]
  return {'active':active,'categories':cats,'updated_at':now(),'timezone':'Asia/Qatar'}

@app.post('/api/auth/register')
async def register(request:Request):
 d=await body_json(request);username=str(d.get('username','')).strip();password=str(d.get('password',''))
 if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username):raise HTTPException(400,'Username must be 3–40 letters, numbers, dots, underscores or hyphens')
 if len(password)<12 or len(password)>256:raise HTTPException(400,'Password must be 12–256 characters')
 with connect() as c:
  try:c.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)',(username,password_hash(password),'user',now()))
  except sqlite3.IntegrityError:raise HTTPException(409,'Username already exists')
  uid=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id'];token=issue_session(c,uid);audit(c,{'id':uid},'register','user',uid)
 response=JSONResponse({'user':{'id':uid,'username':username,'role':'user'}});response.set_cookie(SESSION_COOKIE,token,httponly=True,secure=os.environ.get('SESSION_SECURE','0')=='1',samesite='strict',max_age=SESSION_HOURS*3600,path='/');return response
@app.post('/api/auth/login')
async def login(request:Request):
 d=await body_json(request);username=str(d.get('username','')).strip();password=str(d.get('password',''))
 with connect() as c:
  row=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
  if not row or not password_ok(password,row['password_hash']):raise HTTPException(401,'Invalid username or password')
  token=issue_session(c,row['id']);c.execute('UPDATE users SET last_login=? WHERE id=?',(now(),row['id']));audit(c,{'id':row['id']},'login','user',row['id'])
 response=JSONResponse({'user':user_obj({**dict(row),'last_login':now()})});response.set_cookie(SESSION_COOKIE,token,httponly=True,secure=os.environ.get('SESSION_SECURE','0')=='1',samesite='strict',max_age=SESSION_HOURS*3600,path='/');return response
@app.post('/api/auth/logout')
async def logout(request:Request):
 raw=request.cookies.get(SESSION_COOKIE,'');token=raw.rsplit('.',1)[0] if '.' in raw else ''
 if token:
  with connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
 response=JSONResponse({'ok':True});response.delete_cookie(SESSION_COOKIE,path='/');return response
@app.get('/api/auth/me')
async def me(request:Request):
 u=await current_user(request)
 if not u: return {'user':None}
 return {'user':u}
@app.get('/api/me/favorites')
async def favorites(request:Request):
 u=await current_user(request,True)
 with connect() as c:return [offer_obj(r) for r in c.execute("SELECT o.* FROM favorites f JOIN offers o ON o.id=f.offer_id WHERE f.user_id=? AND o.is_active=1 AND o.verification_status='verified' AND (o.end_date IS NULL OR o.end_date>=?) AND (o.start_date IS NULL OR o.start_date<=?) AND COALESCE(o.last_checked_at,o.verified_at)>=? ORDER BY f.created_at DESC",(u['id'],today(),today(),fresh_cutoff())).fetchall()]
@app.post('/api/me/favorites/{offer_id}')
async def favorite(offer_id:str,request:Request):
 u=await current_user(request,True);d=await body_json(request);add=bool(d.get('favorite',True))
 with connect() as c:
  if add:
   if not c.execute("SELECT 1 FROM offers WHERE id=? AND is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=?",(offer_id,today(),today(),fresh_cutoff())).fetchone():raise HTTPException(404,'Offer not found')
   c.execute('INSERT OR IGNORE INTO favorites(user_id,offer_id,created_at) VALUES(?,?,?)',(u['id'],offer_id,now()))
  else:c.execute('DELETE FROM favorites WHERE user_id=? AND offer_id=?',(u['id'],offer_id))
 return {'offer_id':offer_id,'favorite':add}

async def admin_user(request:Request):return await current_user(request,True,True)
@app.post('/api/admin/login')
async def admin_login(request:Request):
 # Shared login endpoint returns 403 to non-admin accounts, preventing role confusion.
 d=await body_json(request);username=str(d.get('username','')).strip();password=str(d.get('password',''))
 with connect() as c:
  row=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
  if not row or not password_ok(password,row['password_hash']):raise HTTPException(401,'Invalid credentials')
  if row['role']!='admin':raise HTTPException(403,'Administrator role required')
  token=issue_session(c,row['id']);c.execute('UPDATE users SET last_login=? WHERE id=?',(now(),row['id']));audit(c,{'id':row['id']},'admin_login','user',row['id'])
 response=JSONResponse({'user':user_obj(row)});response.set_cookie(SESSION_COOKIE,token,httponly=True,secure=os.environ.get('SESSION_SECURE','0')=='1',samesite='strict',max_age=SESSION_HOURS*3600,path='/');return response
@app.get('/api/admin/overview')
async def admin_overview(request:Request,u=Depends(admin_user)):
 with connect() as c:
  def count(sql,args=()):return c.execute(sql,args).fetchone()[0]
  last=c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
  return {'offers':{'active':count("SELECT COUNT(*) FROM offers WHERE is_active=1 AND verification_status='verified' AND (end_date IS NULL OR end_date>=?) AND (start_date IS NULL OR start_date<=?) AND COALESCE(last_checked_at,verified_at)>=? AND NOT EXISTS (SELECT 1 FROM merchant_aliases ha JOIN merchants hidden ON hidden.id=ha.merchant_id WHERE ha.alias=offers.store_name AND hidden.active=0)",(today(),today(),fresh_cutoff())),'pending':count("SELECT COUNT(*) FROM offers WHERE verification_status='pending'"),'expired':count("SELECT COUNT(*) FROM offers WHERE verification_status='expired' OR (end_date IS NOT NULL AND end_date<?)",(today(),)),'verified':count("SELECT COUNT(*) FROM offers WHERE verification_status='verified'"),'rejected':count("SELECT COUNT(*) FROM offers WHERE verification_status='rejected'")},'merchants':count('SELECT COUNT(DISTINCT store_name) FROM offers'),'malls':count('SELECT COUNT(*) FROM malls WHERE active=1'),'sources':count('SELECT COUNT(*) FROM sources WHERE enabled=1'),'users':count('SELECT COUNT(*) FROM users'),'last_job':dict(last) if last else None}
@app.get('/api/admin/offers')
async def admin_offers(request:Request,status:str='all',q:str|None=None,page:int=1,limit:int=50,u=Depends(admin_user)):
 page=max(1,page);limit=min(200,max(1,limit));where=[];args=[]
 if status!='all':where.append('verification_status=?');args.append(status)
 if q:where.append('(title LIKE ? OR store_name LIKE ? OR source_url LIKE ?)');args += [f'%{q[:100]}%']*3
 clause=' WHERE '+' AND '.join(where) if where else ''
 with connect() as c:
  total=c.execute('SELECT COUNT(*) FROM offers'+clause,args).fetchone()[0];rows=c.execute('SELECT * FROM offers'+clause+' ORDER BY updated_at DESC LIMIT ? OFFSET ?',args+[limit,(page-1)*limit]).fetchall()
 return {'offers':[offer_obj(r) for r in rows],'total':total,'page':page,'pages':(total+limit-1)//limit}
@app.post('/api/admin/offers')
async def create_manual_offer(request:Request,u=Depends(admin_user)):
 d=await body_json(request);store=str(d.get('store_name','')).strip()[:200];title=str(d.get('title','')).strip()[:300];source=str(d.get('source_url','')).strip()
 if not store or not title or not is_public_http_url(source):raise HTTPException(400,'Merchant, title, and public original source URL are required')
 start=d.get('start_date') or None;end=d.get('end_date') or None
 for label,value in (('start_date',start),('end_date',end)):
  if value:
   try:dt.date.fromisoformat(value)
   except ValueError:raise HTTPException(400,f'{label} must be YYYY-MM-DD')
 if start and end and start>end:raise HTTPException(400,'Start date cannot be after end date')
 category=str(d.get('category','other'))[:80]
 with connect() as c:
  if not c.execute('SELECT 1 FROM categories WHERE slug=? AND active=1',(category,)).fetchone():raise HTTPException(400,'Choose an active category from the category directory')
 oid=hashlib.sha256((source+'|'+store.casefold()+'|'+title.casefold()).encode()).hexdigest()[:20]
 values={'id':oid,'store_name':store,'title':title,'title_ar':str(d.get('title_ar','')).strip()[:300] or None,'description':str(d.get('description','')).strip()[:3000] or None,'category':category,'discount_text':str(d.get('discount_text','')).strip()[:300] or None,'original_price':str(d.get('original_price','')).strip()[:100] or None,'sale_price':str(d.get('sale_price','')).strip()[:100] or None,'promo_code':str(d.get('promo_code','')).strip()[:100] or None,'terms':str(d.get('terms','')).strip()[:3000] or None,'redemption_instructions':str(d.get('redemption_instructions','')).strip()[:1000] or None,'start_date':start,'end_date':end,'source_url':source,'verification_status':'pending','verification_reason':'Manually submitted; verify original source before publication.','last_checked_at':None,'first_seen':today(),'last_seen':today(),'is_active':False,'manual_status':'auto','evidence':None,'confidence':0.0}
 with connect() as c:
  if c.execute('SELECT 1 FROM offers WHERE id=?',(oid,)).fetchone():raise HTTPException(409,'This offer already exists')
  _upsert_offer(c,values);audit(c,u,'offer_create','offer',oid,{'title':title,'source_url':source})
 return {'ok':True,'id':oid,'verification_status':'pending'}

def update_state_record(offer_id,changes=None,decision=None,reason=None):
 path=ROOT/'data/state.json'
 try:state=json.loads(path.read_text(encoding='utf-8'))
 except Exception:return
 record=state.get('offers',{}).get(offer_id)
 if not record:return
 if changes:
  fields=set(record.get('manual_fields',[]));fields.update(changes.keys());record['manual_fields']=sorted(fields);record.update(changes);record['manual_status']='edited';record['verification_status']='pending';record['verification_reason']='Offer details were edited and require re-verification.';record['verified_at']=None;record['is_active']=False
 if decision:
  status={'approve':'verified','reject':'rejected','pending':'pending'}[decision];record['verification_status']=status;record['manual_status']={'approve':'approved','reject':'rejected','pending':'edited' if record.get('manual_fields') else 'auto'}[decision];record['verification_reason']=reason or ('Manually approved after source review.' if decision=='approve' else 'Manually rejected after source review.' if decision=='reject' else 'Returned to review.');record['verified_at']=now() if decision=='approve' else None;record['is_active']=bool(decision=='approve' and (not record.get('end_date') or record['end_date']>=today()) and (not record.get('start_date') or record['start_date']<=today()))
 try:
  tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
 except OSError:pass

@app.post('/api/admin/offers/{offer_id}/review')
async def review_offer(offer_id:str,request:Request,u=Depends(admin_user)):
 d=await body_json(request);decision=d.get('decision');reason=str(d.get('reason','')).strip()[:1000]
 if decision not in ('approve','reject','pending'):raise HTTPException(400,'decision must be approve, reject, or pending')
 with connect() as c:
  row=c.execute('SELECT * FROM offers WHERE id=?',(offer_id,)).fetchone()
  if not row:raise HTTPException(404,'Offer not found')
  status={'approve':'verified','reject':'rejected','pending':'pending'}[decision];active=int(decision=='approve' and (not row['end_date'] or row['end_date']>=today()) and (not row['start_date'] or row['start_date']<=today()))
  c.execute('UPDATE offers SET verification_status=?,verification_reason=?,verified_at=?,manual_status=?,is_active=?,updated_at=? WHERE id=?',(status,reason or ('Manually approved by administrator.' if decision=='approve' else 'Manually rejected by administrator.' if decision=='reject' else 'Returned to review queue.'),now() if decision=='approve' else None,{'approve':'approved','reject':'rejected','pending':'edited' if row['manual_fields'] not in (None,'[]') else 'auto'}[decision],active,now(),offer_id));audit(c,u,'offer_'+decision,'offer',offer_id,{'reason':reason})
 update_state_record(offer_id,decision=decision,reason=reason)
 return {'id':offer_id,'verification_status':status,'is_active':bool(active)}
@app.get('/api/admin/offers/{offer_id}')
async def admin_get_offer(offer_id:str,request:Request,u=Depends(admin_user)):
 with connect() as c:
  row=c.execute('SELECT * FROM offers WHERE id=?',(offer_id,)).fetchone()
  if not row:raise HTTPException(404,'Offer not found')
 return offer_obj(row)

@app.patch('/api/admin/offers/{offer_id}')
async def edit_offer(offer_id:str,request:Request,u=Depends(admin_user)):
 d=await body_json(request);allowed_fields={'store_name','title','title_ar','description','category','discount_text','original_price','sale_price','promo_code','terms','redemption_instructions','start_date','end_date','image_url','source_url','mall_name'}
 changes={k:(None if v=='' and k in {'title_ar','description','discount_text','original_price','sale_price','promo_code','terms','redemption_instructions','start_date','end_date','image_url','mall_name'} else v) for k,v in d.items() if k in allowed_fields}
 if not changes:raise HTTPException(400,'No editable fields supplied')
 for k,v in changes.items():
  if not isinstance(v,(str,type(None))) or (isinstance(v,str) and len(v)>3000):raise HTTPException(400,f'Invalid {k}')
 for key in ('source_url','image_url'):
  if changes.get(key) and not is_public_http_url(changes[key]):raise HTTPException(400,f'{key} must be a public HTTP(S) URL')
 start=changes.get('start_date');end=changes.get('end_date')
 for label,value in (('start_date',start),('end_date',end)):
  if value:
   try:dt.date.fromisoformat(value)
   except ValueError:raise HTTPException(400,f'{label} must be YYYY-MM-DD')
 if start and end and start>end:raise HTTPException(400,'Start date cannot be after end date')
 if 'category' in changes:
  with connect() as c:
   if not c.execute('SELECT 1 FROM categories WHERE slug=?',(changes['category'],)).fetchone():raise HTTPException(400,'Choose a category from the category directory')
 with connect() as c:
  if not c.execute('SELECT 1 FROM offers WHERE id=?',(offer_id,)).fetchone():raise HTTPException(404,'Offer not found')
  row=c.execute('SELECT manual_fields FROM offers WHERE id=?',(offer_id,)).fetchone()
  try:manual_fields=set(json.loads(row['manual_fields'] or '[]'))
  except Exception:manual_fields=set()
  manual_fields.update(changes.keys())
  sql=','.join(f'{k}=?' for k in changes);c.execute(f"UPDATE offers SET {sql},manual_fields=?,verification_status='pending',verification_reason='Offer details were edited and require re-verification.',verified_at=NULL,manual_status='edited',is_active=0,updated_at=? WHERE id=?",list(changes.values())+[json.dumps(sorted(manual_fields)),now(),offer_id])
  audit(c,u,'offer_edit','offer',offer_id,changes)
 update_state_record(offer_id,changes=changes)
 return {'ok':True,'id':offer_id,'verification_status':'pending','is_active':False,'updated':list(changes)}

@app.post('/api/admin/offers/bulk-review')
async def bulk_review(request:Request,u=Depends(admin_user)):
 d=await body_json(request);ids=d.get('ids',[]);decision=d.get('decision')
 if not isinstance(ids,list) or len(ids)>500 or decision not in ('approve','reject','pending'):raise HTTPException(400,'Invalid bulk request')
 status={'approve':'verified','reject':'rejected','pending':'pending'}[decision];n=0
 with connect() as c:
  for oid in ids:
   row=c.execute('SELECT end_date,start_date,manual_fields FROM offers WHERE id=?',(str(oid),)).fetchone()
   if not row:continue
   active=int(decision=='approve' and (not row['end_date'] or row['end_date']>=today()) and (not row['start_date'] or row['start_date']<=today()))
   manual_status={'approve':'approved','reject':'rejected','pending':'edited' if row['manual_fields'] not in (None,'[]') else 'auto'}[decision]
   c.execute('UPDATE offers SET verification_status=?,manual_status=?,is_active=?,verified_at=?,updated_at=? WHERE id=?',(status,manual_status,active,now() if decision=='approve' else None,now(),str(oid)));n+=1
  audit(c,u,'bulk_'+decision,'offer',None,{'count':n})
 for oid in ids:update_state_record(str(oid),decision=decision,reason='Bulk review action by administrator.')
 return {'updated':n}
@app.get('/api/admin/sources')
async def admin_sources(request:Request,u=Depends(admin_user)):
 with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM sources WHERE deleted_at IS NULL ORDER BY name,url').fetchall()]
def is_public_http_url(value):
 try:
  parsed=urlparse(value)
  if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:return False
  host=parsed.hostname.rstrip('.').lower()
  if host in ('localhost','localhost.localdomain') or host.endswith(('.local','.internal','.localhost')):return False
  try:ips=[ipaddress.ip_address(host)]
  except ValueError:ips=[ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)]
  return bool(ips) and all(ip.is_global for ip in ips)
 except Exception:return False

@app.post('/api/admin/sources')
async def add_source(request:Request,u=Depends(admin_user)):
 d=await body_json(request);name=str(d.get('name','')).strip()[:160];url=str(d.get('url','')).strip();typ=d.get('source_type','text');mall_id=d.get('mall_id')
 if not name or not is_public_http_url(url):raise HTTPException(400,'Valid public HTTP(S) source URL required; local/private network targets are blocked')
 if typ not in ('text','images','api','rss','sitemap'):raise HTTPException(400,'Unsupported source type')
 if typ=='sitemap' and not mall_id:raise HTTPException(400,'Choose a mall for a sitemap source')
 with connect() as c:
  try:c.execute('INSERT INTO sources(name,url,source_type,mall_id,settings,created_at) VALUES(?,?,?,?,?,?)',(name,url,typ,mall_id,json.dumps({k:d.get(k) for k in ('branch','image_contains','context_must','contains','max_urls') if d.get(k) is not None},ensure_ascii=False),now()))
  except sqlite3.IntegrityError:raise HTTPException(409,'Source URL already exists or mall is invalid')
  audit(c,u,'source_add','source',url,{'name':name,'type':typ})
 return {'ok':True}
@app.patch('/api/admin/sources/{source_id}')
async def update_source(source_id:int,request:Request,u=Depends(admin_user)):
 d=await body_json(request);enabled=int(bool(d.get('enabled')))
 with connect() as c:
  row=c.execute('SELECT * FROM sources WHERE id=? AND deleted_at IS NULL',(source_id,)).fetchone()
  if not row:raise HTTPException(404,'Source not found')
  c.execute('UPDATE sources SET enabled=? WHERE id=?',(enabled,source_id))
  if not enabled and row['source_type'] in ('text','images','api'):
   c.execute("UPDATE offers SET is_active=0,verification_status=CASE WHEN manual_status='approved' THEN verification_status ELSE 'pending' END,verification_reason='Source disabled by administrator.',updated_at=? WHERE source_url=?",(now(),row['url']))
  audit(c,u,'source_toggle','source',source_id,{'enabled':bool(enabled)})
 return {'ok':True,'enabled':bool(enabled)}
@app.delete('/api/admin/sources/{source_id}')
async def delete_source(source_id:int,request:Request,u=Depends(admin_user)):
 with connect() as c:
  row=c.execute('SELECT * FROM sources WHERE id=? AND deleted_at IS NULL',(source_id,)).fetchone()
  if not row:raise HTTPException(404,'Source not found')
  c.execute('UPDATE sources SET enabled=0,deleted_at=? WHERE id=?',(now(),source_id))
  if row['source_type'] in ('text','images','api'):
   c.execute("UPDATE offers SET is_active=0,verification_status=CASE WHEN manual_status='approved' THEN verification_status ELSE 'pending' END,verification_reason='Source configuration was removed.',updated_at=? WHERE source_url=?",(now(),row['url']))
  audit(c,u,'source_delete','source',source_id,{'url':row['url']})
 return {'ok':True}
@app.get('/api/admin/malls')
async def admin_malls(request:Request,u=Depends(admin_user)):
 with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM malls ORDER BY name').fetchall()]
@app.post('/api/admin/malls')
async def add_mall(request:Request,u=Depends(admin_user)):
 d=await body_json(request);mid=re.sub(r'[^a-z0-9-]','-',str(d.get('id') or d.get('name','')).lower()).strip('-')[:80];name=str(d.get('name','')).strip()[:160]
 if not mid or not name:raise HTTPException(400,'Mall id and name required')
 website=str(d.get('website','')).strip()
 if website and not is_public_http_url(website):raise HTTPException(400,'Website must be a public HTTP(S) URL')
 with connect() as c:
  try:c.execute('INSERT INTO malls(id,name,name_ar,area,website,updated_at) VALUES(?,?,?,?,?,?)',(mid,name,str(d.get('name_ar',''))[:160],str(d.get('area',''))[:160],website[:500],now()))
  except sqlite3.IntegrityError:raise HTTPException(409,'Mall id already exists')
  audit(c,u,'mall_add','mall',mid,{'name':name})
 return {'ok':True,'id':mid}
@app.get('/api/admin/categories')
async def admin_categories(request:Request,u=Depends(admin_user)):
 with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM categories ORDER BY name').fetchall()]
@app.post('/api/admin/categories')
async def add_category(request:Request,u=Depends(admin_user)):
 d=await body_json(request);slug=re.sub(r'[^a-z0-9-]','-',str(d.get('slug') or d.get('name','')).lower()).strip('-')[:80];name=str(d.get('name','')).strip()[:120];parent_slug=str(d.get('parent_slug','')).strip() or None
 if not slug or not name:raise HTTPException(400,'Category slug and name required')
 with connect() as c:
  if parent_slug and not c.execute('SELECT 1 FROM categories WHERE slug=? AND active=1 AND parent_slug IS NULL',(parent_slug,)).fetchone():raise HTTPException(400,'Parent category must be an active top-level category')
  if parent_slug==slug:raise HTTPException(400,'A category cannot be its own parent')
  try:c.execute('INSERT INTO categories(slug,name,name_ar,parent_slug) VALUES(?,?,?,?)',(slug,name,str(d.get('name_ar',''))[:120],parent_slug))
  except sqlite3.IntegrityError:raise HTTPException(409,'Category already exists')
  audit(c,u,'category_add','category',slug,{'name':name})
 return {'ok':True,'slug':slug}
@app.get('/api/admin/merchants')
async def admin_merchants(request:Request,u=Depends(admin_user)):
 with connect() as c:
  rows=c.execute('SELECT m.*,COUNT(DISTINCT o.id) AS offer_count FROM merchants m LEFT JOIN merchant_aliases a ON a.merchant_id=m.id LEFT JOIN offers o ON o.store_name=a.alias GROUP BY m.id ORDER BY m.name').fetchall()
  return [{**dict(r),'aliases':[x['alias'] for x in c.execute('SELECT alias FROM merchant_aliases WHERE merchant_id=? ORDER BY alias',(r['id'],)).fetchall()]} for r in rows]
@app.post('/api/admin/merchants')
async def add_merchant(request:Request,u=Depends(admin_user)):
 d=await body_json(request);name=str(d.get('name','')).strip()[:200];name_ar=str(d.get('name_ar','')).strip()[:200] or None;website=str(d.get('website','')).strip() or None;logo=str(d.get('logo_url','')).strip() or None
 if not name:raise HTTPException(400,'Merchant name is required')
 for value,label in ((website,'website'),(logo,'logo_url')):
  if value and not is_public_http_url(value):raise HTTPException(400,f'{label} must be a public HTTP(S) URL')
 mid=re.sub(r'[^a-z0-9-]','-',name.casefold()).strip('-')[:60] or hashlib.sha1(name.encode()).hexdigest()[:12]
 with connect() as c:
  try:
   c.execute('INSERT INTO merchants(id,name,name_ar,website,logo_url,updated_at) VALUES(?,?,?,?,?,?)',(mid,name,name_ar,website,logo,now()))
   c.execute('INSERT OR IGNORE INTO merchant_aliases(alias,merchant_id,created_at) VALUES(?,?,?)',(name,mid,now()))
  except sqlite3.IntegrityError:raise HTTPException(409,'Merchant already exists')
  audit(c,u,'merchant_add','merchant',mid,{'name':name})
 return {'ok':True,'id':mid}
@app.patch('/api/admin/merchants/{merchant_id}')
async def edit_merchant(merchant_id:str,request:Request,u=Depends(admin_user)):
 d=await body_json(request);fields={'name','name_ar','website','logo_url','active'};changes={k:v for k,v in d.items() if k in fields}
 if not changes:raise HTTPException(400,'No editable fields supplied')
 for key in ('website','logo_url'):
  if changes.get(key) and (not isinstance(changes[key],str) or not is_public_http_url(changes[key])):raise HTTPException(400,f'{key} must be a public HTTP(S) URL')
 if 'active' in changes:changes['active']=int(bool(changes['active']))
 with connect() as c:
  old=c.execute('SELECT * FROM merchants WHERE id=?',(merchant_id,)).fetchone()
  if not old:raise HTTPException(404,'Merchant not found')
  if changes.get('name') and changes['name']!=old['name']:
   c.execute('INSERT OR IGNORE INTO merchant_aliases(alias,merchant_id,created_at) VALUES(?,?,?)',(old['name'],merchant_id,now()))
   c.execute('INSERT OR IGNORE INTO merchant_aliases(alias,merchant_id,created_at) VALUES(?,?,?)',(changes['name'],merchant_id,now()))
  sql=','.join(f'{k}=?' for k in changes);c.execute(f'UPDATE merchants SET {sql},updated_at=? WHERE id=?',list(changes.values())+[now(),merchant_id]);audit(c,u,'merchant_edit','merchant',merchant_id,changes)
 return {'ok':True}
@app.patch('/api/admin/categories/{category_id}')
async def edit_category(category_id:int,request:Request,u=Depends(admin_user)):
 d=await body_json(request);changes={k:v for k,v in d.items() if k in ('name','name_ar','active','parent_slug')}
 if 'parent_slug' in changes and not changes['parent_slug']:changes['parent_slug']=None
 if not changes:raise HTTPException(400,'No editable fields supplied')
 if 'active' in changes:changes['active']=int(bool(changes['active']))
 if changes.get('parent_slug') and not isinstance(changes['parent_slug'],str):raise HTTPException(400,'Invalid parent category')
 with connect() as c:
  row=c.execute('SELECT * FROM categories WHERE id=?',(category_id,)).fetchone()
  if not row:raise HTTPException(404,'Category not found')
  if changes.get('parent_slug') and (changes['parent_slug']==row['slug'] or not c.execute('SELECT 1 FROM categories WHERE slug=? AND active=1 AND parent_slug IS NULL',(changes['parent_slug'],)).fetchone()):raise HTTPException(400,'Parent category must be an active top-level category and cannot be self-referential')
  c.execute('UPDATE categories SET '+','.join(f'{k}=?' for k in changes.keys())+' WHERE id=?',list(changes.values())+[category_id])
  audit(c,u,'category_edit','category',row['slug'],changes)
 return {'ok':True}
@app.patch('/api/admin/malls/{mall_id}')
async def edit_mall(mall_id:str,request:Request,u=Depends(admin_user)):
 d=await body_json(request);changes={k:v for k,v in d.items() if k in ('name','name_ar','area','website','active')}
 if not changes:raise HTTPException(400,'No editable fields supplied')
 if 'website' in changes and changes['website'] and not is_public_http_url(changes['website']):raise HTTPException(400,'Website must be a public HTTP(S) URL')
 if 'active' in changes:changes['active']=int(bool(changes['active']))
 with connect() as c:
  row=c.execute('SELECT * FROM malls WHERE id=?',(mall_id,)).fetchone()
  if not row:raise HTTPException(404,'Mall not found')
  c.execute('UPDATE malls SET '+','.join(f'{k}=?' for k in changes.keys())+',updated_at=? WHERE id=?',list(changes.values())+[now(),mall_id])
  if changes.get('active')==0:c.execute("UPDATE offers SET is_active=0,verification_status=CASE WHEN manual_status='approved' THEN verification_status ELSE 'pending' END,verification_reason='Mall disabled by administrator.',updated_at=? WHERE mall_id=?",(now(),mall_id))
  audit(c,u,'mall_edit','mall',mall_id,changes)
 return {'ok':True}
@app.get('/api/admin/users')
async def admin_users(request:Request,u=Depends(admin_user)):
 with connect() as c:return [user_obj(r) for r in c.execute('SELECT * FROM users ORDER BY created_at DESC').fetchall()]
@app.patch('/api/admin/users/{user_id}')
async def admin_update_user(user_id:int,request:Request,u=Depends(admin_user)):
 d=await body_json(request);role=d.get('role');active=d.get('active')
 if role not in (None,'user','admin') or active not in (None,True,False):raise HTTPException(400,'Invalid user update')
 if user_id==u['id'] and (role=='user' or active is False):raise HTTPException(400,'Cannot disable or demote your own account')
 with connect() as c:
  row=c.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
  if not row:raise HTTPException(404,'User not found')
  if role is not None:c.execute('UPDATE users SET role=? WHERE id=?',(role,user_id))
  if active is not None:c.execute('UPDATE users SET active=? WHERE id=?',(int(active),user_id))
  audit(c,u,'user_update','user',user_id,d)
  return user_obj(c.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone())
@app.get('/api/admin/audit')
async def admin_audit(request:Request,limit:int=100,u=Depends(admin_user)):
 with connect() as c:return [dict(r) for r in c.execute('SELECT a.*,u.username AS actor FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT ?',(min(500,max(1,limit)),)).fetchall()]
@app.get('/api/admin/settings')
async def admin_settings(request:Request,u=Depends(admin_user)):
 with connect() as c:return {r['key']:r['value'] for r in c.execute('SELECT key,value FROM app_settings').fetchall()}
@app.patch('/api/admin/settings')
async def update_admin_settings(request:Request,u=Depends(admin_user)):
 d=await body_json(request)
 if 'discovery_interval_hours' not in d:raise HTTPException(400,'discovery_interval_hours is required')
 try:hours=int(d['discovery_interval_hours'])
 except (ValueError,TypeError):raise HTTPException(400,'Interval must be an integer from 1 to 168 hours')
 if not 1<=hours<=168:raise HTTPException(400,'Interval must be between 1 and 168 hours')
 with connect() as c:
  c.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('discovery_interval_hours',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(str(hours),now()))
  audit(c,u,'schedule_update','settings','discovery_interval_hours',{'hours':hours})
 return {'discovery_interval_hours':hours,'note':'The background scheduler reads the new interval after its current sleep cycle completes.'}
@app.get('/api/admin/jobs')
async def admin_jobs(request:Request,limit:int=50,u=Depends(admin_user)):
 with connect() as c:return [dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?',(min(200,max(1,limit)),)).fetchall()]

def build_runtime_sources():
 base_path=ROOT/'sources.json'
 try:base=json.loads(base_path.read_text(encoding='utf-8'))
 except Exception:base={'malls':[],'feeds':[],'discovery_queries':[],'search_feed_templates':[]}
 with connect() as c:
  mall_rows=c.execute('SELECT * FROM malls WHERE active=1 ORDER BY name').fetchall()
  source_rows=c.execute('SELECT * FROM sources WHERE enabled=1 AND deleted_at IS NULL ORDER BY id').fetchall()
 base_malls={m.get('id'):dict(m) for m in base.get('malls',[]) if m.get('id')}
 runtime=[]
 for row in mall_rows:
  mid=row['id'];orig=base_malls.get(mid,{})
  m={'id':mid,'name':row['name'],'name_ar':row['name_ar'],'area':row['area'],'pages':[]}
  for sr in source_rows:
   if sr['mall_id']!=mid:continue
   settings=json.loads(sr['settings'] or '{}')
   if sr['source_type'] in ('text','images','api'):
    m['pages'].append({**settings,'url':sr['url'],'type':sr['source_type']})
   elif sr['source_type']=='sitemap':
    m.setdefault('sitemaps',[]).append({**settings,'url':sr['url']})
  if not m['pages'] and not m.get('sitemaps') and not orig.get('pages') and not orig.get('sitemap'):continue
  runtime.append(m)
 # Sources without a mall are collected under a clearly labelled discovery bucket.
 generic=[sr for sr in source_rows if sr['mall_id'] is None and sr['source_type'] in ('text','images','api')]
 if generic:
  gm={'id':'web-discovery','name':'Web discovery','name_ar':'اكتشاف الويب','area':'Qatar','pages':[]}
  for sr in generic:
   settings=json.loads(sr['settings'] or '{}');gm['pages'].append({**settings,'url':sr['url'],'type':sr['source_type']})
  runtime.append(gm)
 feeds=[]
 for sr in source_rows:
  if sr['source_type']=='rss':
   settings=json.loads(sr['settings'] or '{}');feeds.append({**settings,'name':sr['name'],'url':sr['url']})
 # Preserve search discovery settings, but source URLs themselves are managed in the database.
 config={'malls':runtime,'feeds':feeds,'discovery_queries':base.get('discovery_queries',[]),'search_feed_templates':base.get('search_feed_templates',[])}
 target=ROOT/'data'/'runtime_sources.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
 return target

def run_collection(created_by=None):
 start=now()
 with connect() as c:
  cur=c.execute('INSERT INTO jobs(kind,status,started_at,created_at,created_by) VALUES(?,?,?,?,?)',('discovery','running',start,start,created_by));job_id=cur.lastrowid
 try:
  runtime_config=build_runtime_sources()
  proc=subprocess.run([sys.executable,str(ROOT/'crawler'/'run.py')],cwd=str(ROOT),capture_output=True,text=True,timeout=int(os.environ.get('COLLECTOR_TIMEOUT','900')),env={**os.environ,'TZ':'Asia/Qatar','SOURCES_CONFIG_PATH':str(runtime_config)})
  if proc.returncode:raise RuntimeError((proc.stderr or proc.stdout)[-3000:])
  imported=sync_state_to_db()
  with connect() as c:c.execute('UPDATE jobs SET status=?,finished_at=?,summary=? WHERE id=?',('success',now(),json.dumps({'output':proc.stdout[-4000:],'offers_synced':imported}),job_id))
  return {'job_id':job_id,'status':'success','output':proc.stdout[-4000:],'offers_synced':imported}
 except Exception as e:
  with connect() as c:c.execute('UPDATE jobs SET status=?,finished_at=?,error=? WHERE id=?',('failed',now(),str(e)[:4000],job_id))
  raise
@app.post('/api/admin/jobs/run')
async def start_job(request:Request,u=Depends(admin_user)):
 try:return await asyncio.to_thread(run_collection,u['id'])
 except Exception as e:raise HTTPException(500,'Collector failed. See job logs for details.')
@app.post('/api/admin/jobs/{job_id}/retry')
async def retry_job(job_id:int,request:Request,u=Depends(admin_user)):
 with connect() as c:
  row=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
  if not row:raise HTTPException(404,'Job not found')
 return await start_job(request,u)

# Serve the same frontend locally/on a server. GitHub Pages still uses the generated JSON fallback.
@app.get('/')
def home():return FileResponse(ROOT/'web'/'index.html')
@app.get('/admin')
def admin_page():return FileResponse(ROOT/'web'/'admin.html')
@app.get('/{asset_path:path}')
def static_asset(asset_path:str):
 if asset_path.startswith('api/'):raise HTTPException(404,'Not found')
 path=(ROOT/'web'/asset_path).resolve()
 if not str(path).startswith(str((ROOT/'web').resolve())) or not path.is_file():raise HTTPException(404,'Not found')
 return FileResponse(path)

if __name__=='__main__':
 import uvicorn
 uvicorn.run('api:app',host=os.environ.get('HOST','127.0.0.1'),port=int(os.environ.get('PORT','8000')),reload=False)
