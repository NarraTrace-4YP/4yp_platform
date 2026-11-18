import React, { useEffect, useRef } from 'react'

const vertexSource = `
  attribute vec3 aPosition;
  uniform float uTime;
  uniform float uScroll;
  uniform vec2 uPointer;
  uniform float uAspect;
  varying float vDepth;

  void main() {
    float depth = fract(aPosition.z + uTime * 0.022 + uScroll * 0.12);
    float scale = mix(0.15, 1.28, depth);
    vec2 position = aPosition.xy * scale;
    position += uPointer * depth * 0.08;
    position.x /= uAspect;
    gl_Position = vec4(position, 0.0, 1.0);
    gl_PointSize = mix(1.0, 3.8, depth);
    vDepth = depth;
  }
`

const fragmentSource = `
  precision mediump float;
  varying float vDepth;

  void main() {
    float distanceFromCentre = length(gl_PointCoord - 0.5);
    float alpha = (1.0 - smoothstep(0.08, 0.5, distanceFromCentre)) * mix(0.06, 0.42, vDepth);
    gl_FragColor = vec4(0.043, 0.369, 0.541, alpha);
  }
`

function createShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  return shader
}

export default function MotionCanvas({ scroll = 0 }: { scroll?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const gl = canvas?.getContext('webgl', { alpha: true, antialias: true })
    if (!canvas || !gl || reduceMotion) return

    const vertex = createShader(gl, gl.VERTEX_SHADER, vertexSource)
    const fragment = createShader(gl, gl.FRAGMENT_SHADER, fragmentSource)
    const program = gl.createProgram()
    if (!vertex || !fragment || !program) return
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
    gl.useProgram(program)

    let seed = 41
    const random = () => {
      seed = (seed * 16807) % 2147483647
      return (seed - 1) / 2147483646
    }
    const points: number[] = []
    for (let index = 0; index < 190; index += 1) {
      points.push((random() * 2 - 1) * 1.4, (random() * 2 - 1) * 1.4, random())
    }

    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(points), gl.STATIC_DRAW)
    const position = gl.getAttribLocation(program, 'aPosition')
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 3, gl.FLOAT, false, 0, 0)

    const uniforms = {
      time: gl.getUniformLocation(program, 'uTime'),
      scroll: gl.getUniformLocation(program, 'uScroll'),
      pointer: gl.getUniformLocation(program, 'uPointer'),
      aspect: gl.getUniformLocation(program, 'uAspect'),
    }
    const pointer = { x: 0, y: 0, targetX: 0, targetY: 0 }
    let aspect = 1
    let animation = 0
    const started = performance.now()

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      aspect = rect.width / Math.max(rect.height, 1)
      gl.viewport(0, 0, canvas.width, canvas.height)
    }
    const move = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      pointer.targetX = (event.clientX - rect.left) / rect.width * 2 - 1
      pointer.targetY = -((event.clientY - rect.top) / rect.height * 2 - 1)
    }
    const leave = () => {
      pointer.targetX = 0
      pointer.targetY = 0
    }
    const draw = (now: number) => {
      pointer.x += (pointer.targetX - pointer.x) * 0.055
      pointer.y += (pointer.targetY - pointer.y) * 0.055
      gl.clearColor(0, 0, 0, 0)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.uniform1f(uniforms.time, (now - started) / 1000)
      gl.uniform1f(uniforms.scroll, scroll)
      gl.uniform2f(uniforms.pointer, pointer.x, pointer.y)
      gl.uniform1f(uniforms.aspect, aspect)
      gl.drawArrays(gl.POINTS, 0, points.length / 3)
      animation = requestAnimationFrame(draw)
    }

    resize()
    window.addEventListener('resize', resize, { passive: true })
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerleave', leave)
    animation = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(animation)
      window.removeEventListener('resize', resize)
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerleave', leave)
      gl.deleteBuffer(buffer)
      gl.deleteProgram(program)
      gl.deleteShader(vertex)
      gl.deleteShader(fragment)
    }
  }, [scroll])

  return <canvas ref={canvasRef} className="app-webgl" aria-hidden="true" />
}
