import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const app = path.join(root, 'app')
const required = ['server/index.mjs', 'server/admin-cli.mjs', 'server/agy-cli.mjs', 'client/index.html', 'prompts/dm.md', 'data/dnd-ru/items.json', 'package.json', 'package-lock.json', 'release.json', 'LICENSE']
for (const file of required) if (!fs.existsSync(path.join(app, file))) throw new Error('Missing release file: ' + file)
const packageData = JSON.parse(fs.readFileSync(path.join(app, 'package.json'), 'utf8'))
if (packageData.scripts || packageData.devDependencies) throw new Error('Runtime package must contain no build scripts or development dependencies')
const forbiddenNames = /(?:^|\/)(?:\.env(?:\.|$)|AGENTS\.md|CLAUDE\.md|node_modules|uploads|\.git)(?:\/|$)|\.(?:tsx?|map|pyc)$/i
const forbiddenValues = [/https?:\/\/[^\s\/"']+:[^\s\/"']+@/i, /MASTER_PASSWORD/, /DND_USE_PTY/, /claude-opus-/, /claude-sonnet-/, /SILERO_TTS_URL/, /node-pty/]
const denylist = path.join(root, '.private/release-denylist.json')
const privateValues = fs.existsSync(denylist) ? JSON.parse(fs.readFileSync(denylist, 'utf8')) : []
let count = 0
function scan(folder) {
  for (const entry of fs.readdirSync(folder, {withFileTypes: true})) {
    const file = path.join(folder, entry.name)
    const relative = path.relative(app, file).replaceAll(path.sep, '/')
    if (forbiddenNames.test(relative)) throw new Error('Private/development file in release: ' + relative)
    if (entry.isDirectory()) scan(file)
    else {
      count++
      if (/\.(?:mjs|js|json|css|html|md|txt)$/.test(file) || entry.name === 'LICENSE') {
        const text = fs.readFileSync(file, 'utf8')
        for (const pattern of forbiddenValues) if (pattern.test(text)) throw new Error('Private/legacy value in release: ' + relative + ' (' + pattern.source + ')')
        for (const value of privateValues) if (value && text.toLowerCase().includes(value.toLowerCase())) throw new Error('Private installation value in release: ' + relative)
      }
    }
  }
}
scan(app)
console.log('Release verified: ' + count + ' files; no app sources, maps, old credentials or legacy providers')
