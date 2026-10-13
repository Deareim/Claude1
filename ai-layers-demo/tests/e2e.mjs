// End-to-end checks for the demo page (index.html). No build step, no dependencies in the repo.
// Run:  PLAYWRIGHT_MODULE=$(npm root -g)/playwright/index.mjs node ai-layers-demo/tests/e2e.mjs
// Optional: CHROMIUM_PATH=/path/to/chromium   AXE_PATH=/path/to/axe-core/axe.min.js (adds an a11y audit)
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const here = path.dirname(fileURLToPath(import.meta.url));
const PAGE = pathToFileURL(path.join(here, '..', 'index.html')).href;
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});

let failed = 0, passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log(`ok   ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n     ${String(e.message).split('\n').join('\n     ')}`); }
}
const assert = (cond, msg) => { if (!cond) throw new Error(msg); };

async function open(opts = {}, hash = '') {
  const ctx = await browser.newContext({ viewport: { width: 1360, height: 1000 }, reducedMotion: 'reduce', ...opts });
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/fonts\.g/.test(m.text())) page.errors.push(m.text()); });
  await page.goto(PAGE + hash);
  return page;
}
// Expected outcome per layer mix, from the case facts (not copied from the app code).
const expectFixed = ({ agents: a, data: d, checks: c }) =>
  a === 0 || (c && !d) ? '0 of 2' : !d ? `${a} done, ${a} wrong` : a === 1 ? '1 of 2' : '2 of 2';
const expectTime = ({ agents: a, data: d, checks: c }) =>
  a === 0 || (c && !d) ? '≈ 45 min' : a === 1 ? '≈ 30 min' : '≈ 40 sec';
const tilesDone = page => page.waitForFunction(() => [...document.querySelectorAll('#tiles .v')].length === 4 && ![...document.querySelectorAll('#tiles .v')].some(v => v.textContent === '–'));
const tileVals = page => page.$$eval('#tiles .tile', ts => ts.map(t => ({ k: t.querySelector('.k').textContent, v: t.querySelector('.v').textContent, s: t.textContent })));
async function settle(page, decision = 'ok') {
  await page.waitForFunction(() => document.querySelector('.gate .ok') || ([...document.querySelectorAll('#tiles .v')].length === 4 && ![...document.querySelectorAll('#tiles .v')].some(v => v.textContent === '–')));
  if (await page.$('.gate .ok')) await page.click(`.gate .${decision}`);
  await tilesDone(page);
}
async function setFree(page, c) {
  await page.click(`[data-agents="${c.agents}"]`);
  await page.click(`[data-data="${c.data ? 1 : 0}"]`);
  for (const g of ['ids', 'checks']) {
    const on = (await page.getAttribute(`[data-g="${g}"]`, 'aria-pressed')) === 'true';
    if (on !== c[g]) await page.click(`[data-g="${g}"]`);
  }
}

await test('loads without errors, with the expected title', async () => {
  const page = await open();
  assert((await page.title()) === 'One Complaint, Six Ways', 'title');
  assert(page.errors.length === 0, 'errors: ' + page.errors.join('; '));
  await page.context().close();
});

await test('story: every step renders and completes', async () => {
  const page = await open();
  const labels = await page.$$eval('#steps button', bs => bs.map(b => b.textContent));
  assert(labels.length === 9, 'expected 9 steps, got ' + labels.length);
  for (let i = 1; i < labels.length; i++) {
    await page.click('#next');
    const label = labels[i];
    if (label === 'Build it') {
      await page.click('#bdesc'); await page.click('#bcreate', { timeout: 5000 });
      assert(await page.isVisible('text=Billing agent is live.'), 'builder did not finish');
    } else if (label === 'Side by side') {
      const rows = await page.$$eval('#recap tbody tr', r => r.length);
      assert(rows === 6, 'recap rows ' + rows);
    } else {
      await settle(page);
      const you = await page.$$eval('#chat .msg.you', m => m.length);
      assert(you === 1, `${label}: expected 1 user message, got ${you}`);
    }
  }
  assert(await page.isDisabled('#next'), 'Next should be disabled on the last step');
  const rows = await page.$$eval('#recap tbody tr', rs => rs.map(r => [...r.querySelectorAll('td .c')].map(c => c.textContent)));
  const want = [['Guessed', '0 of 2', '≈ 45 min', 'Medium'], ['Guessed', '1 done, 1 wrong', '≈ 30 min', 'High'], ['Guessed', '2 done, 2 wrong', '≈ 40 sec', 'High'],
    ['Right', '2 of 2', '≈ 40 sec', 'High'], ['Right', '2 of 2', '≈ 40 sec', 'Medium'], ['Right', '2 of 2', '≈ 40 sec', 'Under control']];
  assert(JSON.stringify(rows) === JSON.stringify(want), 'recap values:\n' + JSON.stringify(rows));
  const heads = await page.$$eval('#recap thead th', t => t.map(x => x.textContent));
  assert(heads.includes('Time to resolve'), 'recap header: ' + heads.join(', '));
  assert(page.errors.length === 0, 'errors: ' + page.errors.join('; '));
  await page.context().close();
});

await test('free play: all 24 layer mixes keep their invariants', async () => {
  const page = await open({}, '#free');
  const problems = [];
  for (const agents of [0, 1, 2]) for (const data of [false, true]) for (const ids of [false, true]) for (const checks of [false, true]) {
    const c = { agents, data, ids, checks }, tag = JSON.stringify(c);
    await setFree(page, c); await settle(page);
    const text = await page.$eval('#app', e => e.innerText);
    if (/undefined|NaN|\[object/.test(text)) problems.push(`${tag}: bad text`);
    const t = await tileVals(page);
    const facts = t[0].v, risk = t[3].v;
    const wantFacts = data ? 'Right' : checks ? 'Not guessed' : 'Guessed';
    if (facts !== wantFacts) problems.push(`${tag}: facts ${facts} != ${wantFacts}`);
    if (t[1].v !== expectFixed(c)) problems.push(`${tag}: problems fixed ${t[1].v} != ${expectFixed(c)}`);
    if (t[2].v !== expectTime(c)) problems.push(`${tag}: time ${t[2].v} != ${expectTime(c)}`);
    if (t[2].k !== 'Time to resolve') problems.push(`${tag}: time tile label ${t[2].k}`);
    const work = await page.$eval('#work', e => e.innerText);
    const statuses = await page.$$eval('#work .st', s => s.map(x => x.className));
    const gate = /approval needed/i.test(work);
    if (gate !== (agents >= 1 && data && checks)) problems.push(`${tag}: approval step ${gate}`);
    if (agents >= 1 && /Denied: its ID/.test(work) !== ids) problems.push(`${tag}: discount denial should follow agent IDs`);
    if (agents >= 1 && /Sets 15% off/.test(work) === ids) problems.push(`${tag}: discount given should follow missing agent IDs`);
    if (agents >= 1 && !ids && !/shared admin login/.test(work)) problems.push(`${tag}: missing shared-login warning`);
    if (/Audit log/i.test(work) !== checks) problems.push(`${tag}: audit log should follow live checks`);
    if (statuses.some(s => /\b(risk|wrong)\b/.test(s)) && risk === 'Under control') problems.push(`${tag}: risky step but risk tile says Under control`);
    if (!checks && agents >= 1 && risk === 'Under control') problems.push(`${tag}: agents without live checks cannot be Under control`);
    const you = await page.$$eval('#chat .msg.you', m => m.length);
    if (you !== 1) problems.push(`${tag}: ${you} user messages`);
  }
  assert(problems.length === 0, problems.join('\n'));
  assert(page.errors.length === 0, 'errors: ' + page.errors.join('; '));
  await page.context().close();
});

await test('live checks: a rejected credit shows in the work log, email, chat, tile and recap', async () => {
  const story = await open();
  for (let i = 0; i < 7; i++) { await story.click('#next'); if (await story.$('#bdesc')) { await story.click('#bdesc'); await story.click('#bcreate'); } }
  await settle(story, 'no');
  const reply = await story.$eval('#work', e => [...e.querySelectorAll('.row')].find(r => /^Reply agent/i.test(r.querySelector('.actor')?.textContent || '')).innerText);
  assert(!/credit €1,500/.test(reply) && /billing will confirm/.test(reply), 'reply email ignores the rejection: ' + reply);
  const chat = await story.$eval('#chat', e => e.innerText);
  assert(/credit you stopped/.test(chat), 'assistant summary ignores the rejection');
  await story.click('#next');
  const last = await story.$$eval('#recap tbody tr:last-child td .c', c => c.map(x => x.textContent));
  assert(last[1] === '1 of 2', 'recap ignores the rejection: ' + last.join(' | '));
  await story.context().close();
  for (const agents of [1, 2]) {
    const page = await open({}, '#free');
    await setFree(page, { agents, data: true, ids: true, checks: true });
    await settle(page, 'no');
    const work = await page.$eval('#work', e => e.innerText);
    assert(/Credit stopped by you/.test(work), `agents=${agents}: no 'Credit stopped' row`);
    const fixed = (await tileVals(page))[1];
    assert(/stopped/i.test(fixed.s), `agents=${agents}: 'Problems fixed' tile does not mention the stopped credit: "${fixed.s}"`);
    await page.context().close();
  }
});

await test('under the hood: Off / Tech / SAP show the right labels', async () => {
  const page = await open();
  await page.click('#next'); await settle(page);
  const vis = sel => page.$$eval(sel, els => els.filter(e => e.offsetParent !== null).length);
  assert((await vis('.sap')) === 0 && (await vis('.tech')) === 0, 'Off should show neither');
  await page.click('[data-view="tech"]');
  assert((await vis('.tech')) > 0 && (await vis('.sap')) === 0, 'Tech should show only tech');
  await page.click('[data-view="sap"]');
  assert((await vis('.sap')) > 0 && (await vis('.tech')) === 0, 'SAP should show only SAP');
  const sapText = await page.$$eval('.sap', e => e.map(x => x.textContent).join('|'));
  for (const name of ['Joule', 'Business Data Cloud', 'Identity, Access & Security', 'AI Agent Hub']) assert(sapText.includes(name), 'missing SAP label ' + name);
  await page.context().close();
});

await test('neutral by default: no SAP wording visible before the SAP view is chosen', async () => {
  const page = await open();
  for (let i = 0; i < 8; i++) {
    await page.click('#next');
    if (await page.$('#bdesc')) { await page.click('#bdesc'); await page.click('#bcreate'); }
    else if (await page.isVisible('#runview') && await page.$eval('#cfg', e => e.textContent)) await settle(page);
    const text = await page.$eval('#app', e => e.innerText);
    assert(!/\bSAP\b|Joule/.test(text.replace(/Under the hood\s*Off\s*Tech\s*SAP/, '')), `step ${i + 1} shows SAP wording`);
  }
  await page.context().close();
});

await test('keyboard: arrow keys move through the story', async () => {
  const page = await open();
  await page.keyboard.press('ArrowRight');
  assert((await page.$eval('[aria-current="step"]', b => b.textContent)) === 'Assistant', 'ArrowRight');
  await page.keyboard.press('ArrowLeft');
  assert((await page.$eval('[aria-current="step"]', b => b.textContent)) === 'The case', 'ArrowLeft');
  await page.context().close();
});

await test('fast clicking: only the last step\'s run is shown', async () => {
  const page = await open({ reducedMotion: 'no-preference' });
  for (let i = 0; i < 4; i++) await page.click('#next');
  await page.waitForTimeout(300);
  await page.click('#next'); await page.click('#next');
  await page.waitForTimeout(9000);
  const you = await page.$$eval('#chat .msg.you', m => m.length);
  assert(you === 1, 'user messages: ' + you);
  assert((await page.$eval('#cfg', e => e.textContent)) === 'Assistant + agent team + company data + agent IDs', 'cfg mismatch');
  assert(page.errors.length === 0, 'errors: ' + page.errors.join('; '));
  await page.context().close();
});

await test('builder: leaving mid-typing and coming back resets it', async () => {
  const page = await open({ reducedMotion: 'no-preference' });
  await page.click('#next'); await page.click('#next');
  await page.click('#bdesc'); await page.waitForTimeout(400);
  await page.click('#next'); await page.waitForTimeout(1500);
  assert(await page.isHidden('#builder'), 'builder still visible on next step');
  await page.click('#prev'); await page.waitForTimeout(3000);
  assert(await page.isVisible('#bdesc'), 'builder not reset');
  assert(page.errors.length === 0, 'errors: ' + page.errors.join('; '));
  await page.context().close();
});

for (const width of [400, 768, 1280]) {
  await test(`layout at ${width}px: no sideways scroll, Next button reachable`, async () => {
    const page = await open({ viewport: { width, height: 800 } });
    const issues = [];
    for (let i = 0; i < 9; i++) {
      if (i) await page.click('#next');
      if (await page.$('#bdesc')) { await page.click('#bdesc'); await page.click('#bcreate'); }
      else if (await page.isVisible('#runview') && await page.$eval('#cfg', e => e.textContent)) await settle(page);
      const m = await page.evaluate(() => {
        const n = document.querySelector('#next').getBoundingClientRect();
        return { sw: document.documentElement.scrollWidth, iw: innerWidth, nextRight: n.right, nextLeft: n.left };
      });
      if (m.sw > m.iw) issues.push(`step ${i}: page ${m.sw}px wide`);
      if (m.nextRight > m.iw || m.nextLeft < 0) issues.push(`step ${i}: Next button off screen (${Math.round(m.nextLeft)}-${Math.round(m.nextRight)})`);
    }
    assert(issues.length === 0, issues.join('\n'));
    await page.context().close();
  });
}

await test('phone: the page uses the device width', async () => {
  const page = await open({ viewport: { width: 390, height: 800 }, isMobile: true, hasTouch: true });
  assert((await page.evaluate(() => innerWidth)) === 390, 'innerWidth ' + (await page.evaluate(() => innerWidth)));
  await page.context().close();
});

await test('switched-off layers look switched off', async () => {
  const page = await open();
  await page.click('#next');
  const c = await page.evaluate(() => {
    const muted = getComputedStyle(document.querySelector('.casefile dt')).color;
    const off = getComputedStyle(document.querySelector('#b-build .band-name')).color;
    const on = getComputedStyle(document.querySelector('#b-engage .band-name')).color;
    return { muted, off, on };
  });
  assert(c.off === c.muted && c.on !== c.muted, JSON.stringify(c));
  await page.context().close();
});

await test('dark theme: page paints its own dark background', async () => {
  const page = await open({ colorScheme: 'dark' });
  const bg = await page.$eval('body', b => getComputedStyle(b).backgroundColor);
  assert(bg === 'rgb(14, 20, 28)', 'body background ' + bg);
  await page.context().close();
});

if (process.env.AXE_PATH && fs.existsSync(process.env.AXE_PATH)) {
  for (const scheme of ['light', 'dark']) {
    await test(`accessibility audit (axe, ${scheme}): no serious or critical issues`, async () => {
      const page = await open({ colorScheme: scheme });
      const found = new Map();
      for (let i = 0; i < 9; i++) {
        if (i) await page.click('#next');
        if (await page.$('#bdesc')) { await page.click('#bdesc'); await page.click('#bcreate'); }
        else if (await page.isVisible('#runview') && await page.$eval('#cfg', e => e.textContent)) await settle(page);
        for (const view of ['off', 'tech', 'sap']) {
          await page.click(`[data-view="${view}"]`);
          await page.addScriptTag({ path: process.env.AXE_PATH });
          const res = await page.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'] })).violations
            .filter(v => v.impact === 'serious' || v.impact === 'critical')
            .map(v => ({ id: v.id, n: v.nodes.length, ex: v.nodes[0].target.join(' ') + ' :: ' + (v.nodes[0].failureSummary || '').split('\n')[1] })));
          for (const v of res) if (!found.has(v.id)) found.set(v.id, `${v.id} (step ${i}, ${view}) e.g. ${v.ex}`);
        }
      }
      assert(found.size === 0, [...found.values()].join('\n'));
      await page.context().close();
    });
  }
}

await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
