#!/usr/bin/env node
'use strict';

const { spawn } = require('node:child_process');
const { existsSync } = require('node:fs');
const { dirname, join } = require('node:path');

/** Select only admitted targets and supported libc versions. */
function selectTarget(targets, platform, arch, glibc) {
  return Object.values(targets).find(target => {
    if (target.os !== platform || target.cpu !== arch) return false;
    if (target.libc !== 'glibc') return true;
    const observed = typeof glibc === 'string' && glibc.match(/^([0-9]+)\.([0-9]+)(?:\.[0-9]+)*$/);
    const minimum = target.minimumLibc.split('.').map(Number);
    return Boolean(observed) && (Number(observed[1]) > minimum[0]
      || (Number(observed[1]) === minimum[0] && Number(observed[2]) >= minimum[1]));
  });
}

/** Run the installed native CLI without changing its streams. */
function launch() {
  const metadata = require('../package.json');
  const glibc = process.platform === 'linux' ? process.report.getReport().header.glibcVersionRuntime : null;
  const target = selectTarget(metadata.brewdocTargets, process.platform, process.arch, glibc);
  const fallback = 'Use Python: python -m pip install brewdoc (or uv tool install brewdoc / pipx install brewdoc).';
  function refuse(reason) {
    process.stderr.write(`brewdoc: ${reason}. ${fallback}\n`);
    process.exitCode = 1;
  }
  if (!target) {
    refuse(`unsupported native target ${process.platform}/${process.arch} or operating-system/libc version`);
    return;
  }
  let executable;
  try {
    const packagePath = require.resolve(`${target.package}/package.json`);
    const installed = require(packagePath);
    if (installed.version !== metadata.version) {
      refuse(`native package version ${installed.version} differs from ${metadata.version}`);
      return;
    }
    executable = join(dirname(packagePath), target.executable);
    if (!existsSync(executable)) {
      refuse(`native executable is missing from ${target.package}`);
      return;
    }
  } catch (error) {
    refuse(`native package ${target.package} is unavailable; reinstall with optional dependencies`);
    return;
  }
  const signals = process.platform === 'win32' ? ['SIGINT', 'SIGTERM', 'SIGBREAK'] : ['SIGINT', 'SIGTERM', 'SIGHUP'];
  const child = spawn(executable, process.argv.slice(2), {
    stdio: 'inherit', shell: false, detached: process.platform !== 'win32',
  });
  const handlers = new Map();
  for (const signal of signals) {
    const handler = () => {
      try {
        if (process.platform === 'win32') child.kill(signal === 'SIGBREAK' ? 'SIGTERM' : signal);
        else process.kill(-child.pid, signal);
      } catch (error) {
        if (error.code !== 'ESRCH') refuse(`cannot forward ${signal}: ${error.code}`);
      }
    };
    handlers.set(signal, handler);
    process.on(signal, handler);
  }
  let failed = false;
  child.once('error', error => {
    failed = true;
    refuse(`cannot launch native executable: ${error.code}`);
  });
  child.once('close', (code, signal) => {
    for (const [name, handler] of handlers) process.removeListener(name, handler);
    if (failed) return;
    if (signal && process.platform !== 'win32') process.kill(process.pid, signal);
    else process.exitCode = code === null ? 1 : code;
  });
}

module.exports = { selectTarget };
if (require.main === module) launch();
