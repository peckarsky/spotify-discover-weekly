const TITLE = 'Discover Weekly archiving needs attention';
const MARKER = '<!-- discover-weekly:failure-alert -->';

module.exports = async function notify({ github, context, core, result, dryRun }) {
  if (result !== 'failure' && (result !== 'success' || dryRun)) return;

  const issues = await github.paginate(github.rest.issues.listForRepo, {
    ...context.repo,
    creator: 'github-actions[bot]',
    state: 'all',
    per_page: 100,
  });
  const alert = issues.find(issue => !issue.pull_request && issue.body?.includes(MARKER));

  if (result === 'success') {
    if (alert?.state === 'open') {
      await github.rest.issues.update({
        ...context.repo, issue_number: alert.number, state: 'closed', state_reason: 'completed',
      });
      core.info('Closed the failure alert after a successful archive run.');
    }
    return;
  }

  if (alert?.state === 'open') {
    core.info('An open failure alert already exists; no duplicate notification created.');
    return;
  }

  const repoUrl = `https://github.com/${context.repo.owner}/${context.repo.repo}`;
  const body = `${MARKER}
@${context.repo.owner}, the scheduled Spotify archive needs attention.

[Failed run](${repoUrl}/actions/runs/${context.runId}) · [Latest archive runs](${repoUrl}/actions/workflows/archive.yml)

Open the failed step to see the error. If Spotify authentication expired:

- For web-player session or browser authentication errors, replace the repository secret \`SPOTIFY_SP_DC\` with the current cookie value from your signed-in Spotify web player.
- For OAuth expiration, invalid-grant, or refresh-token errors, run \`python3 spotify_oauth.py\` locally to authorize again, then replace \`SPOTIFY_REFRESH_TOKEN\` with the new value in \`.spotify-oauth.json\`.

[Update repository secrets](${repoUrl}/settings/secrets/actions), then manually run **Archive Discover Weekly** with **dry_run unchecked** to retry the archive. Other failures may require a code or dependency fix; do not rotate credentials unless the error indicates authentication.

Never paste credentials into this public issue. The workflow reuses this issue during repeated failures and closes it after a successful archive run. A read-only dry run does not close it.
`;

  if (alert) {
    await github.rest.issues.update({
      ...context.repo, issue_number: alert.number, state: 'open', title: TITLE, body,
      assignees: [context.repo.owner],
    });
    core.info('Reopened the failure alert for a new failure.');
  } else {
    await github.rest.issues.create({
      ...context.repo, title: TITLE, body, assignees: [context.repo.owner],
    });
    core.info('Created a failure alert assigned to the repository owner.');
  }
};
