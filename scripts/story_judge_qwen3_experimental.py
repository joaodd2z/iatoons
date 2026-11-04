"""Phase 3 runner. Reuses phase 2 builders, validator, scores and deduplication unchanged."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import story_judge_ollama_experimental as S
from scripts.story_miner_ollama_experimental import api, resources

MODEL='qwen3:4b'

def thinking_mode(metadata):
    values=metadata.get('thinking',{}).get('values',[])
    if False in values: return False
    if True in values: return True
    raise ValueError('Model does not advertise a supported boolean thinking control')

def request_payload(group,segments,thinking=False):
    ids=[c['candidate_id'] for c in group]
    schema={'type':'object','properties':{'avaliacoes':{'type':'array','minItems':len(group),
        'maxItems':len(group),'items':json.loads(json.dumps(S.ITEM))}},
        'required':['avaliacoes'],'additionalProperties':False}
    schema['properties']['avaliacoes']['items']['properties']['candidate_id']['enum']=ids
    # Same instructions as phase 2. No prompt tuning between models.
    prompt=('Julgue os blocos fixos. NAO descubra timestamps, NAO escolha falas isoladas, NAO escreva scores. '
        'Cada candidato inclui TODAS as falas identificadas em segment_ids; a tabela falas e compartilhada apenas para evitar repeticao. '
        'Existe historia se ha situacao compreensivel, conflito/objetivo e mudanca; cenas sem arco devem ser false. '
        'Seja conciso: citacoes de 3-10 palavras, no maximo 70 caracteres. Para cada criterio marque presente somente quando houver evidencia narrativa real. Nos campos conflito, setup, escalada, '
        'payoff, gancho_potencial e mudanca copie UMA citacao exata curta do texto que prova o criterio; use "" se ausente. '
        'Payoff precisa ser consequencia/resolucao, nao repetir setup. Escalada precisa desenvolver o conflito. '
        'Mudanca exige um estado novo. Gancho exige surpresa/tensao, nao uma fala casual. Momento_forte tambem e citacao. '
        'Citas distintas para criterios distintos quando possivel. Motivo: justificativa curta e especifica. '
        'Nao invente personagens/fatos. Responda TODOS os candidate_ids, exatamente uma vez.\n'+
        json.dumps(S.payload(group,segments),ensure_ascii=False))
    return {'model':MODEL,'prompt':prompt,'stream':False,'format':schema,'keep_alive':'5m','think':thinking,
            'options':{'temperature':0,'num_ctx':8192,'num_predict':2200}}

def write_json(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def prepare(out):
    before=shutil.disk_usage('D:/').free
    existing=api('tags')['models']; info=next((m for m in existing if m['name']==MODEL),None)
    download={'model':MODEL,'already_installed':info is not None,'free_before_bytes':before}
    started=time.perf_counter()
    if info is None:
        req=urllib.request.Request('http://127.0.0.1:11434/api/pull',
            data=json.dumps({'model':MODEL,'stream':True}).encode(),headers={'Content-Type':'application/json'})
        last_bucket=None
        with urllib.request.urlopen(req,timeout=180) as response:
            for line in response:
                progress=json.loads(line)
                if 'error' in progress: raise RuntimeError(progress['error'])
                write_json(out/'download_progress.json',progress)
                total=progress.get('total',0); completed=progress.get('completed',0)
                bucket=int(completed/max(1,total)*10) if total else progress.get('status')
                if bucket!=last_bucket:
                    print('DOWNLOAD '+progress.get('status','')+(f' {completed/max(1,total):.0%}' if total else ''),flush=True)
                    last_bucket=bucket
        info=next(m for m in api('tags')['models'] if m['name']==MODEL)
    download.update(seconds=time.perf_counter()-started,installed=info,free_after_bytes=shutil.disk_usage('D:/').free)
    write_json(out/'download.json',download)
    return download

class FileTime(ctypes.Structure):
    _fields_=[('low',ctypes.c_ulong),('high',ctypes.c_ulong)]
    def value(self): return self.low+(self.high<<32)

def cpu_times():
    idle,kernel,user=FileTime(),FileTime(),FileTime()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user))
    return idle.value(),kernel.value()+user.value()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--batch-size',type=int,default=5,choices=range(1,6))
    p.add_argument('--first-id',default=None)
    p.add_argument('--stop-on-invalid-schema',action='store_true')
    args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    raw=args.cache.read_bytes(); cache=json.loads(raw); baseline=json.loads(args.baseline.read_text(encoding='utf-8'))
    if 'Family.Guy.S07E11' not in cache['video_path']: raise ValueError('Only S07E11 allowed')
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=baseline['source_sha256']: raise ValueError('Cache differs from phase 2')
    generated,filtered,rejected=S.candidates(cache)
    manifest={c['candidate_id']:c for c in filtered}
    for old in baseline['evaluated']:
        new=manifest.get(old['candidate_id'])
        if new is None or any(new[k]!=old[k] for k in ('inicio','fim','segment_ids','texto')):
            raise ValueError('Candidate differs from phase 2: '+old['candidate_id'])
    write_json(args.output/'candidates.json',filtered)
    groups=S.batches(filtered,cache['segmentos'],limit=len(set(c['anchor'] for c in filtered)))
    groups=[group[i:i+args.batch_size] for group in groups for i in range(0,len(group),args.batch_size)]
    if args.first_id:
        if args.first_id not in manifest: raise ValueError('Unknown diagnostic candidate id')
        groups.sort(key=lambda group:0 if any(c['candidate_id']==args.first_id for c in group) else 1)
    report={'model':MODEL,'source_sha256':digest,'generated':len(generated),'prefiltered':len(filtered),
            'prefilter_rejections':rejected,'eligible':len(filtered),'planned_requests':len(groups),
            'requests':0,'invalid_json':0,'evaluated':[],'request_metrics':[],'sent_ids':[],
            'batch_size_limit':args.batch_size,
            'first_id':args.first_id,'stop_on_invalid_schema':args.stop_on_invalid_schema,
            'shared_phase2_ids':[c['candidate_id'] for c in baseline['evaluated']],
            'thinking':False,'score_rules':'phase2 unchanged, threshold 55',
            'distinct':[],'duplicate_rejections':[]}
    started=time.perf_counter(); experiment_start=None; stop=threading.Event(); samples=[]; worker=None
    try:
        report['download']=prepare(args.output)
        if api('ps')['models']: raise RuntimeError('Another model is loaded; no automatic unloading of unrelated models')
        metadata=api('show',{'model':MODEL})
        report['thinking_controls']=metadata.get('thinking')
        report['thinking']=thinking_mode(metadata)
        samples.append(resources()); report['resources_before']=samples[0]
        def monitor():
            previous=cpu_times()
            while not stop.is_set():
                try:
                    sample=resources(); current=cpu_times()
                    sample['cpu_percent']=100*(1-(current[0]-previous[0])/max(1,current[1]-previous[1]))
                    previous=current; samples.append(sample)
                except Exception: pass
                stop.wait(1)
        worker=threading.Thread(target=monitor,daemon=True); worker.start()
        t=time.perf_counter()
        health=api('generate',{'model':MODEL,'prompt':'Responda somente OK.','stream':False,
            'think':report['thinking'],'keep_alive':'5m','options':{'num_ctx':8192,'num_predict':256,'temperature':0}},timeout=180)
        write_json(args.output/'health.json',health)
        report['health']={'seconds':time.perf_counter()-t,'load_seconds':health.get('load_duration',0)/1e9,
            'response':health.get('response',''),'running':api('ps')}
        write_json(args.output/'report.json',report)
        if health.get('response','').strip().upper().strip('.!')!='OK':
            raise RuntimeError('Health response did not complete the requested answer; no narrative run')
        print('HEALTH '+json.dumps(report['health'],ensure_ascii=False),flush=True)
        experiment_start=time.perf_counter()
        for n,group in enumerate(groups,1):
            report['requests']+=1; report['sent_ids'].extend(c['candidate_id'] for c in group)
            t=time.perf_counter()
            try:
                response=api('generate',request_payload(group,cache['segmentos'],report['thinking']),timeout=360)
            except Exception as exc:
                report['request_metrics'].append({'ids':[c['candidate_id'] for c in group],
                    'candidates':len(group),'seconds':time.perf_counter()-t,'api_error':str(exc)})
                raise
            write_json(args.output/f'request_{n:02d}.json',response)
            metric={'ids':[c['candidate_id'] for c in group],'candidates':len(group),'seconds':time.perf_counter()-t,
                'prompt_tokens':response.get('prompt_eval_count',0),'output_tokens':response.get('eval_count',0),
                'load_seconds':response.get('load_duration',0)/1e9,'done_reason':response.get('done_reason')}
            try: report['evaluated'].extend(S.validate(response.get('response',''),group))
            except (ValueError,TypeError) as exc:
                report['invalid_json']+=1; metric['rejection']=str(exc)
            report['request_metrics'].append(metric)
            report['total_seconds']=time.perf_counter()-experiment_start
            approved=[c for c in report['evaluated'] if c['aprovada_experimento']]
            report['distinct'],report['duplicate_rejections']=S.deduplicate(approved)
            S.write_report(args.output,report)
            print(f"REQUEST {n}/{len(groups)}: {len(group)} candidates; {len(report['evaluated'])}/{len(filtered)} evaluated; {len(approved)} approved; {len(report['distinct'])} distinct",flush=True)
            if args.stop_on_invalid_schema and 'rejection' in metric:
                report['stopped_error']='Schema failure on minimum batch; no narrative-zero conclusion'
                break
    except Exception as exc:
        report['stopped_error']=f'{type(exc).__name__}: {exc}'; print('STOP '+report['stopped_error'],flush=True)
    finally:
        stop.set()
        if worker: worker.join(6)
        report['elapsed_including_download_health_seconds']=time.perf_counter()-started
        if experiment_start is not None: report['total_seconds']=time.perf_counter()-experiment_start
        report['source_unchanged']=hashlib.sha256(args.cache.read_bytes()).hexdigest()==digest
        report['not_sent_ids']=sorted(set(manifest)-set(report['sent_ids']))
        report['not_evaluated_ids']=sorted(set(manifest)-{c['candidate_id'] for c in report['evaluated']})
        if samples:
            report['resources_after_before_unload']=resources()
            report['resources_peak']={k:max(x.get(k,0) for x in samples) for k in
                ('ram_used_gib','vram_used_mib','gpu_percent','cpu_percent')}
        try:
            api('generate',{'model':MODEL,'keep_alive':0})
            report['resources_after_unload']=resources()
        except Exception as exc: report['unload_error']=str(exc)
        S.write_report(args.output,report)

if __name__=='__main__': main()
