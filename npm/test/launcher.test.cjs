'use strict';

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { constants, copyFileSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const test = require('node:test');
const { selectTarget } = require('../bin/brewdoc.cjs');

/** Install a test-local launcher beside the actual Node executable. */
function installation(t, nativeVersion = '0.3.1') {
  const directory = mkdtempSync(join(tmpdir(), 'brewdoc npm Unicode-é '));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const binary = join(directory, 'node_modules', 'native', 'binary');
  mkdirSync(binary, { recursive: true });
  mkdirSync(join(directory, 'bin'));
  copyFileSync(resolve(__dirname, '../bin/brewdoc.cjs'), join(directory, 'bin', 'brewdoc.cjs'));
  const binaryName = { win32: 'brewdoc.exe' }[process.platform] ?? 'brewdoc';
  copyFileSync(process.execPath, join(binary, binaryName), constants.COPYFILE_FICLONE);
  writeFileSync(join(directory, 'package.json'), JSON.stringify({
    version: '0.3.1', brewdocTargets: { host: {
      package: 'native', os: process.platform, cpu: process.arch, executable: `binary/${binaryName}`,
    } },
  }));
  writeFileSync(join(directory, 'node_modules', 'native', 'package.json'), JSON.stringify({ version: nativeVersion }));
  return { directory, launcher: join(directory, 'bin', 'brewdoc.cjs') };
}

/** Retain independent streams and the caller's final process status. */
function childResult(child) {
  const stdout = [], stderr = [];
  child.stdout.on('data', chunk => stdout.push(chunk));
  child.stderr.on('data', chunk => stderr.push(chunk));
  return once(child, 'close').then(([code, signal]) => ({
    code, signal, stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr),
  }));
}

test('forwards literal arguments, Unicode paths, cwd and environment', async t => {
  // GIVEN a native command installed under a space and Unicode path.
  const { directory, launcher } = installation(t);
  assert.equal(readFileSync(join(directory, 'package.json'), 'utf8').includes('0.3.1'), true, 'Version precondition changed');
  const script = 'process.stdout.write(JSON.stringify({argv:process.argv.slice(1),cwd:process.cwd(),value:process.env.BREWDOC_TEST_VALUE}))';
  const args = ['path with spaces/é.epub', 'literal;$(never)', '"quoted"', '--out', 'out é.md'];
  // WHEN the launcher receives literal arguments and an explicit cwd.
  const child = spawn(process.execPath, [launcher, '-e', script, '--', ...args], {
    cwd: directory, env: { ...process.env, BREWDOC_TEST_VALUE: 'forwarded' },
  });
  child.stdin.end();
  const result = await childResult(child);
  // THEN shell metacharacters and process context pass through unchanged.
  assert.deepEqual({ code: result.code, signal: result.signal, stdout: JSON.parse(result.stdout), stderr: result.stderr.toString() }, {
    code: 0, signal: null, stdout: { argv: args, cwd: realpathSync(directory), value: 'forwarded' }, stderr: '',
  }, 'Launcher changed native argv or context');
});

test('inherits concurrent large stdin, stdout and stderr without buffering limits', async t => {
  // GIVEN a native command that writes both streams before consuming stdin.
  const { launcher } = installation(t);
  const count = 2 * 1024 * 1024;
  assert.equal(Buffer.alloc(count).length, 2097152, 'Large-stream precondition changed');
  const script = `process.stdout.write(Buffer.alloc(${count},65));process.stderr.write(Buffer.alloc(${count},66));let size=0;process.stdin.on('data',b=>size+=b.length);process.stdin.on('end',()=>process.stdout.write(String(size)))`;
  // WHEN both output streams and stdin exceed the usual buffered exec limit.
  const child = spawn(process.execPath, [launcher, '-e', script]);
  const completed = childResult(child);
  child.stdin.end(Buffer.alloc(count, 67));
  const result = await completed;
  // THEN every byte reaches its original stream.
  assert.deepEqual(result, {
    code: 0, signal: null,
    stdout: Buffer.concat([Buffer.alloc(count, 65), Buffer.from(String(count))]),
    stderr: Buffer.alloc(count, 66),
  }, 'Inherited streams lost, mixed or truncated bytes');
});

for (const code of [0, 1, 2, 17]) {
  test(`preserves native exit code ${code}`, async t => {
    // GIVEN an installed command with a known exit status.
    const { launcher } = installation(t);
    assert.equal(Number.isInteger(code), true, 'Exit-status precondition changed');
    // WHEN the command exits through the launcher.
    const child = spawn(process.execPath, [launcher, '-e', `process.exit(${code})`]);
    child.stdin.end();
    const result = await childResult(child);
    // THEN the status and empty streams pass through exactly.
    assert.deepEqual(result, { code, signal: null, stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) }, 'Native exit status changed');
  });
}

test('missing optional package refuses with Python fallback', async t => {
  // GIVEN a valid launcher with its optional native package omitted.
  const { directory, launcher } = installation(t);
  rmSync(join(directory, 'node_modules'), { recursive: true });
  assert.equal(readFileSync(join(directory, 'package.json'), 'utf8').includes('native'), true, 'Package-selection precondition changed');
  // WHEN help is requested without a native dependency.
  const child = spawn(process.execPath, [launcher, '--help']);
  child.stdin.end();
  const result = await childResult(child);
  // THEN no installer is launched and the refusal names the fallback.
  assert.deepEqual({ code: result.code, signal: result.signal, stdout: result.stdout.toString(), stderr: result.stderr.toString() }, {
    code: 1, signal: null, stdout: '',
    stderr: 'brewdoc: native package native is unavailable; reinstall with optional dependencies. Use Python: python -m pip install brewdoc (or uv tool install brewdoc / pipx install brewdoc).\n',
  }, 'Missing dependency refusal changed');
});

test('wrong native package version refuses before running it', async t => {
  // GIVEN mismatched main and native package versions.
  const { launcher } = installation(t, '0.3.0');
  assert.equal(selectTarget({}, 'linux', 'x64', '2.39'), undefined, 'Empty-target precondition changed');
  // WHEN the wrapper would otherwise invoke a native command.
  const child = spawn(process.execPath, [launcher, '-e', 'process.stdout.write("should not run")']);
  child.stdin.end();
  const result = await childResult(child);
  // THEN the mismatch refuses without native stdout.
  assert.deepEqual({ code: result.code, stdout: result.stdout.toString(), stderr: result.stderr.toString() }, {
    code: 1, stdout: '',
    stderr: 'brewdoc: native package version 0.3.0 differs from 0.3.1. Use Python: python -m pip install brewdoc (or uv tool install brewdoc / pipx install brewdoc).\n',
  }, 'Version mismatch ran the native command');
});

test('missing executable refuses without invoking an installer', async t => {
  // GIVEN a native package whose contained executable was removed.
  const { directory, launcher } = installation(t);
  const executable = join(directory, 'node_modules', 'native', 'binary', { win32: 'brewdoc.exe' }[process.platform] ?? 'brewdoc');
  assert.equal(readFileSync(executable).subarray(0, 2).length, 2, 'Executable precondition changed');
  rmSync(executable);
  // WHEN the launcher receives a self-check request.
  const child = spawn(process.execPath, [launcher, '--self-check']);
  child.stdin.end();
  const result = await childResult(child);
  // THEN the missing executable gives one refusal and empty stdout.
  assert.deepEqual({ code: result.code, stdout: result.stdout.toString(), stderr: result.stderr.toString() }, {
    code: 1, stdout: '',
    stderr: 'brewdoc: native executable is missing from native. Use Python: python -m pip install brewdoc (or uv tool install brewdoc / pipx install brewdoc).\n',
  }, 'Missing executable refusal changed');
});

test('spawn error produces one diagnostic and status one', async t => {
  // GIVEN an existing directory at the selected executable path.
  const { directory, launcher } = installation(t);
  const executable = join(directory, 'node_modules', 'native', 'binary', { win32: 'brewdoc.exe' }[process.platform] ?? 'brewdoc');
  rmSync(executable);
  mkdirSync(executable);
  assert.deepEqual(selectTarget({}, process.platform, process.arch, null), undefined, 'Empty-target precondition changed');
  // WHEN the operating system refuses to spawn that path.
  const child = spawn(process.execPath, [launcher, '--self-check']);
  child.stdin.end();
  const result = await childResult(child);
  // THEN no stack trace, native output or installer replaces the spawn refusal.
  assert.deepEqual({ code: result.code, signal: result.signal, stdout: result.stdout.toString() }, {
    code: 1, signal: null, stdout: '',
  }, 'Spawn failure changed status or stdout');
  assert.match(result.stderr.toString(), /^brewdoc: cannot launch native executable: (EACCES|EPERM|UNKNOWN)\. Use Python: python -m pip install brewdoc \(or uv tool install brewdoc \/ pipx install brewdoc\)\.\n$/, 'Spawn failure emitted an unexpected diagnostic');
});

test('rejects old glibc, musl and unsupported architecture without selecting a binary', () => {
  // GIVEN the sole admitted Linux glibc x64 package.
  const targets = { linux: { os: 'linux', cpu: 'x64', libc: 'glibc', minimumLibc: '2.35' } };
  assert.deepEqual(selectTarget(targets, 'linux', 'x64', '2.39'), targets.linux, 'Admitted-target precondition changed');
  // WHEN libc or architecture is unsupported.
  const selected = [selectTarget(targets, 'linux', 'x64', null), selectTarget(targets, 'linux', 'arm64', '2.39'),
    selectTarget(targets, 'linux', 'x64', '2.28')];
  // THEN neither unsupported target reaches a native executable.
  assert.deepEqual(selected, [undefined, undefined, undefined], 'Unsupported platform selected a binary');
});

test('forwards SIGTERM and preserves signal termination', { skip: process.platform === 'win32', timeout: 5000 }, async t => {
  // GIVEN a running native child in the launcher's owned process group.
  const { launcher } = installation(t);
  const child = spawn(process.execPath, [launcher, '-e', 'process.stdout.write(String(process.pid));setInterval(()=>{},1000)']);
  const completed = childResult(child);
  const [ready] = await once(child.stdout, 'data');
  const nativePid = Number(ready.toString());
  assert.equal(Number.isInteger(nativePid), true, 'Native child readiness changed');
  // WHEN only the launcher receives a cancellation signal.
  child.kill('SIGTERM');
  const result = await completed;
  // THEN the native process is gone and the caller sees SIGTERM.
  assert.deepEqual({ code: result.code, signal: result.signal, stderr: result.stderr.toString() }, {
    code: null, signal: 'SIGTERM', stderr: '',
  }, 'Signal status or cleanup changed');
  assert.throws(() => process.kill(nativePid, 0), { code: 'ESRCH' }, 'Native child survived launcher cancellation');
});
