import {spawnSync} from 'node:child_process'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = path.join(root, '.private/app')
const command = spawnSync(process.execPath, ['--import', 'tsx', '--test', '--test-reporter=spec', 'tests/*.test.ts'], {cwd: source, stdio: 'inherit'})
if (command.error) console.error(command.error.message)
process.exitCode = command.status ?? 1
