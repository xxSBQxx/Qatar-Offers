"""Playwright browser checks using in-memory test fixtures; no real offers are published.
Run: python -m pytest -q browser_tests.py
"""
import json, os, shutil
from pathlib import Path
from datetime import datetime, timezone
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'web'
NOW=datetime.now(timezone.utc).isoformat(timespec='seconds')
TEST_OFFER={
    'id':'playwright-test-offer','store_name':'Test Merchant','title':'Playwright Test Offer 25% off',
    'description':'Test-only fixture; never included in production offer data.','category':'fashion',
    'discount_text':'25% off','source_url':'https://merchant.example/offers/test','image_url':None,
    'mall_id':'mall-of-qatar','mall_name':'Mall of Qatar','start_date':None,'end_date':'2026-12-31',
    'verification_status':'verified','verified_at':NOW,'last_checked_at':NOW,'is_active':True,'views':0,
}
MALLS=json.loads((WEB/'malls.json').read_text(encoding='utf-8'))
CATEGORIES=[{'slug':'fashion','name':'Fashion','name_ar':'الأزياء','offer_count':1},{'slug':'food','name':'Food & Dining','name_ar':'الطعام والمطاعم','offer_count':0}]

def install_api_mocks(page,admin=False):
    state={'logged_in':False,'reports':[]}
    def handle(route):
        req=route.request;url=req.url;path=url.split('://',1)[-1].split('/',1)[-1];path='/'+path.split('?',1)[0]
        data={};status=200
        if path=='/api/health':data={'ok':True,'timezone':'Asia/Qatar'}
        elif path=='/api/offers':data={'offers':[TEST_OFFER],'page':1,'limit':100,'total':1,'pages':1}
        elif path==f"/api/offers/{TEST_OFFER['id']}":data={'offer':TEST_OFFER,'related':[]}
        elif path=='/api/malls':data=MALLS
        elif path=='/api/categories':data=CATEGORIES
        elif path=='/api/merchants':data=[]
        elif path=='/api/auth/me':data={'user':({'id':1,'username':'test-admin','role':'admin'} if state['logged_in'] else None)}
        elif path=='/api/me/favorites':data=[]
        elif path=='/api/admin/login':state['logged_in']=True;data={'user':{'id':1,'username':'test-admin','role':'admin'}}
        elif path=='/api/admin/overview':data={'offers':{'active':1,'pending':0,'expired':0,'verified':1,'rejected':0},'merchants':1,'malls':len(MALLS),'sources':len(MALLS),'users':0,'last_job':None}
        elif path=='/api/admin/offers':data={'offers':[],'total':0,'page':1,'pages':1}
        elif path=='/api/admin/merchants':data=[]
        elif path=='/api/admin/sources':data=[]
        elif path=='/api/admin/discoveries':data=[]
        elif path=='/api/admin/reports':data=state['reports']
        elif path=='/api/admin/malls':data=MALLS
        elif path=='/api/admin/categories':data=CATEGORIES
        elif path=='/api/admin/users':data=[]
        elif path=='/api/admin/jobs':data=[]
        elif path=='/api/admin/audit':data=[]
        elif path=='/api/admin/settings':data={'discovery_interval_hours':'6'}
        elif path=='/api/reports' and req.method=='POST':data={'ok':True,'report_id':42,'message':'Report received'}
        elif path.startswith('/api/admin/'):data={'ok':True}
        elif path.startswith('/api/me/favorites/'):data={'ok':True}
        else:status=404;data={'detail':'Not found in Playwright test mock'}
        route.fulfill(status=status,content_type='application/json',headers={'access-control-allow-origin':'*','access-control-allow-credentials':'true'},body=json.dumps(data))
    page.route('**/api/**',handle)
    return state

def launch_chromium(playwright):
    executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH') or shutil.which('chromium') or shutil.which('chromium-browser')
    options={'headless':True,'args':['--no-sandbox','--no-proxy-server']}
    if executable:options['executable_path']=executable
    return playwright.chromium.launch(**options)

def render_file(page,path):
    html=(WEB/path).read_text(encoding='utf-8')
    html=html.replace('<head>','<head><base href="http://qatar-offers.test/">',1)
    shim='''<script>try{window.localStorage.getItem('__probe__')}catch(e){const __store={};Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:k=>Object.prototype.hasOwnProperty.call(__store,k)?__store[k]:null,setItem:(k,v)=>{__store[k]=String(v)},removeItem:k=>{delete __store[k]},clear:()=>{for(const k of Object.keys(__store))delete __store[k]}}})}</script>'''
    html=html.replace('<body>','<body>'+shim,1)
    page.set_content(html,wait_until='domcontentloaded')

def test_home_search_mall_directory_cookie_offer_and_legal_routes():
    with sync_playwright() as p:
        browser=launch_chromium(p)
        context=browser.new_context(viewport={'width':1280,'height':900},timezone_id='Asia/Qatar')
        page=context.new_page();install_api_mocks(page)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        render_file(page,'index.html')
        assert page.title().startswith('Qatar Offers')
        assert page.get_by_role('heading',name='Browse Qatar malls').is_visible()
        assert page.locator('#mallsGrid .mallcard').count()>=8
        assert page.locator('#offerGrid .offer').count()==1
        assert page.locator('#cookieBanner').is_visible()
        page.get_by_role('button',name='Use essentials only').click()
        assert page.locator('#cookieBanner').is_hidden()
        page.get_by_label('Search offers and merchants').fill('nonexistent-offer-query')
        assert '0 offers' in page.locator('#resultCount').inner_text()
        page.get_by_label('Search offers and merchants').fill('Playwright Test Offer')
        assert page.locator('#offerGrid .offer').count()==1
        page.locator('#offerGrid [data-open="playwright-test-offer"]').click()
        assert page.get_by_role('dialog',name='Playwright Test Offer 25% off').is_visible()
        assert page.get_by_role('link',name='Visit merchant source').get_attribute('href')=='https://merchant.example/offers/test'
        page.locator('#dialogClose').click()
        page.get_by_role('button',name='Cookie settings').click()
        assert page.locator('#cookieBanner').is_visible()
        for filename,heading in [('privacy.html','Privacy Policy'),('terms.html','Terms of Use'),('cookies.html','Cookies & Local Storage')]:
            render_file(page,filename);assert page.get_by_role('heading',name=heading).is_visible()
        browse_page=context.new_page();install_api_mocks(browse_page);browse_page.on('pageerror',lambda e:errors.append(str(e)))
        render_file(browse_page,'browse.html')
        browse_page.get_by_role('heading',name='Mall not found').wait_for(state='visible')
        assert 'No current verified offers' in browse_page.locator('#offersGrid').inner_text()
        report_page=context.new_page();install_api_mocks(report_page);report_page.on('pageerror',lambda e:errors.append(str(e)))
        render_file(report_page,'report.html')
        report_page.locator('#reportForm').wait_for(state='visible')
        report_page.locator('#reportMessage').fill('This report is long enough to test the required minimum text length.')
        report_page.locator('#reportConsent').check()
        report_page.get_by_role('button',name='Submit report').click()
        report_page.locator('#reportStatus').wait_for(state='visible')
        assert 'Report received' in report_page.locator('#reportStatus').inner_text()
        assert not errors,errors
        context.close();browser.close()

def test_mobile_layout_and_admin_login_discovery_reports():
    with sync_playwright() as p:
        browser=launch_chromium(p)
        context=browser.new_context(viewport={'width':390,'height':844},timezone_id='Asia/Qatar')
        page=context.new_page();install_api_mocks(page)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        render_file(page,'index.html')
        assert page.locator('body').evaluate('(el)=>el.scrollWidth <= window.innerWidth')
        page= context.new_page();install_api_mocks(page)
        page.on('pageerror',lambda e:errors.append(str(e)))
        render_file(page,'admin.html')
        assert page.get_by_role('heading',name='Admin sign in').is_visible()
        page.locator('#username').fill('test-admin');page.locator('#password').fill('Not-used-by-mock123!')
        page.get_by_role('button',name='Sign in securely').click()
        page.locator('.side').evaluate('(el)=>{el.scrollLeft=el.scrollWidth}')
        page.locator('[data-view=discoveries]').click()
        page.get_by_role('heading',name='Source discovery review').wait_for(state='visible')
        assert page.get_by_role('heading',name='Source discovery review').is_visible()
        page.locator('.side').evaluate('(el)=>{el.scrollLeft=el.scrollWidth}')
        page.locator('[data-view=reports]').click()
        page.get_by_role('heading',name='Report and rights-request inbox').wait_for(state='visible')
        assert page.get_by_role('heading',name='Report and rights-request inbox').is_visible()
        assert not errors,errors
        context.close();browser.close()
