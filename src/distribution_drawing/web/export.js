'use strict';

const DatasetExport = (() => {
  const el = id => document.getElementById(id);
  let session = null, readConfig, loadConfig, activeJob = null, browserJob = null;

  function save(content, name, type) {
    const url = URL.createObjectURL(new Blob([ content ], {type}));
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  function portable(config) {
    save(GENERATOR_TEMPLATE.replace('__CONFIG__', JSON.stringify(config)),
         'generate_distribution.py', 'text/x-python');
    el('status').textContent =
        'Generator saved. Run it with Python 3 on any computer with a C++17 compiler.';
  }

  function browserDownload(config) {
    if (config.counts.reduce((a, b) => a + b, 0) > 1000000) {
      portable(config);
      return;
    }
    const data = [], width = BigInt(config.max) - BigInt(config.min) + 1n,
          bins = BigInt(config.counts.length);
    config.counts.forEach((count, i) => {
      const low = Number(BigInt(config.min) + BigInt(i) * width / bins);
      const span = Number(BigInt(i + 1) * width / bins - BigInt(i) * width / bins);
      for (let j = 0; j < count; j++)
        data.push(low + Math.min(span - 1, Math.floor(Math.random() * span)));
    });
    if (config.order === 'sorted')
      data.sort((a, b) => a - b);
    else
      for (let i = data.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [data[i], data[j]] = [ data[j], data[i] ];
      }
    let content, extension = config.format, type = 'text/plain';
    if (config.format === 'binary') {
      const narrow = config.min >= 0 && config.max <= 4294967295, stride = narrow ? 4 : 8;
      content = new ArrayBuffer(data.length * stride);
      const view = new DataView(content);
      data.forEach((value, i) => narrow ? view.setUint32(i * stride, value, true)
                                        : view.setBigInt64(i * stride, BigInt(value), true));
      extension = 'bin';
      type = 'application/octet-stream';
    } else
      content = data.join('\n') + '\n';
    save(content, `distribution-${config.order}.${extension}`, type);
    el('status').textContent = `Downloaded ${data.length.toLocaleString()} keys.`;
  }

  async function streamDownload(config) {
    const extension = config.format === 'binary' ? 'bin' : config.format;
    let handle;
    try {
      handle = await window.showSaveFilePicker({suggestedName: `distribution-${config.order}.${extension}`});
    } catch (error) {
      if (error.name !== 'AbortError') el('status').textContent = 'Could not choose a file: ' + error.message;
      return;
    }
    const job = {cancelled: false, worker: null, writable: null};
    browserJob = job;
    const count = config.counts.reduce((a, b) => a + b, 0), started = performance.now();
    let written = 0;
    const update = (status, error = '') => showJob({status, count, bytes: written,
      progress: job.position / count || 0, elapsed: (performance.now() - started) / 1000, error});
    el('download').disabled = true;
    update('generating');
    try {
      job.writable = await handle.createWritable();
      if (job.cancelled) throw new DOMException('Cancelled', 'AbortError');
      job.worker = new Worker('stream-worker.js');
      let first = true;
      while (!job.cancelled) {
        const batch = await new Promise((resolve, reject) => {
          job.reject = reject;
          job.worker.onmessage = event => event.data.error ? reject(new Error(event.data.error)) : resolve(event.data);
          job.worker.onerror = event => reject(new Error(event.message || 'Generation worker failed.'));
          job.worker.postMessage(first ? {config} : {});
          first = false;
        });
        job.reject = null;
        if (job.cancelled) break;
        await job.writable.write(batch.buffer);
        written += batch.buffer.byteLength;
        job.position = batch.position;
        update('generating');
        if (batch.position === count) break;
      }
      if (job.cancelled) throw new DOMException('Cancelled', 'AbortError');
      el('jobTitle').textContent = 'Saving to disk';
      el('cancelJob').hidden = true;
      await job.writable.close();
      job.writable = null;
      update('complete');
      el('jobTitle').textContent = 'Dataset saved';
      el('downloadResult').hidden = true;
      el('status').textContent = 'Dataset saved to the file you chose.';
    } catch (error) {
      if (job.writable) {
        try { await job.writable.abort(); } catch (_) { /* The browser may already have closed it. */ }
      }
      update(job.cancelled || error.name === 'AbortError' ? 'cancelled' : 'failed',
             job.cancelled || error.name === 'AbortError' ? '' : error.message);
    } finally {
      if (job.worker) job.worker.terminate();
      browserJob = null;
      el('download').disabled = false;
    }
  }

  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {} : {
      method : 'POST',
      headers : {'Content-Type' : 'application/json', 'X-Distribution-Token' : session.token},
      body : JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok)
      throw new Error(value.error || 'The local engine could not complete this request.');
    return value;
  }

  function showJob(job) {
    const running =
        [ 'queued', 'building', 'generating', 'flushing', 'cancelling' ].includes(job.status);
    el('jobPanel').hidden = false;
    el('download').disabled = running;
    el('cancelJob').hidden = !running;
    el('cancelJob').disabled = job.status === 'cancelling';
    const labels = {
      queued : 'Getting ready',
      building : 'Preparing the native engine',
      generating : 'Generating your dataset',
      flushing : 'Saving to disk',
      cancelling : 'Cancelling',
      cancelled : 'Generation cancelled',
      failed : 'Generation failed',
      complete : 'Last generated dataset'
    };
    el('jobTitle').textContent = labels[job.status] || job.status;
    if (job.status === 'building' || job.status === 'queued')
      el('jobProgress').removeAttribute('value');
    else
      el('jobProgress').value = job.status === 'complete' ? 1 : job.progress;
    el('jobMessage').textContent =
        job.error || `${job.count.toLocaleString()} values · ${(job.bytes / 1e9).toFixed(3)} GB · ${
                         job.elapsed.toFixed(1)} s`;
    el('jobPath').textContent = job.status === 'complete' ? job.path : '';
    el('downloadResult').hidden = job.status !== 'complete';
    if (job.download)
      el('downloadResult').href = job.download;
    if (!running)
      activeJob = null;
    return running;
  }

  async function poll() {
    if (!activeJob)
      return;
    try {
      const job = await request(`/api/jobs/${activeJob}`);
      if (showJob(job))
        setTimeout(poll, 350);
    } catch (error) {
      el('jobMessage').textContent = error.message + ' Reconnecting…';
      setTimeout(poll, 1500);
    }
  }

  async function start() {
    const config = readConfig();
    if (!config)
      return;
    if (!session) {
      if (typeof window.showSaveFilePicker === 'function' && typeof Worker !== 'undefined')
        await streamDownload(config);
      else
        browserDownload(config);
      return;
    }
    el('download').disabled = true;
    try {
      const job = await request('/api/jobs', {config});
      activeJob = job.id;
      showJob(job);
      poll();
    } catch (error) {
      el('status').textContent = error.message;
      el('download').disabled = false;
    }
  }

  async function init(read, load) {
    el('download').disabled = true;
    readConfig = read;
    loadConfig = load;
    el('download').onclick = start;
    el('exportScript').onclick = () => {
      const config = readConfig();
      if (config)
        portable(config);
    };
    el('saveDrawing').onclick = () => {
      const config = readConfig();
      if (config)
        save(JSON.stringify(config, null, 2) + '\n', 'drawing.json', 'application/json');
    };
    el('loadDrawing').onchange = async event => {
      try {
        const file = event.target.files[0];
        if (file) {
          if (file.size > 1024 * 1024)
            throw new Error('Drawing files must be smaller than 1 MB.');
          loadConfig(JSON.parse(await file.text()));
        }
      } catch (error) {
        el('status').textContent = 'Could not open drawing: ' + error.message;
      }
      event.target.value = '';
    };
    el('cancelJob').onclick = async () => {
      if (browserJob) {
        browserJob.cancelled = true;
        el('cancelJob').disabled = true;
        el('jobTitle').textContent = 'Cancelling';
        if (browserJob.reject) browserJob.reject(new DOMException('Cancelled', 'AbortError'));
        return;
      }
      if (!activeJob)
        return;
      el('cancelJob').disabled = true;
      try {
        showJob(await request(`/api/jobs/${activeJob}/cancel`, {}));
      } catch (error) {
        el('jobMessage').textContent = error.message;
        el('cancelJob').disabled = false;
      }
    };
    if (location.protocol !== 'file:') {
      try {
        session = await request('/api/session');
      } catch (_) { /* Static editor. */
      }
    }
    el('engineBadge').textContent = session ? 'Local engine' : 'Browser mode';
    el('download').textContent = session ? 'Generate dataset' : 'Download dataset';
    el('exportNote').textContent =
        session
            ? 'Generated here, saved on your computer. No separate script to run.'
            : typeof window.showSaveFilePicker === 'function' && typeof Worker !== 'undefined'
                  ? 'Save datasets directly from your browser. Large files can take time and disk space.'
                  : 'This browser downloads up to 1M values directly; larger datasets export a generator script.';
    if (session) {
      try {
        const history = await request('/api/jobs');
        const recent = history.jobs[history.jobs.length - 1];
        if (recent && showJob(recent)) {
          activeJob = recent.id;
          poll();
        }
      } catch (_) { /* A fresh session has no history. */
      }
    }
    if (!activeJob)
      el('download').disabled = false;
  }
  return {init};
})();
