// Runs after `vite build`: writes build.json next to the built files with the fingerprint of the source.
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { sourceHash } from './hash.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, '..', 'stockterminal', 'app', 'static', 'new', 'build.json')
writeFileSync(out, JSON.stringify({ source: sourceHash(root) }) + '\n')
console.log('build.json written')
