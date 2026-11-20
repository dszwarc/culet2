const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/my_jobs_refresh.js'), 'utf8');

function harness() {
    const listeners = {};
    let timers = new Map(), nextId = 1, requests = 0, interacting = false, dialog = false, scanner = false;
    const page = { matches: (value) => value === '.my-jobs-page' };
    const document = {
        visibilityState: 'visible',
        activeElement: { closest: () => interacting },
        addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
        querySelector(selector) { return selector === '.my-jobs-page' ? page : (dialog ? {} : null); },
        getElementById() { return { classList: { contains: () => scanner } }; },
    };
    const emit = (name, detail = {}, target = null) => {
        const event = { detail, target };
        for (const fn of listeners[name] || []) fn(event);
        return event;
    };
    const window = {
        setTimeout(fn, ms) { const id = nextId++; timers.set(id, {fn, ms}); return id; },
        clearTimeout(id) { timers.delete(id); },
        htmx: { trigger(element, name) {
            assert.equal(element, page); assert.equal(name, 'culet:refresh');
            requests++;
            emit('htmx:beforeRequest', {elt: page});
        }},
    };
    vm.runInNewContext(source, {window, document});
    return {
        page, document, emit,
        requestCount: () => requests,
        delays: () => [...timers.values()].map((timer) => timer.ms),
        fire() { const entry = [...timers.entries()][0]; assert.ok(entry); timers.delete(entry[0]); entry[1].fn(); },
        focus(value) { interacting = value; },
        dialog(value) { dialog = value; },
        scanner(value) { scanner = value; },
        beforeSwap() { const detail = {elt: page, shouldSwap: true}; emit('htmx:beforeSwap', detail); return detail.shouldSwap; },
        finish() { emit('htmx:afterRequest', {elt: page}); },
    };
}

test('refreshes at 60 seconds and waits for the request to finish', () => {
    const h = harness();
    assert.deepEqual(h.delays(), [60000]);
    h.fire(); assert.equal(h.requestCount(), 1); assert.deepEqual(h.delays(), []);
    h.beforeSwap(); h.finish(); assert.deepEqual(h.delays(), [60000]);
});

test('hidden tabs stop polling and refresh once on return', () => {
    const h = harness();
    h.document.visibilityState = 'hidden'; h.emit('visibilitychange');
    assert.deepEqual(h.delays(), []); assert.equal(h.requestCount(), 0);
    h.document.visibilityState = 'visible'; h.emit('visibilitychange');
    assert.deepEqual(h.delays(), [0]); h.fire();
    assert.equal(h.requestCount(), 1); assert.deepEqual(h.delays(), []);
    h.beforeSwap(); h.finish(); assert.deepEqual(h.delays(), [60000]);
});

test('interaction, an open dialog, and scanner defer requests', () => {
    for (const set of ['focus', 'dialog', 'scanner']) {
        const h = harness(); h[set](true); h.fire();
        assert.equal(h.requestCount(), 0); assert.deepEqual(h.delays(), [1000]);
        h[set](false); h.fire(); assert.equal(h.requestCount(), 1);
    }
});

test('interaction that starts during a request cancels its swap and retries', () => {
    const h = harness(); h.fire(); h.focus(true);
    assert.equal(h.beforeSwap(), false); h.finish();
    assert.deepEqual(h.delays(), [1000]); h.focus(false); h.fire();
    assert.equal(h.requestCount(), 2);
});

test('returning while a request is in flight does not duplicate it', () => {
    const h = harness(); h.fire();
    h.document.visibilityState = 'hidden'; h.emit('visibilitychange');
    h.document.visibilityState = 'visible'; h.emit('visibilitychange');
    assert.deepEqual(h.delays(), []); assert.equal(h.requestCount(), 1);
    assert.equal(h.beforeSwap(), true); h.finish();
    assert.deepEqual(h.delays(), [60000]); assert.equal(h.requestCount(), 1);
});

test('a response arriving while hidden is discarded and refreshed on return', () => {
    const h = harness(); h.fire();
    h.document.visibilityState = 'hidden'; h.emit('visibilitychange');
    assert.equal(h.beforeSwap(), false); h.finish(); assert.deepEqual(h.delays(), []);
    h.document.visibilityState = 'visible'; h.emit('visibilitychange'); h.fire();
    assert.equal(h.requestCount(), 2);
});


test('an edited form remains protected after focus leaves until reset', () => {
    const h = harness();
    const form = {isConnected: true};
    h.emit('input', {}, {closest: () => form});
    h.fire(); assert.equal(h.requestCount(), 0); assert.deepEqual(h.delays(), [1000]);
    h.emit('reset', {}, form); h.fire(); assert.equal(h.requestCount(), 1);
});
