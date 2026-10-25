import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {spawnSync} from 'node:child_process'
// Author: Arahort — arahort.pro, alex@arahort.pro. Run only in the disposable Linux image.
assert.equal(process.platform, 'linux')
assert.equal(process.getuid(), 1000)
assert.equal(process.env.HOME, '/home/dnd')
assert.equal(typeof process.execve, 'function')
const release = JSON.parse(fs.readFileSync('/app/release.json', 'utf8'))
assert.equal(release.version, process.env.EXPECTED_VERSION)
assert.equal(release.schemaVersion, 1)
const native = '/home/dnd/.local/bin/agy'
fs.accessSync(native, fs.constants.R_OK | fs.constants.W_OK | fs.constants.X_OK)
assert.equal(fs.lstatSync(native).uid, process.getuid())
assert.equal(fs.lstatSync(native).isSymbolicLink(), false)
assert.throws(() => fs.accessSync('/usr/local/lib/dnd-ai-arahort/agy', fs.constants.W_OK))
const versionCheck = spawnSync('/usr/local/bin/agy', ['--version'], {encoding: 'utf8', timeout: 5000})
assert.equal(versionCheck.status, 0, 'Native --version failed')
assert.match(versionCheck.stdout, /\d+\.\d+\.\d+/)
console.log('Native version utility verified:', versionCheck.stdout.trim())
const {bootstrapNativeAgy} = await import('/app/server/agy-cli.mjs')
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'dnd-runtime-smoke-'))
try {
  const home = path.join(temporary, 'home')
  const uploads = path.join(temporary, 'uploads')
  const runtime = path.join(temporary, 'runtime')
  fs.mkdirSync(home)
  fs.mkdirSync(uploads)
  fs.mkdirSync(runtime, {mode: 0o700})
  const verifier = path.join(temporary, 'verify.mjs')
  const seed = path.join(temporary, 'seed')
  fs.writeFileSync(seed, '#!/bin/sh\nexec /usr/local/bin/node ' + verifier + ' "$@"\n', {mode: 0o755})
  assert.equal(bootstrapNativeAgy({home, bootstrapPath: seed}), 'initialized')
  const fixtureNative = path.join(home, '.local/bin/agy')
  const upgraded = fs.readFileSync(fixtureNative, 'utf8') + '# simulated native update\n'
  fs.writeFileSync(fixtureNative, upgraded)
  assert.equal(bootstrapNativeAgy({home, bootstrapPath: seed}), 'preserved')
  assert.equal(fs.readFileSync(fixtureNative, 'utf8'), upgraded)
  function settings(enabled, effort = 'medium') {
    fs.writeFileSync(path.join(runtime, 'agy-settings.json'), JSON.stringify({schemaVersion: 1, effort, proxy: {enabled, address: 'http://proxy.invalid', port: 3128, login: 'fixture-user', password: 'fixture-password'}}), {mode: 0o600})
  }
  function check({enabled, snapshot = false}) {
    settings(enabled)
    const expectedProxy = snapshot ? 'http://snapshot.invalid:8080/' : enabled ? 'http://fixture-user:fixture-password@proxy.invalid:3128/' : undefined
    const args = snapshot ? ['--model', 'gemini-3.8-flash-high', '--effort', 'high', '-p='] : ['-p=']
    fs.writeFileSync(verifier, `import assert from 'node:assert/strict'
import fs from 'node:fs'
const args=process.argv.slice(2),expected=${JSON.stringify(expectedProxy)},snapshot=${snapshot}
for(const key of ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'])assert.equal(process.env[key],expected)
assert.equal(process.env.AGY_CLI_DISABLE_AUTO_UPDATE,undefined)
assert.equal(process.env.DND_AGY_RUNTIME_SNAPSHOT,undefined)
assert.equal(process.env.PG_PASSWORD,undefined)
assert.equal(args[args.indexOf('--model')+1],snapshot?'gemini-3.8-flash-high':'gemini-3.8-flash-medium')
assert.equal(args[args.indexOf('--effort')+1],snapshot?'high':'medium')
assert.ok(args.includes('--dangerously-skip-permissions'))
const stat=fs.readFileSync('/proc/self/stat','utf8').slice(fs.readFileSync('/proc/self/stat','utf8').lastIndexOf(')')+1).trim().split(/\\s+/)
console.log(JSON.stringify({ok:true,pid:process.pid,pgrp:Number(stat[2])}))
`)
    const env = {HOME: home, UPLOADS_DIR: uploads, PATH: process.env.PATH, DND_AGY_NATIVE_BIN: fixtureNative, AGY_CLI_DISABLE_AUTO_UPDATE: 'true'}
    if (snapshot) {
      env.DND_AGY_RUNTIME_SNAPSHOT = '1'
      for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) env[key] = expectedProxy
    } else {
      env.PG_PASSWORD = 'must-not-reach-native'
      env.HTTP_PROXY = env.HTTPS_PROXY = 'http://legacy.invalid:8080'
    }
    const result = spawnSync(process.execPath, ['/app/server/agy-cli.mjs', ...args], {env, detached: true, encoding: 'utf8', timeout: 10000})
    assert.equal(result.status, 0, 'Native exec fixture failed: ' + result.stderr)
    const report = JSON.parse(result.stdout)
    assert.equal(report.ok, true)
    assert.equal(report.pid, result.pid)
    assert.equal(report.pgrp, result.pid)
  }
  check({enabled: true})
  check({enabled: false})
  check({enabled: false, snapshot: true})
  console.log('Runtime verified: UID 1000, writable persistent native, preserved updates, Linux execve/PID/group, proxy ON/OFF and active snapshot; no AGY or network calls')
} finally {
  assert.equal(path.dirname(temporary), path.resolve(os.tmpdir()))
  assert.ok(path.basename(temporary).startsWith('dnd-runtime-smoke-'))
  fs.rmSync(temporary, {recursive: true, force: true})
}
