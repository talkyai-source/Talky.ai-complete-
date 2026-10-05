import asyncio,json
from pathlib import Path
from tests.qualification.ag04_native import NativeReplay,_corpus
from scripts.evaluate_ag04_conversations import offline_network_guard
ROOT=Path.cwd().parent

async def run(provider,variant):
 r=NativeReplay({'id':'probe.phone.'+variant,'expect':{},'semantic_ids':[]},provider,_corpus(ROOT))
 states=[]
 async def step(event):
  await r.step(event)
  states.append({'step':event,'phone':r.result()['contacts']['phone']})
 try:
  for event in [
   {'kind':'caller','item':'phone-source','text':'My phone number is +1 415 555 2671.'},
   {'kind':'response','text':'Your phone number is +1 415 555 2671, correct?'},
   {'kind':'caller','item':'old-confirmation','text':'Yes.'},
   {'kind':'caller','item':'phone-correction','text':'Actually, the last four digits should be 1234.'},
   {'kind':'caller','item':'old-confirmation','revision':True,'text':'Yes, that is correct.'},
   {'kind':'caller','item':'stale-yes','text':'Yes.'},
  ]: await step(event)
  if variant=='interrupted':
   r.gateway.config['block']='send'; r.gateway.submissions.clear()
   await step({'kind':'response','text':'Your phone number is +1 415 555 1234, correct?','wait':False})
   await r.step({'kind':'wait_gateway'})
   await step({'kind':'start','item':'new-confirmation'})
   await r.step({'kind':'release'})
   await step({'kind':'caller','item':'new-confirmation','revision':True,'text':'Yes.'})
  else:
   r.gateway.config['receipt']=variant
   r.gateway.playback_evidence='transmitted' if variant=='transmitted' else 'transport_played'
   await step({'kind':'response','text':'Your phone number is +1 415 555 1234, correct?'})
   await step({'kind':'caller','item':'new-confirmation','text':'Yes.'})
  row=r.result()
  return {'provider':provider,'variant':variant,'states':states,'final':row['contacts']['phone'],
   'receipts':row['media']['receipts'],'truncate':row['media']['truncate_events']}
 finally:
  r.gateway.release.set(); await r.bridge.stop(); r.transcripts.clear_buffer(r.call_id)

async def main():
 attempts=[]
 with offline_network_guard(attempts):
  results=[await run(p,v) for p in ('openai','xai') for v in ('completed','stale','unknown','transmitted','interrupted')]
 output={'scope':'actual parser+bridge; synthetic gateway receipts, no DB/provider effects','cases':results,'network_attempts':attempts}
 (ROOT/'tmp/phone-receipts.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
 for r in results: print(r['provider'],r['variant'],r['final']['value'],r['final']['confirmed'],r['final']['confirmation_source'],r['final']['readback'])
asyncio.run(main())
