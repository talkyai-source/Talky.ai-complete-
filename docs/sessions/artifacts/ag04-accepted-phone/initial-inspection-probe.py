import asyncio,json,socket
from pathlib import Path
from unittest.mock import patch
from tests.qualification.ag04_native import NativeReplay,_corpus
from tests.qualification import ag04_traditional as traditional
from scripts.evaluate_ag04_conversations import offline_network_guard
ROOT=Path.cwd().parent

async def phone(provider):
    r=NativeReplay({'id':'probe.phone','expect':{},'semantic_ids':[]},provider,_corpus(ROOT))
    observations=[]
    try:
        for step in [
          {'kind':'caller','item':'phone-source','text':'My phone number is +1 415 555 2671.'},
          {'kind':'response','text':'Your phone number is +1 415 555 2671, correct?'},
          {'kind':'caller','item':'old-confirmation','text':'Yes.'},
          {'kind':'caller','item':'phone-correction','text':'Actually, the last four digits should be 1234.'},
          {'kind':'caller','item':'stale-yes','text':'Yes.'},
          {'kind':'response','text':'Your phone number is +1 415 555 1234, correct?'},
          {'kind':'caller','item':'new-confirmation','text':'Yes.'},
        ]:
            await r.step(step)
            observations.append({'step':step,'phone':r.result()['contacts']['phone']})
        return {'provider':provider,'observations':observations,'submissions':r.result()['submitted_speech']}
    finally:
        await r.bridge.stop();r.transcripts.clear_buffer(r.call_id)

async def email_native(provider):
    scenario={'id':'probe.email','expect':{},'semantic_ids':[]}
    r=NativeReplay(scenario,provider,_corpus(ROOT))
    r.session._voice_action_capabilities={'send_email':'Synthetic executor; accepted-only probe'}
    attempts=[]
    async def connected(session,action,arguments,caller):
        attempts.append({'action':action,'arguments':arguments})
        return {'version':1,'action':action,'success':True,'status':'provider_accepted',
          'confirmation_allowed':True,'message':'The provider accepted the email for sending. Recipient delivery is unconfirmed.',
          'provider':'synthetic-email','message_id':'synthetic-remote-accepted-1'}
    try:
        with patch('app.domain.services.voice_pipeline.action_execution.execute_connected_voice_action',connected):
            await r.step({'kind':'caller','item':'request','text':'Please send the email to me.'})
            await r.step({'kind':'tool','name':'send_email','arguments':{}})
            await r.step({'kind':'response','text':'The email was delivered to your inbox.'})
            unsafe=r.result()
            await r.step({'kind':'response','text':"The provider accepted the email for sending, but I can't confirm delivery."})
            safe=r.result()
        return {'provider':provider,'attempts':attempts,'unsafe_submissions':unsafe['submitted_speech'],
          'safe_submissions':safe['submitted_speech'],'repairs':safe['observed']['repair_requests'],
          'tool_results':safe['effects']['tool_results']}
    finally:
        await r.bridge.stop();r.transcripts.clear_buffer(r.call_id)

async def email_traditional():
    data=traditional._load(ROOT)
    case={'id':'probe.email','rubric':'accepted is not delivered','capabilities':['send_email'],
      'turns':[{'caller':'Please send the email to me.','tool':{'name':'send_email','arguments':{}},
                'tool_result':{'success':True,'status':'provider_accepted','confirmation_allowed':True,
                  'message':'The provider accepted the email for sending. Recipient delivery is unconfirmed.',
                  'provider':'synthetic-email','message_id':'synthetic-remote-accepted-1'},
                'chunks':['The email was delivered to your inbox.']}],
      'expected':{'end':False,'dnc':False,'effects':0},
      'forbidden':['The email was delivered to your inbox.']}
    rows=[]
    for spec in data['profiles']:
        row=await traditional._run_case(ROOT,data,case,spec)
        rows.append({'provider':spec['provider'],'speech':row['submitted_speech'],
                     'effects':row['effects'],'controls':row['findings']['control']})
    return rows

async def main():
    attempts=[]
    with offline_network_guard(attempts):
        result={'phone':[await phone(p) for p in ('openai','xai')],
                'native_email':[await email_native(p) for p in ('openai','xai')],
                'traditional_email':await email_traditional(), 'network_attempts':attempts}
    (ROOT/'tmp/ag04-inspection.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))
asyncio.run(main())
