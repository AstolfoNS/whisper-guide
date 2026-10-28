/** Backgrounding never starts or resumes audio; returning requires a user gesture. */
export function installPageLifecycle(suspend: () => void): () => void {
  const visibility = () => { if (document.visibilityState === 'hidden') suspend() }
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('pagehide', suspend)
  return () => {
    document.removeEventListener('visibilitychange', visibility)
    window.removeEventListener('pagehide', suspend)
  }
}
