"""Offline tests for extraction, verification gating, expiry, and removal detection."""
import sys, pathlib, datetime as dt, json, tempfile
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import ai,run
TODAY=dt.datetime.now(dt.timezone.utc).date().isoformat()
FUTURE=(dt.date.fromisoformat(TODAY)+dt.timedelta(days=30)).isoformat()
PAST=(dt.date.fromisoformat(TODAY)-dt.timedelta(days=5)).isoformat()

items=ai.text_offers(f"Promotions\nNike\nNike up to 50% off\nValid 01 Sep 2026 - 31 Dec 2026\nCarrefour\nBuy 1 get 1 Free\n",'Mall of Qatar','https://mall.example/offers',TODAY)
assert items and any(x['discount_text'] and x['store_name']=='Nike' for x in items),items
assert all(x['source_url']=='https://mall.example/offers' for x in items)
assert all(x['category'] in run.CATS for x in items)
old=ai.text_offers('Nike\nSale up to 50% off\n01 Jan 2020 - 31 Jan 2020','Example Mall','https://example.test',TODAY)
assert old==[],old
# Do not invent dates.
no_date=ai.text_offers('Nike\nUp to 40% off\nShop now','Example Mall','https://example.test/offers',TODAY)
assert no_date and all(x['end_date'] is None for x in no_date),no_date

class Response:
 def __init__(self,text,url='https://example.test/offers'):
  self.text=text;self.content=text.encode();self.headers={'content-type':'text/html'};self.url=url
original_fetch,original_allowed,original_feeds=run.fetch,run.allowed,run.feed_entries
run.allowed=lambda *a,**k:True
assert 'title: Nike promotion' in run.flatten_json({'items':[{'title':'Nike promotion','discount':'50% off'}]})
# RSS/Atom parsing uses the standard library and can be tested without network access.
run.fetch=lambda url,**kwargs:Response('<rss><channel><item><title>Nike offer</title><link>https://merchant.example/offer</link><description>50% off</description></item></channel></rss>',url)
assert run.feed_entries('https://feed.example/rss')[0]['url']=='https://merchant.example/offer'
run.feed_entries=lambda url:[]
page={'html':'<html><body><h1>Nike</h1><p>Nike up to 50% off</p><p>Valid until 31 Dec 2027</p></body></html>'}
run.fetch=lambda url,**kwargs:Response(page['html'],url)
root=pathlib.Path(tempfile.mkdtemp())
(root/'sources.json').write_text(json.dumps({'malls':[{'id':'test-mall','name':'Test Mall','name_ar':'مجمع تجريبي','pages':[{'url':'https://example.test/offers','type':'text'}]}]}),encoding='utf-8')
run.main(root)
out=json.loads((root/'web/offers.json').read_text(encoding='utf-8'))
assert out['offers'] and out['offers'][0]['mall_name']=='Test Mall',out
assert out['offers'][0]['verification_status']=='verified' and out['offers'][0]['is_active'] is True,out['offers'][0]
# A changed page which removes the offer deactivates it for review instead of leaving it live.
page['html']='<html><body><h1>Welcome to the mall</h1><p>Visit us</p></body></html>'
run.main(root)
state=json.loads((root/'data/state.json').read_text(encoding='utf-8'))
assert all(o.get('verification_status')!='verified' or not o.get('is_active') for o in state['offers'].values()),state['offers']
# The same offer on two configured pages deduplicates; one page dropping it does not invalidate the other source.
root2=pathlib.Path(tempfile.mkdtemp())
page2={'one':'<html><body><h1>Nike</h1><p>Nike up to 50% off</p><p>Valid until 31 Dec 2027</p></body></html>','two':'<html><body><h1>Nike</h1><p>Nike up to 50% off</p><p>Valid until 31 Dec 2027</p></body></html>'}
(root2/'sources.json').write_text(json.dumps({'malls':[{'id':'mall-a','name':'Mall A','pages':[{'url':'https://a.example/offers','type':'text'},{'url':'https://b.example/offers','type':'text'}]}]}),encoding='utf-8')
run.fetch=lambda url,**kwargs:Response(page2['one'] if 'a.example' in url else page2['two'],url)
run.main(root2)
state2=json.loads((root2/'data/state.json').read_text(encoding='utf-8'))
assert len(state2['offers'])==1,state2['offers']
page2['one']='<html><body><h1>Welcome</h1><p>No promotions here</p></body></html>'
run.main(root2)
state2=json.loads((root2/'data/state.json').read_text(encoding='utf-8'))
assert len(state2['offers'])==1 and next(iter(state2['offers'].values()))['is_active'] is True,state2['offers']
# Manual corrections survive subsequent source syncs and remain pending until reviewed again.
root3=pathlib.Path(tempfile.mkdtemp())
page3={'html':'<html><body><h1>Nike</h1><p>Nike up to 50% off</p><p>Valid until 31 Dec 2027</p></body></html>'}
(root3/'sources.json').write_text(json.dumps({'malls':[{'id':'mall-edit','name':'Mall Edit','pages':[{'url':'https://edit.example/offers','type':'text'}]}]}),encoding='utf-8')
run.fetch=lambda url,**kwargs:Response(page3['html'],url)
run.main(root3)
state3=json.loads((root3/'data/state.json').read_text(encoding='utf-8'));manual_id=next(iter(state3['offers']))
state3['offers'][manual_id]['title']='Administrator corrected title';state3['offers'][manual_id]['description']='Administrator corrected description';state3['offers'][manual_id]['manual_fields']=['title','description'];state3['offers'][manual_id]['manual_status']='edited';state3['offers'][manual_id]['verification_status']='pending';state3['offers'][manual_id]['is_active']=False
(root3/'data/state.json').write_text(json.dumps(state3,ensure_ascii=False,indent=2),encoding='utf-8')
page3['html']='<html><body><h1>New heading</h1><h2>Nike</h2><p>Nike up to 50% off</p><p>Valid until 31 Dec 2027</p></body></html>'
run.main(root3)
state3=json.loads((root3/'data/state.json').read_text(encoding='utf-8'));record=state3['offers'][manual_id]
assert record['title']=='Administrator corrected title' and record['description']=='Administrator corrected description',record
assert record['verification_status']=='pending' and not record['is_active'],record
assert len(state3['offers'])==1,state3['offers']
run.fetch,run.allowed,run.feed_entries=original_fetch,original_allowed,original_feeds
print('all local extraction, deduplication, verification, expiry and manual-correction tests passed')
