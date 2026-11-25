// Load a setting saved by an earlier installation on this origin.
function readWeftGitSetting(storage, key) {
  const current = storage.getItem(key);
  if (current !== null) return current;
  const previous = storage.getItem(key.replace(/^weftgit/, "freethegit"));
  if (previous !== null) {
    try { storage.setItem(key, previous); }
    catch { /* A full storage area can still return the saved value. */ }
  }
  return previous;
}
