import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createRequire} from 'node:module'
import {createHash} from 'node:crypto'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(path.join(root, '.private/app/package.json'))
const archiver = require('archiver')
const version = JSON.parse(fs.readFileSync(path.join(root, 'app/release.json'), 'utf8')).version
if (!/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(version)) throw new Error('Invalid version')
const artifacts = path.join(root, 'artifacts')
fs.mkdirSync(artifacts, {recursive: true})
const file = path.join(artifacts, 'dnd-ai-arahort-' + version + '-local.tar.gz')
const target = fs.createWriteStream(file)
const archive = archiver('tar', {gzip: true, gzipOptions: {level: 9}})
const done = new Promise((resolve, reject) => { target.on('close', resolve); target.on('error', reject); archive.on('error', reject) })
archive.pipe(target)
const publicFiles = ['install.sh', 'Dockerfile', 'compose.yaml', 'compose.http.yaml', 'compose.https.yaml', 'compose.local.yaml', '.dockerignore', '.env.example', 'release-source.json', 'README.md', 'LICENSE']
for (const name of publicFiles) archive.file(path.join(root, name), {name: 'dnd-ai-arahort/' + name})
for (const name of ['app', 'ops', 'docs', 'licenses']) archive.directory(path.join(root, name), 'dnd-ai-arahort/' + name, entry => {
  if (entry.name.split('/').includes('__pycache__') || entry.name.endsWith('.pyc')) return false
  return entry
})
await archive.finalize()
await done
for (const name of ['app.tar.gz', path.basename(file)]) {
  const digest = createHash('sha256')
  for await (const chunk of fs.createReadStream(path.join(artifacts, name))) digest.update(chunk)
  fs.writeFileSync(path.join(artifacts, name + '.sha256'), digest.digest('hex') + '  ' + name + '\n')
}
console.log('Local installation archive: ' + file)
