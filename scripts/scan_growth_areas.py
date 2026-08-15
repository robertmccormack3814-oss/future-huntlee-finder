from __future__ import annotations

import json, os, re, smtplib, ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]; DATA_PATH=ROOT/'data.json'; SOURCES_PATH=ROOT/'scanner_sources.json'; ALERT_PATH=ROOT/'latest_alert.json'
UA='FutureHuntleeFinder/1.1 (+GitHub Actions; public planning research)'; TIMEOUT=20; MAX_DISCOVERY_PAGES=120; MIN_HOMES=5000; MIN_INFRA_CATEGORIES=3; ALERT_SCORE=75
URL_HINTS=('growth','precinct','greenfield','release','masterplan','master-plan','structure-plan','structureplan','new-community','urban-development')
REJECT_PATH_PARTS=('/news/','/media/','/the-planning-system/housing/','low-and-mid-rise','transport-oriented-development-program','housing-policy','planning-reforms')
GENERIC_NAME_TERMS=('government','investing','investment','reform','responsibility','housing crisis','planning rules','fast track','program','policy','announcement','deliver a pipeline','new planning','shared responsibility')
CATEGORY_TERMS={'schools':('school','education','primary school','high school'),'retail':('town centre','shopping centre','retail','local centre','neighbourhood centre'),'transport':('rail','metro','station','bus','transport','motorway','highway','road upgrade'),'employment':('jobs','employment','business park','industrial','commercial centre'),'parks':('park','open space','sporting','recreation','community facility'),'utilities':('water','wastewater','sewer','electricity','infrastructure contribution')}
COMMERCIAL_INFRA_TERMS={'Town centre / major centre':('town centre','city centre','major centre','metropolitan centre'),'Shopping centre / retail precinct':('shopping centre','retail centre','retail precinct','retail hub'),'Local / neighbourhood centres':('local centre','neighbourhood centre','neighborhood centre','village centre'),'Supermarket / grocery retail':('supermarket','grocery','food retail'),'Commercial / employment precinct':('commercial precinct','employment precinct','employment land','commercial centre','commercial core'),'Business park / office precinct':('business park','office precinct','office space','business precinct'),'Industrial / logistics precinct':('industrial precinct','industrial land','logistics precinct','warehouse','freight precinct'),'Health / medical facilities':('health centre','medical centre','health precinct','medical precinct','health services'),'Hospital':('hospital',),'Hospitality / accommodation':('hotel','hospitality','accommodation precinct'),'Childcare / early learning':('childcare','child care','early learning centre'),'Community / civic facilities':('community centre','community facility','civic centre','library')}
STATUS_WORDS={'under construction':10,'construction':9,'contract awarded':9,'funded':9,'approved':8,'rezoned':8,'adopted':8,'structure plan':7,'master plan':7,'planning proposal':6,'draft':5,'investigation':4,'future':3}

def fetch(url):
 r=requests.get(url,timeout=TIMEOUT,headers={'User-Agent':UA}); r.raise_for_status(); return r.text

def clean_text(html):
 s=BeautifulSoup(html,'lxml'); [x.decompose() for x in s(['script','style','noscript','svg'])]; title=s.title.get_text(' ',strip=True) if s.title else ''; return title[:160],re.sub(r'\s+',' ',' '.join(s.stripped_strings))
def extract_urls_from_sitemap(xml): return [x.get_text(strip=True) for x in BeautifulSoup(xml,'xml').find_all('loc')]
def sitemap_candidates(url):
 try: urls=extract_urls_from_sitemap(fetch(url))
 except: return []
 nested=[u for u in urls if u.endswith('.xml') or 'sitemap' in u.lower()]; pages=[]
 if nested:
  for sm in nested[:30]:
   try: pages+=extract_urls_from_sitemap(fetch(sm))
   except: pass
 else: pages=urls
 return [u for u in pages if any(h in u.lower() for h in URL_HINTS) and not any(b in u.lower() for b in REJECT_PATH_PARTS)]
def linked_candidates(seed):
 try: s=BeautifulSoup(fetch(seed),'lxml')
 except: return []
 host=urlparse(seed).netloc; out=[]
 for a in s.find_all('a',href=True):
  u=urljoin(seed,a['href']).split('#')[0]; blob=(u+' '+a.get_text(' ',strip=True)).lower()
  if urlparse(u).netloc==host and any(h.replace('-',' ') in blob.replace('-',' ') for h in URL_HINTS) and not any(b in u.lower() for b in REJECT_PATH_PARTS): out.append(u)
 return out
def extract_largest_home_count(text):
 vals=[]
 for p in (r'(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})\s+(?:new\s+)?(?:homes|dwellings|lots|residences)',r'(?:homes|dwellings|lots|residences)\s*(?:of|for|:)?\s*(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})'):
  for m in re.finditer(p,text,re.I):
   try: vals.append(int(m.group(1).replace(',','')))
   except: pass
 return max(vals) if vals else None
def extract_largest_number_before(text,nouns):
 vals=[]
 for m in re.finditer(rf'([\d,]{{3,}})\s+(?:new\s+)?(?:{nouns})',text,re.I):
  try: vals.append(int(m.group(1).replace(',','')))
  except: pass
 return max(vals) if vals else None
def category_hits(text):
 t=text.lower(); return {k:any(x in t for x in v) for k,v in CATEGORY_TERMS.items()}
def extract_commercial_infrastructure(text):
 t=text.lower(); return [k for k,v in COMMERCIAL_INFRA_TERMS.items() if any(x in t for x in v)][:8]
def infer_stage(text):
 t=text.lower(); label,best='Planning pipeline',4
 for k,v in STATUS_WORDS.items():
  if k in t and v>best: label,best=k.title(),v
 return label,best
def score_candidate(homes,hits,stage_score):
 scale=10 if homes>=30000 else 9 if homes>=15000 else 8 if homes>=10000 else 7; infra=min(10,4+sum(hits.values())); scores={'scale':scale,'infrastructure':infra,'schools':9 if hits['schools'] else 4,'retail':8 if hits['retail'] else 4,'transport':9 if hits['transport'] else 4,'employment':9 if hits['employment'] else 4,'earliness':10 if stage_score<=5 else 8 if stage_score<=7 else 6 if stage_score<=8 else 4,'certainty':stage_score}; w={'scale':18,'infrastructure':18,'schools':10,'retail':10,'transport':12,'employment':12,'earliness':10,'certainty':10}; return scores,round(sum(scores[k]/10*w[k] for k in w))
def project_name(title,url):
 path=urlparse(url).path.rstrip('/'); slug=path.split('/')[-1].replace('-',' ').strip(); t=re.sub(r'\s*[-|–].*$','',title).strip()
 # Prefer URL slug for dedicated precinct/growth-area pages because government page titles can contain site branding.
 candidate=slug.title() if slug else t
 for suffix in (' Growth Area',' Precinct',' Master Plan',' Structure Plan'): 
  if suffix.lower() in candidate.lower(): break
 if any(x in candidate.lower() for x in GENERIC_NAME_TERMS) or len(candidate)<3: return None
 if candidate.lower() in ('housing','growth','precinct','greenfield','transport oriented development','low and mid'): return None
 return candidate[:100]
def derive_region(text,state):
 for m in ('Greater Macarthur','Western Sydney','Moreton Bay','South East Queensland','Ballarat','Geelong','Melbourne'):
  if m.lower() in text.lower(): return m
 return state
def summarise(homes,jobs,pop,hits,stage):
 h=[f'At least {homes:,} planned homes/dwellings detected in official planning text',f'Planning stage signal: {stage}'];
 if pop:h.append(f'Population signal: approximately {pop:,} people')
 if jobs:h.append(f'Employment signal: approximately {jobs:,} jobs')
 labels={'schools':'schools/education','retail':'town or shopping centres','transport':'major transport/road investment','employment':'employment land/jobs','parks':'parks/community infrastructure','utilities':'major utilities/infrastructure servicing'}; p=[labels[k] for k,v in hits.items() if v]
 if p:h.append('Infrastructure signals: '+', '.join(p))
 return h[:5]
def normalise(s): return re.sub(r'[^a-z0-9]+','',s.lower())
def send_email(alerts):
 host=os.getenv('SMTP_HOST'); user=os.getenv('SMTP_USERNAME'); password=os.getenv('SMTP_PASSWORD'); to=os.getenv('ALERT_EMAIL')
 if not all([host,user,password,to]) or not alerts:return
 msg=EmailMessage(); msg['Subject']=f'Future Huntlee Finder: {len(alerts)} promising update(s)'; msg['From']=user; msg['To']=to; lines=['Future Huntlee Finder found promising planning updates:\n']
 for c in alerts: lines += [f"{c['name']} ({c['state']}) — {c['score']}/100",f"Homes: {c.get('homes','—')}",f"Stage: {c.get('stage','—')}",c.get('source',''),'']
 msg.set_content('\n'.join(lines)); ctx=ssl.create_default_context()
 with smtplib.SMTP_SSL(host,int(os.getenv('SMTP_PORT','465')),context=ctx) as smtp: smtp.login(user,password); smtp.send_message(msg)
def main():
 db=json.loads(DATA_PATH.read_text()); sources=json.loads(SOURCES_PATH.read_text());
 # Purge automatic cards that are policy/news/program pages rather than a geographically specific project.
 db['candidates']=[c for c in db['candidates'] if not (c.get('discovery')=='automatic' and (any(x in c.get('source','').lower() for x in REJECT_PATH_PARTS) or any(x in c.get('name','').lower() for x in GENERIC_NAME_TERMS) or c.get('name','').lower() in ('low and mid','transport oriented development','transport oriented development program')))]
 existing_urls={c.get('source'):c for c in db['candidates'] if c.get('source')}; discovered=[]
 for sm in sources['sitemaps']:
  for u in sitemap_candidates(sm['url']): discovered.append((sm['state'],u))
 for seed in sources['seed_pages']:
  for u in linked_candidates(seed['url']): discovered.append((seed['state'],u))
 dedup=[]; seen=set()
 for state,u in discovered:
  if u not in seen:seen.add(u);dedup.append((state,u))
 alerts=[]; log=[]
 for state,url in dedup[:MAX_DISCOVERY_PAGES]:
  try:
   title,text=clean_text(fetch(url)); name=project_name(title,url)
   if not name: continue
   homes=extract_largest_home_count(text)
   if not homes or homes<MIN_HOMES:continue
   hits=category_hits(text)
   if sum(hits.values())<MIN_INFRA_CATEGORIES:continue
   stage,ss=infer_stage(text); scores,total=score_candidate(homes,hits,ss); jobs=extract_largest_number_before(text,r'jobs|employees'); pop=extract_largest_number_before(text,r'people|residents|population'); commercial=extract_commercial_infrastructure(text)
   c={'name':name,'state':state,'region':derive_region(text,state),'stage':stage,'status':'HIGH CONVICTION' if total>=85 else 'WATCH CLOSELY' if total>=75 else 'EARLY WATCH','homes':homes,'population':pop,'jobs':jobs,'commercial_infrastructure':commercial,'scores':scores,'highlights':summarise(homes,jobs,pop,hits,stage),'risk':'Automatically discovered from official planning material. Verify project boundaries, delivery timing, local supply, infrastructure funding and purchase price before investment decisions.','source':url,'score':total,'last_checked':datetime.now(timezone.utc).date().isoformat(),'discovery':'automatic'}
   prev=existing_urls.get(url); changed=False
   if prev: changed=abs(total-prev.get('score',0))>=5 or homes!=prev.get('homes') or stage!=prev.get('stage') or commercial!=prev.get('commercial_infrastructure',[]); prev.update(c)
   else: db['candidates'].append(c); existing_urls[url]=c; changed=True
   if changed and total>=ALERT_SCORE:alerts.append(c)
   log.append({'name':name,'url':url,'score':total,'homes':homes})
  except Exception as e:log.append({'url':url,'error':str(e)[:180]})
 db['updated']=datetime.now(timezone.utc).date().isoformat(); db['scanner']={'last_run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'pages_considered':len(dedup[:MAX_DISCOVERY_PAGES]),'qualified_pages':len([x for x in log if 'score' in x]),'alert_count':len(alerts),'mode':'project-specific-official-planning-autodiscovery'}; db['candidates'].sort(key=lambda c:c.get('score',0),reverse=True); DATA_PATH.write_text(json.dumps(db,indent=2,ensure_ascii=False)+'\n'); ALERT_PATH.write_text(json.dumps({'generated':db['scanner']['last_run_utc'],'alerts':alerts},indent=2,ensure_ascii=False)+'\n'); send_email(alerts); print(f"Scanned {len(dedup[:MAX_DISCOVERY_PAGES])} planning URLs; {len(alerts)} alert-worthy change(s).")
if __name__=='__main__':main()
