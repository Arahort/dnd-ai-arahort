import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createRequire} from 'node:module'
import {spawnSync} from 'node:child_process'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = path.join(root, '.private/app')
const output = path.join(root, 'app')
function inside(base, target) {
  const relative = path.relative(base, target)
  return relative && !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative)
}
function run(args, cwd = source) {
  const result = spawnSync(process.execPath, args, {cwd, stdio: 'inherit'})
  if (result.error || result.status !== 0) throw new Error(result.error?.message || 'Build command failed')
}
if (!fs.existsSync(path.join(source, 'src/App.tsx'))) throw new Error('Закрытые исходники отсутствуют. Для установки используй опубликованный образ или архив релиза.')
if (!inside(root, output) || path.basename(output) !== 'app') throw new Error('Invalid build output path')
const require = createRequire(path.join(source, 'package.json'))
const {build} = require('esbuild')
const original = JSON.parse(fs.readFileSync(path.join(source, 'package.json'), 'utf8'))
run([require.resolve('typescript/bin/tsc'), '--noEmit', '--incremental', 'false'])
fs.rmSync(output, {recursive: true, force: true})
fs.mkdirSync(path.join(output, 'server'), {recursive: true})
run([path.join(source, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', path.join(output, 'client'), '--emptyOutDir'])
const bundled = await build({
  entryPoints: {'index': path.join(source, 'server/index.ts'), 'admin-cli': path.join(source, 'server/admin-cli.ts'), 'agy-cli': path.join(source, 'server/agyCli.ts')},
  outdir: path.join(output, 'server'),
  outExtension: {'.js': '.mjs'},
  bundle: true,
  packages: 'external',
  platform: 'node',
  format: 'esm',
  target: 'node22',
  minify: true,
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'info',
  metafile: true,
})
fs.cpSync(path.join(source, 'prompts'), path.join(output, 'prompts'), {recursive: true})
const dataDir = path.join(output, 'data/dnd-ru')
fs.mkdirSync(dataDir, {recursive: true})
for (const name of ['spells', 'items', 'classes', 'species', 'backgrounds']) fs.copyFileSync(path.join(source, 'src/data/dnd-ru', name + '.json'), path.join(dataDir, name + '.json'))
const version = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).version
const runtime = {name: 'dnd-ai-arahort', version, private: true, type: 'module', author: 'Arahort <alex@arahort.pro> (https://arahort.pro)', license: 'SEE LICENSE IN LICENSE', engines: {node: '>=22'}, dependencies: {}}
const externalPackages = new Set()
for (const file of Object.values(bundled.metafile.outputs)) for (const item of file.imports) {
  if (!item.external || item.path.startsWith('node:') || !original.dependencies[item.path.split('/').slice(0, item.path.startsWith('@') ? 2 : 1).join('/')]) continue
  externalPackages.add(item.path.split('/').slice(0, item.path.startsWith('@') ? 2 : 1).join('/'))
}
for (const name of externalPackages) runtime.dependencies[name] = original.dependencies[name]
if (original.overrides?.qs) runtime.overrides = {qs: original.overrides.qs}
fs.writeFileSync(path.join(output, 'package.json'), JSON.stringify(runtime, null, 2) + '\n')
const privateLock = JSON.parse(fs.readFileSync(path.join(source, 'package-lock.json'), 'utf8'))
const lock = {name: runtime.name, version, lockfileVersion: 3, requires: true, packages: {'': {name: runtime.name, version, license: runtime.license, engines: runtime.engines, dependencies: runtime.dependencies}}}
for (const [key, value] of Object.entries(privateLock.packages || {})) if (key && !value.dev) lock.packages[key] = value
fs.writeFileSync(path.join(output, 'package-lock.json'), JSON.stringify(lock, null, 2) + '\n')
const npmCli = process.env.npm_execpath || path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js')
const npmResult = spawnSync(process.execPath, [npmCli, 'install', '--package-lock-only', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], {cwd: output, stdio: 'inherit'})
if (npmResult.status !== 0) throw new Error('Could not create the production dependency lockfile')
fs.writeFileSync(path.join(output, 'release.json'), JSON.stringify({format: 1, version, schemaVersion: 1, builtAt: new Date().toISOString(), model: 'gemini-3.8-flash-high', author: 'Arahort'}, null, 2) + '\n')
for (const file of ['LICENSE']) if (fs.existsSync(path.join(root, file))) fs.copyFileSync(path.join(root, file), path.join(output, file))
const notices = []
const productionLock = JSON.parse(fs.readFileSync(path.join(output, 'package-lock.json'), 'utf8'))
const licensedPackages = new Map(Object.entries(privateLock.packages).filter(([key, value]) => key && !value.dev))
for (const [key, value] of Object.entries(productionLock.packages)) if (key) licensedPackages.set(key, value)
for (const [key, value] of licensedPackages) {
  if (!key) continue
  const packageDir = path.join(source, key)
  const normalized = key.replace(/^node_modules\//, '').replace(/[^a-zA-Z0-9._-]/g, '_')
  const files = fs.existsSync(packageDir) ? fs.readdirSync(packageDir).filter(file => /^(license|licence|copying|notice)(\.|$)/i.test(file)) : []
  for (const file of files) {
    if (!fs.statSync(path.join(packageDir, file)).isFile()) continue
    const target = path.join(output, 'licenses/npm', normalized)
    fs.mkdirSync(target, {recursive: true})
    fs.copyFileSync(path.join(packageDir, file), path.join(target, file))
  }
  notices.push({package: key.replace(/^node_modules\//, ''), version: value.version, license: value.license || 'See upstream package notices'})
}
fs.mkdirSync(path.join(output, 'licenses'), {recursive: true})
fs.writeFileSync(path.join(output, 'licenses/npm.json'), JSON.stringify(notices, null, 2) + '\n')
if (fs.existsSync(path.join(root, 'licenses'))) fs.cpSync(path.join(root, 'licenses'), path.join(output, 'licenses/content'), {recursive: true})
run([path.join(root, 'tools/verify-release.mjs')], root)
if (process.argv.includes('--archive')) {
  const archiver = require('archiver')
  const artifacts = path.join(root, 'artifacts')
  fs.mkdirSync(artifacts, {recursive: true})
  const file = path.join(artifacts, 'app.tar.gz')
  const target = fs.createWriteStream(file)
  const archive = archiver('tar', {gzip: true, gzipOptions: {level: 9}})
  const done = new Promise((resolve, reject) => { target.on('close', resolve); target.on('error', reject); archive.on('error', reject) })
  archive.pipe(target)
  archive.directory(output, 'app')
  await archive.finalize()
  await done
  console.log('Release archive: ' + file)
}
console.log('Built DND AI Arahort ' + version)
if (process.argv.includes('--local')) await import('./package-local.mjs')
