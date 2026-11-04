const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const templateDirectory = path.join(__dirname, '..', 'templates');
const templates = [
  'feedback.html',
  'feedbacks.html',
  'history.html',
  'home.html',
  'modes.html',
  'practice.html',
  'scenarios.html',
  'session_not_found.html',
  'shelves.html',
];

for (const template of templates) {
  test(`${template}: includes exactly one mobile viewport declaration`, () => {
    const html = fs.readFileSync(path.join(templateDirectory, template), 'utf8');
    const viewport = html.match(
      /<meta\s+name="viewport"\s+content="width=device-width, initial-scale=1"\s*\/?>/g
    ) || [];
    assert.equal(viewport.length, 1);
  });
}

test('list action groups wrap on narrow screens', () => {
  for (const template of ['history.html', 'feedbacks.html']) {
    const html = fs.readFileSync(path.join(templateDirectory, template), 'utf8');
    assert.match(html, /d-flex flex-wrap justify-content-between gap-2/);
    assert.match(html, /d-flex flex-wrap gap-2/);
  }
});

test('feedback footer actions wrap on narrow screens', () => {
  const html = fs.readFileSync(path.join(templateDirectory, 'feedback.html'), 'utf8');
  assert.match(html, /d-flex flex-wrap gap-2 mt-3/);
});

test('practice header wraps and content uses mobile width', () => {
  const html = fs.readFileSync(path.join(templateDirectory, 'practice.html'), 'utf8');
  assert.match(html, /d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3/);
  assert.match(html, /@media \(max-width: 575\.98px\)/);
  assert.match(html, /#chat,[\s\S]*#status[\s\S]*width: calc\(100% - 1rem\)/);
  assert.match(html, /<div class="container mb-3" style="max-width: 900px;">/);
});