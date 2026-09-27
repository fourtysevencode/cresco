export function eligibleForPullRequest(finding) {
  return finding?.severity === 'critical' || finding?.severity === 'high' || finding?.severity === 'medium' ||
    (finding?.category === 'logic' && finding?.blocksProject === true);
}
