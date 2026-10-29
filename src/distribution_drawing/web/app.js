'use strict';
const $ = id => document.getElementById(id);
const canvas = $('plot'), ctx = canvas.getContext('2d');
let mode = 'draw', values = new Float64Array(256), min = 0, max = 255, drawing = false, last = null,
    particles = [], animation = 0, lastPour = 0, pourPoint = null, batches = [], sceneDirty = true;
const scene = document.createElement('canvas');
function range() {
  const a = Number($('min').value), b = Number($('max').value);
  const valid = $('min').value !== '' && $('max').value !== '' && Number.isSafeInteger(a) &&
                Number.isSafeInteger(b) && b >= a && Number.isSafeInteger(b - a + 1);
  $('min').classList.toggle('invalid', !valid);
  $('max').classList.toggle('invalid', !valid);
  if (!valid) {
    $('status').textContent = 'Enter a valid inclusive range of safe integers.';
    return false;
  }
  if (a !== min || b !== max) {
    end();
    const old = values, length = Math.min(4096, b - a + 1);
    values = new Float64Array(length);
    for (let i = 0; i < length; i++)
      values[i] = old[Math.min(old.length - 1, Math.floor((i + .5) * old.length / length))];
    // Sand represents actual counts, so changing its bins clears it rather than duplicating grains.
    if (mode === 'sand')
      values.fill(0);
    particles = [];
    batches = [];
    sceneDirty = true;
    min = a;
    max = b;
  }
  $('minLabel').textContent = min.toLocaleString();
  $('maxLabel').textContent = max.toLocaleString();
  return true;
}
function updateSummary() {
  let mass = 0;
  for (const value of values) mass += value;
  const requested = Number($('total').value);
  const count = mode === 'draw'
    ? (mass > 0 && Number.isSafeInteger(requested) && requested > 0 && requested <= 2000000000 ? requested : 0)
    : Math.round(mass);
  $('datasetSummary').textContent = `${count.toLocaleString()} values`;
}
function renderBase() {
  const rect = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#363a4f';
  ctx.lineWidth = dpr;
  for (let i = 1; i < 5; i++) {
    ctx.beginPath();
    ctx.moveTo(0, h * i / 5);
    ctx.lineTo(w, h * i / 5);
    ctx.stroke()
  }
  let occupied = 0;
  for (const v of values)
    if (v > 0)
      occupied++;
  $('emptyState').hidden = occupied > 0 || particles.length > 0;
  updateSummary();
  // Aggregate sand into display columns: thousands of bins can be narrower than a pixel.
  let display = values;
  if (mode === 'sand' && values.length > 180) {
    const columns = Math.min(180, values.length), sums = new Float64Array(columns), sizes = new Uint16Array(columns);
    for (let i = 0; i < values.length; i++) {
      const column = Math.floor(i * columns / values.length);
      sums[column] += values[i];
      sizes[column]++;
    }
    for (let i = 0; i < columns; i++) sums[i] /= sizes[i];
    display = sums;
  }
  let peak = 0;
  for (const v of display)
    peak = Math.max(peak, v);
  if (!peak && !particles.length)
    return;
  const scale = mode === 'draw' ? 1 : peak * 1.08;
  if (peak) {
    ctx.fillStyle = mode === 'sand' ? '#eed49f55' : '#c6a0f655';
    ctx.strokeStyle = mode === 'sand' ? '#eed49f' : '#c6a0f6';
    ctx.lineWidth = 1.75 * dpr;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < display.length; i++)
      ctx.lineTo((i + .5) * w / display.length, h - (display[i] / scale) * (h - 8 * dpr));
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < display.length; i++) {
      const x = (i + .5) * w / display.length, y = h - (display[i] / scale) * (h - 8 * dpr);
      if (!i)
        ctx.moveTo(x, y);
      else
        ctx.lineTo(x, y)
    }
    ctx.stroke();
  }
}
function render() {
  if (sceneDirty) {
    renderBase();
    scene.width = canvas.width;
    scene.height = canvas.height;
    scene.getContext('2d').drawImage(canvas, 0, 0);
    sceneDirty = false;
  } else {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(scene, 0, 0);
  }
  const dpr = window.devicePixelRatio || 1;
  for (const color of ['#eed49f', '#f5a97f']) {
    ctx.beginPath();
    ctx.fillStyle = color;
    for (const p of particles)
      if (p.color === color) {
        const x = p.x * canvas.width, y = p.y * canvas.height, r = p.radius * dpr;
        ctx.moveTo(x + r, y);
        ctx.arc(x, y, r, 0, Math.PI * 2);
      }
    ctx.fill();
  }
}
function point(e) {
  const r = canvas.getBoundingClientRect();
  return {
    x: Math.max(
        0, Math.min(values.length - 1, Math.floor((e.clientX - r.left) / r.width * values.length))),
        y: Math.max(0, Math.min(1, 1 - (e.clientY - r.top) / r.height))
  }
}
function drawTo(p) {
  const q = last || p, steps = Math.max(1, Math.abs(p.x - q.x));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    values[Math.round(q.x + (p.x - q.x) * t)] = q.y + (p.y - q.y) * t
  }
  last = p;
  sceneDirty = true;
  render()
}
function integer(id, lo, hi) {
  const s = $(id).value, v = Number(s);
  if (s === '' || !Number.isSafeInteger(v) || v < lo || v > hi) {
    $('status').textContent = `Enter a whole number between ${lo.toLocaleString()} and ${
        hi.toLocaleString()} for ${id === 'total' ? 'output elements' : 'grains per drop'}.`;
    return null
  }
  return v
}
function animate(now) {
  animation = 0;
  if (drawing && mode === 'sand' && pourPoint && now - lastPour >= 150) {
    lastPour = now;
    drop(pourPoint);
  }
  const pending = [];
  for (const batch of batches) {
    // Commit the whole count vector in at most 12 small steps during the fall.
    const step = Math.min(12, Math.max(0, Math.floor((now - batch.start - 100) / 35)));
    if (step > batch.step) {
      for (let i = 0; i < values.length; i++)
        values[i] +=
            Math.floor(batch.counts[i] * step / 12) - Math.floor(batch.counts[i] * batch.step / 12);
      batch.step = step;
      sceneDirty = true;
    }
    if (step < 12)
      pending.push(batch);
  }
  batches = pending;
  particles = particles.filter(grain => {
    const t = Math.max(0, Math.min(1, (now - grain.start) / grain.duration));
    grain.x = grain.fromX + (grain.toX - grain.fromX) * t;
    grain.y = grain.fromY + (grain.toY - grain.fromY) * t * t;
    return t < 1;
  });
  render();
  if (particles.length || batches.length || (drawing && mode === 'sand')) {
    if (!animation)
      animation = requestAnimationFrame(animate);
  } else if (mode === 'sand')
    $('status').textContent = 'Sand settled. Ready to export.';
}
function drop(p) {
  const n = integer('grains', 1, 2000000000);
  if (n === null)
    return;
  let used = 0;
  for (const v of values)
    used += v;
  for (const batch of batches)
    for (const v of batch.counts)
      used += v - Math.floor(v * batch.step / 12);
  if (used + n > 2000000000) {
    drawing = false;
    $('status').textContent =
        'The 2 billion grain limit is reached. Use a smaller drop or clear the canvas.';
    return;
  }
  const sigma = Number($('spread').value) / 100 * values.length, now = performance.now();
  batches.push({counts : SandMath.batch(values.length, p.x, sigma, n), start : now, step : 0});
  // A fixed visual budget keeps a billion-grain pour as cheap as a small one.
  const visual = Math.min(n, 48, 240 - particles.length);
  for (let i = 0; i < visual; i++) {
    let x = p.x;
    if (sigma)
      do {
        x = Math.round(p.x +
                       sigma * Math.sqrt(-2 * Math.log(Math.max(Number.MIN_VALUE, Math.random()))) *
                           Math.cos(2 * Math.PI * Math.random()));
      } while (x < 0 || x >= values.length);
    particles.push({
      fromX : (p.x + .5) / values.length,
      fromY : 1 - p.y,
      toX : (x + .5) / values.length,
      toY : .98,
      x : (p.x + .5) / values.length,
      y : 1 - p.y,
      start : now + Math.random() * 80,
      duration : 280 + Math.random() * 200,
      radius : 1.5 + Math.random() * 1.7,
      color : Math.random() < .5 ? '#eed49f' : '#f5a97f'
    });
  }
  $('status').textContent = `Pouring ${n.toLocaleString()} grains…`;
  if (!animation)
    animation = requestAnimationFrame(animate);
}
canvas.addEventListener('pointerdown', e => {
  if (!range())
    return;
  drawing = true;
  last = null;
  canvas.setPointerCapture(e.pointerId);
  const p = point(e);
  if (mode === 'draw')
    drawTo(p);
  else {
    pourPoint = p;
    drop(p);
    lastPour = performance.now()
  }
});
canvas.addEventListener('pointermove', e => {
  if (!drawing)
    return;
  if (mode === 'draw')
    drawTo(point(e));
  else
    pourPoint = point(e)
});
function end() {
  drawing = false;
  last = null;
  pourPoint = null
}
canvas.addEventListener('pointerup', end);
canvas.addEventListener('pointercancel', end);
canvas.addEventListener('lostpointercapture', end);
function setMode(next) {
  if (mode === next)
    return;
  end();
  mode = next;
  values.fill(0);
  particles = [];
  batches = [];
  sceneDirty = true;
  $('drawMode').classList.toggle('active', mode === 'draw');
  $('sandMode').classList.toggle('active', mode === 'sand');
  $('drawMode').setAttribute('aria-pressed', String(mode === 'draw'));
  $('sandMode').setAttribute('aria-pressed', String(mode === 'sand'));
  $('drawControls').hidden = mode !== 'draw';
  $('sandControls').hidden = mode !== 'sand';
  $('modeTitle').textContent = mode === 'draw' ? 'Draw profile' : 'Pour sand';
  $('hint').textContent =
      mode === 'draw'
          ? 'Drag across the plot to shape your distribution.'
          : 'Hold to pour. Move the cursor to steer.';
  $('status').textContent = 'Canvas cleared when switching modes.';
  render()
}
$('drawMode').onclick = () => setMode('draw');
$('sandMode').onclick = () => setMode('sand');
$('clear').onclick = () => {
  end();
  values.fill(0);
  particles = [];
  batches = [];
  sceneDirty = true;
  $('status').textContent = 'Canvas cleared.';
  render()
};
for (const id of ['min', 'max'])
  $(id).addEventListener('input', () => {
    if (range()) {
      $('status').textContent = mode === 'sand' ? 'Range updated; sand cleared.' : 'Range updated.';
      sceneDirty = true;
      render()
    }
  });
$('total').addEventListener('input', updateSummary);
$('spread').oninput = () => { $('spreadValue').textContent = $('spread').value + '%' };
function exportConfig() {
  if (!range())
    return null;
  if (batches.length) {
    $('status').textContent = 'Wait for the grains to settle before exporting.';
    return null;
  }
  let sum = 0;
  for (const value of values)
    sum += value;
  if (!sum) {
    $('status').textContent = 'Draw a shape or pour sand first.';
    return null;
  }
  let counts;
  if (mode === 'draw') {
    const total = integer('total', 1, 2000000000);
    if (total === null)
      return null;
    let allocated = 0;
    const fractions = [];
    counts = Array.from(values, (value, i) => {
      const exact = value / sum * total, whole = Math.floor(exact);
      allocated += whole;
      fractions.push({i, f : exact - whole});
      return whole;
    });
    fractions.sort((a, b) => b.f - a.f || a.i - b.i);
    for (let i = 0; i < total - allocated; i++)
      counts[fractions[i].i]++;
  } else
    counts = Array.from(values);
  return {
    counts,
    min,
    max,
    order : $('order').value,
    format : $('format').value,
    editor : {mode, weights : Array.from(values)}
  };
}

function importConfig(config) {
  if (!config || !Array.isArray(config.counts) || config.counts.length < 1 ||
      config.counts.length > 4096 || !Number.isSafeInteger(config.min) ||
      !Number.isSafeInteger(config.max) || config.max < config.min ||
      !Number.isSafeInteger(config.max - config.min + 1) ||
      config.counts.length > config.max - config.min + 1 ||
      config.counts.some(value => !Number.isSafeInteger(value) || value < 0))
    throw new Error('Invalid key range or counts.');
  const total = config.counts.reduce((a, b) => a + b, 0);
  if (total < 1 || total > 2000000000)
    throw new Error('Use between 1 and 2 billion values.');
  const editor = config.editor;
  const restore = editor && [ 'draw', 'sand' ].includes(editor.mode) &&
                  Array.isArray(editor.weights) && editor.weights.length === config.counts.length &&
                  editor.weights.every(v => Number.isFinite(v) && v >= 0 &&
                                            v <= (editor.mode === 'draw' ? 1 : 2000000000));
  end();
  setMode(restore ? editor.mode : 'sand');
  $('min').value = config.min;
  $('max').value = config.max;
  range();
  values = Float64Array.from(restore ? editor.weights : config.counts);
  particles = [];
  batches = [];
  $('total').value = total;
  if ([ 'txt', 'csv', 'binary' ].includes(config.format))
    $('format').value = config.format;
  if ([ 'u32', 'i64' ].includes(config.format))
    $('format').value = 'binary';
  if ([ 'sorted', 'shuffled' ].includes(config.order))
    $('order').value = config.order;
  sceneDirty = true;
  render();
  $('status').textContent = 'Drawing loaded.';
}
$('sampleProfile').onclick = () => {
  end();
  setMode('draw');
  for(let i=0;i<values.length;i++) {
    const x=(i+.5)/values.length;
    values[i]=.72*Math.exp(-Math.pow((x-.3)/.11,2)) + .42*Math.exp(-Math.pow((x-.72)/.16,2));
  }
  sceneDirty=true;render();
  $('status').textContent='Sample loaded. Draw over it to make it yours.';
};
DatasetExport.init(exportConfig, importConfig);
window.addEventListener('resize', () => {
  sceneDirty = true;
  render()
});
render();
