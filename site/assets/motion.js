(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = window.matchMedia('(pointer: fine)').matches;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  document.documentElement.classList.add('motion-ready');

  const progress = document.createElement('div');
  progress.className = 'scroll-progress';
  progress.setAttribute('aria-hidden', 'true');
  progress.innerHTML = '<span></span>';
  document.body.prepend(progress);

  let scrollProgress = 0;
  const updateProgress = () => {
    const available = document.documentElement.scrollHeight - window.innerHeight;
    scrollProgress = available > 0 ? window.scrollY / available : 0;
    progress.style.setProperty('--scroll-progress', String(scrollProgress));
    document.documentElement.dataset.scroll = window.scrollY < 8 ? 'top' : 'moved';
  };
  window.addEventListener('scroll', updateProgress, { passive: true });
  window.addEventListener('resize', updateProgress, { passive: true });
  updateProgress();

  const splitHeading = (heading) => {
    const label = heading.textContent.trim();
    heading.setAttribute('aria-label', label);
    heading.textContent = '';
    label.split(/\s+/).forEach((word, index, words) => {
      const wrapper = document.createElement('span');
      wrapper.className = 'word-reveal';
      wrapper.setAttribute('aria-hidden', 'true');
      wrapper.style.setProperty('--word-index', String(index));
      wrapper.textContent = word;
      heading.append(wrapper);
      if (index < words.length - 1) heading.append(' ');
    });
  };
  document.querySelectorAll('.hero-text h1, .page-head h1').forEach(splitHeading);

  const revealTargets = [
    ...document.querySelectorAll(
      '.section-head > *, .grid:not(.hero) > .cell, .steps > *, .ref-table tr, .two-col > *, .term-list > *, .footer .wrap > *'
    ),
  ];
  revealTargets.forEach((element, index) => {
    element.classList.add('motion-reveal');
    element.style.setProperty('--reveal-order', String(index % 6));
  });

  if (reduceMotion || !('IntersectionObserver' in window)) {
    revealTargets.forEach((element) => element.classList.add('is-visible'));
  } else {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: '0px 0px -10% 0px', threshold: 0.08 }
    );
    revealTargets.forEach((element) => observer.observe(element));
  }

  class MomentumScroll {
    constructor() {
      this.current = window.scrollY;
      this.target = this.current;
      this.running = false;
      this.tick = this.tick.bind(this);
      window.addEventListener('scroll', () => {
        if (!this.running) this.current = this.target = window.scrollY;
      }, { passive: true });
      window.addEventListener('wheel', (event) => this.onWheel(event), { passive: false });
    }

    onWheel(event) {
      if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      const nested = event.target.closest('[data-native-scroll], textarea, select');
      if (nested) return;
      event.preventDefault();
      const maximum = document.documentElement.scrollHeight - window.innerHeight;
      this.target = clamp(this.target + event.deltaY, 0, maximum);
      if (!this.running) {
        this.running = true;
        requestAnimationFrame(this.tick);
      }
    }

    tick() {
      this.current += (this.target - this.current) * 0.115;
      if (Math.abs(this.target - this.current) < 0.35) {
        this.current = this.target;
        this.running = false;
      }
      window.scrollTo(0, this.current);
      if (this.running) requestAnimationFrame(this.tick);
    }

    to(value) {
      this.target = clamp(value, 0, document.documentElement.scrollHeight - window.innerHeight);
      if (!this.running) {
        this.running = true;
        requestAnimationFrame(this.tick);
      }
    }
  }

  const scroller = !reduceMotion && finePointer ? new MomentumScroll() : null;

  document.addEventListener('click', (event) => {
    const anchor = event.target.closest('a[href]');
    if (!anchor || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const url = new URL(anchor.href, window.location.href);

    if (url.origin === window.location.origin && url.pathname === window.location.pathname && url.hash) {
      const target = document.querySelector(url.hash);
      if (!target) return;
      event.preventDefault();
      const y = target.getBoundingClientRect().top + window.scrollY - 88;
      scroller ? scroller.to(y) : window.scrollTo({ top: y, behavior: reduceMotion ? 'auto' : 'smooth' });
      history.pushState(null, '', url.hash);
      return;
    }

    if (
      url.origin === window.location.origin &&
      !url.hash &&
      anchor.target !== '_blank' &&
      !anchor.hasAttribute('download')
    ) {
      event.preventDefault();
      document.documentElement.classList.add('is-leaving');
      window.setTimeout(() => window.location.assign(url.href), reduceMotion ? 0 : 320);
    }
  });

  if (finePointer && !reduceMotion) {
    document.querySelectorAll('.btn').forEach((button) => {
      button.classList.add('motion-magnetic');
      button.addEventListener('pointermove', (event) => {
        const rect = button.getBoundingClientRect();
        button.style.setProperty('--magnetic-x', `${(event.clientX - rect.left - rect.width / 2) * 0.12}px`);
        button.style.setProperty('--magnetic-y', `${(event.clientY - rect.top - rect.height / 2) * 0.18}px`);
      });
      button.addEventListener('pointerleave', () => {
        button.style.setProperty('--magnetic-x', '0px');
        button.style.setProperty('--magnetic-y', '0px');
      });
    });

    document.querySelectorAll('.tool, .stage, .doc-group').forEach((card) => {
      card.classList.add('motion-tilt');
      card.addEventListener('pointermove', (event) => {
        const rect = card.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width - 0.5;
        const y = (event.clientY - rect.top) / rect.height - 0.5;
        card.style.setProperty('--tilt-x', `${(-y * 2.4).toFixed(2)}deg`);
        card.style.setProperty('--tilt-y', `${(x * 2.4).toFixed(2)}deg`);
        card.style.setProperty('--spot-x', `${((x + 0.5) * 100).toFixed(1)}%`);
        card.style.setProperty('--spot-y', `${((y + 0.5) * 100).toFixed(1)}%`);
      });
      card.addEventListener('pointerleave', () => {
        card.style.setProperty('--tilt-x', '0deg');
        card.style.setProperty('--tilt-y', '0deg');
      });
    });
  }

  class TraceField {
    constructor(canvas) {
      this.canvas = canvas;
      this.gl = canvas.getContext('webgl', { alpha: true, antialias: true });
      if (!this.gl) {
        canvas.hidden = true;
        return;
      }
      this.pointer = { x: 0, y: 0, tx: 0, ty: 0 };
      this.started = performance.now();
      this.build();
      this.resize();
      this.frame = this.frame.bind(this);
      window.addEventListener('resize', () => this.resize(), { passive: true });
      this.figure = canvas.closest('.hero-figure');
      this.figure?.addEventListener('pointermove', (event) => {
        const rect = canvas.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width;
        const y = (event.clientY - rect.top) / rect.height;
        this.pointer.tx = x * 2 - 1;
        this.pointer.ty = -(y * 2 - 1);
        this.figure.style.setProperty('--hero-x', `${(x * 100).toFixed(1)}%`);
        this.figure.style.setProperty('--hero-y', `${(y * 100).toFixed(1)}%`);
        this.figure.style.setProperty('--hero-rx', `${((0.5 - y) * 2.2).toFixed(2)}deg`);
        this.figure.style.setProperty('--hero-ry', `${((x - 0.5) * 2.2).toFixed(2)}deg`);
      });
      this.figure?.addEventListener('pointerleave', () => {
        this.pointer.tx = this.pointer.ty = 0;
        this.figure.style.setProperty('--hero-rx', '0deg');
        this.figure.style.setProperty('--hero-ry', '0deg');
      });
      requestAnimationFrame(this.frame);
    }

    shader(type, source) {
      const shader = this.gl.createShader(type);
      this.gl.shaderSource(shader, source);
      this.gl.compileShader(shader);
      return shader;
    }

    build() {
      const vertex = `
        attribute vec3 aPosition;
        uniform float uTime;
        uniform float uScroll;
        uniform vec2 uPointer;
        uniform float uAspect;
        varying float vDepth;
        void main() {
          float depth = fract(aPosition.z + uTime * 0.028 + uScroll * 0.18);
          float scale = mix(0.18, 1.34, depth);
          vec2 position = aPosition.xy * scale;
          position += uPointer * depth * 0.11;
          position.x /= uAspect;
          gl_Position = vec4(position, 0.0, 1.0);
          gl_PointSize = mix(1.2, 4.2, depth);
          vDepth = depth;
        }
      `;
      const fragment = `
        precision mediump float;
        varying float vDepth;
        void main() {
          vec2 point = gl_PointCoord - 0.5;
          float alpha = (1.0 - smoothstep(0.08, 0.5, length(point))) * mix(0.08, 0.48, vDepth);
          gl_FragColor = vec4(0.043, 0.369, 0.541, alpha);
        }
      `;
      const program = this.gl.createProgram();
      this.gl.attachShader(program, this.shader(this.gl.VERTEX_SHADER, vertex));
      this.gl.attachShader(program, this.shader(this.gl.FRAGMENT_SHADER, fragment));
      this.gl.linkProgram(program);
      this.gl.useProgram(program);
      this.program = program;

      const points = [];
      let seed = 71;
      const random = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };
      for (let index = 0; index < 240; index += 1) {
        points.push((random() * 2 - 1) * 1.35, (random() * 2 - 1) * 1.35, random());
      }
      const buffer = this.gl.createBuffer();
      this.gl.bindBuffer(this.gl.ARRAY_BUFFER, buffer);
      this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(points), this.gl.STATIC_DRAW);
      const position = this.gl.getAttribLocation(program, 'aPosition');
      this.gl.enableVertexAttribArray(position);
      this.gl.vertexAttribPointer(position, 3, this.gl.FLOAT, false, 0, 0);
      this.count = points.length / 3;
      this.uniforms = {
        time: this.gl.getUniformLocation(program, 'uTime'),
        scroll: this.gl.getUniformLocation(program, 'uScroll'),
        pointer: this.gl.getUniformLocation(program, 'uPointer'),
        aspect: this.gl.getUniformLocation(program, 'uAspect'),
      };
      this.gl.enable(this.gl.BLEND);
      this.gl.blendFunc(this.gl.SRC_ALPHA, this.gl.ONE_MINUS_SRC_ALPHA);
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      this.canvas.width = Math.max(1, Math.round(rect.width * dpr));
      this.canvas.height = Math.max(1, Math.round(rect.height * dpr));
      this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
      this.aspect = rect.width / Math.max(rect.height, 1);
    }

    frame(now) {
      this.pointer.x += (this.pointer.tx - this.pointer.x) * 0.055;
      this.pointer.y += (this.pointer.ty - this.pointer.y) * 0.055;
      this.gl.clearColor(0, 0, 0, 0);
      this.gl.clear(this.gl.COLOR_BUFFER_BIT);
      this.gl.uniform1f(this.uniforms.time, reduceMotion ? 0 : (now - this.started) / 1000);
      this.gl.uniform1f(this.uniforms.scroll, scrollProgress);
      this.gl.uniform2f(this.uniforms.pointer, this.pointer.x, this.pointer.y);
      this.gl.uniform1f(this.uniforms.aspect, this.aspect);
      this.gl.drawArrays(this.gl.POINTS, 0, this.count);
      if (!reduceMotion) requestAnimationFrame(this.frame);
    }
  }

  document.querySelectorAll('.hero-webgl').forEach((canvas) => new TraceField(canvas));
})();
