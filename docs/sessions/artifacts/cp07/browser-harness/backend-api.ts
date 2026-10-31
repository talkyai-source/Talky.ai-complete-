// Synthetic event source only; the real event-stream hook and components are bundled.
export const backendApi = {events:{list:async (_options:unknown, signal:AbortSignal) => {
  if (signal?.aborted) throw new DOMException('Account changed','AbortError');
  const source = (window as any).__cp07;
  source.eventReads++;
  const account = source.responseOwner === 'other' ? (source.identity === 'A' ? 'B' : 'A') : source.identity;
  const owner = source.identities[account];
  const result = {items: source.events[source.identity] || [], next_cursor:null,
    ...(source.responseOwner === 'missing' ? {} : {tenant_id:owner?.tenantId,user_id:owner?.userId})};
  if (source.holdNextRead) {
    source.holdNextRead = false;
    return new Promise(resolve => { source.pendingRead = () => { resolve(result); source.pendingRead = null; }; });
  }
  return result;
}}};
