import assert from 'node:assert/strict';
import test from 'node:test';
import { useState } from 'react';
import { C, el, act, dom, state, render, response, find, click, settle } from './bh-harness.mjs';

// Sign-off 1.3: Escape closes the call details drawer and focus returns to the
// control that opened it; the delete confirmation keeps its own Escape.
const page = (items) => ({ items, page: 1, page_size: 20, total: items.length });
const detail = (id) => ({ id, tenant_id: `tenant-${id}`, tenant_name: id, phone_number: id,
    campaign_id: null, campaign_name: null, status: 'completed', duration_seconds: 5,
    timeline: [], created_at: '2026-10-01T00:00:00Z' });
const recording = (id) => ({ id: `recording-${id}`, call_id: id, tenant_id: `tenant-${id}`,
    status: 'ready', mime_type: 'audio/mpeg', storage: 'local', duration_seconds: 5,
    created_at: '2026-10-01T00:00:00Z', playable: true, legal_hold: false });
const route = (request) => {
    if (request.path.startsWith('/api/v1/admin/calls/')) return response(detail(request.path.split('/').at(-1)));
    const query = new URL(request.path, 'https://example.invalid');
    const id = query.searchParams.get('call_id');
    if (query.pathname === '/api/v1/admin/recordings') return response(page([recording(id)]));
    if (query.pathname === '/api/v1/admin/feedback') return response(page([]));
    return response({ detail: 'unrouted' }, 500);
};
const escape = async () => {
    await act(async () => document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    await settle();
};
const drawerOpen = () => find('.call-detail-drawer') !== null;

// A page that opens the drawer from a button, as the call tables do from a row.
function Page({ closes }) {
    const [id, setId] = useState(null);
    return el('div', null,
        el('button', { type: 'button', id: 'opener', onClick: () => setId('A') }, 'Open call'),
        el(C.CallDetailDrawer, { callId: id, onClose: () => { closes.count += 1; setId(null); } }));
}

async function open(closes) {
    state.route = route;
    await render(el(Page, { closes }));
    const opener = find('#opener');
    await act(async () => { opener.focus(); opener.click(); });
    await settle();
    assert.equal(drawerOpen(), true);
    return opener;
}

test('Escape closes the call details drawer and returns focus to the control that opened it', async () => {
    const closes = { count: 0 };
    const opener = await open(closes);
    await escape();
    assert.equal(closes.count, 1);
    assert.equal(drawerOpen(), false);
    assert.equal(document.activeElement === opener, true);
});

test('the Close call details button also returns focus to the control that opened it', async () => {
    const closes = { count: 0 };
    const opener = await open(closes);
    await act(async () => find('.drawer-close').click());
    await settle();
    assert.equal(drawerOpen(), false);
    assert.equal(document.activeElement === opener, true);
});

test('Escape in the delete confirmation closes the confirmation only, then a second Escape closes the drawer', async () => {
    const closes = { count: 0 };
    await open(closes);
    const tab = [...state.host.querySelectorAll('[role="tab"]')].find((item) => item.textContent.includes('Recordings'));
    assert.equal(tab !== undefined, true);
    await act(async () => tab.click());
    await click('Delete');
    assert.equal(document.body.textContent.includes('Delete permanently'), true);
    await escape();
    assert.equal(document.body.textContent.includes('Delete permanently'), false);
    assert.equal(closes.count, 0);
    assert.equal(drawerOpen(), true);
    await escape();
    assert.equal(closes.count, 1);
    assert.equal(drawerOpen(), false);
});
