'use strict';
// Large pours use randomized stratified allocation: exact totals, bounded work,
// and a smoother profile than independent grain sampling.
const SandMath = (() => {
  function cdf(x) {
    const sign = x < 0 ? -1 : 1;
    x = Math.abs(x) / Math.SQRT2;
    const t = 1 / (1 + 0.3275911*x);
    const erf = 1 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t - 0.284496736)*t + 0.254829592)*t*Math.exp(-x*x);
    return .5*(1 + sign*erf);
  }
  function batch(length, center, sigma, count, random = Math.random) {
    const result = new Float64Array(length);
    if (!sigma) { result[center] = count; return result; }
    const lower = cdf((-.5-center)/sigma), mass = cdf((length-.5-center)/sigma)-lower;
    const offset = random();
    let allocated = 0;
    for (let i=0;i<length;i++) {
      const cumulative = i===length-1 ? count : Math.min(count, Math.max(allocated,
        Math.floor(count*(cdf((i+.5-center)/sigma)-lower)/mass + offset)));
      result[i] = cumulative-allocated;
      allocated = cumulative;
    }
    return result;
  }
  return { batch };
})();
