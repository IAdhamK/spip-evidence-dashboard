export function createLatestRequestGuard() {
  let revision = 0;

  return Object.freeze({
    begin() {
      revision += 1;
      return revision;
    },
    invalidate() {
      revision += 1;
    },
    isCurrent(requestRevision) {
      return requestRevision === revision;
    },
  });
}
