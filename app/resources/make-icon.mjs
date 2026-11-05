// Draws resources/icon.png (1024 x 1024): two interlocked rings in the agent colors on a
// graphite rounded square laid out on Apple's icon grid. electron-builder makes the
// .icns and the Linux sizes from it. Run: node resources/make-icon.mjs
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'

const SIZE = 1024
const SS = 4 // supersamples per axis
const BODY = { x: 100, y: 100, size: 824, radius: 185 } // macOS icon grid
const RING = { r: 205, width: 70 }
const ORANGE = [224, 130, 95]
const GREEN = [63, 191, 150]
const centers = { orange: [412, 512], green: [612, 512] }

// Rounded square with superellipse corners, close to Apple's continuous curve.
function inBody(x, y) {
  const { x: bx, y: by, size, radius } = BODY
  const dx = Math.max(bx + radius - x, 0, x - (bx + size - radius))
  const dy = Math.max(by + radius - y, 0, y - (by + size - radius))
  return (dx / radius) ** 5 + (dy / radius) ** 5 <= 1
}

function inRing([cx, cy], x, y) {
  const d = Math.hypot(x - cx, y - cy)
  return Math.abs(d - RING.r) <= RING.width / 2
}

// Graphite gradient, lighter at the top.
function background(y) {
  const t = (y - BODY.y) / BODY.size
  return [0, 1, 2].map((i) => Math.round([52, 54, 61][i] * (1 - t) + [22, 23, 27][i] * t))
}

function sample(x, y) {
  if (!inBody(x, y)) return null
  const o = inRing(centers.orange, x, y)
  const g = inRing(centers.green, x, y)
  // Interlocked: orange crosses over green at the top, under it at the bottom.
  if (o && g) return y < 512 ? ORANGE : GREEN
  if (o) return ORANGE
  if (g) return GREEN
  return background(y)
}

const rows = []
for (let py = 0; py < SIZE; py++) {
  const row = Buffer.alloc(1 + SIZE * 4) // filter byte 0, then RGBA
  for (let px = 0; px < SIZE; px++) {
    let r = 0
    let g = 0
    let b = 0
    let hits = 0
    for (let sy = 0; sy < SS; sy++) {
      for (let sx = 0; sx < SS; sx++) {
        const c = sample(px + (sx + 0.5) / SS, py + (sy + 0.5) / SS)
        if (!c) continue
        r += c[0]
        g += c[1]
        b += c[2]
        hits++
      }
    }
    if (hits) {
      row.set([Math.round(r / hits), Math.round(g / hits), Math.round(b / hits), Math.round((255 * hits) / SS ** 2)], 1 + px * 4)
    }
  }
  rows.push(row)
}

const CRC = new Int32Array(256).map((_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c
})
function crc32(buf) {
  let c = -1
  for (const byte of buf) c = CRC[(c ^ byte) & 255] ^ (c >>> 8)
  return (c ^ -1) >>> 0
}
function chunk(type, data) {
  const head = Buffer.alloc(8)
  head.writeUInt32BE(data.length, 0)
  head.write(type, 4, 'ascii')
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0)
  return Buffer.concat([head, data, crc])
}

const ihdr = Buffer.alloc(13)
ihdr.writeUInt32BE(SIZE, 0)
ihdr.writeUInt32BE(SIZE, 4)
ihdr.set([8, 6, 0, 0, 0], 8) // 8-bit RGBA
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk('IHDR', ihdr),
  chunk('IDAT', deflateSync(Buffer.concat(rows), { level: 9 })),
  chunk('IEND', Buffer.alloc(0)),
])
const out = join(dirname(fileURLToPath(import.meta.url)), 'icon.png')
writeFileSync(out, png)
console.log(`wrote ${out}`)
