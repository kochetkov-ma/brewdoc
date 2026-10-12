'use strict';

const { createCipheriv, createHash, publicEncrypt, randomBytes, constants } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

/** Encrypt one bounded publication summary without producing plaintext output. */
function encrypt(payload) {
  if (payload.length > 8192) throw new Error('Invalid diagnostic');
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payload));
  const fields = ['schema', 'package', 'version', 'tarball_sha256', 'source_sha', 'controller_sha',
    'run_id', 'run_attempt', 'code', 'cause', 'summary'];
  if (!value || Array.isArray(value) || Object.keys(value).length !== fields.length
      || fields.some(field => !Object.hasOwn(value, field)) || value.schema !== 1
      || !['brewdoc-linux-x64-glibc', 'brewdoc-darwin-arm64', 'brewdoc'].includes(value.package)
      || typeof value.version !== 'string' || !/^\d+\.\d+\.\d+$/.test(value.version)
      || typeof value.tarball_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.tarball_sha256)
      || ['source_sha', 'controller_sha'].some(field => typeof value[field] !== 'string' || !/^[a-f0-9]{40}$/.test(value[field]))
      || ['run_id', 'run_attempt'].some(field => !Number.isSafeInteger(value[field]) || value[field] <= 0)
      || ['code', 'cause'].some(field => typeof value[field] !== 'string' || value[field].length > 64)
      || typeof value.summary !== 'string' || !value.summary || Buffer.byteLength(value.summary) > 4096) {
    throw new Error('Invalid diagnostic');
  }
  const publicKey = readFileSync(join(__dirname, '..', 'npm-diagnostic-public.pem'));
  const key = randomBytes(32);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv, { authTagLength: 16 });
  cipher.setAAD(Buffer.from('brewdoc-npm-diagnostic-v1'));
  const ciphertext = Buffer.concat([cipher.update(payload), cipher.final()]);
  return { schema: 1, algorithm: 'RSA-OAEP-SHA256+A256GCM',
    key_sha256: createHash('sha256').update(publicKey).digest('hex'),
    encrypted_key: publicEncrypt({ key: publicKey, padding: constants.RSA_PKCS1_OAEP_PADDING,
      oaepHash: 'sha256' }, key).toString('base64'),
    iv: iv.toString('base64'), tag: cipher.getAuthTag().toString('base64'), ciphertext: ciphertext.toString('base64') };
}

/** Bound stdin before encrypting with the fixed tracked public key. */
async function main() {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    if (bytes > 8192) throw new Error('Invalid diagnostic');
    chunks.push(chunk);
  }
  process.stdout.write(JSON.stringify(encrypt(Buffer.concat(chunks))) + '\n');
}

module.exports = { encrypt };
if (require.main === module) main().catch(() => { process.exitCode = 1; });
