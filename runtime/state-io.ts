import { randomUUID } from 'crypto';
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs';
import { basename, dirname, join } from 'path';

const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_POLL_MS = 10;
const DEFAULT_STALE_MS = 300_000;

type LockOwner = { pid: number; token: string };
type LockOptions = { timeoutMs?: number; pollMs?: number; staleMs?: number };

export function lockPathFor(path: string): string {
  return join(dirname(path), `.${basename(path)}.lock.d`);
}

function readOwner(lockPath: string): LockOwner | null {
  try {
    const value: unknown = JSON.parse(readFileSync(join(lockPath, 'owner.json'), 'utf-8'));
    if (!value || typeof value !== 'object') return null;
    const owner = value as Record<string, unknown>;
    if (!Number.isInteger(owner.pid) || typeof owner.token !== 'string' || !owner.token) {
      return null;
    }
    return { pid: owner.pid as number, token: owner.token };
  } catch {
    return null;
  }
}

function pidAlive(pid: number): boolean {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error: any) {
    return error?.code === 'EPERM';
  }
}

function isStale(lockPath: string, staleMs: number): boolean {
  const owner = readOwner(lockPath);
  if (owner) return !pidAlive(owner.pid);
  try {
    return Date.now() - statSync(lockPath).mtimeMs > staleMs;
  } catch {
    return true;
  }
}

function removeLock(lockPath: string): void {
  rmSync(lockPath, { recursive: true, force: true });
}

function sleep(milliseconds: number): void {
  if (milliseconds <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

export function withStateLock<T>(path: string, operation: () => T, options: LockOptions = {}): T {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const pollMs = options.pollMs ?? DEFAULT_POLL_MS;
  const staleMs = options.staleMs ?? DEFAULT_STALE_MS;
  if (timeoutMs < 0 || pollMs < 0 || staleMs < 0) {
    throw new Error('lock timing values must be non-negative');
  }
  const lockPath = lockPathFor(path);
  mkdirSync(dirname(lockPath), { recursive: true });
  const token = `${process.pid}-${randomUUID().replaceAll('-', '')}`;
  const deadline = Date.now() + timeoutMs;
  while (true) {
    try {
      mkdirSync(lockPath);
    } catch (error: any) {
      if (error?.code !== 'EEXIST') throw error;
      if (isStale(lockPath, staleMs)) {
        removeLock(lockPath);
        continue;
      }
      if (Date.now() >= deadline) throw new Error(`timed out waiting for state lock: ${path}`);
      sleep(pollMs);
      continue;
    }
    try {
      writeFileSync(
        join(lockPath, 'owner.json'),
        JSON.stringify({ pid: process.pid, token, created_at: Date.now() / 1000 }) + '\n',
        'utf-8',
      );
    } catch (error) {
      removeLock(lockPath);
      throw error;
    }
    break;
  }
  try {
    return operation();
  } finally {
    const owner = readOwner(lockPath);
    if (owner?.pid === process.pid && owner.token === token) removeLock(lockPath);
  }
}

export function appendJsonl(path: string, value: Record<string, unknown>): void {
  withStateLock(path, () => appendFileSync(path, JSON.stringify(value) + '\n', 'utf-8'));
}

export function ensureStateParent(path: string): void {
  if (!existsSync(dirname(path))) mkdirSync(dirname(path), { recursive: true });
}
