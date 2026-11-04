"""Explicit-only local experiment. Never imports production modules or Whisper."""
import argparse
import ctypes
import difflib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'qwen2.5:3b'
FIELDS = ['inicio', 'fim', 'personagens', 'resumo', 'conflito', 'gancho_potencial', 'payoff', 'tipo', 'potencial']
SCHEMA = {'type': 'object', 'properties': {'historias': {'type': 'array', 'items': {
    'type': 'object', 'properties': {k: {'type': ('number' if k in ('inicio','fim','potencial') else
                                              'array' if k == 'personagens' else 'string'),
                                      **({'items': {'type': 'string'}} if k == 'personagens' else {})}
                                 for k in FIELDS}, 'required': FIELDS, 'additionalProperties': False}}},
          'required': ['historias'], 'additionalProperties': False}

def api(endpoint, payload=None, timeout=120):
    req = urllib.request.Request('http://127.0.0.1:11434/api/' + endpoint,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)

def generate(prompt, structured=True):
    return api('generate', {'model': MODEL, 'prompt': prompt, 'stream': False,
        'keep_alive': '5m', 'options': {'temperature': 0.2, 'num_ctx': 4096, 'num_predict': 1400},
        **({'format': SCHEMA} if structured else {})})

def validate(raw, start, end, duration):
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('historias'), list):
        raise ValueError('Expected historias array')
    accepted, rejected = [], []
    for h in data['historias']:
        try:
            if not isinstance(h, dict) or any(k not in h for k in FIELDS):
                raise ValueError('Missing fields')
            if any(type(h[k]) not in (int,float) or not math.isfinite(h[k]) for k in ('inicio','fim','potencial')):
                raise ValueError('Invalid numeric value')
            a,b = h['inicio'],h['fim']
            if not 0 < b-a <= 180: raise ValueError('Duration outside (0,180]')
            if a < start or b > end or a < 0 or b > duration: raise ValueError('Outside source window')
            if duration > 180 and b-a >= duration*.8: raise ValueError('Almost entire episode')
            if not 0 <= h['potencial'] <= 100: raise ValueError('Invalid potential')
            if not isinstance(h['personagens'],list) or any(not isinstance(x,str) for x in h['personagens']):
                raise ValueError('Invalid characters')
            if any(not isinstance(h[k],str) for k in FIELDS if k not in ('inicio','fim','potencial','personagens')):
                raise ValueError('Invalid textual field')
            if len(h['resumo'].strip()) < 15 or len(h['conflito'].strip()) < 8:
                raise ValueError('Insufficient description/conflict')
            accepted.append(h)
        except (ValueError,TypeError) as exc:
            rejected.append({'candidate': h, 'reason': str(exc)})
    return accepted,rejected,len(data['historias'])

def similar(a,b):
    def sim(key):
        return difflib.SequenceMatcher(None,a[key].lower(),b[key].lower()).ratio()
    overlap = max(0,min(a['fim'],b['fim'])-max(a['inicio'],b['inicio']))
    iou = overlap / (max(a['fim'],b['fim'])-min(a['inicio'],b['inicio']))
    characters = bool(set(a['personagens']) & set(b['personagens']))
    return (iou >= .55 and (sim('conflito') >= .4 or sim('resumo') >= .4)) or (
        overlap > 0 and characters and sim('conflito') >= .7 and sim('resumo') >= .6)

def deduplicate(items):
    result=[]
    for h in sorted(items,key=lambda x:x['potencial'],reverse=True):
        if not any(similar(h,x) for x in result): result.append(h)
    return result

def narrative_sanity(h):
    # Allow a small difference from the 20s target, not individual 1-4s lines.
    if h['fim']-h['inicio'] < 15:
        return 'Fragmento muito abaixo do alvo de 20s; nenhuma micro-historia demonstrada'
    conflict=h['conflito'].strip().lower()
    if conflict.startswith(('nenhum conflito','nenhum conflito notavel','sem conflito')):
        return 'A propria resposta declara ausencia de conflito'
    return None

def review_report(path):
    """Post-run sanity review only; no inference and no source-cache writes."""
    report=json.loads(Path(path).read_text(encoding='utf-8'))
    valid=[]; rejected=[]
    for h in report['stories']:
        reason=narrative_sanity(h)
        if reason: rejected.append({'candidate':h,'reason':reason})
        else: valid.append(h)
    reviewed={'structural_report':report,'narrative_candidates':valid,'narrative_rejections':rejected,
              'resources_after_unload':resources(),
              'review_policy':'15s lower tolerance for the 20s target; no declared absence of conflict',
              'note':'Structural counts are not narrative approval; invalid truncated JSON has unknown candidate count'}
    destination=Path(path).parent
    (destination/'reviewed_report.json').write_text(json.dumps(reviewed,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Story Miner experimental - Family Guy S07E11', '',
           'Scores abaixo sao do modelo, nao aprovacao humana. Fragmentos rejeitados nao sao oportunidades uteis.', '',
           f"Candidatos estruturais distintos: {len(report['stories'])}; candidatos apos revisao narrativa: {len(valid)}.", '',
           '## 15 entradas com maior potencial informado pelo modelo', '']
    for i,h in enumerate(report['stories'][:15],1):
        lines.extend([f"### #{i} | {h['inicio']:.2f}-{h['fim']:.2f}s | {h['fim']-h['inicio']:.2f}s | potencial {h['potencial']}",
            f"Tipo: {h['tipo']}",f"Personagens: {', '.join(h['personagens']) or 'Nao identificados'}",
            f"Conflito: {h['conflito']}",f"Gancho: {h['gancho_potencial']}",f"Payoff: {h['payoff']}",
            f"Resumo: {h['resumo']}",f"Revisao: {narrative_sanity(h) or 'Requer verificacao humana nas falas'}",''])
    (destination/'qualidade.md').write_text('\n'.join(lines),encoding='utf-8')
    return reviewed

class Memory(ctypes.Structure):
    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)] + [(x,ctypes.c_ulonglong) for x in
        ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]

def resources():
    m=Memory(); m.length=ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    values=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu',
        '--format=csv,noheader,nounits'],creationflags=subprocess.CREATE_NO_WINDOW,text=True,timeout=5).strip().split(',')
    return {'ram_used_gib':(m.total-m.available)/2**30,'ram_available_gib':m.available/2**30,
            'vram_used_mib':float(values[0]),'gpu_percent':float(values[1])}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    cache=json.loads(args.cache.read_text(encoding='utf-8'))
    out=args.output; out.mkdir(parents=True,exist_ok=True)
    report={'source':str(args.cache),'episode':cache['video_path'],'model':MODEL,
            'calls':0,'invalid_json':0,'raw':0,'rejections':[],'windows':[], 'stories':[]}
    samples=[]; stopped=threading.Event()
    def monitor():
        while not stopped.is_set():
            try: samples.append(resources())
            except Exception: pass
            stopped.wait(1)
    before=resources(); samples.append(before)
    worker=threading.Thread(target=monitor,daemon=True); worker.start()
    started=time.perf_counter()
    def save():
        report['total_seconds']=time.perf_counter()-started
        report['resources']={'before':before,'after':samples[-1],
            'peak_ram_used_gib':max(x['ram_used_gib'] for x in samples),
            'peak_vram_used_mib':max(x['vram_used_mib'] for x in samples),
            'peak_gpu_percent':max(x['gpu_percent'] for x in samples)}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        report['calls']+=1
        t=time.perf_counter(); health=generate('Responda somente OK.',False)
        report['health']={'seconds':time.perf_counter()-t,'load_seconds':health.get('load_duration',0)/1e9,
            'response':health.get('response'),'running':api('ps')}
        if not health.get('response','').strip(): raise RuntimeError('Empty health response')
        save(); print('HEALTH '+json.dumps(report['health'],ensure_ascii=False),flush=True)
        duration=cache['duracao']; all_items=[]
        for start in range(0,math.ceil(duration),60):
            end=min(start+90,duration)
            segments=[s for s in cache['segmentos'] if s['inicio']>=start and s['fim']<=end]
            if not segments: continue
            text='\n'.join(f"[{s['inicio']:.2f}-{s['fim']:.2f}] {s['texto']}" for s in segments)
            prompt=(f'Voce e um Story Miner. Encontre micro-historias concretas SOMENTE nas falas abaixo. '
                f'Janela absoluta {start}-{end}s. Use timestamps absolutos existentes. '
                'Busque conflito, segredo, absurdo, revelacao, plano frustrado, setup/payoff ou mudanca real. '
                'Nao crie remix ou titulo. Nao invente fatos ou nomes; use personagem nao identificado quando necessario. '
                'Alvo 20-150s, pequenas diferencas aceitaveis, nunca >180s ou fora da janela. '
                'Resumo e conflito especificos, gancho e payoff fundamentados; payoff vazio se ausente. '
                'Potencial 0-100. Nao invente quantidade. JSON estrito {historias:[{inicio,fim,personagens,'
                'resumo,conflito,gancho_potencial,payoff,tipo,potencial}]}; lista vazia se nao houver material.\n'+text)
            t=time.perf_counter(); report['calls']+=1
            response=generate(prompt)
            (out/f'window_{start:04d}.json').write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8')
            try: valid,rejected,count=validate(response['response'],start,end,duration)
            except (ValueError,TypeError,KeyError):
                report['invalid_json']+=1; report['calls']+=1
                response=generate('Repare SOMENTE a estrutura JSON sem inventar historias. Campos exigidos: '+
                    ','.join(FIELDS)+'\n'+response.get('response',''))
                try: valid,rejected,count=validate(response['response'],start,end,duration)
                except (ValueError,TypeError,KeyError) as exc:
                    valid,rejected,count=[],[{'reason':'Invalid JSON after one repair: '+str(exc)}],0
            report['raw']+=count; report['rejections'].extend(rejected); all_items.extend(valid)
            report['stories']=deduplicate(all_items); report['after_filter']=len(all_items)
            report['windows'].append({'inicio':start,'fim':end,'seconds':time.perf_counter()-t,
                                      'accepted':len(valid),'raw':count})
            save(); print(f'WINDOW {start}-{end}: {len(valid)} accepted; {len(report["stories"])} distinct',flush=True)
    except Exception as exc:
        report['stopped_error']=f'{type(exc).__name__}: {exc}'
        print('STOP '+report['stopped_error'],flush=True)
    finally:
        stopped.set(); worker.join(6); samples.append(resources()); save()
        # Release only the model loaded explicitly by this experiment.
        try: api('generate',{'model':MODEL,'keep_alive':0})
        except Exception: pass

if __name__=='__main__': main()
