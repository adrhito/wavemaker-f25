/* The water the pistons move: the browser's copy of wavemodel/flow.py.
 *
 * Same chain, same numbers (tests/test_flow.py runs both and compares):
 * LinMot motion (motion.py) -> water out of each pipe through the trapped air
 * (air.pipe_flux, quasi-static) -> the camera-fitted linear dispersive channel,
 * solved by FFT at a complex frequency (exponential window) -> surface,
 * flux and the velocity field through the depth.
 *
 * FLOW.simulate(commands, depth, geo, cal, opts) -> {t, x, eta, q, z, xv, u, w,
 *   disc, inner, Qrows, rows, metrics}; arrays are flat Float32Array/Float64Array,
 *   time-major: eta[i * nx + j], u[(i * nz + k) * nxv + j], disc[i * 30 + p].
 * Works in a page (window.FLOW) and in Node (module.exports).
 */
(function (root) {
  "use strict";
  const G = 9.81, P0 = 101325.0, RHO = 1000.0;
  const FINE = 0.002, DT = 0.05, BOTTOM_MM = 370.0, F_FLOW_MAX = 3.0, CAMERA_X = 5.18;
  const RAISE_MM_S = 40.0, SETTLE_S = 10.0, SIGMA_DECADES = 6.9;
  const TRAPEZOIDAL = 0, BESTEHORN = 1, S_CURVE = 2, SINE = 3;

  // ------------------------------------------------------------- motion.py

  function ramp(vc, aMax, jerk, profile, kj) {
    if (vc <= 0) return [0, () => 0];
    if (profile === TRAPEZOIDAL || jerk <= 0) return [vc / aMax, t => aMax * t];
    if (profile === BESTEHORN) {
      const J = jerk * kj, A = Math.min(aMax, Math.sqrt(2 * J * vc / Math.PI)), T = 2 * vc / A;
      return [T, t => A * (t / 2 - T * Math.sin(2 * Math.PI * t / T) / (4 * Math.PI))];
    }
    const J = jerk;
    if (vc <= aMax * aMax / J) {
      const Tj = Math.sqrt(vc / J), T = 2 * Tj;
      return [T, t => (t < Tj ? 0.5 * J * t * t : vc - 0.5 * J * (T - t) * (T - t))];
    }
    const Tj = aMax / J, Tc = vc / aMax - Tj, T = 2 * Tj + Tc;
    return [T, t => (t < Tj ? 0.5 * J * t * t : t < Tj + Tc ? 0.5 * J * Tj * Tj + aMax * (t - Tj)
      : vc - 0.5 * J * (T - t) * (T - t))];
  }

  // A rest-to-rest move of d mm: {x: Float64Array (from 0), T, peak}
  function move(distance, leg, profile, kj, dt) {
    const d = Math.abs(distance);
    if (d <= 1e-9) return { x: new Float64Array([0]), T: 0, peak: 0 };
    const vSet = Math.max(leg.speed, 1e-6);
    if (profile === SINE) {
      const J = leg.jerk > 0 ? Math.max(leg.jerk * kj, 1e-6) : Infinity;
      const T = Math.max(Math.PI * d / (2 * vSet),
        Math.PI * Math.sqrt(d / (2 * Math.max(Math.min(leg.accel, leg.decel), 1e-6))),
        J !== Infinity ? Math.cbrt(Math.PI ** 3 * d / (2 * J)) : 0);
      const n = Math.ceil((T + dt / 2) / dt), x = new Float64Array(n);   // np.arange(0, T + dt/2, dt), last = T
      for (let i = 0; i < n; i++) { const t = i === n - 1 ? T : i * dt; x[i] = d * (1 - Math.cos(Math.PI * t / T)) / 2; }
      return { x, T, peak: d * Math.PI / (2 * T) };
    }
    const dist = vc => {
      const [Ta] = ramp(vc, leg.accel, leg.jerk, profile, kj), [Td] = ramp(vc, leg.decel, leg.jerk, profile, kj);
      return vc * (Ta + Td) / 2;
    };
    let vc;
    if (dist(vSet) <= d) vc = vSet;
    else {
      let lo = 0, hi = vSet;
      for (let i = 0; i < 80; i++) { const mid = (lo + hi) / 2; if (dist(mid) < d) lo = mid; else hi = mid; }
      vc = (lo + hi) / 2;
    }
    const [Ta, va] = ramp(vc, leg.accel, leg.jerk, profile, kj), [Td, vd] = ramp(vc, leg.decel, leg.jerk, profile, kj);
    const Tc = Math.max(0, (d - vc * (Ta + Td) / 2) / vc), T = Ta + Tc + Td;
    const n = Math.ceil((T + dt / 2) / dt), t = new Float64Array(n), v = new Float64Array(n);   // np.arange(0, T + dt/2, dt)
    for (let i = 0; i < n; i++) t[i] = i * dt;
    for (let i = 0; i < n; i++) {
      const ti = t[i];
      let vi = ti < Ta ? va(Math.min(ti, Ta)) : ti < Ta + Tc ? vc : vc - vd(Math.min(Math.max(ti - Ta - Tc, 0), Td));
      v[i] = Math.max(vi, 0);
    }
    const x = new Float64Array(n);
    for (let i = 1; i < n; i++) x[i] = x[i - 1] + (v[i] + v[i - 1]) / 2 * (t[i] - t[i - 1]);
    if (x[n - 1] > 0) { const s = d / x[n - 1]; for (let i = 0; i < n; i++) x[i] *= s; }
    return { x, T, peak: vc };
  }

  // motion.run: rest at p1 until delay, then legs 1->2->1...; positions on n samples
  function run(p, n, kj, dt, delay, cycles) {
    const pos = new Float64Array(n).fill(p.position1);
    const duration = (n - 1) * dt;
    let kFrom = 1, kTo = 2, now = delay, done = 0;
    const maxLegs = Math.round(2 * cycles);
    while (now < duration && done < maxLegs) {
      const a = kFrom === 1 ? p.position1 : p.position2, b = kTo === 2 ? p.position2 : p.position1;
      const leg = kTo === 1 ? p.leg1 : p.leg2;
      const m = move(b - a, leg, p.profile, kj, dt), sign = b >= a ? 1 : -1;
      const i0 = Math.round(now / dt);
      for (let i = 0; i < m.x.length && i0 + i < n; i++) if (i0 + i >= 0) pos[i0 + i] = a + sign * m.x[i];
      const end = now + m.T, j0 = Math.min(n, Math.round(end / dt));
      for (let i = Math.max(j0, 0); i < n; i++) pos[i] = b;
      now = end + (leg.dwell || 0) / 1000;
      done++;
      [kFrom, kTo] = [kTo, kFrom];
    }
    return pos;
  }

  function cycleTime(p, kj) {
    const a = move(p.position2 - p.position1, p.leg2, p.profile, kj, 0.002);
    const b = move(p.position1 - p.position2, p.leg1, p.profile, kj, 0.002);
    return a.T + b.T;
  }

  function discMM(cmd, n, kj) {
    const top = BOTTOM_MM - cmd.stroke;
    const leg = { speed: cmd.speed, accel: cmd.accel, decel: cmd.decel, jerk: cmd.jerk, dwell: 0 };
    const profile = cmd.profile === undefined ? BESTEHORN : cmd.profile;
    let p, cycles;
    if (cmd.kind === "push_down") {
      p = { position1: top, position2: BOTTOM_MM, leg1: leg, leg2: leg, profile };
      cycles = 0.5;
    } else {
      p = { position1: BOTTOM_MM, position2: top, leg1: leg, leg2: leg, profile };
      if (cmd.kind === "raise") cycles = 0.5;
      else if (cmd.kind === "single") cycles = 1;
      else { const c = cycleTime(p, kj); cycles = c > 0 ? Math.max(1, Math.round(cmd.seconds / c)) : 1; }
    }
    return run(p, n, kj, FINE, cmd.start, cycles);
  }

  // ------------------------------------------------------------- air.py

  // L with (p0 + rho g (h - zd + L)) L^n = C, Newton from above
  function airLength(zd, C, h, n) {
    let L = Math.max(Math.pow(C / P0, 1 / n), 1e-9) * 1.2 + 1e-6;
    for (let i = 0; i < 40; i++) {
      const p = P0 + RHO * G * (h - zd + L), Ln = Math.pow(L, n);
      const step = (p * Ln - C) / (RHO * G * Ln + n * p * Math.pow(L, n - 1));
      L = Math.max(L - step, 1e-9);
      if (Math.abs(step) < 1e-10) break;
    }
    return L;
  }

  function staged(zdPark, zdTo, zb, h, seal) {
    let C = P0 * (zdPark - zb);
    if (zdTo < zdPark) C = Math.min(C, (P0 + RHO * G * (h - zb)) * (zdTo - zb));
    else if (seal === "leaky") C = Math.max(C, P0 * Math.max(zdTo - h, 0));
    return C;
  }

  // Water out of one pipe: q on nCoarse intervals of `step` fine samples, and the inner level at the start
  function pipeFlux(zd, zb, h, area, step, nCoarse, zdPark, seal, n) {
    n = n || 1.4;
    const zd0 = zd[0];
    const L0 = airLength(zd0, staged(zdPark, zd0, zb, h, seal), h, 1.0);
    const p0 = P0 + RHO * G * (h - zd0 + L0);
    let c = p0 * Math.pow(L0, n);
    const pb = P0 + RHO * G * (h - zb);
    const N = zd.length, zin = new Float64Array(N);
    let i0 = N, i1 = N;
    for (let i = 0; i < N - 1; i++) if (zd[i + 1] !== zd[i]) { i0 = i; break; }
    for (let i = N - 2; i >= 0; i--) if (zd[i + 1] !== zd[i]) { i1 = i + 2; break; }
    let Lprev = null;
    for (let i = 0; i < N; i++) {
      if (i >= i0 && i < i1) {
        const vent = pb * Math.pow(Math.max(zd[i] - zb, 1e-9), n);
        const leak = seal === "leaky" ? P0 * Math.pow(Math.max(zd[i] - h, 0), n) : 0;
        if (c < leak) c = leak;
        if (c > vent) c = vent;
        Lprev = null;
      }
      // the air is unchanged while the disc rests, so reuse the last length
      const same = i > 0 && zd[i] === zd[i - 1] && Lprev !== null;
      const L = same ? Lprev : airLength(zd[i], c, h, n);
      Lprev = L;
      zin[i] = zd[i] - L;
    }
    const q = new Float64Array(nCoarse);
    for (let j = 0; j < nCoarse; j++) {
      const a = Math.min(j * step, N - 1), b = Math.min((j + 1) * step, N - 1);
      q[j] = area * (zin[a] - zin[b]) / (step * FINE);
    }
    return { q, innerStart: zin[0] };
  }

  // ------------------------------------------------------------- complex helpers

  function fft(re, im, inverse) {                 // in place, radix 2
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = (inverse ? 2 : -2) * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = a + len / 2;
          const xr = re[b] * cr - im[b] * ci, xi = re[b] * ci + im[b] * cr;
          re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
          const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
        }
      }
    }
    if (inverse) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
  }

  function rfft(x, nfft) {                         // -> half spectrum (nfft/2 + 1)
    const re = new Float64Array(nfft), im = new Float64Array(nfft);
    re.set(x.length > nfft ? x.subarray(0, nfft) : x);
    fft(re, im, false);
    const m = nfft / 2 + 1;
    return [re.slice(0, m), im.slice(0, m)];
  }

  function irfft(Fr, Fi, nfft, nOut) {             // half spectrum -> first nOut real samples
    const re = new Float64Array(nfft), im = new Float64Array(nfft), m = nfft / 2 + 1;
    for (let k = 0; k < m; k++) { re[k] = Fr[k]; im[k] = Fi[k]; }
    im[0] = 0; im[m - 1] = 0;
    for (let k = 1; k < m - 1; k++) { re[nfft - k] = Fr[k]; im[nfft - k] = -Fi[k]; }
    fft(re, im, true);
    return re.subarray(0, nOut);
  }

  const cmul = (ar, ai, br, bi) => [ar * br - ai * bi, ar * bi + ai * br];
  const cdiv = (ar, ai, br, bi) => { const d = br * br + bi * bi; return [(ar * br + ai * bi) / d, (ai * br - ar * bi) / d]; };
  const cexp = (ar, ai) => { const e = Math.exp(ar); return [e * Math.cos(ai), e * Math.sin(ai)]; };
  function ctanh(ar, ai) {
    if (ar > 20) return [1, 0];
    if (ar < -20) return [-1, 0];
    const d = Math.cosh(2 * ar) + Math.cos(2 * ai);
    return [Math.sinh(2 * ar) / d, Math.sin(2 * ai) / d];
  }

  function wavenumberReal(w, h) {
    if (w <= 0) return 0;
    let k = Math.max(w / Math.sqrt(G * h), w * w / G, 1e-12);
    for (let i = 0; i < 30; i++) {
      const th = Math.tanh(k * h), f = G * k * th - w * w, df = G * th + G * k * h * (1 - th * th);
      k = Math.max(k - f / df, 1e-12);
    }
    return k;
  }

  // k for w - i sigma, decaying branch (flow.wavenumbers_complex)
  function wavenumberComplex(w, sigma, h) {
    const kr = wavenumberReal(w, h);
    const cg = w > 0 ? 0.5 * (w / Math.max(kr, 1e-12)) * (1 + 2 * kr * h / Math.max(Math.sinh(2 * kr * h), 1e-12))
      : Math.sqrt(G * h);
    let a = kr, b = -sigma / cg;
    const W2r = w * w - sigma * sigma, W2i = -2 * w * sigma;
    for (let i = 0; i < 30; i++) {
      const [tr, ti] = ctanh(a * h, b * h);
      const fr = G * (a * tr - b * ti) - W2r, fi = G * (a * ti + b * tr) - W2i;
      // df = g th + g k h (1 - th^2)
      const [t2r, t2i] = cmul(tr, ti, tr, ti);
      const [khr, khi] = [G * a * h, G * b * h];
      const [mr, mi] = cmul(khr, khi, 1 - t2r, -t2i);
      const dr = G * tr + mr, di = G * ti + mi;
      const [sr, si] = cdiv(fr, fi, dr, di);
      a -= sr; b -= si;
    }
    return [a, b];
  }

  // ------------------------------------------------------------- the simulation

  function rowGainDelay(cal, row) {
    const p = cal.params, fitted = cal.fitted_rows || [];
    if (fitted.indexOf(row) >= 0) return [p["g" + row], p["tau" + row], true];
    const gs = fitted.map(r => p["g" + r]);
    return [gs.length ? gs.reduce((s, v) => s + v, 0) / gs.length : 1, 0, false];
  }

  function pistonFlux(commands, depth, nt, geo, seal) {
    const step = Math.round(DT / FINE), nf = (nt - 1) * step + 1, np = geo.rows * geo.columns;
    const kj = geo.kj, zdPark = geo.disc_zero - geo.park_mm / 1000;
    const pos = [];
    for (let i = 0; i < np; i++) pos.push(null);
    const sorted = commands.slice().sort((a, b) => a.start - b.start);
    for (const cmd of sorted) {
      let traj = discMM(cmd, nf, kj), i0 = Math.round(cmd.start / FINE);
      if (cmd.kind === "push_down") {
        const tR = cmd.stroke / RAISE_MM_S, r1 = cmd.start - SETTLE_S, r0 = r1 - tR;
        for (let i = 0; i < nf; i++) {
          const t = i * FINE;
          if (t < cmd.start) {
            const r = Math.min(Math.max((t - r0) / tR, 0), 1);
            traj[i] = BOTTOM_MM - cmd.stroke * (1 - Math.cos(Math.PI * r)) / 2;
          }
        }
        i0 = Math.max(0, Math.round(r0 / FINE));
      }
      for (const pn of cmd.pistons) {
        if (pn < 1 || pn > np) throw new Error("piston " + pn + " is not 1-" + np);
        if (!pos[pn - 1]) pos[pn - 1] = new Float64Array(nf).fill(geo.park_mm);
        pos[pn - 1].set(traj.subarray(i0), i0);
      }
    }
    const disc = new Float32Array(nt * np), inner = new Float32Array(nt * np).fill(NaN);
    const Qrows = [];
    for (let r = 0; r < geo.columns; r++) Qrows.push(new Float64Array(nt));
    for (let i = 0; i < np; i++) {
      const row = Math.floor(i / geo.rows) + 1, zb = geo.row_bottom[row - 1];
      const mm = pos[i] || null;
      for (let j = 0; j < nt; j++) disc[j * np + i] = mm ? mm[Math.min(j * step, nf - 1)] : geo.park_mm;
      if (zb >= depth) continue;
      const zd = new Float64Array(nf);
      if (mm) for (let j = 0; j < nf; j++) zd[j] = geo.disc_zero - mm[j] / 1000;
      else zd.fill(zdPark);
      const { q, innerStart } = pipeFlux(zd, zb, depth, geo.bore_area, step, nt - 1, zdPark, seal);
      let level = innerStart;
      inner[i] = level;
      for (let j = 0; j < nt - 1; j++) {
        Qrows[row - 1][j] += q[j];
        level -= q[j] * DT / geo.bore_area;
        inner[(j + 1) * np + i] = level;
      }
    }
    return { disc, inner, Qrows };
  }

  function channel(Qrows, depth, x, cal, width, z, xv) {
    const p = cal.params, nt = Qrows[0].length;
    const nfft = 1 << Math.ceil(Math.log2(nt + Math.floor(300.0 / DT)));
    const sigma = SIGMA_DECADES / (nfft * DT), m = nfft / 2 + 1;
    const hEff = depth * (p.h_scale === undefined ? 1 : p.h_scale);
    const L = p.L, Re = p.Re, Rh = p.Rh;
    const weighted = new Float64Array(nt);
    const Sr = new Float64Array(m), Si = new Float64Array(m);
    const kR = new Float64Array(m), kI = new Float64Array(m), cR = new Float64Array(m), cI = new Float64Array(m);
    const kcR = new Float64Array(m), kcI = new Float64Array(m), lpR = new Float64Array(m), lpI = new Float64Array(m);
    const wv = new Float64Array(m);
    for (let k = 0; k < m; k++) {
      const w = 2 * Math.PI * k / (nfft * DT);
      wv[k] = w;
      const [a, b] = wavenumberComplex(w, sigma, hEff);
      kR[k] = a; kI[k] = b;
      [cR[k], cI[k]] = cdiv(w, -sigma, a, b);
      const delta = Math.exp(p.log_d0) + Math.exp(p.log_d1) * w + Math.exp(p.log_d2) * w * w;
      kcR[k] = a; kcI[k] = b - delta;
      // loop = 1 - Re Rh e^{-i W tau_h} e^{-2 i kc L}
      const [e1r, e1i] = cexp(-sigma * p.tau_h, -w * p.tau_h);
      const [e2r, e2i] = cexp(2 * kcI[k] * L, -2 * kcR[k] * L);
      const [er, ei] = cmul(e1r, e1i, e2r, e2i);
      lpR[k] = 1 - Re * Rh * er; lpI[k] = -Re * Rh * ei;
    }
    for (let r = 0; r < Qrows.length; r++) {
      const Q = Qrows[r];
      let any = false;
      for (let j = 0; j < nt; j++) if (Q[j] !== 0) { any = true; break; }
      if (!any) continue;
      const [gain, tau] = rowGainDelay(cal, r + 1);
      for (let j = 0; j < nt; j++) weighted[j] = Q[j] * Math.exp(-sigma * j * DT);
      const [Fr, Fi] = rfft(weighted, nfft);
      for (let k = 0; k < m; k++) {
        const [dr, di] = cexp(-sigma * tau, -wv[k] * tau);       // e^{-i W tau}
        const [vr, vi] = cmul(Fr[k], Fi[k], dr, di);
        Sr[k] += gain * vr; Si[k] += gain * vi;
      }
    }
    for (let k = 0; k < m; k++) {
      const [a, b] = cdiv(Sr[k], Si[k], width * cR[k], width * cI[k]);
      Sr[k] = a; Si[k] = b;
    }
    const back = new Float64Array(nt);
    for (let j = 0; j < nt; j++) back[j] = Math.exp(sigma * j * DT);

    // right/left parts at one x: R = e^{-i kc x}/loop, Lf = Re e^{-i kc (2L - x)}/loop
    function parts(xi, k) {
      const [r0, r1] = cexp(kcI[k] * xi, -kcR[k] * xi);
      const [rr, ri] = cdiv(r0, r1, lpR[k], lpI[k]);
      const [l0, l1] = cexp(kcI[k] * (2 * L - xi), -kcR[k] * (2 * L - xi));
      const [lr, li] = cdiv(Re * l0, Re * l1, lpR[k], lpI[k]);
      return [rr, ri, lr, li];
    }
    const nx = x.length, eta = new Float32Array(nt * nx), qx = new Float32Array(nt * nx);
    const Er = new Float64Array(m), Ei = new Float64Array(m), Qr = new Float64Array(m), Qi = new Float64Array(m);
    for (let jx = 0; jx < nx; jx++) {
      for (let k = 0; k < m; k++) {
        const [rr, ri, lr, li] = parts(x[jx], k);
        [Er[k], Ei[k]] = cmul(Sr[k], Si[k], rr + lr, ri + li);
        const [sc, si] = cmul(Sr[k], Si[k], cR[k], cI[k]);
        [Qr[k], Qi[k]] = cmul(sc, si, rr - lr, ri - li);
      }
      const e = irfft(Er, Ei, nfft, nt), q = irfft(Qr, Qi, nfft, nt);
      for (let j = 0; j < nt; j++) { eta[j * nx + jx] = e[j] * back[j]; qx[j * nx + jx] = q[j] * back[j]; }
    }
    const out = { eta, q: qx };
    if (z && xv) {
      const nz = z.length, nv = xv.length, u = new Float32Array(nt * nz * nv), w = new Float32Array(nt * nz * nv);
      const kmax = Math.floor(F_FLOW_MAX * nfft * DT);
      const Ur = new Float64Array(m), Ui = new Float64Array(m), Wr = new Float64Array(m), Wi = new Float64Array(m);
      for (let jx = 0; jx < nv; jx++) {
        const pr = [];
        for (let k = 0; k <= kmax && k < m; k++) pr.push(parts(xv[jx], k));
        for (let kz = 0; kz < nz; kz++) {
          const zj = z[kz];
          Ur.fill(0); Ui.fill(0); Wr.fill(0); Wi.fill(0);
          for (let k = 0; k <= kmax && k < m; k++) {
            const [rr, ri, lr, li] = pr[k];
            const [sc, si] = cmul(Sr[k], Si[k], cR[k], cI[k]);
            const [qr, qi] = cmul(sc, si, rr - lr, ri - li);
            const [er, ei] = cmul(Sr[k], Si[k], rr + lr, ri + li);
            let pur, pui, pwr, pwi;
            if (k === 0) { pur = 1 / depth; pui = 0; pwr = (zj + depth) / depth; pwi = 0; }
            else {
              const [ar, ai] = cexp(kR[k] * zj, kI[k] * zj);
              const [br, bi] = cexp(-kR[k] * (zj + 2 * depth), -kI[k] * (zj + 2 * depth));
              const [e2r, e2i] = cexp(-2 * kR[k] * depth, -2 * kI[k] * depth);
              const [hr, hi] = cdiv(ar + br, ai + bi, 1 - e2r, -e2i);
              [pur, pui] = cmul(kR[k], kI[k], hr, hi);
              [pwr, pwi] = cdiv(ar - br, ai - bi, 1 - e2r, -e2i);
            }
            [Ur[k], Ui[k]] = cmul(qr, qi, pur, pui);
            // d eta/dt = i W eta, W = w - i sigma
            const [dr, di] = cmul(0 + sigma, wv[k], er, ei);
            [Wr[k], Wi[k]] = cmul(dr, di, pwr, pwi);
          }
          const uu = irfft(Ur, Ui, nfft, nt), ww = irfft(Wr, Wi, nfft, nt);
          for (let j = 0; j < nt; j++) {
            u[(j * nz + kz) * nv + jx] = uu[j] * back[j];
            w[(j * nz + kz) * nv + jx] = ww[j] * back[j];
          }
        }
      }
      out.u = u; out.w = w;
    }
    return out;
  }

  function simulate(commands, depth, geo, cal, opts) {
    opts = opts || {};
    const duration = opts.duration || 60, dx = opts.dx || 0.1, seal = opts.seal || "tight";
    const nt = Math.round(duration / DT) + 1;
    const cmds = commands.map(c => Object.assign({ stroke: 250, speed: 400, accel: 10000, jerk: 2000,
      kind: "single", seconds: 10, start: 1 }, c, { decel: c.decel == null ? (c.accel || 10000) : c.decel }));
    const t = new Float64Array(nt);
    for (let i = 0; i < nt; i++) t[i] = i * DT;
    const { disc, inner, Qrows } = pistonFlux(cmds, depth, nt, geo, seal);
    const nx = Math.floor(geo.channel_length / dx + 0.5) + 1, x = new Float64Array(nx);
    for (let j = 0; j < nx; j++) x[j] = j * dx;
    let z = null, xv = null;
    if (opts.flow !== false) {
      const nz = opts.zLevels || 6, dxv = opts.dxFlow || 0.25;
      z = new Float64Array(nz);
      for (let k = 0; k < nz; k++) z[k] = -depth * (k + 0.5) / nz;
      const nv = Math.floor(geo.channel_length / dxv + 0.5) + 1;
      xv = new Float64Array(nv);
      for (let j = 0; j < nv; j++) xv[j] = j * dxv;
    }
    const ch = channel(Qrows, depth, x, cal, geo.channel_width, z, xv);
    const rows = [];
    for (let r = 0; r < geo.columns; r++) {
      const [gain, tau, fitted] = rowGainDelay(cal, r + 1);
      let v = 0, vmax = 0;
      for (let j = 0; j < nt; j++) { v += Qrows[r][j] * DT; vmax = Math.max(vmax, v); }
      rows.push({ row: r + 1, wet: geo.row_bottom[r] < depth, bottom_m: geo.row_bottom[r], gain, delay_s: tau,
                  fitted, volume_out_l: vmax * 1000 });
    }
    const sim = { t, x, nt, nx, z, xv, eta: ch.eta, q: ch.q, u: ch.u, w: ch.w, disc, inner, Qrows, rows, depth };
    sim.metrics = metrics(sim);
    return sim;
  }

  function metrics(sim) {
    const { t, x, eta, nt, nx, depth } = sim;
    let j = 0;
    for (let i = 0; i < nx; i++) if (Math.abs(x[i] - CAMERA_X) < Math.abs(x[j] - CAMERA_X)) j = i;
    let mx = -Infinity, mn = Infinity, imx = 0, peak = 0;
    for (let i = 0; i < nt; i++) {
      const v = eta[i * nx + j];
      if (v > mx) { mx = v; imx = i; }
      if (v < mn) mn = v;
    }
    for (let i = 0; i < eta.length; i++) peak = Math.max(peak, Math.abs(eta[i]));
    const thr = 0.1 * Math.max(Math.abs(mx), Math.abs(mn), 1e-12);
    let arrival = null;
    for (let i = 0; i < nt; i++) if (Math.abs(eta[i * nx + j]) > thr) { arrival = t[i]; break; }
    const out = { camera_x_m: x[j], camera_index: j, crest_at_camera_mm: mx * 1000, trough_at_camera_mm: mn * 1000,
                  crest_time_s: t[imx], arrival_at_camera_s: arrival, largest_anywhere_mm: peak * 1000,
                  a_over_h: peak / depth,
                  wet_rows: sim.rows.filter(r => r.wet).map(r => r.row),
                  moving_rows: sim.rows.filter(r => r.volume_out_l > 0.01).map(r => r.row) };
    const warn = [];
    if (out.a_over_h > 0.1) warn.push("waves over a tenth of the depth: linear theory underestimates crests and steepening");
    const unfit = sim.rows.filter(r => !r.fitted && r.volume_out_l > 0.01).map(r => r.row);
    if (unfit.length) warn.push("rows " + unfit.join(", ") + " have no camera fit yet; their gain is the fitted rows' mean");
    out.warnings = warn;
    return out;
  }

  const FLOW = { simulate, metrics, discMM, pipeFlux, wavenumberComplex, DT, CAMERA_X, BOTTOM_MM };
  if (typeof module !== "undefined" && module.exports) module.exports = FLOW;
  else root.FLOW = FLOW;
})(typeof window !== "undefined" ? window : this);
