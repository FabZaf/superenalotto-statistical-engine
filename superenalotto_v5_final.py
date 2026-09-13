# SUPERNALOTTO ENGINE V5.2.1 FINAL
# Incremental archive updater + rigorous Discovery/Confirmation statistical protocol
# Candidate tickets are exploratory only; no predictive claim.
import os, re, time, requests, numpy as np, pandas as pd
from bs4 import BeautifulSoup

BASE_URL='https://www.superenalotto.it/archivio-estrazioni'
FIRST_YEAR,LAST_YEAR=2009,2026
DISCOVERY_START,DISCOVERY_END='2016-01-01','2021-12-31'
CONFIRMATION_START,CONFIRMATION_END='2022-01-01','2026-12-31'
N_NULL=2000; MIN_HISTORY=100; SEED=123456; ALPHA=.05
STRATEGIES=['FREQUENCY','DELAY','MIXED']; N_TICKETS=10
OUTPUT='superenalotto_storico.csv'; QUALITY='superenalotto_data_quality.csv'
DISC='v5_results_discovery.csv'; FULL='v5_results_full_protocol.csv'; OOS='v5_oos_draws.csv'; TICKETS='v5_candidate_tickets.csv'
MONTHS={1:'gennaio',2:'febbraio',3:'marzo',4:'aprile',5:'maggio',6:'giugno',7:'luglio',8:'agosto',9:'settembre',10:'ottobre',11:'novembre',12:'dicembre'}

S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept-Language':'it-IT,it;q=0.9,en;q=0.8'})

def parse_month(y,m):
    url=f'{BASE_URL}/{y}/{MONTHS[m]}'; print(f'WEB FETCH {y}-{m:02d}',flush=True)
    try:
        r=S.get(url,timeout=10); r.raise_for_status()
        lines=[x.strip() for x in BeautifulSoup(r.text,'html.parser').get_text('\n',strip=True).splitlines() if x.strip()]
    except Exception as e:
        print(f' HTTP ERROR ({y}-{m:02d}): {e}',flush=True); return []
    rev={v:k for k,v in MONTHS.items()}; out=[]
    for i,line in enumerate(lines):
        z=re.search(r'Concorso\s*(?:Nº|N°|No\.?|n\.)?\s*(\d+)\s+del\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})',line,re.I)
        if not z or z.group(3).lower() not in rev: continue
        c,d,mt,yy=int(z.group(1)),int(z.group(2)),z.group(3).lower(),int(z.group(4))
        try: date=pd.Timestamp(yy,rev[mt],d)
        except: continue
        nums=[]; jolly=star=None
        for j in range(i+1,min(i+40,len(lines))):
            x=lines[j]
            if x.lower()=='jolly' and j+1<len(lines):
                try:
                    v=int(lines[j+1]); jolly=v if 1<=v<=90 else None
                except: pass
            if x.lower()=='superstar' and j+1<len(lines):
                try:
                    v=int(lines[j+1]); star=v if 1<=v<=90 else None
                except: pass
            if re.fullmatch(r'\d{1,2}',x):
                v=int(x)
                if 1<=v<=90: nums.append(v)
                if len(nums)==6: break
        if len(nums)==6 and len(set(nums))==6:
            nums=sorted(nums); out.append({'concorso':c,'data':date,'n1':nums[0],'n2':nums[1],'n3':nums[2],'n4':nums[3],'n5':nums[4],'n6':nums[5],'jolly':jolly,'superstar':star,'source':url})
    return out

def archive():
    req=['concorso','data','n1','n2','n3','n4','n5','n6']; existing=pd.DataFrame(); known=set()
    if os.path.exists(OUTPUT):
        try:
            existing=pd.read_csv(OUTPUT); ok=all(c in existing.columns for c in req)
            if ok and not existing.empty:
                existing['data']=pd.to_datetime(existing['data'],errors='coerce')
                existing=existing.dropna(subset=['concorso','data'])
                existing['concorso']=pd.to_numeric(existing['concorso'],errors='coerce').astype('Int64')
                existing=existing.dropna(subset=['concorso']); existing['concorso']=existing['concorso'].astype(int)
                known=set(existing['concorso'].unique())
                print(f'CSV LOCALE: {len(existing)} estrazioni | ultimo concorso {max(known)}',flush=True)
            else: existing=pd.DataFrame()
        except Exception as e: print(f'ATTENZIONE CSV: {e}',flush=True); existing=pd.DataFrame()

    now=pd.Timestamp.now(); rows=[]
    if existing.empty:
        print('Nessun CSV locale valido: recupero storico completo 2009-2026.',flush=True)
        years=range(FIRST_YEAR,LAST_YEAR+1)
        months=[(y,m) for y in years for m in range(1,(now.month if y==now.year else 12)+1)]
    else:
        # Find the month containing the latest known contest; only fetch that month onward.
        last_date=pd.to_datetime(existing['data']).max()
        start_y,start_m=int(last_date.year),int(last_date.month)
        months=[]
        for y in range(max(FIRST_YEAR,start_y),LAST_YEAR+1):
            for m in range(start_m if y==start_y else 1,(now.month if y==now.year else 12)+1): months.append((y,m))
        print(f'Aggiornamento incrementale: dal {start_y}-{start_m:02d} in poi.',flush=True)

    for y,m in months:
        for row in parse_month(y,m):
            if row['concorso'] not in known: rows.append(row)
        time.sleep(.05)

    df=pd.concat([existing,pd.DataFrame(rows)],ignore_index=True) if not existing.empty else pd.DataFrame(rows)
    if df.empty: raise RuntimeError('Impossibile procedere: nessun dato storico disponibile.')
    df['concorso']=pd.to_numeric(df['concorso'],errors='coerce'); df['data']=pd.to_datetime(df['data'],errors='coerce')
    df=df.dropna(subset=['concorso','data']); df['concorso']=df['concorso'].astype(int)
    for c,g in df.groupby('concorso'):
        if len(g[['data','n1','n2','n3','n4','n5','n6']].drop_duplicates())>1: raise RuntimeError(f'Concorso duplicato discordante: {c}')
    return df.drop_duplicates('concorso').sort_values(['data','concorso']).reset_index(drop=True)

def validate(df):
    req=['concorso','data','n1','n2','n3','n4','n5','n6']
    if any(c not in df.columns for c in req): raise ValueError('Colonne mancanti')
    df=df.copy(); df['data']=pd.to_datetime(df['data'],errors='coerce'); df['concorso']=pd.to_numeric(df['concorso'],errors='coerce')
    if df['data'].isna().any() or df['concorso'].isna().any(): raise ValueError('Date/concorsi non validi')
    bad=[]
    for i,r in df.iterrows():
        try: ns=[int(r[f'n{k}']) for k in range(1,7)]
        except: bad.append(i); continue
        if len(set(ns))<6 or not all(1<=n<=90 for n in ns): bad.append(i)
    df=df.drop(index=bad).drop_duplicates('concorso').sort_values(['data','concorso']).reset_index(drop=True)
    ix=np.where(df.data.to_numpy()>=pd.Timestamp(DISCOVERY_START))[0]
    if len(ix)==0 or ix[0]<MIN_HISTORY: raise ValueError('Storico pre-Discovery insufficiente')
    pd.DataFrame([{'check':'rows_final','value':len(df)},{'check':'invalid_removed','value':len(bad)},{'check':'first_date','value':str(df.data.min().date())},{'check':'last_date','value':str(df.data.max().date())},{'check':'last_contest','value':int(df.concorso.max())}]).to_csv(QUALITY,index=False)
    df.to_csv(OUTPUT,index=False); print(f'ARCHIVE OK: {len(df)} draws | {df.data.min().date()} -> {df.data.max().date()}',flush=True); return df

class Engine:
    def __init__(self,df):
        self.df=df.sort_values(['data','concorso']).reset_index(drop=True)
        self.draws=self.df[[f'n{i}' for i in range(1,7)]].to_numpy(np.int16); self.dates=self.df.data.to_numpy(); self.contests=self.df.concorso.to_numpy()
    @staticmethod
    def state(hist):
        f=np.zeros(91,np.int32); d=np.zeros(91,np.int32)
        for a in hist: d[1:]+=1; [None for n in a if not (f.__setitem__(n,f[n]+1) or d.__setitem__(n,0))]
        return f,d
    @staticmethod
    def update(a,f,d):
        d[1:]+=1
        for n in a: f[n]+=1; d[n]=0
    @staticmethod
    def pick(st,f,d,r):
        nums=np.arange(1,91)
        if st=='FREQUENCY': w=f[1:].astype(float)+1e-12; return np.sort(r.choice(nums,6,False,p=w/w.sum()))
        if st=='DELAY': w=d[1:].astype(float)+1e-12; return np.sort(r.choice(nums,6,False,p=w/w.sum()))
        ff=f[1:].astype(float); dd=d[1:].astype(float)
        if ff.max(): ff/=ff.max()
        if dd.max(): dd/=dd.max()
        score=.5*ff+.5*dd; top=np.argsort(score)[-30:]; c=nums[top]; w=score[top]+1e-12
        return np.sort(r.choice(c,6,False,p=w/w.sum()))
    def initial_state(self,first): return self.state(self.draws[:first])
    def oos(self,st,start,end,seed):
        ix=np.where((self.dates>=pd.Timestamp(start))&(self.dates<=pd.Timestamp(end)))[0];
        if len(ix)==0: raise ValueError(f'Nessuna estrazione nella fase {start}-{end}')
        first=ix[0]
        if first<MIN_HISTORY: raise ValueError('Storico insufficiente')
        r=np.random.default_rng(seed); f,d=self.initial_state(first); total=0; rows=[]
        for i in ix:
            t=self.pick(st,f,d,r); a=self.draws[i]; h=len(set(t)&set(a)); total+=h
            rows.append({'phase':start+'_'+end,'strategy':st,'concorso':int(self.contests[i]),'data':str(pd.Timestamp(self.dates[i]).date()),'ticket':' '.join(f'{x:02d}' for x in t),'actual':' '.join(f'{x:02d}' for x in a),'hits':h,'seed':seed}); self.update(a,f,d)
        return total,len(ix),rows
    def null(self,st,start,end,n,seed):
        ix=np.where((self.dates>=pd.Timestamp(start))&(self.dates<=pd.Timestamp(end)))[0]
        if len(ix)==0: raise ValueError('Finestra nulla')
        first=ix[0]; hist=self.draws[:first]; L=len(ix); master=np.random.default_rng(seed); out=np.empty(n,np.int32); nums=np.arange(1,91); f0,d0=self.initial_state(first); t0=time.time()
        for s in range(n):
            r=np.random.default_rng(int(master.integers(0,2**63-1))); ar=np.random.default_rng(int(master.integers(0,2**63-1)))
            f=f0.copy(); d=d0.copy(); total=0
            for _ in range(L):
                ticket=self.pick(st,f,d,r); actual=np.sort(ar.choice(nums,6,False)); total+=len(set(ticket)&set(actual)); self.update(actual,f,d)
            out[s]=total
            if (s+1)%100==0: print(f'   {st}: {s+1}/{n} | elapsed {(time.time()-t0)/60:.1f} min',flush=True)
        return out
    def phase(self,name,start,end,strats,n,seed):
        alpha=.05/len(strats); res=[]; logs=[]
        for sid,st in enumerate(strats):
            ss=seed+sid*100000; obs,L,rows=self.oos(st,start,end,ss); logs+=rows; nul=self.null(st,start,end,n,ss+999999); p=(np.sum(nul>=obs)+1)/(n+1); passed=p<alpha
            print(f'[{name}] {st}: observed={obs}, H0 mean={nul.mean():.2f}, p={p:.6f}, Bonf={alpha:.6f}, PASS={passed}',flush=True)
            res.append({'phase':name,'strategy':st,'draws':L,'observed_hits':obs,'observed_mean':obs/L,'null_mean':nul.mean(),'null_std':nul.std(ddof=1),'p_value':p,'alpha_bonferroni':alpha,'passed':passed})
        return pd.DataFrame(res),logs
    def tickets(self,st,n=10,seed=999):
        r=np.random.default_rng(seed); f,d=self.state(self.draws); seen=set(); rows=[]
        while len(rows)<n:
            t=tuple(self.pick(st,f,d,r))
            if t in seen: continue
            seen.add(t); rows.append({'rank':len(rows)+1,'strategy':st,'ticket':' '.join(f'{x:02d}' for x in t),'status':'CANDIDATA - NON GARANTITA'})
        return pd.DataFrame(rows)
    def run(self):
        disc,logs1=self.phase('DISCOVERY OOS',DISCOVERY_START,DISCOVERY_END,STRATEGIES,N_NULL,SEED); disc.to_csv(DISC,index=False); passed=disc.loc[disc.passed,'strategy'].tolist(); logs=logs1
        if passed:
            conf,logs2=self.phase('CONFIRMATION OOS',CONFIRMATION_START,CONFIRMATION_END,passed,N_NULL,SEED+500000); logs+=logs2; pd.concat([disc,conf],ignore_index=True).to_csv(FULL,index=False); confirmed=conf.loc[conf.passed,'strategy'].tolist(); st=confirmed[0] if confirmed else passed[0]; print('ESITO:', 'SEGNALE STATISTICO CONFERMATO - DA APPROFONDIRE' if confirmed else 'SEGNALE NON REPLICATO - NESSUNA EVIDENZA ROBUSTA',flush=True)
        else:
            disc.to_csv(FULL,index=False); st='MIXED'; print('ESITO: H0 NON VIENE RIGETTATA - NESSUNA EVIDENZA STATISTICA OLTRE IL CASO',flush=True)
        pd.DataFrame(logs).to_csv(OOS,index=False); t=self.tickets(st,N_TICKETS,SEED+900000); t.to_csv(TICKETS,index=False)
        print('\nSESTINE CANDIDATE (non garantite):',flush=True); print('\n'.join(f"{int(r['rank']):02d}) {r['ticket']}" for _,r in t.iterrows()),flush=True)
        print('\nFILE GENERATI:',OUTPUT,QUALITY,DISC,FULL,OOS,TICKETS,flush=True)

if __name__=='__main__':
    try: Engine(validate(archive())).run()
    except Exception as e: print(f'\n[!] ERRORE PIPELINE: {e}',flush=True); raise
    
