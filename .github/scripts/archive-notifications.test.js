const test = require('node:test');
const assert = require('node:assert/strict');
const notify = require('./archive-notifications.js');

function fixture(issues = []) {
  const calls = [];
  return {
    calls,
    options: {
      github: {
        paginate: async () => issues,
        rest: { issues: {
          listForRepo: () => {},
          create: async data => calls.push({ method: 'create', ...data }),
          update: async data => calls.push({ method: 'update', ...data }),
        } },
      },
      context: { repo: { owner: 'peckarsky', repo: 'spotify-discover-weekly' }, runId: 123 },
      core: { info: () => {} },
      result: 'failure', dryRun: false,
    },
  };
}

const alert = state => ({ number: 7, state, body: '<!-- discover-weekly:failure-alert -->' });

test('failure creates an assigned alert with a run link and credential recovery instructions', async () => {
  const { calls, options } = fixture();
  await notify(options);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'create');
  assert.deepEqual(calls[0].assignees, ['peckarsky']);
  assert.match(calls[0].body, /actions\/runs\/123/);
  assert.match(calls[0].body, /SPOTIFY_SP_DC/);
  assert.match(calls[0].body, /SPOTIFY_REFRESH_TOKEN/);
});

test('repeated failures do not create duplicate alerts', async () => {
  const { calls, options } = fixture([alert('open')]);
  await notify(options);
  assert.equal(calls.length, 0);
});

test('a new failure reopens the previously resolved alert', async () => {
  const { calls, options } = fixture([alert('closed')]);
  await notify(options);
  assert.equal(calls[0].issue_number, 7);
  assert.equal(calls[0].state, 'open');
});

test('successful archiving closes the alert', async () => {
  const { calls, options } = fixture([alert('open')]);
  await notify({ ...options, result: 'success' });
  assert.equal(calls[0].state, 'closed');
});

test('successful dry runs cannot mask OAuth or playlist-write failures', async () => {
  const { calls, options } = fixture([alert('open')]);
  await notify({ ...options, result: 'success', dryRun: true });
  assert.equal(calls.length, 0);
});

test('skipped and cancelled archive jobs do not resolve alerts', async () => {
  const { calls, options } = fixture([alert('open')]);
  await notify({ ...options, result: 'skipped' });
  await notify({ ...options, result: 'cancelled' });
  assert.equal(calls.length, 0);
});
