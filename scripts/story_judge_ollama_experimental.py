"""Explicit offline-cache experiment: deterministic candidates, local Ollama judge."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.story_miner_ollama_experimental import api, resources

MODEL='qwen2.5:3b'
WEIGHTS={'conflito':20,'setup':15,'escalada':15,'payoff':20,'gancho_potencial':15,'mudanca':15}
TEXT_FIELDS=tuple(WEIGHTS)+('momento_forte','motivo')
ITEM={'type':'object','properties':{
    'candidate_id':{'type':'string'},'tem_historia':{'type':'boolean'},
    'presentes':{'type':'object','properties':{k:{'type':'boolean'} for k in WEIGHTS},
                'required':list(WEIGHTS),'additionalProperties':False},
    **{k:{'type':'string','maxLength':70} for k in TEXT_FIELDS}},
    'required':['candidate_id','tem_historia','presentes',*TEXT_FIELDS],'additionalProperties':False}

def norm(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text).casefold()).strip()

def candidates(cache):
    segments=cache['segmentos']; generated=[]; accepted=[]; rejects=[]; seen=set(); texts=set()
    for anchor in range(0,int(cache['duracao']),30):
        available=[i for i,s in enumerate(segments) if s['inicio']>=anchor]
        if not available: continue
        first=available[0]; start=segments[first]['inicio']
        # Prefer an existing pause or speaker change within 5s of each target end.
        for target in (30,45,60,90,120):
            possible=[i for i in available if 20<=segments[i]['fim']-start<=150]
            if not possible: continue
            def cost(i):
                distance=abs(segments[i]['fim']-start-target)
                if distance>5: return distance
                next_s=segments[i+1] if i+1<len(segments) else None
                pause=next_s['inicio']-segments[i]['fim'] if next_s else 2
                speaker=bool(next_s and segments[i].get('speaker') and next_s.get('speaker') and
                             segments[i]['speaker']!=next_s['speaker'])
                return distance-min(3,max(0,pause)*1.5)-(1 if speaker else 0)
            last=min(possible,key=cost); end=segments[last]['fim']; ids=list(range(first,last+1))
            c={'candidate_id':f'C{anchor:04d}_{target:03d}','anchor':anchor,'target':target,
               'inicio':start,'fim':end,'duracao':end-start,'segment_ids':ids,
               'texto':' '.join(segments[i]['texto'] for i in ids)}
            generated.append(c)
            key=(start,end); words=len(c['texto'].split()); dialogue=sum(segments[i]['fim']-segments[i]['inicio'] for i in ids)
            reason=None
            if not 20<=end-start<=150: reason='duracao invalida'
            elif words<20 or dialogue/(end-start)<.15: reason='quase sem dialogo'
            elif key in seen or norm(c['texto']) in texts: reason='duplicata temporal/textual'
            if reason: rejects.append({'candidate_id':c['candidate_id'],'reason':reason})
            else:
                seen.add(key); texts.add(norm(c['texto'])); accepted.append(c)
    return generated,accepted,rejects

def payload(group,segments):
    ids=sorted(set(i for c in group for i in c['segment_ids']))
    return {'candidatos':[{k:v for k,v in c.items() if k not in ('texto','anchor','target')} for c in group],
            'falas':[{ 'id':i,'inicio':segments[i]['inicio'],'fim':segments[i]['fim'],
                       'texto':segments[i]['texto']} for i in ids]}

def batches(items,segments,limit=15):
    anchors=sorted(set(c['anchor'] for c in items))
    # Explicit stratified request budget, not a narrative-quality rejection.
    chosen=sorted(set(anchors[round(i*(len(anchors)-1)/(min(limit,len(anchors))-1))]
                      for i in range(min(limit,len(anchors))))) if len(anchors)>1 else anchors
    groups=[]
    for anchor in chosen:
        group=[]
        for c in [x for x in items if x['anchor']==anchor]:
            trial=group+[c]
            # Conservative token estimate; reserve 1800 tokens for instructions/output.
            if group and len(json.dumps(payload(trial,segments),ensure_ascii=False))/2.5>4200:
                groups.append(group); group=[]
            group.append(c)
        if group: groups.append(group)
    return groups

def validate(raw,group):
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'avaliacoes'} or not isinstance(data['avaliacoes'],list):
        raise ValueError('invalid root schema')
    expected={c['candidate_id']:c for c in group}; seen=set(); results=[]
    for item in data['avaliacoes']:
        if not isinstance(item,dict) or set(item)!=set(ITEM['required']): raise ValueError('invalid item schema')
        cid=item['candidate_id']
        if not isinstance(cid,str) or cid not in expected or cid in seen: raise ValueError('invalid/duplicate candidate id')
        if type(item['tem_historia']) is not bool: raise ValueError('invalid history boolean')
        flags=item['presentes']
        if not isinstance(flags,dict) or set(flags)!=set(WEIGHTS) or any(type(v) is not bool for v in flags.values()):
            raise ValueError('invalid criterion booleans')
        if any(not isinstance(item[k],str) or len(item[k])>70 for k in TEXT_FIELDS):
            raise ValueError('invalid/oversized text')
        source=expected[cid]; text=norm(source['texto']); criteria={}; issues=[]; used=set()
        for k,weight in WEIGHTS.items():
            evidence=norm(item[k])
            present=flags[k] and len(evidence)>=8 and evidence in text and evidence not in used
            criteria[k]=weight if present else 0
            if present: used.add(evidence)
            if flags[k] and not present: issues.append(k+': evidencia ausente, repetida ou nao encontrada')
        score=sum(criteria.values())
        complete=all(criteria[k]>0 for k in ('conflito','setup','escalada','payoff'))
        approved=(item['tem_historia'] and score>=55 and criteria['conflito']>0 and criteria['setup']>0 and
                  (criteria['escalada']>0 or criteria['mudanca']>0))
        results.append({**source,**item,'score_historia':score,'score_criterios':criteria,
                        'aprovada_experimento':approved,'arco_completo':complete,'avisos':issues})
        seen.add(cid)
    if seen!=set(expected): raise ValueError('missing candidate ids')
    return results

def deduplicate(items):
    ranked=sorted(items,key=lambda x:(x['arco_completo'],x['score_criterios']['payoff'],
                                      x['score_historia'],-x['duracao']),reverse=True)
    kept=[]; rejected=[]
    for c in ranked:
        duplicate=None
        for old in kept:
            overlap=max(0,min(c['fim'],old['fim'])-max(c['inicio'],old['inicio']))
            coverage=overlap/min(c['duracao'],old['duracao'])
            a=set(re.findall(r'\w+',norm(c['conflito']+' '+c['setup'])))
            b=set(re.findall(r'\w+',norm(old['conflito']+' '+old['setup'])))
            similarity=len(a&b)/max(1,len(a|b))
            if coverage>=.65 and similarity>=.4:
                duplicate=old['candidate_id']; break
        if duplicate: rejected.append({'candidate_id':c['candidate_id'],'kept':duplicate})
        else: kept.append(c)
    return kept,rejected

def write_report(out,report):
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Story Judge local - experimento S07E11','',
           'Candidatas experimentais, nao remixes aprovados. Citas sao evidencia textual, nao garantia de interpretacao.', '']
    for i,c in enumerate(report.get('distinct',[])[:15],1):
        lines.extend([f"## #{i} {c['candidate_id']} | {c['inicio']:.2f}-{c['fim']:.2f}s | {c['duracao']:.2f}s | score local {c['score_historia']}"])
        for k in ('conflito','setup','escalada','payoff','gancho_potencial','mudanca','momento_forte','motivo'):
            lines.append(f"- {k}: {c[k] or '(ausente)'}")
        lines.append('')
    (out/'oportunidades.md').write_text('\n'.join(lines),encoding='utf-8')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); raw=args.cache.read_bytes(); cache=json.loads(raw)
    if 'Family.Guy.S07E11' not in cache['video_path']: raise ValueError('Only S07E11 is allowed')
    args.output.mkdir(parents=True,exist_ok=True)
    generated,filtered,rejected=candidates(cache); groups=batches(filtered,cache['segmentos'])
    report={'source_sha256':hashlib.sha256(raw).hexdigest(),'generated':len(generated),'prefiltered':len(filtered),
            'prefilter_rejections':rejected,'sent':sum(map(len,groups)), 'not_sent_budget':len(filtered)-sum(map(len,groups)),
            'requests':0,'invalid_json':0,'request_metrics':[],'evaluated':[],'resources_before':resources(),
            'local_approval_threshold':55,'complete_arc_criteria':['conflito','setup','escalada','payoff']}
    started=time.perf_counter()
    try:
        for n,group in enumerate(groups,1):
            ids=[c['candidate_id'] for c in group]
            schema={'type':'object','properties':{'avaliacoes':{'type':'array','minItems':len(group),
                'maxItems':len(group),'items':ITEM}},'required':['avaliacoes'],'additionalProperties':False}
            schema['properties']['avaliacoes']['items']=json.loads(json.dumps(ITEM))
            schema['properties']['avaliacoes']['items']['properties']['candidate_id']['enum']=ids
            prompt=('Julgue os blocos fixos. NAO descubra timestamps, NAO escolha falas isoladas, NAO escreva scores. '
                'Cada candidato inclui TODAS as falas identificadas em segment_ids; a tabela falas e compartilhada apenas para evitar repeticao. '
                'Existe historia se ha situacao compreensivel, conflito/objetivo e mudanca; cenas sem arco devem ser false. '
                'Seja conciso: citacoes de 3-10 palavras, no maximo 70 caracteres. Para cada criterio marque presente somente quando houver evidencia narrativa real. Nos campos conflito, setup, escalada, '
                'payoff, gancho_potencial e mudanca copie UMA citacao exata curta do texto que prova o criterio; use "" se ausente. '
                'Payoff precisa ser consequencia/resolucao, nao repetir setup. Escalada precisa desenvolver o conflito. '
                'Mudanca exige um estado novo. Gancho exige surpresa/tensao, nao uma fala casual. Momento_forte tambem e citacao. '
                'Citas distintas para criterios distintos quando possivel. Motivo: justificativa curta e especifica. '
                'Nao invente personagens/fatos. Responda TODOS os candidate_ids, exatamente uma vez.\n'+
                json.dumps(payload(group,cache['segmentos']),ensure_ascii=False))
            report['requests']+=1; t=time.perf_counter()
            response=api('generate',{'model':MODEL,'prompt':prompt,'stream':False,'format':schema,'keep_alive':'5m',
                'options':{'temperature':0,'num_ctx':8192,'num_predict':2200}},timeout=120)
            (args.output/f'request_{n:02d}.json').write_text(json.dumps(response,ensure_ascii=False,indent=2),encoding='utf-8')
            metric={'ids':ids,'seconds':time.perf_counter()-t,'estimated_prompt_tokens':len(prompt)/2.5,
                    'prompt_tokens':response.get('prompt_eval_count',0),'output_tokens':response.get('eval_count',0),
                    'load_seconds':response.get('load_duration',0)/1e9,'done_reason':response.get('done_reason')}
            try: report['evaluated'].extend(validate(response.get('response',''),group))
            except (ValueError,TypeError) as exc:
                report['invalid_json']+=1; metric['rejection']=str(exc)
            report['request_metrics'].append(metric)
            report['total_seconds']=time.perf_counter()-started
            approved=[c for c in report['evaluated'] if c['aprovada_experimento']]
            report['distinct'],report['duplicate_rejections']=deduplicate(approved)
            write_report(args.output,report)
            print(f"REQUEST {n}/{len(groups)}: {len(group)} candidates; {len(approved)} approved; {len(report['distinct'])} distinct",flush=True)
    except Exception as exc:
        report['stopped_error']=f'{type(exc).__name__}: {exc}'; print(report['stopped_error'],flush=True)
    finally:
        report['total_seconds']=time.perf_counter()-started; report['resources_after']=resources()
        report['source_unchanged']=hashlib.sha256(args.cache.read_bytes()).hexdigest()==report['source_sha256']
        write_report(args.output,report)
        try: api('generate',{'model':MODEL,'keep_alive':0})
        except Exception: pass

if __name__=='__main__': main()
