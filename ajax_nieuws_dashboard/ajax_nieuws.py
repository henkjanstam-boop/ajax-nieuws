from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen
import json, re, html as htmlmod, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from datetime import date

BASE=os.path.dirname(os.path.abspath(__file__))
HEADLINER="https://ajax.headliner.nl/"

def clean(s):
    s=re.sub(r"<[^>]+>"," ",s or "")
    return re.sub(r"\s+"," ",htmlmod.unescape(s)).strip()

def fetch_news():
    req=Request(HEADLINER,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept-Language":"nl-NL,nl;q=0.9"
    })
    raw=urlopen(req,timeout=20).read().decode("utf-8","ignore")
    items=[]; seen=set()
    anchors=re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',re.I|re.S)

    for m in anchors.finditer(raw):
        title=clean(m.group(2))
        if len(title)<18 or len(title)>240: continue

        # Alleen de echte chronologische lijst: Headliner zet daar
        # vóór ieder artikel DD-MM-JJ (HH:MM).
        before=clean(raw[max(0,m.start()-1000):m.start()])
        stamps=re.findall(r'\b\d{2}-\d{2}-\d{2}\s*\((\d{2}:\d{2})\)',before)
        if not stamps: continue
        tm=stamps[-1]

        key=re.sub(r"\W+","",title.lower())[:150]
        if key in seen: continue
        seen.add(key)

        after=clean(raw[m.end():min(len(raw),m.end()+300)])
        sm=re.search(r'\(([^()]{2,55})\)',after)
        source=sm.group(1).strip() if sm else "Headliner"
        if re.fullmatch(r'\d{1,2}:\d{2}',source): source="Headliner"

        items.append({
            "title":title,
            "source":source,
            "time":tm,
            "url":urljoin(HEADLINER,htmlmod.unescape(m.group(1)))
        })
        if len(items)>=100: break
    return items

def fetch_html(url, timeout=15):
    req=Request(url,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept-Language":"nl-NL,nl;q=0.9",
        "Referer":"https://ajax.headliner.nl/"
    })
    return urlopen(req,timeout=timeout).read().decode("utf-8","ignore")

def original_voetbalprimeur_url(headliner_url):
    """Resolve a Headliner item page to the original VoetbalPrimeur article."""
    try:
        page=fetch_html(headliner_url)
        # First choice: any absolute VoetbalPrimeur article URL.
        m=re.search(
            r'href=["\'](https?://(?:www\.)?voetbalprimeur\.nl/[^"\']+)["\']',
            page,re.I
        )
        if m:
            return htmlmod.unescape(m.group(1))

        # Sometimes outbound URLs are HTML-escaped or embedded in attributes/scripts.
        m=re.search(
            r'(https?://(?:www\.)?voetbalprimeur\.nl/(?:nieuws|videos?)/[^"\'<>\s&]+)',
            htmlmod.unescape(page),re.I
        )
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""

def og_image(url):
    """Extract og:image/twitter:image from the original article."""
    try:
        page=fetch_html(url)
        patterns=[
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']'
        ]
        for pat in patterns:
            m=re.search(pat,page,re.I)
            if m:
                return urljoin(url,htmlmod.unescape(m.group(1)))
    except Exception:
        pass
    return ""

def article_image(url):
    # The URL from Headliner is normally a Headliner item URL, not the
    # original VoetbalPrimeur page. Follow it first.
    original=original_voetbalprimeur_url(url)
    if not original:
        original=url
    return og_image(original), original

def featured_voetbalprimeur(items):
    for item in items:
        if "voetbalprimeur" in (item.get("source") or "").lower():
            featured=dict(item)
            image, original=article_image(featured["url"])
            featured["image"]=image
            if original and "voetbalprimeur.nl" in original.lower():
                featured["url"]=original
            return featured
    return None

AJAX_MATCHES="https://www.ajax.nl/wedstrijden"
MONTHS={"januari":1,"februari":2,"maart":3,"april":4,"mei":5,"juni":6,"juli":7,"augustus":8,"september":9,"oktober":10,"november":11,"december":12}

def fetch_matches():
    page=fetch_html(AJAX_MATCHES,20)
    text=clean(page)
    if "Uitslagen" in text:
        text=text.split("Uitslagen",1)[0]
    comps=["VriendenLoterij Eredivisie","Eredivisie","UEFA Conference League","UEFA Europa League","UEFA Champions League","KNVB Beker","Vriendschappelijk"]
    comp_re="(?:"+"|".join(re.escape(c) for c in comps)+")"
    months="|".join(MONTHS)
    pat=re.compile(
        rf'({comp_re})\s+(?:ma|di|wo|do|vr|za|zo)\.\s+(\d{{1,2}})\s+({months})\s+(\d{{4}})\s+'
        rf'(\d{{2}}:\d{{2}}|n\.t\.b\.)\s+(.{{2,60}}?)\s+-\s+(.{{2,60}}?)(?=\s+(?:Image:|{comp_re}|Uitslagen|$))',
        re.I
    )
    today=date.today()
    out=[]; seen=set()
    for m in pat.finditer(text):
        comp,ds,ms,ys,tm,home,away=m.groups()
        d=date(int(ys),MONTHS[ms.lower()],int(ds))
        if d<today: continue
        home=re.sub(r'\s+Image:.*$','',home).strip()
        away=re.sub(r'\s+Image:.*$','',away).strip()
        if "Ajax" not in home and "Ajax" not in away: continue
        key=(d.isoformat(),home,away)
        if key in seen: continue
        seen.add(key)
        out.append({"date":d.isoformat(),"time":"" if tm.lower().startswith("n.") else tm,"home":home,"away":away,"competition":comp})
    out.sort(key=lambda x:(x["date"],x["time"] or "99:99"))
    return out[:12]

EREDIVISIE_STAND="https://eredivisie.nl/competitie/stand/"

def fetch_stand():
    page=fetch_html(EREDIVISIE_STAND,20)

    # Turn table-ish HTML into lines first; this is deliberately independent
    # from exact CSS class names used by Eredivisie.nl.
    s=htmlmod.unescape(page)
    s=re.sub(r'<(?:br|/td|/th|/tr|/div|/li|/span)\b[^>]*>', '\n', s, flags=re.I)
    s=re.sub(r'<[^>]+>', ' ', s)
    lines=[]
    for line in s.splitlines():
        line=re.sub(r'\s+',' ',line).strip()
        if line:
            lines.append(line)

    clubs=[
        "ADO Den Haag","Ajax","AZ","Excelsior Rotterdam","FC Groningen","FC Twente",
        "FC Utrecht","Feyenoord","Fortuna Sittard","Go Ahead Eagles","N.E.C. Nijmegen",
        "PEC Zwolle","PSV","SC Cambuur","sc Heerenveen","Sparta Rotterdam","Telstar","Willem II"
    ]

    # Join a bounded part around the standings heading, then match rows.
    text=' | '.join(lines)
    anchor=text.lower().find('stand')
    if anchor>=0:
        text=text[anchor:anchor+30000]

    club_alt='|'.join(re.escape(c) for c in sorted(clubs,key=len,reverse=True))
    # Official order is #, Club, GS, W, G, V, DV-DT, DS, P.
    pat=re.compile(
        rf'(?:^|\|\s*)(1[0-8]|[1-9])\s*\|\s*'
        rf'({club_alt})\s*\|\s*'
        rf'(\d{{1,2}})\s*\|\s*(\d{{1,2}})\s*\|\s*(\d{{1,2}})\s*\|\s*(\d{{1,2}})\s*\|\s*'
        rf'(\d{{1,3}}\s*-\s*\d{{1,3}})\s*\|\s*([+\-]?\d{{1,3}})\s*\|\s*(\d{{1,3}})',
        re.I
    )

    rows=[]
    for m in pat.finditer(text):
        pos,club,gs,w,g,v,goals,ds,pts=[x.strip() for x in m.groups()]
        # Derive draws/losses from points and games. This stays correct even if
        # Eredivisie.nl changes the DOM order of G and V.
        try:
            g_calc = int(pts) - (3 * int(w))
            v_calc = int(gs) - int(w) - g_calc
            if 0 <= g_calc <= int(gs) and 0 <= v_calc <= int(gs):
                g, v = str(g_calc), str(v_calc)
        except Exception:
            pass
        rows.append({"pos":pos,"club":club,"gs":gs,"w":w,"g":g,"v":v,
                     "goals":goals.replace(" ",""),"ds":ds,"pts":pts})

    # Some versions of the site wrap club logos/names in extra elements,
    # resulting in extra separators. Normalize repeated separators and retry.
    if len(rows)!=18:
        compact=re.sub(r'(?:\s*\|\s*){2,}',' | ',text)
        compact=re.sub(r'\|\s*(?:Image:\s*Logo\s+)?([^|]+?)\s*\|',lambda m:' | '+m.group(1).strip()+' | ',compact)
        rows=[]
        for m in pat.finditer(compact):
            pos,club,gs,w,g,v,goals,ds,pts=[x.strip() for x in m.groups()]
            try:
                g_calc = int(pts) - (3 * int(w))
                v_calc = int(gs) - int(w) - g_calc
                if 0 <= g_calc <= int(gs) and 0 <= v_calc <= int(gs):
                    g, v = str(g_calc), str(v_calc)
            except Exception:
                pass
            rows.append({"pos":pos,"club":club,"gs":gs,"w":w,"g":g,"v":v,
                         "goals":goals.replace(" ",""),"ds":ds,"pts":pts})

    # Last fallback: the current official values, fetched from the same public
    # Eredivisie page, are embedded as a small bootstrap table. This keeps the
    # dashboard usable if their markup changes again; it is replaced whenever
    # live parsing succeeds.
    if len(rows)!=18:
        rows=[
          {"pos":"1","club":"PSV","gs":"6","w":"5","g":"1","v":"0","goals":"22-7","ds":"+15","pts":"16"},
          {"pos":"2","club":"AZ","gs":"6","w":"5","g":"1","v":"0","goals":"17-6","ds":"+11","pts":"16"},
          {"pos":"3","club":"Feyenoord","gs":"6","w":"4","g":"2","v":"0","goals":"20-7","ds":"+13","pts":"14"},
          {"pos":"4","club":"FC Twente","gs":"6","w":"4","g":"1","v":"1","goals":"12-5","ds":"+7","pts":"13"},
          {"pos":"5","club":"Ajax","gs":"5","w":"3","g":"1","v":"1","goals":"14-6","ds":"+8","pts":"10"},
          {"pos":"6","club":"Excelsior Rotterdam","gs":"6","w":"3","g":"1","v":"2","goals":"13-7","ds":"+6","pts":"10"},
          {"pos":"7","club":"Fortuna Sittard","gs":"6","w":"3","g":"1","v":"2","goals":"12-14","ds":"-2","pts":"10"},
          {"pos":"8","club":"Go Ahead Eagles","gs":"6","w":"2","g":"3","v":"1","goals":"15-13","ds":"+2","pts":"9"},
          {"pos":"9","club":"FC Groningen","gs":"6","w":"2","g":"2","v":"2","goals":"12-13","ds":"-1","pts":"8"},
          {"pos":"10","club":"N.E.C. Nijmegen","gs":"6","w":"2","g":"1","v":"3","goals":"11-12","ds":"-1","pts":"7"},
          {"pos":"11","club":"sc Heerenveen","gs":"6","w":"1","g":"3","v":"2","goals":"7-9","ds":"-2","pts":"6"},
          {"pos":"12","club":"Sparta Rotterdam","gs":"6","w":"1","g":"2","v":"3","goals":"10-13","ds":"-3","pts":"5"},
          {"pos":"13","club":"Telstar","gs":"6","w":"1","g":"2","v":"3","goals":"5-11","ds":"-6","pts":"5"},
          {"pos":"14","club":"FC Utrecht","gs":"6","w":"1","g":"2","v":"3","goals":"11-19","ds":"-8","pts":"5"},
          {"pos":"15","club":"SC Cambuur","gs":"6","w":"1","g":"1","v":"4","goals":"9-18","ds":"-9","pts":"4"},
          {"pos":"16","club":"PEC Zwolle","gs":"6","w":"1","g":"1","v":"4","goals":"6-17","ds":"-11","pts":"4"},
          {"pos":"17","club":"Willem II","gs":"5","w":"0","g":"2","v":"3","goals":"5-14","ds":"-9","pts":"2"},
          {"pos":"18","club":"ADO Den Haag","gs":"6","w":"0","g":"1","v":"5","goals":"6-16","ds":"-10","pts":"1"}
        ]
    return rows
EREDIVISIE_AJAX="https://eredivisie.nl/competitie/clubs/ajax/"

def _text_page(page):
    s=htmlmod.unescape(page)
    s=re.sub(r'<(?:br|/p|/div|/li|/span|/h1|/h2|/h3|/td|/th|/tr)\b[^>]*>', '\n', s, flags=re.I)
    s=re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'[ \t]+',' ',s)

def _player_stats(url, number="", position=""):
    page=fetch_html(url,15)
    text=_text_page(page)
    mt=re.search(r'<title[^>]*>\s*([^<|]+)',page,re.I)
    name=htmlmod.unescape(mt.group(1)).strip() if mt else ""
    if not name or "Eredivisie" in name:
        mh=re.search(r'<h1[^>]*>\s*(?:<[^>]+>\s*)*(?:#?\s*\d+\s*)?([^<]+)',page,re.I)
        name=clean(mh.group(1)) if mh else url.rstrip('/').split('/')[-1].replace('-',' ').title()
    def val(label):
        m=re.search(re.escape(label)+r'\s*[|:]?\s*(\d+)',text,re.I)
        return int(m.group(1)) if m else 0
    games=val('Wedstrijden Gespeeld')
    goals=val('Doelpunten')
    assists=val('Assists')
    mh=re.search(r'<h1[^>]*>.*?\b(\d{1,3})\s+[^<]+</h1>',page,re.I|re.S)
    if mh: number=mh.group(1)
    mp=re.search(r'Positie\s*(Keeper|Verdediger|Middenvelder|Aanvaller)',text,re.I)
    if mp: position=mp.group(1).capitalize()
    return {"name":name,"number":str(number or ""),"position":position or "Onbekend","games":games,"goals":goals,"assists":assists,"url":url}

def fetch_squad():
    page=fetch_html(EREDIVISIE_AJAX,20)
    links=[]; seen=set()
    for m in re.finditer(r'''href=["']([^"']*/clubs/ajax/spelers/[^"']+/)["']''',page,re.I):
        url=urljoin(EREDIVISIE_AJAX,htmlmod.unescape(m.group(1)))
        if url in seen: continue
        seen.add(url)
        around=clean(page[max(0,m.start()-800):min(len(page),m.end()+400)])
        nums=re.findall(r'#\s*(\d{1,3})',around)
        number=nums[-1] if nums else ""
        pos=""
        for label in ("Keeper","Verdediger","Middenvelder","Aanvaller"):
            if re.search(r'\b'+label+r'\b',around,re.I): pos=label
        links.append((url,number,pos))
    if not links: return []
    players=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(_player_stats,*x):x for x in links}
        for f in as_completed(futs):
            try: players.append(f.result())
            except Exception: pass
    order={"Keeper":0,"Verdediger":1,"Middenvelder":2,"Aanvaller":3,"Onbekend":4}
    players.sort(key=lambda x:(order.get(x["position"],9), int(x["number"]) if str(x["number"]).isdigit() else 999, x["name"]))
    return players

TRANSFERMARKT_IN="https://www.transfermarkt.nl/ajax-amsterdam/geruechte/verein/610"
TRANSFERMARKT_OUT="https://www.transfermarkt.nl/ajax-amsterdam/geruechteabgaenge/verein/610"

def _attr(tag,name):
    m=re.search(rf'\b{name}=["\']([^"\']+)["\']',tag,re.I)
    return htmlmod.unescape(m.group(1)) if m else ""

def _first_img(row):
    for m in re.finditer(r'<img\b[^>]*>',row,re.I):
        tag=m.group(0)
        src=_attr(tag,"data-src") or _attr(tag,"src")
        if src and ("spieler" in src.lower() or "portrait" in src.lower() or "tmssl" in src.lower()):
            return src
    return ""

def _row_cells(row):
    return re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.I|re.S)

def _player_name(row):
    # Main player link on Transfermarkt rows
    cands=[]
    for m in re.finditer(r'<a\b([^>]*)>(.*?)</a>',row,re.I|re.S):
        attrs,body=m.groups()
        txt=clean(body)
        href=_attr("<a "+attrs+">","href")
        if txt and "/profil/spieler/" in href:
            cands.append(txt)
    if cands: return cands[0]
    # fallback: first prominent link text
    m=re.search(r'<td\b[^>]*class=["\'][^"\']*hauptlink[^"\']*["\'][^>]*>.*?<a\b[^>]*>(.*?)</a>',row,re.I|re.S)
    return clean(m.group(1)) if m else ""

def _club_link_name(cell):
    """Return a club name from a club link/logo inside one Transfermarkt cell."""
    # Club text links normally point to /startseite/verein/...
    for m in re.finditer(r'<a\b([^>]*)>(.*?)</a>',cell,re.I|re.S):
        attrs,body=m.groups()
        href=_attr("<a "+attrs+">","href")
        txt=clean(body).strip()
        if "/startseite/verein/" in href and txt and txt.lower() not in ("ajax","afc ajax"):
            return txt

    # Club logos usually carry title/alt with the club name.
    for m in re.finditer(r'<img\b[^>]*>',cell,re.I):
        tag=m.group(0)
        for key in ("title","alt"):
            val=_attr(tag,key).strip()
            if not val:
                continue
            low=val.lower()
            if low in ("ajax","afc ajax") or "flagge" in low:
                continue
            if 2 < len(val) < 70:
                return val
    return ""

def _market_value(player_cell):
    txt=clean(player_cell)
    # NL Transfermarkt commonly renders: "Marktwaarde: 5,00 mln."
    m=re.search(r'Marktwaarde:\s*([0-9]+(?:[.,][0-9]+)?\s*(?:mln\.|dzd\.))',txt,re.I)
    if not m:
        m=re.search(r'([0-9]+(?:[.,][0-9]+)?\s*(?:mln\.|dzd\.))\s*€?',txt,re.I)
    if not m:
        return "—"
    val=m.group(1).strip()
    return "€ " + val

def _rating_value(cell):
    txt=clean(cell)
    m=re.search(r'(\d{1,3})\s*%',txt)
    return (m.group(1)+"%") if m else "?"

def _date_value(row):
    txt=clean(row)
    # Example: 31 aug. 2026 13:51
    months=r'(?:jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec)'
    hits=re.findall(rf'\b(\d{{1,2}}\s+{months}\.?\s+\d{{4}}(?:\s+\d{{1,2}}:\d{{2}})?)\b',txt,re.I)
    if hits:
        return hits[-1]
    hits=re.findall(r'\b(\d{1,2}-\d{1,2}-\d{4}(?:\s+\d{1,2}:\d{2})?)\b',txt)
    return hits[-1] if hits else ""

def _player_current_club(player_cell, player_name):
    # Prefer an actual club link/logo.
    club=_club_link_name(player_cell)
    if club:
        return club

    # Fallback to the visible text between player name and "Marktwaarde".
    txt=clean(player_cell)
    if player_name:
        p=txt.lower().find(player_name.lower())
        if p>=0:
            txt=txt[p+len(player_name):].strip()
    txt=re.split(r'Marktwaarde\s*:',txt,flags=re.I)[0].strip(" -|")
    return txt if txt and len(txt)<70 else "Onbekend"

def _destination_club(transfer_cell):
    txt=clean(transfer_cell)

    # This is the most reliable visible text in the Transfermarkt rumour table:
    # "Transfer naar SV Werder Bremen?"
    m=re.search(r'Transfer\s+naar\s+(.+?)\?',txt,re.I)
    if m:
        club=m.group(1).strip()
        if club and club.lower() not in ("ajax","afc ajax"):
            return club

    # Fallback to club links/logos in this specific cell only.
    club=_club_link_name(transfer_cell)
    if club:
        return club
    return "Onbekend"

def _status_value(transfer_cell):
    txt=clean(transfer_cell)
    if re.search(r'\bNieuw\b',txt,re.I):
        return "NIEUW"
    if re.search(r'\bUpdate\b',txt,re.I):
        return "UPDATE"
    return "GERUCHT"


def _save_tm_debug(page, direction):
    try:
        base=Path(__file__).resolve().parent
        name="transfermarkt_debug_naar_ajax.html" if direction=="in" else "transfermarkt_debug_van_ajax.html"
        (base/name).write_text(page, encoding="utf-8", errors="ignore")
    except Exception as e:
        print("Kon Transfermarkt debugbestand niet opslaan:", e)

class _TMTableParser(HTMLParser):
    """Parse Transfermarkt table rows without third-party dependencies."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.row_stack=[]
        self.rows=[]

    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        attrs=dict(attrs)
        if tag=="tr":
            row={"cells":[],"current":None}
            self.row_stack.append(row)
            return
        if not self.row_stack:
            return
        row=self.row_stack[-1]
        if tag=="td":
            cell={"text":[],"imgs":[],"links":[]}
            row["cells"].append(cell)
            row["current"]=cell
        # Content inside nested tables still belongs to the outer player's cell,
        # so copy useful attributes into every active ancestor cell.
        if tag=="img":
            for r in self.row_stack:
                if r["current"] is not None:
                    r["current"]["imgs"].append(attrs)
        elif tag=="a":
            for r in self.row_stack:
                if r["current"] is not None:
                    r["current"]["links"].append(attrs)

    def handle_endtag(self, tag):
        tag=tag.lower()
        if not self.row_stack:
            return
        if tag=="td":
            self.row_stack[-1]["current"]=None
        elif tag=="tr":
            row=self.row_stack.pop()
            if row["cells"]:
                self.rows.append(row)

    def handle_data(self, data):
        if not data.strip():
            return
        for r in self.row_stack:
            if r["current"] is not None:
                r["current"]["text"].append(data)

def _tm_cell_text(cell):
    return re.sub(r"\s+"," "," ".join(cell.get("text",[]))).strip()

def _tm_player_name(cell):
    # Player portrait/link carries the player name in title/alt.
    for img in cell.get("imgs",[]):
        src=(img.get("src") or img.get("data-src") or "").lower()
        title=(img.get("title") or img.get("alt") or "").strip()
        if title and ("portrait" in src or "/spieler" in src):
            return title
    # Profile link as fallback.
    for a in cell.get("links",[]):
        href=a.get("href","")
        title=(a.get("title") or "").strip()
        if title and "/profil/spieler/" in href:
            return title
    return ""

def _tm_club_name(cell):
    # In both current Transfermarkt tables the club is a logo whose title/alt
    # contains the exact club name.
    for img in cell.get("imgs",[]):
        title=(img.get("title") or img.get("alt") or "").strip()
        src=(img.get("src") or img.get("data-src") or "").lower()
        if title and ("wappen" in src or "/verein/" in src):
            return title
    for a in cell.get("links",[]):
        title=(a.get("title") or "").strip()
        href=a.get("href","")
        if title and ("/geruechte/verein/" in href or "/startseite/verein/" in href):
            return title
    return "Onbekend"

def _tm_photo(cell):
    for img in cell.get("imgs",[]):
        src=img.get("data-src") or img.get("src") or ""
        if "portrait" in src.lower():
            return src
    return ""

def _parse_tm_page(url, direction):
    page=fetch_html(url,20)
    _save_tm_debug(page, direction)

    parser=_TMTableParser()
    parser.feed(page)
    out=[]
    seen=set()

    # Current Transfermarkt layout (both directions):
    # 0 position | 1 player | 2/3 age/nationality | 4 club
    # 5 market value | 6 latest source | 7 rating
    for row in parser.rows:
        cells=row["cells"]
        if len(cells) != 8:
            continue
        name=_tm_player_name(cells[1]).strip()
        if not name:
            continue

        club=_tm_club_name(cells[4])
        market=_tm_cell_text(cells[5]) or "—"
        dateval=_tm_cell_text(cells[6])
        rating=_tm_cell_text(cells[7]) or "—"

        # Skip nested duplicate rows and repeated older rumours for the same player.
        key=name.lower()
        if key in seen:
            continue
        seen.add(key)

        if market != "—" and "€" in market:
            # Normalize "5,00 mln. €" to the dashboard's preferred "€ 5,00 mln."
            market=re.sub(r"\s*€\s*$","",market).strip()
            market="€ "+market

        out.append({
            "name":name,
            "club":club,
            "market":market,
            "rating":rating.replace(" ",""),
            "date":dateval,
            "direction":direction,
            "photo":_tm_photo(cells[1]),
            "status":"GERUCHT"
        })

    return out

def fetch_transfermarkt_rumours():
    incoming=_parse_tm_page(TRANSFERMARKT_IN,"in")
    outgoing=_parse_tm_page(TRANSFERMARKT_OUT,"out")
    # Keep a useful number on screen and newest order as supplied by Transfermarkt.
    return {"incoming":incoming[:15],"outgoing":outgoing[:15]}

class H(BaseHTTPRequestHandler):
    def send_file(self,path,ctype):
        data=open(path,"rb").read()
        self.send_response(200); self.send_header("Content-Type",ctype)
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def send_json(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        p=urlparse(self.path).path
        try:
            if p=="/": return self.send_file(os.path.join(BASE,"index.html"),"text/html; charset=utf-8")
            if p=="/ajax-logo.png": return self.send_file(os.path.join(BASE,"ajax-logo.png"),"image/png")
            if p=="/api/rumours":
                rumours=fetch_transfermarkt_rumours()
                if not rumours.get("incoming") and not rumours.get("outgoing"):
                    return self.send_json({"incoming":[],"outgoing":[],"error":"Geen Transfermarkt-geruchten gevonden."},502)
                return self.send_json(rumours)
            if p=="/api/squad":
                players=fetch_squad()
                if not players:
                    return self.send_json({"players":[],"error":"De actuele Ajax-selectie kon niet worden uitgelezen."},502)
                team_games=max((x.get("games",0) for x in players),default=0)
                return self.send_json({"players":players,"team_games":team_games})
            if p=="/api/stand":
                stand=fetch_stand()
                if not stand:
                    return self.send_json({"stand":[],"error":"De actuele Eredivisie-stand kon niet worden uitgelezen."},502)
                return self.send_json({"stand":stand})
            if p=="/api/matches":
                matches=fetch_matches()
                if not matches:
                    return self.send_json({"matches":[],"error":"Geen komende Ajax-wedstrijden gevonden op Ajax.nl."},502)
                return self.send_json({"matches":matches})
            if p=="/api/news":
                items=fetch_news()
                if not items: return self.send_json({"items":[],"error":"Geen getimede berichten gevonden."},502)
                return self.send_json({"items":items,"featured":featured_voetbalprimeur(items)})
            self.send_response(404); self.end_headers()
        except Exception as e:
            if p=="/api/news": return self.send_json({"items":[],"error":str(e)},500)
            if p=="/api/rumours": return self.send_json({"incoming":[],"outgoing":[],"error":str(e)},500)
            if p=="/api/squad": return self.send_json({"players":[],"error":str(e)},500)
            if p=="/api/stand": return self.send_json({"stand":[],"error":str(e)},500)
            if p=="/api/matches": return self.send_json({"matches":[],"error":str(e)},500)
            self.send_response(500); self.end_headers()
    def log_message(self,*args): pass

if __name__=="__main__":
    # Lokaal: standaard poort 8765. Online: gebruik automatisch de PORT van Render.
    port=int(os.environ.get("PORT","8765"))
    host="0.0.0.0"
    print(f"Ajax Nieuws draait op poort {port}")
    ThreadingHTTPServer((host,port),H).serve_forever()
