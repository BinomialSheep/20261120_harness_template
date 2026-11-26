// この問題（Painting the Floor）専用の描画。問題ごとに 1 回だけ書く。
// レポート本体とは caseView.load / caseView.draw の 2 つでつながる。無い問題では、公式ビジュアライザの HTML へのリンクになる。
window.caseView = (() => {
  const DIR = { U: [-1, 0], D: [1, 0], L: [0, -1], R: [0, 1] };

  function load(input, output) {
    const a = input.trim().split(/\s+/), N = +a[0], S = a.slice(5, 5 + N);
    let i = +a[3], j = +a[4], p = 0, sinceShot = 0;
    const inside = (x, y) => x >= 0 && x < N && y >= 0 && y < N && S[x][y] !== '#';
    const painted = Array.from({ length: N }, () => Array(N).fill(false));
    const steps = [], shots = [], count = { move: 0, extra: 0, pass: 0, weak: 0 };
    for (const line of output.trim().split('\n').slice(1)) {
      const [op, d] = line.trim().split(/\s+/), from = [i, j];
      if (op === '1') {
        const [di, dj] = DIR[d];
        if (inside(i + di, j + dj)) { i += di; j += dj; }
        sinceShot++; count.move++;
        if (sinceShot > 1) count.extra++;
        steps.push({ kind: 'move', from, to: [i, j], extra: sinceShot > 1 });
      } else if (op === '2') {
        const [di, dj] = DIR[d], fresh = [], cells = [];
        for (let k = 0, x = i, y = j; k <= p && inside(x, y); k++, x += di, y += dj) {
          cells.push([x, y]);
          if (!painted[x][y]) { painted[x][y] = true; fresh.push([x, y]); }
        }
        if (fresh.length <= 2) count.weak++;
        steps.push({ kind: 'shot', from, to: [i, j], shot: shots.length });
        shots.push({ step: steps.length - 1, fresh, cells, vertical: di !== 0 });
        p = Math.floor(p / 2); sinceShot = 0;
      } else {
        count.pass++; sinceShot = Math.max(sinceShot, 1);
        steps.push({ kind: 'pass', from, to: [i, j] });
      }
      p++;
    }
    return { N, S, steps, shots, start: [+a[3], +a[4]],
      stats: [['行動', steps.length], ['発射', shots.length], ['余分な移動', count.extra], ['待ち', count.pass], ['2マス以下の発射', count.weak]] };
  }

  function draw(canvas, c, t) {
    const css = getComputedStyle(canvas), v = n => css.getPropertyValue(n).trim();
    const size = canvas.clientWidth, dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.height = size * dpr;
    const g = canvas.getContext('2d'), u = size / c.N;
    g.scale(dpr, dpr);
    g.fillStyle = v('--surface'); g.fillRect(0, 0, size, size);
    g.strokeStyle = v('--grid'); g.lineWidth = 1;
    for (let k = 0; k <= c.N; k++) { g.beginPath(); g.moveTo(k * u, 0); g.lineTo(k * u, size); g.moveTo(0, k * u); g.lineTo(size, k * u); g.stroke(); }
    for (let x = 0; x < c.N; x++) for (let y = 0; y < c.N; y++) if (c.S[x][y] === '#') { g.fillStyle = v('--wall'); g.fillRect(y * u, x * u, u, u); }
    // 発射 1 回が新しく塗ったマスを 1 本の帯で描く。短い帯（2 マス以下）ほど損をしている
    for (const s of c.shots) {
      if (s.step >= t) break;
      g.fillStyle = v(s.fresh.length <= 2 ? '--s2' : s.fresh.length <= 4 ? '--b2' : '--b1');
      if (s.step === t - 1) g.fillStyle = v('--s1');
      let run = [];
      const flush = () => {
        if (!run.length) return;
        const [x0, y0] = run.reduce((m, q) => [Math.min(m[0], q[0]), Math.min(m[1], q[1])]);
        const [x1, y1] = run.reduce((m, q) => [Math.max(m[0], q[0]), Math.max(m[1], q[1])]);
        g.beginPath(); g.roundRect(y0 * u + 1.5, x0 * u + 1.5, (y1 - y0 + 1) * u - 3, (x1 - x0 + 1) * u - 3, 3); g.fill();
        run = [];
      };
      for (const q of s.fresh) {
        const last = run[run.length - 1];
        if (last && Math.abs(last[0] - q[0]) + Math.abs(last[1] - q[1]) !== 1) flush();
        run.push(q);
      }
      flush();
    }
    const at = q => [(q[1] + 0.5) * u, (q[0] + 0.5) * u];
    for (const s of c.steps.slice(0, t)) {
      if (s.kind === 'move') {
        g.strokeStyle = v(s.extra ? '--path2' : '--path'); g.lineWidth = s.extra ? 2.5 : 1.25; g.lineCap = 'round';
        g.beginPath(); g.moveTo(...at(s.from)); g.lineTo(...at(s.to)); g.stroke();
      } else if (s.kind === 'pass') {
        g.strokeStyle = v('--path2'); g.lineWidth = 2; g.beginPath(); g.arc(...at(s.to), u * 0.22, 0, 7); g.stroke();
      }
    }
    const here = t > 0 ? c.steps[Math.min(t, c.steps.length) - 1].to : c.start;
    g.fillStyle = v('--ink'); g.strokeStyle = v('--surface'); g.lineWidth = 2;
    g.beginPath(); g.arc(...at(here), u * 0.24, 0, 7); g.fill(); g.stroke();
  }
  return { load, draw };
})();
