"""Portable Qatar Offers collector. Official sources are verified in-page; discovery candidates are queued.
No hosted AI API is used. The same collector can run in GitHub Actions or on a private server.
"""
from __future__ import annotations
import os, re, json, time, hashlib, pathlib, datetime as dt, urllib.robotparser, email.utils, xml.etree.ElementTree as ET, socket, ipaddress, difflib
from urllib.parse import urljoin, urlparse, quote_plus
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
import ai

ROOT=pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH=pathlib.Path(os.environ.get('SOURCES_CONFIG_PATH', ROOT/'sources.json'))
QATAR=ZoneInfo('Asia/Qatar')
UA={'User-Agent':os.environ.get('CRAWLER_USER_AGENT','QatarOffersBot/1.0 (+https://github.com/xxSBQxx/qatar-offers)')}
KEEP_DAYS=int(os.environ.get('KEEP_DAYS','10'))
MAX_NEW_IMAGES=int(os.environ.get('MAX_NEW_IMAGES','25'))
SLEEP=max(0.5,float(os.environ.get('CRAWLER_SLEEP','2')))
MAX_PAGE_BYTES=int(os.environ.get('MAX_PAGE_BYTES','5000000'))
CATS={'fashion','food','electronics','beauty','home','grocery','other'}
_SESSION=requests.Session(); _ROBOTS={}; _LAST_HOST={}

def jload(p,default):
 try:return json.loads(pathlib.Path(p).read_text(encoding='utf-8'))
 except (OSError,ValueError,TypeError):return default

def jsave(p,obj):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)

def public_url(url):
 try:
  parsed=urlparse(url)
  if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:return False
  host=parsed.hostname.rstrip('.').lower()
  if host in ('localhost','localhost.localdomain') or host.endswith(('.local','.internal','.localhost')):return False
  try:ips=[ipaddress.ip_address(host)]
  except ValueError:ips=[ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)]
  return bool(ips) and all(ip.is_global for ip in ips)
 except Exception:return False

def allowed(url, user_agent='QatarOffersBot'):
 parsed=urlparse(url)
 if not public_url(url):return False
 host=parsed.netloc.lower()
 if host not in _ROBOTS:
  rp=urllib.robotparser.RobotFileParser();rp.set_url(f'{parsed.scheme}://{host}/robots.txt')
  try:
   r=_SESSION.get(rp.url,headers=UA,timeout=8,allow_redirects=False)
   if r.status_code==200:rp.parse(r.text.splitlines())
   elif r.status_code in (401,403) or 300 <= r.status_code < 400 or r.status_code >= 500:rp.parse(['User-agent: *','Disallow: /'])
   else:rp.parse(['User-agent: *','Allow: /'])
  except requests.RequestException:rp.parse(['User-agent: *','Disallow: /'])
  _ROBOTS[host]=rp
 try:return _ROBOTS[host].can_fetch(user_agent,url) and _ROBOTS[host].can_fetch('*',url)
 except Exception:return False

def fetch(url, timeout=15, max_bytes=MAX_PAGE_BYTES, respect_robots=True, retries=3):
 if respect_robots and not allowed(url):raise PermissionError(f'robots.txt disallows or URL is not public: {url}')
 if not public_url(url):raise PermissionError(f'private or non-public URL blocked: {url}')
 last=None
 for attempt in range(max(1,min(4,int(retries)))):
  current=url
  try:
   for _ in range(6):
    if not public_url(current):raise PermissionError(f'redirect to non-public URL blocked: {current}')
    if respect_robots and current!=url and not allowed(current):raise PermissionError(f'robots.txt disallows redirected URL: {current}')
    current_host=urlparse(current).netloc.lower();wait=SLEEP-(time.monotonic()-_LAST_HOST.get(current_host,0))
    if wait>0:time.sleep(wait)
    r=_SESSION.get(current,headers=UA,timeout=timeout,allow_redirects=False,stream=True)
    if r.status_code in (301,302,303,307,308):
     location=r.headers.get('location');r.close()
     if not location:raise ValueError('redirect without a location')
     current=urljoin(current,location);continue
    if r.status_code in (429,500,502,503,504):
     delay=min(15,2**attempt);r.close();time.sleep(delay);break
    r.raise_for_status();chunks=[];size=0
    for chunk in r.iter_content(65536):
     if not chunk:continue
     size+=len(chunk)
     if size>max_bytes:r.close();raise ValueError(f'response exceeds {max_bytes} bytes')
     chunks.append(chunk)
    r._content=b''.join(chunks);r._content_consumed=True;r.url=current
    _LAST_HOST[current_host]=time.monotonic();return r
   else:raise ValueError('too many redirects')
  except (requests.RequestException,ValueError,PermissionError) as e:
   last=e
   if isinstance(e,PermissionError):raise
   if attempt<max(1,min(4,int(retries)))-1:time.sleep(2**attempt)
 raise last or RuntimeError('fetch failed')

def text_of(soup):
 for t in soup(['script','style','nav','footer','noscript','svg','form']):t.decompose()
 return re.sub(r'\n{2,}','\n',soup.get_text('\n',strip=True))

def flatten_json(value):
 out=[]
 def walk(item,key=''):
  if isinstance(item,dict):
   for k,v in item.items():
    if isinstance(v,(str,int,float)) and not isinstance(v,bool):out.append(f'{k}: {v}')
    else:walk(v,str(k))
  elif isinstance(item,list):
   for v in item:walk(v,key)
  elif isinstance(item,(str,int,float)) and not isinstance(item,bool):out.append(str(item))
 walk(value)
 return '\n'.join(out)

def find_images(soup,base,spec):
 out=[];seen=set();fragment=spec.get('image_contains','/uploads/')
 for img in soup.find_all('img'):
  src=img.get('src') or img.get('data-src') or img.get('data-lazy-src') or ''
  if not src or (fragment and fragment not in src):continue
  url=urljoin(base,src)
  if url in seen or urlparse(url).scheme not in ('http','https'):continue
  box=img.find_parent(['li','article','div','figure']);ctx=box.get_text(' ',strip=True)[:500] if box else ''
  if spec.get('context_must') and spec['context_must'].casefold() not in ctx.casefold():continue
  seen.add(url);out.append((url,ctx,img.get('alt') or ''))
 return out

def sitemap_urls(spec):
 try:
  r=fetch(spec['url']);xml=r.text
  # Parse XML safely; only locations matching configured path.
  root=ET.fromstring(xml);locs=[e.text.strip() for e in root.iter() if e.tag.endswith('loc') and e.text]
  for child in [u for u in locs if u.endswith('.xml')][:5]:
   try:
    rr=fetch(child);cr=ET.fromstring(rr.text);locs += [e.text.strip() for e in cr.iter() if e.tag.endswith('loc') and e.text and not e.text.strip().endswith('.xml')]
   except Exception as exc:print('sitemap child skipped:',str(exc)[:160])
  contains=spec.get('contains','')
  return list(dict.fromkeys(u for u in locs if contains in u and not u.endswith('.xml')))[:int(spec.get('max_urls',30))]
 except Exception as exc:print('sitemap skipped:',str(exc)[:180]);return []

def feed_entries(url,timeout=10,retries=1):
 """Read RSS/Atom with the standard XML parser; feed URLs must be configured."""
 try:
  r=fetch(url,timeout=timeout,max_bytes=2_000_000,retries=retries);root=ET.fromstring(r.content);out=[]
  for item in [e for e in root.iter() if e.tag.rsplit('}',1)[-1].lower() in ('item','entry')][:100]:
   fields={e.tag.rsplit('}',1)[-1].lower():e for e in list(item)}
   title_el=fields.get('title');desc_el=fields.get('description') or fields.get('summary') or fields.get('content');link_el=fields.get('link')
   href=''
   if link_el is not None:href=link_el.attrib.get('href') or (link_el.text or '').strip()
   title=' '.join(''.join(title_el.itertext()).split()) if title_el is not None else ''
   desc=' '.join(''.join(desc_el.itertext()).split())[:1000] if desc_el is not None else ''
   if href.startswith('http'):out.append({'title':title,'url':href,'description':desc})
  return out
 except Exception as exc:print('feed skipped:',str(exc)[:160]);return []

def google_cse_entries(queries):
 """Optional official Google Programmable Search API. Results are leads only; no result is auto-trusted."""
 key=os.environ.get('GOOGLE_CSE_API_KEY','').strip();cx=os.environ.get('GOOGLE_CSE_ID','').strip()
 if not key or not cx:return []
 out=[];max_queries=max(1,min(20,int(os.environ.get('GOOGLE_CSE_MAX_QUERIES','20'))))
 for query in list(dict.fromkeys(queries))[:max_queries]:
  try:
   response=_SESSION.get('https://www.googleapis.com/customsearch/v1',params={'key':key,'cx':cx,'q':query,'num':10,'gl':'qa','cr':'countryQA'},headers=UA,timeout=15)
   response.raise_for_status();payload=response.json()
   for item in payload.get('items',[]):
    link=item.get('link');title=' '.join(str(item.get('title') or '').split())[:300];snippet=' '.join(str(item.get('snippet') or '').split())[:1000]
    if isinstance(link,str) and urlparse(link).scheme in ('http','https') and public_url(link):
     out.append({'title':title,'url':link,'description':snippet,'feed_name':'Google Programmable Search','query':query,'trusted':False})
  except Exception as exc:print('Google Programmable Search skipped:',str(exc)[:180])
 return out

def clean_date(v):
 try:return dt.date.fromisoformat(v).isoformat() if isinstance(v,str) else None
 except ValueError:return None

def s300(v):return v.strip()[:500] if isinstance(v,str) and v.strip() else None

def normalize(raw,today):
 if not isinstance(raw,dict):return None
 store,title=s300(raw.get('store_name')),s300(raw.get('title'))
 if not store or not title:return None
 start,end=clean_date(raw.get('start_date')),clean_date(raw.get('end_date'))
 if end and end<today:return None
 if start and end and start>end:return None
 url=raw.get('source_url')
 if not isinstance(url,str) or not re.match(r'^https?://',url):return None
 return {'store_name':store,'title':title,'title_ar':s300(raw.get('title_ar')),'description':s300(raw.get('description')),'discount_text':s300(raw.get('discount_text')),'category':raw.get('category') if raw.get('category') in CATS else 'other','start_date':start,'end_date':end,'language':raw.get('language') if raw.get('language') in ('en','ar','mixed') else ('ar' if re.search(r'[\u0600-\u06ff]',title) else 'en'),'source_url':url,'image_url':raw.get('image_url') if isinstance(raw.get('image_url'),str) and raw.get('image_url','').startswith('https://') else None,'original_price':s300(raw.get('original_price')),'sale_price':s300(raw.get('sale_price')),'promo_code':s300(raw.get('promo_code')),'terms':s300(raw.get('terms')),'redemption_instructions':s300(raw.get('redemption_instructions')),'evidence':s300(raw.get('evidence') or raw.get('description')),'confidence':max(0,min(1,float(raw.get('confidence') or 0.4)))}

def source_is_official(url,configured_url,source):
 try:
  host=urlparse(url).hostname.lower().removeprefix('www.')
  configured=urlparse(configured_url).hostname.lower().removeprefix('www.')
  allow={x.lower().removeprefix('www.') for x in source.get('trusted_domains',[])}
  return host==configured or host.endswith('.'+configured) or host in allow
 except Exception:return False

def touch_offer(state,offer_id,today,nowdt):
 o=state['offers'].get(offer_id)
 if not o:return
 o['last_seen']=today;o['last_checked_at']=nowdt.isoformat(timespec='seconds')
 end=o.get('end_date');start=o.get('start_date')
 valid_date=(not end or end>=today) and (not start or start<=today)
 if o.get('manual_status')=='rejected':o['verification_status']='rejected';o['is_active']=False;return
 if o.get('manual_status')=='edited':o['verification_status']='pending';o['is_active']=False;return
 has_evidence=bool(o.get('evidence') and (o.get('discount_text') or re.search(r'\b(?:offer|sale|promotion|buy|free|deal|voucher|coupon)\b|عرض|خصم|تخفيض',o.get('evidence',''),re.I)))
 if valid_date and has_evidence:
  o['verification_status']='verified';o['verification_reason']='Reconfirmed on the current source page.';o['verified_at']=nowdt.isoformat(timespec='seconds');o['is_active']=True
 elif not valid_date:
  o['verification_status']='expired' if end and end<today else 'pending';o['is_active']=False

def add_offers(state,mall,offers,source_url,branch,today,source_cfg,verified_source=True):
 ids=[]
 for raw in offers:
  o=normalize(raw,today)
  if not o:continue
  if branch:o['description']=((o['description'] or '')+f' Branch: {branch}.').strip()
  # Keep stable IDs across wording/date updates by matching source + merchant + title.
  normalized_store=re.sub(r'\W+','',o['store_name'].lower())
  normalized_title=re.sub(r'\W+','',o['title'].lower())
  scope='merchant-global' if mall.get('id')=='web-discovery' else mall.get('id','discovery')
  evidence_hash=hashlib.sha256((o.get('evidence') or '').encode()).hexdigest()
  base='|'.join([scope,normalized_store,normalized_title,o.get('end_date') or 'no-expiry'])
  oid=hashlib.sha256(base.encode()).hexdigest()[:20]
  old=state['offers'].get(oid,{})
  if not old:
   # If an admin edited the title/store/source URL, retain the existing record ID by matching its original source evidence.
   for candidate_id,candidate in state['offers'].items():
    manual_fields=set(candidate.get('manual_fields',[]))
    same_source=candidate.get('source_url')==o.get('source_url') or 'source_url' in manual_fields
    same_mall=candidate.get('mall_id')==mall.get('id','discovery')
    same_end=candidate.get('end_date')==o.get('end_date') or 'end_date' in manual_fields
    previous_evidence=str(candidate.get('evidence') or '').casefold();new_evidence=str(o.get('evidence') or '').casefold()
    similarity=difflib.SequenceMatcher(None,previous_evidence,new_evidence).ratio() if previous_evidence and new_evidence else 0
    if manual_fields and same_source and same_mall and same_end and (candidate.get('last_source_hash')==evidence_hash or similarity>=0.45):
     oid=candidate_id;old=candidate;break
  verified=bool(verified_source and o.get('evidence') and (o.get('discount_text') or re.search(r'\b(?:offer|sale|promotion|buy|free|deal|voucher|coupon)\b|عرض|خصم|تخفيض',o['evidence'],re.I)))
  record={**old,**o,'id':oid,'mall_id':old.get('mall_id') if old.get('manual_status')=='edited' else mall.get('id','discovery'),'mall_name':old.get('mall_name') if old.get('manual_status')=='edited' and 'mall_name' in old.get('manual_fields',[]) else mall.get('name','Web discovery'),'origin':'local_extractor','verification_status':'verified' if verified else 'pending','verification_reason':'Evidence extracted from a currently reachable configured source page.' if verified else 'Discovered candidate requires source verification or manual review.','verified_at':dt.datetime.now(QATAR).isoformat(timespec='seconds') if verified else old.get('verified_at'),'source_url':o.get('source_url') or source_url,'first_seen':old.get('first_seen',today),'last_seen':today,'last_checked_at':dt.datetime.now(QATAR).isoformat(timespec='seconds'),'last_source_hash':evidence_hash,'manual_status':old.get('manual_status','auto'),'manual_fields':old.get('manual_fields',[]),'is_active':bool(verified and (not o.get('end_date') or o['end_date']>=today) and (not o.get('start_date') or o['start_date']<=today))}
  # Keep administrator corrections until explicitly reviewed; extraction confidence never overrides them.
  for field in record.get('manual_fields',[]):
   if field in old:record[field]=old[field]
  if old.get('manual_status')=='approved':record['verification_status']='verified'
  elif old.get('manual_status')=='rejected':record['verification_status']='rejected';record['is_active']=False
  elif old.get('manual_status')=='edited' or (record.get('manual_fields') and old.get('manual_status') not in ('approved','rejected')):record['manual_status']='edited';record['verification_status']='pending';record['verification_reason']='Offer details were edited and require re-verification.';record['is_active']=False
  state['offers'][oid]=record;ids.append(oid)
 return ids

def main(root=ROOT):
 root=pathlib.Path(root);nowdt=dt.datetime.now(QATAR);today=nowdt.date().isoformat()
 cfg=jload(pathlib.Path(os.environ.get('SOURCES_CONFIG_PATH', root/'sources.json')),{'malls':[]});state=jload(root/'data/state.json',{})
 for k in ('pages','images','searched','offers','jobs','sources','discovered_sources'):state.setdefault(k,{})
 status=[];new_images=0;failures=[];configured_urls=[];seen_ids=set();removed_candidates=set();source_checks={}
 # Configured official mall and retailer sources.
 for mall in cfg.get('malls',[]):
  pages=list(mall.get('pages',[]))
  sitemap_specs=mall.get('sitemaps') or ([mall['sitemap']] if mall.get('sitemap') else [])
  for sitemap_spec in sitemap_specs:
   sitemap_found=sitemap_urls(sitemap_spec)
   source_checks[sitemap_spec.get('url','')]={'checked_at':nowdt.isoformat(timespec='seconds'),'status':'ok' if sitemap_found else 'empty','error':None}
   if sitemap_found:
    current_sitemap_urls=set(sitemap_found)|{p.get('url') for p in pages}
    contains=sitemap_spec.get('contains','')
    for old_url,old_rec in state['pages'].items():
     if contains and contains in old_url and old_url not in current_sitemap_urls:removed_candidates.update(old_rec.get('offer_ids',[]))
    pages += [{'url':u,'type':'text'} for u in sitemap_found if u not in {p.get('url') for p in pages}]
  source_status=[]
  for page in pages:
   url=page.get('url','');configured_urls.append(url)
   try:
    response=fetch(url);ctype=(response.headers.get('content-type') or '').lower()
    if page.get('type')=='api':
     payload=json.loads(response.text);api_text=flatten_json(payload);soup=None
    else:
     if 'html' not in ctype and not response.text.lstrip().startswith(('<!DOCTYPE','<html','<?xml')):raise ValueError('unsupported content type '+ctype)
     soup=BeautifulSoup(response.text,'html.parser');api_text=None
    source_status.append({'title':urlparse(url).netloc+urlparse(url).path[:40],'url':url,'status':'ok'})
    if page.get('type')=='images':
     found_images=find_images(soup,url,page)
     current_image_urls={item[0] for item in found_images}
     for image_url,context,alt in found_images:
      old=state['images'].get(image_url)
      checked=old.get('last_checked_at') if old else None
      try:recent_check=bool(checked and (nowdt-dt.datetime.fromisoformat(checked)).total_seconds()<86400)
      except (ValueError,TypeError):recent_check=False
      if old and old.get('offer_ids') and recent_check:
       for oid in old['offer_ids']:
        if oid in state['offers']:touch_offer(state,oid,today,nowdt);seen_ids.add(oid)
       continue
      if new_images>=MAX_NEW_IMAGES:continue
      try:
       ir=fetch(image_url,max_bytes=6_000_000);digest=hashlib.sha256(ir.content).hexdigest()
       if old and old.get('hash')==digest:
        for oid in old.get('offer_ids',[]):
         if oid in state['offers']:touch_offer(state,oid,today,nowdt);seen_ids.add(oid)
        old['last_checked_at']=nowdt.isoformat(timespec='seconds');continue
       mime=(ir.headers.get('content-type') or 'image/jpeg').split(';')[0]
       offers=ai.image_offers(ir.content,mime,context+' '+alt,mall['name'],page.get('branch'),today,url)
       for offer in offers: offer['image_url']=image_url if page.get('display_images') is True else None
       ids=add_offers(state,mall,offers,url,page.get('branch'),today,url,True);seen_ids.update(ids)
       if old:
        for prior_id in old.get('offer_ids',[]):
         if prior_id not in ids and prior_id in state['offers'] and state['offers'][prior_id].get('manual_status')!='approved':
          removed_candidates.add(prior_id)
       state['images'][image_url]={'offer_ids':ids,'hash':digest,'last_checked_at':nowdt.isoformat(timespec='seconds')};new_images+=1
      except Exception as exc:
       failures.append({'url':image_url,'error':str(exc)[:240]})
       if old:removed_candidates.update(old.get('offer_ids',[]))
     for old_image_url,image_record in state['images'].items():
      if image_record.get('page_url')==url and old_image_url not in current_image_urls:removed_candidates.update(image_record.get('offer_ids',[]))
    else:
     text=api_text if page.get('type')=='api' else text_of(soup);page_hash=hashlib.sha256(text.encode()).hexdigest();old=state['pages'].get(url)
     if old and old.get('hash')==page_hash:
      for oid in old.get('offer_ids',[]):
       if oid in state['offers']:touch_offer(state,oid,today,nowdt);seen_ids.add(oid)
     else:
      offers=ai.text_offers(text,mall['name'],url,today)
      ids=add_offers(state,mall,offers,url,page.get('branch'),today,page,True);seen_ids.update(ids)
      # Check after all sources are processed: another source may still confirm the same offer.
      if old:removed_candidates.update(prior_id for prior_id in old.get('offer_ids',[]) if prior_id not in ids)
      state['pages'][url]={'hash':page_hash,'offer_ids':ids,'last_checked_at':nowdt.isoformat(timespec='seconds')}
   except Exception as exc:
    source_status.append({'title':urlparse(url).netloc+urlparse(url).path[:40],'url':url,'status':'error','error':str(exc)[:240]})
    old_page=state['pages'].get(url)
    if old_page:removed_candidates.update(old_page.get('offer_ids',[]))
    for image_record in state['images'].values():
     if image_record.get('page_url')==url:removed_candidates.update(image_record.get('offer_ids',[]))
    failures.append({'url':url,'error':str(exc)[:240]})
  status.append({'id':mall.get('id'),'name':mall.get('name'),'checked_at':nowdt.isoformat(timespec='seconds'),'sources':source_status})
 # Configured RSS/Atom sources; entries are candidates, not automatically trusted/published.
 discovery=[]
 for feed in cfg.get('feeds',[]):
  entries=feed_entries(feed.get('url',''),timeout=10,retries=1)
  source_checks[feed.get('url','')]={'checked_at':nowdt.isoformat(timespec='seconds'),'status':'ok' if entries else 'empty','error':None}
  for entry in entries:discovery.append({**entry,'feed_name':feed.get('name','RSS discovery'),'trusted':False})
 # Public search RSS queries rotate each six-hour window to bound collection time while covering all categories over time.
 search_queries=list(cfg.get('discovery_queries',[]))+list(cfg.get('social_discovery_queries',[]))
 query_limit=max(1,min(20,int(os.environ.get('RSS_SEARCH_QUERIES_PER_RUN','8'))))
 if search_queries:
  start_index=int(nowdt.timestamp()//(6*3600))%len(search_queries)
  search_queries=[search_queries[(start_index+i)%len(search_queries)] for i in range(min(query_limit,len(search_queries)))]
 for template in cfg.get('search_feed_templates',[]):
  for query in search_queries:
   try:
    url=template.format(query=quote_plus(query))
    for entry in feed_entries(url,timeout=10,retries=1):discovery.append({**entry,'feed_name':'Search RSS','query':query,'trusted':False})
   except Exception as exc:failures.append({'url':str(template),'error':str(exc)[:200]})
 # Official Google Search API is optional. Never scrape result pages or bypass access controls.
 cse_queries=[f'Qatar offers promotions {m.get("name", "")}' for m in cfg.get('malls',[]) if m.get('search')]
 cse_queries += list(cfg.get('social_discovery_queries',[]))
 cse_queries += list(cfg.get('discovery_queries',[]))
 discovery.extend(google_cse_entries(cse_queries))
 # Store discovery leads for administrator review before a URL is promoted to a trusted source.
 for candidate in discovery:
  url=candidate.get('url','')
  if not isinstance(url,str) or not public_url(url):continue
  sid=hashlib.sha256(url.encode()).hexdigest()[:20]
  previous=state['discovered_sources'].get(sid,{})
  state['discovered_sources'][sid]={**previous,'id':sid,'url':url,'title':candidate.get('title') or previous.get('title') or urlparse(url).netloc,'description':candidate.get('description') or previous.get('description'),'discovered_via':candidate.get('feed_name','RSS discovery'),'query':candidate.get('query') or previous.get('query'),'first_seen':previous.get('first_seen',nowdt.isoformat(timespec='seconds')),'last_seen':nowdt.isoformat(timespec='seconds'),'status':previous.get('status','pending')}
 # Bound the persistent review queue so a noisy feed cannot grow the state file indefinitely.
 if len(state['discovered_sources'])>1000:
  ordered=sorted(state['discovered_sources'].items(),key=lambda pair:pair[1].get('last_seen',''),reverse=True)
  state['discovered_sources']=dict(ordered[:1000])
 for candidate in discovery[:int(os.environ.get('MAX_DISCOVERY_ITEMS','100'))]:
  url=candidate['url']
  if url in configured_urls:continue
  try:
   r=fetch(url);ctype=(r.headers.get('content-type') or '').lower()
   if 'html' not in ctype:continue
   soup=BeautifulSoup(r.text,'html.parser');text=text_of(soup)
   # Only create a review candidate if concrete offer-like evidence exists on the article page.
   offers=ai.text_offers(text,'Web discovery',r.url,today)
   if offers:
    mall={'id':'web-discovery','name':'Web discovery'}
    for o in offers:o['source_url']=r.url;o['description']=((o.get('description') or '')+' '+candidate.get('title','')).strip()
    add_offers(state,mall,offers,r.url,None,today,{},False)
  except Exception as exc:failures.append({'url':url,'error':str(exc)[:220]})
 # Deactivate removed/unreachable offers only if no other source reconfirmed them in this run.
 for oid in removed_candidates-seen_ids:
  if oid in state['offers'] and state['offers'][oid].get('manual_status')!='rejected':
   state['offers'][oid]['verification_status']='pending';state['offers'][oid]['is_active']=False;state['offers'][oid]['verification_reason']='Offer was not found after its source changed or became unavailable; manual re-check required.'
   if state['offers'][oid].get('manual_status')=='approved':state['offers'][oid]['manual_status']='auto'
 # Expiry and staleness: expire explicit dates; stale records are not published and remain available to admin audit.
 for oid,o in list(state['offers'].items()):
  end=o.get('end_date');last=o.get('last_seen') or today
  try:stale=(dt.date.fromisoformat(today)-dt.date.fromisoformat(last)).days>KEEP_DAYS
  except ValueError:stale=True
  if end and end<today:o['verification_status']='expired';o['inactive_reason']='Explicit end date has passed.'
  elif stale and o.get('verification_status')=='verified':o['verification_status']='pending';o['verification_reason']='Source has not reconfirmed this offer within the stale threshold.'
  o['is_active']=bool(o.get('verification_status')=='verified' and (not end or end>=today) and (not o.get('start_date') or o['start_date']<=today) and not stale)
  o['days_since_seen']=(dt.date.fromisoformat(today)-dt.date.fromisoformat(last)).days if re.match(r'^\d{4}-\d\d-\d\d$',last) else None
 state['sources']={**source_checks,**{src['url']:{'checked_at':mall_status.get('checked_at'),'status':src.get('status'),'error':src.get('error')} for mall_status in status for src in mall_status.get('sources',[])}}
 state['jobs']['last_run']={'started_at':nowdt.isoformat(timespec='seconds'),'finished_at':dt.datetime.now(QATAR).isoformat(timespec='seconds'),'sources_checked':sum(len(x['sources']) for x in status),'discovery_candidates':len(discovery),'offers_total':len(state['offers']),'active_total':sum(1 for x in state['offers'].values() if x.get('is_active')),'failures':failures[:100]}
 # Publish only source-evidenced verified current offers; no generated sample records.
 public_fields=['id','store_name','mall_name','title','title_ar','description','discount_text','category','start_date','end_date','source_url','language','origin','image_url','original_price','sale_price','promo_code','terms','redemption_instructions','verification_status','verified_at','last_checked_at','is_active']
 live=[o for o in state['offers'].values() if o.get('is_active') and o.get('verification_status')=='verified']
 live.sort(key=lambda o:(o.get('end_date') or '9999-12-31',o.get('store_name','').casefold()))
 jsave(root/'web/offers.json',{'updated':nowdt.isoformat(timespec='seconds'),'timezone':'Asia/Qatar','status':status,'counts':{'active':len(live),'pending':sum(1 for o in state['offers'].values() if o.get('verification_status')=='pending'),'expired':sum(1 for o in state['offers'].values() if o.get('verification_status')=='expired')},'offers':[{k:o.get(k) for k in public_fields} for o in live]})
 jsave(root/'web/malls.json',[{k:m.get(k) for k in ('id','name','name_ar','area','website')} for m in cfg.get('malls',[])])
 jsave(root/'data/state.json',state)
 print(f"done: {len(live)} verified active offers; {state['jobs']['last_run']['discovery_candidates']} discovery candidates; {len(failures)} source failures")
 return state['jobs']['last_run']

if __name__=='__main__':main()
