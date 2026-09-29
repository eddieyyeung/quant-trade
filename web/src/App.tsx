import AppShell from './shell/AppShell';

/**
 * Platform root.
 *
 * All layout and routing lives in the shell; this module exists so the entry
 * point has a single component to mount.
 */
export default function App() {
  return <AppShell />;
}
