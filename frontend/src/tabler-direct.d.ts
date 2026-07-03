/**
 * Ambient type declaration for direct per-icon .mjs imports.
 *
 * @tabler/icons-react v3 ships individual icon files under
 * dist/esm/icons/<IconName>.mjs but has no per-file .d.ts.
 * This wildcard declaration tells TypeScript that any such import
 * returns the same `Icon` type that the barrel's .d.ts uses,
 * so we get full type safety without going through the 5 000-symbol barrel.
 */
declare module '@tabler/icons-react/dist/esm/icons/*.mjs' {
  import type { Icon } from '@tabler/icons-react';
  const icon: Icon;
  export default icon;
}
