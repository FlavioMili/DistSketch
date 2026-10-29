'use strict';

// One batch at a time keeps memory bounded, including for multi-billion-value files.
let bins, total, config, position, mask, shift, key, mul1, mul2, narrow;
function init(c) {
  config = c; position = 0; total = 0; bins = [];
  const width = BigInt(c.max) - BigInt(c.min) + 1n, n = BigInt(c.counts.length);
  c.counts.forEach((count, i) => {
    const start = BigInt(i) * width / n, end = BigInt(i + 1) * width / n;
    if (count) {
      total += count;
      bins.push({end: total, count, lo: Number(BigInt(c.min) + start), span: Number(end - start)});
    }
  });
  const bits = Math.max(1, Math.ceil(Math.log2(total)));
  mask = 2 ** bits - 1; shift = Math.max(1, Math.floor(bits / 2));
  const random = new Uint32Array(3); crypto.getRandomValues(random);
  key = random[0] & mask; mul1 = (random[1] | 1) & mask; mul2 = (random[2] | 1) & mask;
  narrow = c.min >= 0 && c.max <= 4294967295;
}
function permute(x) {
  do {
    x = Math.imul(x ^ key, mul1) & mask;
    x ^= x >>> shift;
    x = Math.imul(x, mul2) & mask;
    x ^= x >>> shift;
  } while (x >= total);
  return x;
}
function value(rank) {
  let lo = 0, hi = bins.length - 1;
  while (lo < hi) { const m = (lo + hi) >>> 1; if (rank < bins[m].end) hi = m; else lo = m + 1; }
  const b = bins[lo], local = rank - (b.end - b.count);
  // BigInt preserves the exact mapping for ranges whose product exceeds 53 bits.
  const offset = b.span * b.count <= Number.MAX_SAFE_INTEGER
    ? Math.floor(local * b.span / b.count)
    : Number(BigInt(local) * BigInt(b.span) / BigInt(b.count));
  return b.lo + offset;
}
function next() {
  const end = Math.min(total, position + 32768), count = end - position;
  let buffer;
  if (config.format === 'binary') {
    buffer = new ArrayBuffer(count * (narrow ? 4 : 8));
    const view = new DataView(buffer);
    for (let i = 0; i < count; i++) {
      const v = value(config.order === 'sorted' ? position + i : permute(position + i));
      if (narrow) view.setUint32(i * 4, v, true); else view.setBigInt64(i * 8, BigInt(v), true);
    }
  } else {
    const lines = new Array(count);
    for (let i = 0; i < count; i++) lines[i] = value(config.order === 'sorted' ? position + i : permute(position + i));
    buffer = new TextEncoder().encode(lines.join('\n') + '\n').buffer;
  }
  position = end;
  postMessage({buffer, position, total}, [buffer]);
}
onmessage = event => {
  try { if (event.data.config) init(event.data.config); next(); }
  catch (error) { postMessage({error: error.message}); }
};
