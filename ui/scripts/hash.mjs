// One fingerprint of everything the build is made from. The self-test (stockterminal/selftest.py)
// computes the same value in Python, so it can tell when the built files are older than the source.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

export const INPUTS = ['src', 'index.html', 'package.json', 'vite.config.ts']

function walk(p, out) {
  if (statSync(p).isDirectory()) for (const n of readdirSync(p).sort()) walk(join(p, n), out)
  else out.push(p)
}

export function sourceHash(root) {
  const files = []
  for (const i of INPUTS) walk(join(root, i), files)
  const h = createHash('sha256')
  for (const f of files.map((f) => relative(root, f).split(sep).join('/')).sort()) {
    h.update(f + '\n')
    // line endings differ between Windows and Mac checkouts: hash the text without \r
    h.update(readFileSync(join(root, f), 'utf8').replace(/\r/g, '') + '\n')
  }
  return h.digest('hex')
}
