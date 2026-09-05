import { writeFileSync } from 'fs';
import { appendJsonl, withStateLock } from '../../runtime/state-io';

const [mode, target, argument] = process.argv.slice(2);
if (!mode || !target) throw new Error('usage: state_io_worker.ts append|hold TARGET [ARG]');

if (mode === 'append') {
  appendJsonl(target, { runtime: 'bun', value: argument || 'appended' });
} else if (mode === 'hold') {
  if (!argument) throw new Error('hold mode requires a marker path');
  withStateLock(target, () => {
    writeFileSync(argument, 'locked\n', 'utf-8');
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
  });
} else {
  throw new Error(`unknown mode: ${mode}`);
}
