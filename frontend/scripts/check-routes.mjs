#!/usr/bin/env node
/* global process */

import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(frontendRoot, '..')
const routeSource = readFileSync(path.join(frontendRoot, 'src/router/routes.ts'), 'utf8')

const expectedRoutes = [
  ["valuation", "valuation"],
  ["holdings", "holdings"],
  ["import", "import"],
]

const errors = []
const actualRoutes = [...routeSource.matchAll(/\{\s*path: '([^']+)',\s*name: '([^']+)'/g)]
  .map((match) => [match[2], match[1]])
if (JSON.stringify(actualRoutes) !== JSON.stringify(expectedRoutes)) {
  errors.push(`canonical routes differ: expected ${JSON.stringify(expectedRoutes)}, got ${JSON.stringify(actualRoutes)}`)
}
const actualLabels = [...routeSource.matchAll(/label: '([^']+)'/g)].map((match) => match[1])
if (JSON.stringify(actualLabels) !== JSON.stringify(['估值', '持仓', '录入'])) {
  errors.push(`primary navigation labels differ: got ${JSON.stringify(actualLabels)}`)
}
if ((routeSource.match(/navLevel: 'primary'/g) ?? []).length !== 3) {
  errors.push('primary navigation must contain exactly three routes')
}
for (const removed of ['today-decisions', 'assets-research', 'trans-records', 'review-reports']) {
  if (routeSource.includes(`name: '${removed}'`)) {
    errors.push(`removed route still registered: ${removed}`)
  }
}

const venvPython = path.join(repoRoot, '.venv/bin/python')
const pythonBin = existsSync(venvPython) ? venvPython : 'python3'

const python = spawnSync(
  pythonBin,
  ['-c', [
    'import json',
    'from fund_analysis.web.legacy_redirects import LEGACY_REDIRECTS',
    'print(json.dumps(LEGACY_REDIRECTS, ensure_ascii=False))',
  ].join('; ')],
  {
    cwd: repoRoot,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONPATH: path.join(repoRoot, 'src') },
    encoding: 'utf8',
  },
)
if (python.status !== 0) {
  errors.push(`backend redirect import failed: ${python.stderr.trim()}`)
} else {
  const redirects = JSON.parse(python.stdout)
  const expectedRedirects = {
    '/': '/valuation',
    '/funds': '/valuation',
    '/assets/funds': '/valuation',
    '/assets/holdings': '/holdings',
    '/trans/import': '/import',
  }
  for (const [source, target] of Object.entries(expectedRedirects)) {
    if (redirects[source] !== target) {
      errors.push(`backend redirect ${source}: expected ${target}, got ${redirects[source]}`)
    }
  }
}

if (errors.length) {
  console.error('Route smoke check failed:')
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log('Route smoke check passed: 3 canonical routes and valuation MVP redirects')
