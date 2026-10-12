"""Deterministic local offer extraction; no hosted AI or model API is used."""
from __future__ import annotations
import datetime as dt
import re
from html import unescape

OFFER_WORDS = re.compile(r"\b(offer|offers|promotion|promotions|sale|discount|deal|deals|save|special price|buy\s*\d|free|clearance|limited time|cashback|voucher|coupon|خصم|عرض|عروض|تخفيضات|تخفيض|مجانا|مجاني|اشتر|كاش باك|قسيمة)\b", re.I)
DISCOUNT = re.compile(r"(?:up to\s*)?\d{1,3}\s*%\s*(?:off|discount|خصم)?|(?:buy\s*\d+\s*(?:get|for)\s*\d+)|buy one get one|\bbogo\b|\b(?:half price|save\s*\d+)\b|\d[\d,.]*\s*(?:QAR|QR|ر\.ق)\s*(?:off|خصم)?", re.I)
PRICE = re.compile(r"(?:QAR|QR|ر\.ق|ر.ق)\s*\d[\d,.]*|\d[\d,.]*\s*(?:QAR|QR|ر\.ق)", re.I)
CODE = re.compile(r"(?:promo(?:tion)?\s*code|coupon\s*code|use\s*code|code|رمز الخصم|كود الخصم)\s*[:：-]?\s*([A-Z0-9_-]{3,20})", re.I)
DATE_PATTERNS = [
 re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\s*(?:-|to|until|through|–|—|تاريخ الانتهاء)\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b", re.I),
 re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s*(?:-|to|until|through|–|—)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", re.I),
]
MONTHS = {m.lower(): i for i,m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"],1)}
for name,num in list(MONTHS.items()): MONTHS[name[:3]]=num

def parse_date(s):
 s=(s or '').strip()
 for fmt in ("%d %B %Y","%d %b %Y","%d/%m/%Y","%d-%m-%Y","%m/%d/%Y","%Y-%m-%d"):
  try: return dt.datetime.strptime(s,fmt).date().isoformat()
  except ValueError: pass
 return None

def dates(text):
 for pat in DATE_PATTERNS:
  m=pat.search(text)
  if m: return parse_date(m.group(1)),parse_date(m.group(2))
 # Single explicit end date: only recognize when clearly labelled.
 m=re.search(r"(?:valid\s+(?:until|through)|expires?\s*(?:on|:)?|ends?\s*(?:on|:)?|until|حتى|ينتهي في|صالح حتى)\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2})",text,re.I)
 return (None,parse_date(m.group(1))) if m else (None,None)

def category(text):
 t=text.lower()
 groups={"fashion":("fashion","clothing","dress","shoes","sneaker","zara","nike","adidas","apparel","ملابس","أزياء"),"food":("restaurant","cafe","coffee","food","dining","burger","pizza","menu","مطعم","طعام","قهوة"),"electronics":("phone","laptop","electronics","computer","samsung","apple","sony","gadget","إلكترونيات","هاتف"),"beauty":("beauty","perfume","cosmetic","skincare","makeup","salon","عطور","جمال","تجميل"),"home":("furniture","home","mattress","decor","kitchen","أثاث","منزل"),"grocery":("grocery","supermarket","fresh","market","produce","بقالة","سوبرماركت")}
 for cat,words in groups.items():
  if any(x in t for x in words): return cat
 return "other"

KNOWN_STORES=["Lulu Hypermarket","Bath & Body Works","Virgin Megastore","Marks & Spencer","Foot Locker","Toys R Us","Home Centre","LC Waikiki","American Eagle","Massimo Dutti","Lulu","Carrefour","Monoprix","IKEA","Centrepoint","Max Fashion","H&M","Zara","Mango","Nike","Adidas","Sephora","Faces","Jarir","Samsung","Apple","iSpot","Fnac","Daiso","Miniso","Al Meera","Decathlon","Swarovski","Pandora","KFC","McDonald's","Starbucks","Shake Shack","Tim Hortons","Pizza Hut","Chili's","Nando's","R&B"]
def store_name(text,fallback):
 low=text.lower()
 for name in sorted(KNOWN_STORES,key=len,reverse=True):
  if name.lower() in low: return name
 for line in text.splitlines():
  line=re.sub(r'\s+',' ',line).strip(' -:|•')
  if 2<=len(line)<=48 and not OFFER_WORDS.search(line) and re.search(r'[A-Za-z\u0600-\u06ff]',line) and line.lower() not in {'promotions','offers','shop now','learn more','read more','terms and conditions'}: return line
 return fallback

def candidate_chunks(text):
 text=unescape(text or '').replace('\xa0',' ')
 lines=[re.sub(r'\s+',' ',x).strip(' \t-•|') for x in text.splitlines()]
 lines=[x for x in lines if x]
 chunks=[]
 for i,line in enumerate(lines):
  if OFFER_WORDS.search(line) or DISCOUNT.search(line):
   chunk=' '.join(lines[max(0,i-2):min(len(lines),i+3)])
   if chunk not in chunks: chunks.append(chunk)
 if not chunks and (OFFER_WORDS.search(text) or DISCOUNT.search(text)): chunks=[re.sub(r'\s+',' ',text)[:1200]]
 return chunks

def text_offers(text,mall,url,today):
 found=[];seen=set()
 for chunk in candidate_chunks(text):
  disc=DISCOUNT.search(chunk)
  if not disc and not re.search(r'\b(sale|promotion|special offer|buy|free|clearance|خصم|عرض|عروض)\b',chunk,re.I): continue
  start,end=dates(chunk)
  if end and end<today: continue
  title=re.sub(r'\s+',' ',chunk)[:180].strip(' -:|')
  if len(title)<4: continue
  store=store_name(chunk,'Mall promotion')
  if store.lower()==title.lower(): store='Mall promotion'
  code=CODE.search(chunk)
  price_matches=PRICE.findall(chunk)
  terms_match=re.search(r'(?:terms\s*(?:and|&)\s*conditions|conditions apply|T&C|الشروط والأحكام)\s*[:：-]?\s*(.{0,300})',chunk,re.I)
  redeem_match=re.search(r'(?:how to redeem|redeem by|show this (?:offer|code)|present this (?:offer|code)|redemption instructions|طريقة الاستفادة|كيفية الاستفادة|استرداد العرض)\s*[:：-]?\s*(.{0,250})',chunk,re.I)
  found_img=None
  key=(store.lower(),title.lower(),end)
  if key in seen: continue
  seen.add(key)
  found.append({"store_name":store,"title":title,"title_ar":None,"description":chunk[:500],"discount_text":disc.group(0).strip() if disc else None,"category":category(chunk),"start_date":start,"end_date":end,"language":"ar" if re.search(r'[\u0600-\u06ff]',chunk) else "en","source_url":url,"original_price":None,"sale_price":price_matches[-1] if price_matches else None,"promo_code":code.group(1) if code else None,"terms":terms_match.group(1).strip() if terms_match else None,"redemption_instructions":redeem_match.group(1).strip() if redeem_match else None,"image_url":found_img,"confidence":0.65 if disc else 0.45,"evidence":chunk[:500]})
 return found

def image_offers(data,mime,ctx,mall,branch,today,source_url=''):
 try:
  from PIL import Image,ImageOps,ImageEnhance
  import pytesseract,io
  im=Image.open(io.BytesIO(data)).convert('RGB')
  im=ImageOps.autocontrast(im)
  if im.width<1000: im=im.resize((im.width*2,im.height*2))
  extracted=pytesseract.image_to_string(im,lang='eng+ara')
 except Exception as exc:
  print('local OCR unavailable for banner:',str(exc)[:160]);return None
 offers=text_offers((ctx or '')+'\n'+extracted,mall,source_url,today)
 for o in offers:
  o['description']=((o.get('description') or '')+(f' Branch: {branch}.' if branch else '')).strip()
 return offers
