import streamlit as st
import pandas as pd
import requests, re, time, io, json, hashlib
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, urljoin, parse_qs, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from rapidfuzz import fuzz
import phonenumbers

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

UA = {"User-Agent":"Mozilla/5.0 (compatible; ProspectDiscovery/23; +https://streamlit.io)"}
TIMEOUT=12

BAD_DOMAINS = [
    'facebook.com','instagram.com','linkedin.com','twitter.com','x.com','youtube.com','wikipedia.org',
    'google.','bing.com','duckduckgo.com','yahoo.com','mapcarta.com','zaubee.com','cybo.com',
    'snupit.co.za','schoolguide.co.za','schoolsdigest.co.za','brabys.com','yellosa.co.za',
    'africabizinfo.com','saschools.co.za','findglocal.com','cylex.net.za','nearplace.com',
    'tripadvisor.','hellopeter.com','property24.com','privateproperty.co.za'
]
GOOD_TLDS = ['.school.za','.ac.za','.edu','.org.za','.co.za','.com','.org','.net']
BAD_ENTITY_WORDS = ['testing yard','licence','license','driving test','drivers test','traffic department','parking','residence','house residence','apartment','estate agent','hostel']

SECTOR_PROFILES = {
    "Schools": {
        "queries": ["school", "private school", "international school", "college", "academy"],
        "include_words": ['school','college','academy','primary','high','prep','preparatory','montessori','waldorf','lycee','lycée','seminary','grammar'],
        "exclude_words": BAD_ENTITY_WORDS,
        "contact_paths": ['contact','contact-us','contacts','admissions','staff','about','office','reception']
    },
    "Universities / Higher Ed": {
        "queries": ["university", "college", "campus", "higher education"],
        "include_words": ['university','college','campus','institute','academy'],
        "exclude_words": BAD_ENTITY_WORDS,
        "contact_paths": ['contact','admissions','staff','faculty','about']
    },
    "General organizations": {
        "queries": ["organization"],
        "include_words": [],
        "exclude_words": [],
        "contact_paths": ['contact','about','team','staff']
    }
}

def init_state():
    for k,v in {
        'debug':[], 'timing':{}, 'cached_candidates':None, 'candidate_key':None, 'prospects':None,
        'last_settings':None
    }.items():
        if k not in st.session_state: st.session_state[k]=v

def log(msg):
    st.session_state.debug.append(str(msg))

def slug(s):
    return re.sub(r'[^a-z0-9]+','_',str(s).lower()).strip('_')[:80] or 'search'

def key_for(obj):
    return hashlib.md5(json.dumps(obj,sort_keys=True,default=str).encode()).hexdigest()

def get(url, timeout=TIMEOUT):
    try:
        r=requests.get(url,headers=UA,timeout=timeout,allow_redirects=True)
        return r
    except Exception as e:
        return None

def clean_url(u):
    if not u: return ''
    u=str(u).strip()
    if u.startswith('//'): u='https:'+u
    if u and not u.startswith(('http://','https://')): u='https://'+u
    return u.rstrip('/')

def domain(u):
    try:
        d=urlparse(clean_url(u)).netloc.lower()
        if d.startswith('www.'): d=d[4:]
        return d
    except Exception:
        return ''

def is_bad_domain(u):
    d=domain(u)
    return any(b in d for b in BAD_DOMAINS)

def normalize_name(s):
    s=str(s or '').lower()
    s=re.sub(r'\([^)]*\)',' ',s)
    s=re.sub(r'[^a-z0-9\s]',' ',s)
    stop={'the','of','and','school','primary','high','college','academy','campus','independent','international','cape','town','western','south','africa'}
    toks=[t for t in s.split() if t not in stop and len(t)>1]
    return ' '.join(toks)

def likely_official_score(name, url, title='', snippet='', location=''):
    if not url or is_bad_domain(url): return -100
    d=domain(url); text=f"{title} {snippet} {d}".lower()
    n=str(name).lower()
    n_norm=normalize_name(name)
    text_norm=normalize_name(text)
    score=0
    if n_norm and n_norm in text_norm: score+=35
    score+= int(fuzz.partial_ratio(n_norm, text_norm) * 0.35) if n_norm else 0
    # domain similarity against meaningful tokens
    d_clean=re.sub(r'\.(co|org|ac|school|edu|za|com|net).*','',d)
    d_clean=re.sub(r'[^a-z0-9]',' ',d_clean)
    score+= int(fuzz.partial_ratio(n_norm, d_clean)*0.35) if n_norm else 0
    if any(tld in d for tld in GOOD_TLDS): score+=8
    if any(w in text for w in ['official','admissions','principal','school','college','academy','learners','students']): score+=8
    if location and any(tok.lower() in text for tok in str(location).split(',')[:2]): score+=5
    if any(w in d for w in ['directory','guide','schools','listing']) and not any(tok in d for tok in n_norm.split()[:1]): score-=15
    if urlparse(clean_url(url)).path not in ['', '/']: score-=2
    return score

def ddg_results(query, max_results=8):
    urls=[]
    # html endpoint
    for base in ["https://duckduckgo.com/html/?q=", "https://html.duckduckgo.com/html/?q="]:
        r=get(base+quote_plus(query), timeout=14)
        if not r or r.status_code!=200: continue
        soup=BeautifulSoup(r.text,'html.parser')
        for a in soup.select('a.result__a, a[href]'):
            href=a.get('href') or ''
            if 'uddg=' in href:
                try: href=unquote(parse_qs(urlparse(href).query).get('uddg',[''])[0])
                except Exception: pass
            if href.startswith('/l/?') and 'uddg=' in href:
                href=unquote(parse_qs(urlparse(href).query).get('uddg',[''])[0])
            if href.startswith('http'):
                title=a.get_text(' ',strip=True)
                urls.append((href,title,''))
            if len(urls)>=max_results: break
        if urls: break
    # de-dupe
    seen=set(); out=[]
    for u,t,s in urls:
        d=domain(u)
        if d and d not in seen:
            seen.add(d); out.append((clean_url(u),t,s))
    return out[:max_results]

def guess_domains(name):
    n=str(name).lower()
    n=re.sub(r'\([^)]*\)',' ',n)
    replacements={'saint':'st','st.':'st','lycée':'lycee','hoërskool':'hoerskool','laerskool':'laerskool'}
    for a,b in replacements.items(): n=n.replace(a,b)
    toks=[t for t in re.sub(r'[^a-z0-9\s]',' ',n).split() if t not in ['school','primary','high','college','academy','the','of','and','cape','town','campus']]
    base=''.join(toks[:4])
    bases=[]
    if base: bases.append(base)
    if len(toks)>=2: bases.append(''.join(toks[:2]))
    if len(toks)>=1: bases.append(toks[0])
    candidates=[]
    for b in dict.fromkeys(bases):
        for tld in ['.co.za','.org.za','.school.za','.ac.za','.com','.org']:
            candidates.append('https://www'+'.'+b+tld)
            candidates.append('https://'+b+tld)
    return candidates[:20]

def validate_url_for_name(name, url, location=''):
    url=clean_url(url)
    if not url or is_bad_domain(url): return None
    r=get(url, timeout=8)
    if not r or r.status_code>=400: return None
    html=r.text[:250000]
    soup=BeautifulSoup(html,'html.parser')
    title=(soup.title.get_text(' ',strip=True) if soup.title else '')
    text=soup.get_text(' ',strip=True)[:3000]
    score=likely_official_score(name, url, title, text, location)
    return {'url':clean_url(r.url or url),'score':score,'title':title}

def resolve_website(name, location='', extra_thorough=False):
    """Return best official URL and candidates."""
    candidates=[]
    # 1 existing direct domain guesses (fast, catches abbotts style less often but cheap)
    for u in guess_domains(name):
        val=validate_url_for_name(name,u,location)
        if val: candidates.append(val)
        if len(candidates)>=3 and not extra_thorough: break
    # 2 search queries
    queries=[f'"{name}" official website', f'"{name}" school website', f'"{name}" "{location}"']
    if extra_thorough:
        queries += [f'"{name}" contact', f'"{name}" admissions', f'"{name}" principal']
    for q in queries:
        for u,t,s in ddg_results(q, max_results=8 if extra_thorough else 5):
            if is_bad_domain(u): continue
            score=likely_official_score(name,u,t,s,location)
            candidates.append({'url':u,'score':score,'title':t})
        # if good enough stop early
        if candidates and max(c['score'] for c in candidates)>=65 and not extra_thorough: break
    # de-dupe by domain, keep highest score
    by={}
    for c in candidates:
        d=domain(c['url'])
        if not d: continue
        if d not in by or c['score']>by[d]['score']: by[d]=c
    ranked=sorted(by.values(), key=lambda x:x['score'], reverse=True)
    best=''; method='not_found'
    if ranked:
        if ranked[0]['score']>=45:
            best=ranked[0]['url']; method='resolved_search'
        elif ranked[0]['score']>=30:
            best=ranked[0]['url']; method='possible_low_confidence'
    cand=' | '.join([f"{c['url']} ({c['score']})" for c in ranked[:5]])
    return best, method, cand

def geocode(location):
    url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(location)}&format=jsonv2&limit=1&addressdetails=1"
    r=get(url, timeout=15); log(f"Geocode HTTP {r.status_code if r else 'ERR'}: {url}")
    if r and r.status_code==200 and r.json():
        j=r.json()[0]
        return float(j['lat']), float(j['lon']), j.get('display_name',location)
    return None,None,location

def overpass_candidates(lat,lon,radius,limit,profile):
    q=f"""
    [out:json][timeout:25];
    (
      node(around:{int(radius*1000)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
      way(around:{int(radius*1000)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
      relation(around:{int(radius*1000)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
    );
    out center tags {limit};
    """
    endpoints=['https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter','https://overpass.osm.ch/api/interpreter']
    rows=[]
    for ep in endpoints:
        try:
            r=requests.post(ep,data={'data':q},headers=UA,timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code!=200: continue
            data=r.json(); elems=data.get('elements',[])
            log(f"Overpass {ep}: {len(elems)} elements")
            for e in elems:
                tags=e.get('tags',{})
                name=tags.get('name') or tags.get('official_name')
                if not name: continue
                rows.append({
                    'prospect_name':name,'sector':profile,'source':'overpass','address':tags.get('addr:full') or ', '.join([tags.get(k,'') for k in ['addr:housenumber','addr:street','addr:suburb'] if tags.get(k)]),
                    'city':tags.get('addr:city',''),'country':tags.get('addr:country',''),
                    'latitude':e.get('lat') or e.get('center',{}).get('lat'),'longitude':e.get('lon') or e.get('center',{}).get('lon'),
                    'website':clean_url(tags.get('website') or tags.get('contact:website') or ''),
                    'osm_phone':tags.get('phone') or tags.get('contact:phone') or '', 'osm_email':tags.get('email') or tags.get('contact:email') or ''
                })
            if rows: break
        except Exception as e: log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return rows[:limit]

def nominatim_candidates(location, limit, profile, queries):
    rows=[]
    for q in queries:
        url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(q+' in '+location)}&format=jsonv2&limit={limit}&addressdetails=1&extratags=1"
        r=get(url, timeout=20); log(f"Nominatim '{q} in {location}': HTTP {r.status_code if r else 'ERR'}")
        if not r or r.status_code!=200: continue
        for j in r.json() or []:
            name=j.get('name') or j.get('display_name','').split(',')[0]
            extra=j.get('extratags') or {}
            addr=j.get('address') or {}
            rows.append({
                'prospect_name':name,'sector':profile,'source':'nominatim','address':j.get('display_name',''),
                'city':addr.get('city') or addr.get('town') or addr.get('suburb') or '', 'country':addr.get('country',''),
                'latitude':j.get('lat'), 'longitude':j.get('lon'),
                'website':clean_url(extra.get('website') or extra.get('contact:website') or ''),
                'osm_phone':extra.get('phone') or extra.get('contact:phone') or '', 'osm_email':extra.get('email') or extra.get('contact:email') or ''
            })
        if len(rows)>=limit: break
    return rows[:limit]

def filter_rows(rows, profile):
    prof=SECTOR_PROFILES.get(profile, SECTOR_PROFILES['Schools'])
    inc=prof['include_words']; exc=prof['exclude_words']
    out=[]; seen=set()
    for r in rows:
        name=str(r.get('prospect_name','')).strip()
        if not name: continue
        low=name.lower()
        if any(x in low for x in exc): continue
        # don't overfilter; for schools require some education-ish term if from noisy source
        if inc and not any(w in low for w in inc) and r.get('source')=='nominatim':
            # allow if class/source has school query; but no if clearly residence
            pass
        k=re.sub(r'[^a-z0-9]+','',low)[:80]
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def extract_emails(text):
    if not text: return []
    text=text.replace('[at]','@').replace('(at)','@').replace(' at ','@').replace('[dot]','.').replace('(dot)','.')
    emails=re.findall(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',text)
    bad=['example.com','sentry.io','wixpress.com','wordpress.com']
    return sorted({e.strip('.,;:').lower() for e in emails if not any(b in e.lower() for b in bad)})

def extract_phones(text, country='ZA'):
    nums=[]
    if not text: return []
    for m in phonenumbers.PhoneNumberMatcher(text, country or 'ZA'):
        try:
            if phonenumbers.is_valid_number(m.number):
                nums.append(phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except Exception: pass
    return sorted(set(nums))

def likely_links(base, html, paths):
    soup=BeautifulSoup(html,'html.parser')
    out=[]
    for a in soup.find_all('a',href=True):
        txt=(a.get_text(' ',strip=True)+' '+a['href']).lower()
        if any(p.replace('-',' ') in txt or p in txt for p in paths):
            u=urljoin(base,a['href'])
            if domain(u)==domain(base): out.append(clean_url(u))
    # common paths
    for p in paths[:8]: out.append(urljoin(base,'/'+p))
    res=[]; seen=set()
    for u in out:
        if u not in seen:
            seen.add(u); res.append(u)
    return res[:8]

def scrape_site(row, profile, find_more=False):
    website=clean_url(row.get('website',''))
    country=(row.get('country') or 'South Africa')
    region='ZA' if 'south africa' in country.lower() or country.upper()=='ZA' else None
    result={'enrichment_status':'no_website','visible_emails':'','generic_emails':'','search_emails':'','best_email':'','email_source':'','website_phone':'','search_phone':'','best_phone':'','phone_source':'','source_pages':'','search_source_pages':''}
    if not website: return result
    pages=[]; texts=[]
    r=get(website, timeout=10)
    if not r or r.status_code>=400:
        result['enrichment_status']='scrape_failed'; return result
    pages.append(clean_url(r.url)); texts.append(r.text)
    paths=SECTOR_PROFILES.get(profile, SECTOR_PROFILES['Schools'])['contact_paths']
    max_pages=6 if find_more else 3
    for u in likely_links(r.url, r.text, paths)[:max_pages-1]:
        rr=get(u, timeout=8)
        if rr and rr.status_code<400 and 'text/html' in rr.headers.get('content-type','text/html'):
            pages.append(clean_url(rr.url)); texts.append(rr.text)
    alltext='\n'.join([BeautifulSoup(t,'html.parser').get_text(' ',strip=True) for t in texts])
    emails=extract_emails(alltext)
    generic=[e for e in emails if any(e.startswith(p) for p in ['info@','admin@','office@','admissions@','contact@','reception@','principal@','hello@'])]
    phones=extract_phones(alltext, region or 'ZA')
    result.update({
        'enrichment_status':'scraped',
        'visible_emails':'; '.join(emails),
        'generic_emails':'; '.join(generic),
        'best_email': (generic[0] if generic else (emails[0] if emails else '')),
        'email_source': ('website_generic' if generic else ('website_visible' if emails else '')),
        'website_phone': '; '.join(phones[:3]),
        'best_phone': phones[0] if phones else (row.get('osm_phone','') or ''),
        'phone_source': 'website' if phones else ('osm' if row.get('osm_phone') else ''),
        'source_pages': '; '.join(pages[:6])
    })
    return result

def search_contact_fallback(row, location='', find_more=False):
    name=row.get('prospect_name','')
    if not find_more: return {'search_emails':'','search_phone':'','search_source_pages':''}
    queries=[f'"{name}" phone', f'"{name}" contact', f'"{name}" admissions']
    texts=[]; pages=[]
    for q in queries:
        for u,t,s in ddg_results(q+' '+location, max_results=4):
            if is_bad_domain(u):
                # snippets may still contain useful info but ours doesn't capture snippets
                continue
            rr=get(u,timeout=8)
            if rr and rr.status_code<400 and 'text/html' in rr.headers.get('content-type','text/html'):
                pages.append(clean_url(rr.url)); texts.append(rr.text[:200000])
        if len(pages)>=4: break
    text='\n'.join([BeautifulSoup(t,'html.parser').get_text(' ',strip=True) for t in texts])
    emails=extract_emails(text)
    region='ZA' if 'south africa' in location.lower() else 'ZA'
    phones=extract_phones(text, region)
    return {'search_emails':'; '.join(emails[:5]), 'search_phone':'; '.join(phones[:3]), 'search_source_pages':'; '.join(pages[:5])}

def enrich_rows(rows, location, profile, extra_thorough=False, find_more_contacts=False, workers=5):
    # resolve websites first
    t0=time.time(); out=[]
    progress=st.progress(0, text='Finding official websites...')
    def resolve_worker(idx,row):
        r=dict(row)
        if r.get('website'):
            r['website_source']=r.get('website_source') or 'map/open data'
            r['website_candidates']=''
        else:
            w,method,cands=resolve_website(r.get('prospect_name',''), location, extra_thorough)
            r['website']=w; r['website_source']=method; r['website_candidates']=cands
        return idx,r
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(resolve_worker,i,r) for i,r in enumerate(rows)]
        tmp=[None]*len(rows)
        for n,f in enumerate(as_completed(futs),1):
            i,r=f.result(); tmp[i]=r
            progress.progress(n/len(rows), text=f'Finding official websites... {n}/{len(rows)}')
    st.session_state.timing['website_resolution_seconds']=round(time.time()-t0,2)
    rows=tmp
    # scrape
    t1=time.time(); progress.progress(0, text='Scraping websites for contacts...')
    def scrape_worker(idx,row):
        res=scrape_site(row, profile, find_more_contacts or extra_thorough)
        # if missing contacts and enabled, search fallback
        sf=search_contact_fallback(row, location, find_more_contacts)
        res.update({k:v for k,v in sf.items() if v})
        # merge best fields
        if not res.get('best_email') and res.get('search_emails'):
            res['best_email']=res['search_emails'].split(';')[0].strip(); res['email_source']='search'
        if not res.get('best_phone') and res.get('search_phone'):
            res['best_phone']=res['search_phone'].split(';')[0].strip(); res['phone_source']='search'
        merged=dict(row); merged.update(res)
        return idx,merged
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(scrape_worker,i,r) for i,r in enumerate(rows)]
        tmp=[None]*len(rows)
        for n,f in enumerate(as_completed(futs),1):
            i,r=f.result(); tmp[i]=r
            progress.progress(n/len(rows), text=f'Scraping websites for contacts... {n}/{len(rows)}')
    progress.empty()
    st.session_state.timing['enrichment_seconds']=round(time.time()-t1,2)
    return tmp

def to_excel(df):
    bio=io.BytesIO()
    with pd.ExcelWriter(bio, engine='openpyxl') as writer:
        df.to_excel(writer,index=False,sheet_name='Prospects')
    return bio.getvalue()

def display_cols(df):
    cols=['prospect_name','website','website_source','website_candidates','best_email','best_phone','email_source','phone_source','enrichment_status','address','source']
    return [c for c in cols if c in df.columns]

init_state()
st.title('Prospect Discovery Engine')
st.caption('Find prospects, resolve official websites, and scrape contact details.')

with st.sidebar:
    st.header('Search setup')
    profile=st.selectbox('Sector', list(SECTOR_PROFILES.keys()), index=0)
    mode=st.radio('How do you want to search?', ['Map / location','School or prospect names','Website URLs'], index=0)
    extra=st.checkbox('Extra thorough website search', value=False, help='Slower. Uses more search queries and accepts plausible official sites with lower confidence.')
    find_more=st.checkbox('Find more contact details when missing', value=False, help='Slower. Searches the web for contact pages if website scraping is incomplete.')
    workers_label=st.select_slider('Processing speed', options=['Slow','Balanced','Fast'], value='Balanced')
    workers={'Slow':2,'Balanced':5,'Fast':8}[workers_label]
    st.caption('Balanced is recommended for Streamlit Cloud.')

if mode=='Map / location':
    c1,c2,c3=st.columns([2,1,1])
    with c1: location=st.text_input('Location', 'Cape Town, Western Cape, South Africa')
    with c2: radius=st.number_input('Radius (km)', 1, 250, 10)
    with c3: limit=st.number_input('Max prospects', 10, 500, 50)
    run=st.button('Find prospects', type='primary')
    cand_key=key_for({'profile':profile,'mode':mode,'location':location,'radius':radius,'limit':limit})
    if run:
        st.session_state.debug=[]; st.session_state.timing={}
        start=time.time()
        if st.session_state.candidate_key==cand_key and st.session_state.cached_candidates is not None:
            st.info(f'Using saved prospect list: {len(st.session_state.cached_candidates)} prospects. Updating websites/contact details only.')
            candidates=st.session_state.cached_candidates
        else:
            st.info('Starting new prospect search...')
            p=st.progress(0, text='Geocoding location...')
            lat,lon,display=geocode(location); p.progress(0.25, text='Searching map/open data...')
            rows=[]
            if lat and lon:
                rows += overpass_candidates(lat,lon,radius,limit,profile)
            if len(rows)<limit:
                rows += nominatim_candidates(location, limit, profile, SECTOR_PROFILES[profile]['queries'])
            rows=filter_rows(rows, profile)[:limit]
            p.progress(1.0, text=f'Prospect list ready: {len(rows)} prospects')
            time.sleep(.2); p.empty()
            candidates=rows
            st.session_state.cached_candidates=candidates; st.session_state.candidate_key=cand_key
            st.session_state.timing['discovery_seconds']=round(time.time()-start,2)
        prospects=enrich_rows(candidates, location, profile, extra, find_more, workers)
        st.session_state.prospects=prospects
        st.session_state.last_settings={'mode':mode,'location':location,'profile':profile,'extra':extra,'find_more':find_more}
        st.session_state.timing['total_seconds']=round(time.time()-start,2)

elif mode=='School or prospect names':
    location=st.text_input('Location hint', 'Cape Town, Western Cape, South Africa')
    names=st.text_area('Enter one name per line', height=200)
    run=st.button('Find prospects', type='primary')
    if run:
        rows=[{'prospect_name':n.strip(),'sector':profile,'source':'manual_name','address':'','city':'','country':'','latitude':'','longitude':'','website':'','osm_phone':'','osm_email':''} for n in names.splitlines() if n.strip()]
        st.session_state.debug=[]; st.session_state.timing={}; start=time.time()
        st.session_state.prospects=enrich_rows(rows, location, profile, extra, find_more, workers)
        st.session_state.timing['total_seconds']=round(time.time()-start,2)
        st.session_state.last_settings={'mode':mode,'location':location,'profile':profile}
else:
    urls=st.text_area('Enter one website URL per line', height=200)
    run=st.button('Find prospects', type='primary')
    if run:
        rows=[]
        for u in urls.splitlines():
            u=clean_url(u.strip())
            if u: rows.append({'prospect_name':domain(u),'sector':profile,'source':'manual_url','address':'','city':'','country':'','latitude':'','longitude':'','website':u,'osm_phone':'','osm_email':'','website_source':'manual'})
        st.session_state.debug=[]; st.session_state.timing={}; start=time.time()
        st.session_state.prospects=enrich_rows(rows, '', profile, extra, find_more, workers)
        st.session_state.timing['total_seconds']=round(time.time()-start,2)
        st.session_state.last_settings={'mode':mode,'profile':profile}

if st.session_state.prospects:
    df=pd.DataFrame(st.session_state.prospects)
    st.subheader('Prospects')
    m1,m2,m3,m4=st.columns(4)
    m1.metric('Prospects', len(df))
    m2.metric('Websites', int(df['website'].fillna('').astype(str).str.len().gt(0).sum()) if 'website' in df else 0)
    m3.metric('Emails', int(df['best_email'].fillna('').astype(str).str.len().gt(0).sum()) if 'best_email' in df else 0)
    m4.metric('Phones', int(df['best_phone'].fillna('').astype(str).str.len().gt(0).sum()) if 'best_phone' in df else 0)
    st.dataframe(df[display_cols(df)], use_container_width=True, hide_index=True)
    q=slug((st.session_state.last_settings or {}).get('location') or mode)
    ts=time.strftime('%Y%m%d_%H%M')
    fname=f"prospect_discovery_{slug(profile)}_{q}_{ts}"
    st.download_button('Download CSV', data=df.to_csv(index=False).encode('utf-8'), file_name=f'{fname}.csv', mime='text/csv')
    st.download_button('Download Excel', data=to_excel(df), file_name=f'{fname}.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

with st.expander('Diagnostics'):
    st.write('Timing')
    st.json(st.session_state.timing or {})
    st.write('Debug log')
    st.text('\n'.join(st.session_state.debug[-200:]))
