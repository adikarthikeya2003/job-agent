// Injected into a rendered Google careers results page (see google_harvest.md).
// __GRAB() reads the job cards; __SAVE(tag) writes them to ~/Downloads/gharvest_<tag>.json.
// The file-download path exists because the extension caps tool output at ~1 KB,
// which is far below one page of job records.
window.__GRAB = function () {
  const out = [];
  for (const li of [...document.querySelectorAll('li')].filter(l => l.querySelector('h3'))) {
    const a = li.querySelector('a[href]'); if (!a) continue;
    const segs = new URL(a.href, location.origin).pathname.split('/').filter(Boolean);
    const slug = segs[segs.length - 1] || '';
    const id = slug.split('-')[0];
    if (!id) continue;
    const spans = [...li.querySelectorAll('span')].map(s => s.innerText.trim());
    const pl = spans.find(s => s.startsWith('place\n'));
    out.push({
      id, slug,
      t: li.querySelector('h3').innerText.trim(),
      l: pl ? pl.replace('place\n', '').replace(/\n/g, ' ').trim() : '',
      d: li.innerText.slice(0, 700).replace(/\s+/g, ' ')
    });
  }
  return out;
};

window.__SAVE = function (tag) {
  const data = window.__GRAB();
  const blob = new Blob([JSON.stringify(data)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'gharvest_' + tag + '.json';
  document.body.appendChild(a); a.click(); a.remove();
  return data.length;
};
