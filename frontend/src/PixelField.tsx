import { useEffect, useRef } from 'react'

const SPEED = 1.2
const INTENSITY = 1.4
const MOTION_RATE = 4
const STATIC_TIME = 12
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5]

function clamp(value: number) { return Math.max(0, Math.min(1, value)) }
function smooth(value: number) { const n = clamp(value); return n * n * (3 - 2 * n) }
function hash(x: number, y: number) {
  let n = (Math.imul(x, 374761393) + Math.imul(y, 668265263)) | 0
  n = Math.imul(n ^ (n >>> 13), 1274126177)
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296
}
function noise(x: number, y: number) {
  const ix = Math.floor(x), iy = Math.floor(y)
  const fx = smooth(x - ix), fy = smooth(y - iy)
  const a = hash(ix, iy), b = hash(ix + 1, iy), c = hash(ix, iy + 1), d = hash(ix + 1, iy + 1)
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy
}
function fractal(x: number, y: number) {
  return noise(x, y) * .57 + noise(x * 2.03 + 7, y * 2.03 - 3) * .29
    + noise(x * 4.07 - 5, y * 4.07 + 11) * .14
}

/** Veiled horizon: full-width pixel mist with a deterministic static frame. */
export function PixelField({ reduced, dark, compact = false }: {
  reduced: boolean; dark: boolean; compact?: boolean
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let width = 1, height = 1, frame = 0, last = 0
    const started = performance.now()
    const timeNow = () => reduced ? STATIC_TIME : STATIC_TIME + (performance.now() - started) / 1000 * SPEED * MOTION_RATE
    function draw(time: number) {
      if (!ctx) return
      ctx.clearRect(0, 0, width, height)
      ctx.fillStyle = dark ? '#7c8d73' : '#5c6c50'
      for (let x = 0; x < width; x++) {
        const nx = x / width
        const horizon = .6 + .05 * Math.sin(nx * 7 + time * .09)
          + .025 * Math.sin(nx * 17 - time * .06)
        for (let y = Math.floor(height * .38); y < height; y++) {
          const ny = y / height
          const depth = smooth((ny - horizon + .12) * 2.65)
          const drift = fractal(nx * 5.2 - time * .055, ny * 3.7 + time * .025)
          const warp = fractal(nx * 6.5 + time * .035, ny * 6 - drift * .9)
          const ribbon = Math.exp(-Math.pow((ny - horizon - .13 - drift * .12) / .16, 2))
          const density = clamp(depth * (.14 + warp * .43) + ribbon * Math.max(0, drift - .34) * .54)
          const threshold = (BAYER[(y % 4) * 4 + x % 4] + .5) / 16
          if (density > threshold) {
            ctx.globalAlpha = (.12 + .42 * warp) * INTENSITY
            ctx.fillRect(x, y, 1, 1)
          }
        }
      }
      ctx.globalAlpha = 1
    }
    function resize() {
      if (!canvas) return
      width = Math.max(1, Math.round(canvas.clientWidth / 2))
      height = Math.max(1, Math.round(canvas.clientHeight / 2))
      canvas.width = width; canvas.height = height
      draw(timeNow())
    }
    function tick(now: number) {
      if (now - last > 50) { draw(timeNow()); last = now }
      frame = requestAnimationFrame(tick)
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    if (!reduced) frame = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(frame); observer.disconnect() }
  }, [reduced, dark, compact])
  return <canvas ref={canvasRef} className={`pixel-field ${compact ? 'compact' : ''}`} aria-hidden="true" data-effect="veiled-horizon" data-motion={reduced ? 'static' : 'animated'} />
}
