/**
 * Starts fraud_api with the repo-root .venv Python when present (Windows + Unix),
 * so `npm run dev:stack` uses the same interpreter as an activated venv.
 *
 * Expects: <repo>/dashboard/dev-api.mjs with fraud_api/, fraudshield_core/ under <repo>/.
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.join(__dirname, '..')
const win = process.platform === 'win32'
const venvPython = path.join(
  projectRoot,
  '.venv',
  win ? 'Scripts/python.exe' : 'bin/python'
)
const python = existsSync(venvPython) ? venvPython : 'python'
const mainPy = path.join(projectRoot, 'fraud_api', 'main.py')

const prevPath = process.env.PYTHONPATH || ''
const pythonpath = prevPath
  ? `${projectRoot}${path.delimiter}${prevPath}`
  : projectRoot

const child = spawn(python, [mainPy], {
  cwd: projectRoot,
  stdio: 'inherit',
  shell: false,
  env: {
    ...process.env,
    PYTHONPATH: pythonpath,
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
  },
})
child.on('exit', (code) => process.exit(code ?? 0))
