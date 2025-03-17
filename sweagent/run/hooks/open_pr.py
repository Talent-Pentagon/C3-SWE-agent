import os
import random
import shlex
from typing import Literal
from urllib.parse import urlparse

from ghapi.all import GhApi
from pydantic import BaseModel

from sweagent.environment.swe_env import SWEEnv
from sweagent.run.hooks.abstract import RunHook
from sweagent.types import AgentRunResult
from sweagent.utils.github import (
    InvalidGithubURL,
    _get_gh_issue_data,
    _is_github_issue_url,
    _parse_gh_issue_url,
)
from sweagent.utils.github import (
    _get_associated_commit_urls as _get_gh_associated_commit_urls,
)
from sweagent.utils.gitlab import (
    InvalidGitlabURL,
    _get_gitlab_issue_data,
    _is_gitlab_issue_url,
    _parse_gitlab_issue_url,
    create_merge_request,
)
from sweagent.utils.gitlab import (
    _get_associated_commit_urls as _get_gitlab_associated_commit_urls,
)
from sweagent.utils.log import get_logger

# NOTE
# THE IMPLEMENTATION DETAILS HERE WILL CHANGE SOON!


def _determine_repo_type(issue_url: str) -> Literal["github", "gitlab"]:
    """Determine if the issue URL is for GitHub or GitLab"""
    if _is_github_issue_url(issue_url):
        return "github"
    elif _is_gitlab_issue_url(issue_url):
        return "gitlab"
    else:
        # Default to GitHub for backward compatibility
        return "github"


# fixme: Bring back the ability to open the PR to a fork
def open_pr(*, logger, token, env: SWEEnv, issue_url, trajectory, _dry_run: bool = False) -> None:
    """Create PR or MR to repository based on the issue URL type

    Args:
        trajectory: Trajectory of actions taken by the agent
        _dry_run: Whether to actually push anything or just simulate it
    """
    repo_type = _determine_repo_type(issue_url)

    if repo_type == "github":
        open_github_pr(
            logger=logger, token=token, env=env, github_url=issue_url, trajectory=trajectory, _dry_run=_dry_run
        )
    elif repo_type == "gitlab":
        # Get GitLab token and token type from environment variables
        gitlab_token = os.getenv("GITLAB_TOKEN", "")
        gitlab_token_type = os.getenv("GITLAB_TOKEN_TYPE", "oauth").lower()
        open_gitlab_mr(
            logger=logger,
            token=gitlab_token,
            token_type=gitlab_token_type,
            env=env,
            gitlab_url=issue_url,
            trajectory=trajectory,
            _dry_run=_dry_run,
        )
    else:
        raise ValueError(f"Unsupported repository type: {repo_type}")


# fixme: Bring back the ability to open the PR to a fork
def open_github_pr(*, logger, token, env: SWEEnv, github_url, trajectory, _dry_run: bool = False) -> None:
    """Create PR to GitHub repository

    Args:
        trajectory: Trajectory of actions taken by the agent
        _dry_run: Whether to actually push anything or just simulate it
    """

    issue_url = github_url
    logger.info("Opening GitHub PR")
    try:
        issue = _get_gh_issue_data(issue_url, token=token)
    except InvalidGithubURL as e:
        msg = "Data path must be a github issue URL if open_pr is set to True."
        raise ValueError(msg) from e
    branch_name = f"swe-agent-fix-#{issue.number}-" + str(random.random())[2:10]
    env.communicate(
        input="git config user.email 'noemail@swe-agent.com' && git config user.name 'SWE-agent'",
        error_msg="Failed to set git user",
        timeout=10,
        check="raise",
    )
    env.communicate(input="rm -f model.patch", error_msg="Failed to remove model patch", timeout=10, check="raise")
    env.communicate(
        input=f"git checkout -b {branch_name}", error_msg="Failed to switch to new branch", timeout=10, check="raise"
    )
    env.communicate(input="git add .", error_msg="Failed to add commits", timeout=10, check="raise")
    dry_run_flag = "--allow-empty" if _dry_run else ""
    commit_msg = [
        shlex.quote("Fix: {issue.title}"),
        shlex.quote("Closes #{issue.number}"),
    ]
    out = env.communicate(
        input=f"git commit -m {commit_msg[0]} -m  {commit_msg[1]} {dry_run_flag}",
        error_msg="Failed to commit changes",
        timeout=10,
        check="raise",
    )
    logger.debug(f"Committed changes: {out}")

    owner, repo, _ = _parse_gh_issue_url(issue_url)
    # fixme: bring this back
    # If `--repo_path` was specified with a different github URL, then the record will contain
    # the forking user
    forker = owner
    head = branch_name
    remote = "origin"
    if forker != owner:
        head = f"{forker}:{branch_name}"
        token_prefix = ""
        if token:
            token_prefix = f"{token}@"
        fork_url = f"https://{token_prefix}github.com/{forker}/{repo}.git"
        logger.debug(f"Using fork: {fork_url}")
        env.communicate(
            input=f"git remote add fork {fork_url}",
            error_msg="Failed to create new git remote",
            timeout=10,
        )
        remote = "fork"
    dry_run_prefix = "echo " if _dry_run else ""
    out = env.communicate(
        input=f"{dry_run_prefix} git push {remote} {branch_name}",
        error_msg=(
            "Failed to push branch to remote. Please check your token and permissions. "
            "You might want to push to a fork with the push_gh_repo_url option."
        ),
        timeout=10,
    )
    logger.debug(f"Pushed commit to {remote=} {branch_name=}: {out}")
    body = (
        f"This is a PR opened by AI tool [SWE Agent](https://github.com/SWE-agent/SWE-agent/) "
        f"to close [#{issue.number}]({issue_url}) ({issue.title}).\n\nCloses #{issue.number}."
    )
    body += "\n\n" + format_trajectory_markdown(trajectory)
    api = GhApi(token=token)
    if not _dry_run:
        args = dict(
            owner=owner,
            repo=repo,
            title=f"SWE-agent[bot] PR to fix: {issue.title}",
            head=head,
            base="main",
            body=body,
            draft=True,
        )
        logger.debug(f"Creating PR with args: {args}")
        pr_info = api.pulls.create(**args)  # type: ignore
        logger.info(
            f"🎉 PR created as a draft at {pr_info.html_url}. Please review it carefully, push "
            "any required changes onto the branch and then click "
            "'Ready for Review' to bring it to the attention of the maintainers.",
        )


# fixme: Bring back the ability to open the MR to a fork
def open_gitlab_mr(
    *, logger, token, token_type: str = "project", env: SWEEnv, gitlab_url, trajectory, _dry_run: bool = False
) -> None:
    """Create Merge Request to GitLab repository

    Args:
        trajectory: Trajectory of actions taken by the agent
        token: GitLab API token
        token_type: Type of token ('oauth', 'private', or 'personal')
        _dry_run: Whether to actually push anything or just simulate it
    """
    issue_url = gitlab_url
    logger.info(f"Opening GitLab MR using {token_type} token type")
    try:
        issue = _get_gitlab_issue_data(issue_url, token=token, token_type=token_type)
    except InvalidGitlabURL as e:
        msg = "Data path must be a GitLab issue URL if open_pr is set to True."
        raise ValueError(msg) from e

    issue_number = issue.get("iid", "")
    issue_title = issue.get("title", "")

    branch_name = f"swe-agent-fix-#{issue_number}-" + str(random.random())[2:10]
    env.communicate(
        input="git config user.email 'noemail@swe-agent.com' && git config user.name 'SWE-agent'",
        error_msg="Failed to set git user",
        timeout=10,
        check="raise",
    )
    env.communicate(input="rm -f model.patch", error_msg="Failed to remove model patch", timeout=10, check="raise")
    env.communicate(
        input=f"git checkout -b {branch_name}", error_msg="Failed to switch to new branch", timeout=10, check="raise"
    )
    env.communicate(input="git add .", error_msg="Failed to add commits", timeout=10, check="raise")
    dry_run_flag = "--allow-empty" if _dry_run else ""
    commit_msg = [
        shlex.quote(f"Fix: {issue_title}"),
        shlex.quote(f"Closes #{issue_number}"),
    ]
    out = env.communicate(
        input=f"git commit -m {commit_msg[0]} -m {commit_msg[1]} {dry_run_flag}",
        error_msg="Failed to commit changes",
        timeout=10,
        check="raise",
    )
    logger.debug(f"Committed changes: {out}")

    gitlab_instance, owner, repo, _ = _parse_gitlab_issue_url(issue_url)
    # Default to origin remote
    remote = "origin"

    # Handle token for push URL if needed
    if token:
        # Ensure we have a proper URL for the GitLab instance
        if not gitlab_instance.startswith("http"):
            gitlab_instance = f"https://{gitlab_instance}"

        # Remove trailing slash if present
        gitlab_instance = gitlab_instance.rstrip("/")

        # Parse the URL to get the hostname
        parsed_url = urlparse(gitlab_instance)
        hostname = parsed_url.netloc

        # Set up git configuration based on token type
        if token_type.lower() in ["private", "personal"]:
            # For private/personal tokens, set the token as username with x-oauth-basic as password
            fork_url = f"https://{token}:x-oauth-basic@{hostname}/{owner}/{repo}.git"
            # Also configure git to use the PRIVATE-TOKEN header
            env.communicate(
                input=f"git config --global http.extraHeader 'PRIVATE-TOKEN: {token}'",
                error_msg="Failed to set git config for private token",
                timeout=10,
            )
        else:  # Default to OAuth2
            fork_url = f"https://oauth2:{token}@{hostname}/{owner}/{repo}.git"

        logger.debug(f"Using GitLab URL with token: {fork_url.replace(token, '***')}")
        env.communicate(
            input=f"git remote set-url origin {fork_url}",
            error_msg="Failed to update git remote",
            timeout=10,
        )

    # Push the branch
    dry_run_prefix = "echo " if _dry_run else ""
    out = env.communicate(
        input=f"{dry_run_prefix} git push {remote} {branch_name}",
        error_msg=("Failed to push branch to remote. Please check your token and permissions."),
        timeout=10,
    )

    # Clean up any git config we set for private tokens
    if token and token_type.lower() in ["private", "personal"]:
        env.communicate(
            input="git config --global --unset http.extraHeader || true",
            error_msg="Failed to clean up git config",
            timeout=10,
        )
    logger.debug(f"Pushed commit to {remote=} {branch_name=}: {out}")

    # Create the merge request description
    body = (
        f"This is a Merge Request opened by AI tool [SWE Agent](https://github.com/SWE-agent/SWE-agent/) "
        f"to close [#{issue_number}]({issue_url}) ({issue_title}).\n\nCloses #{issue_number}."
    )
    body += "\n\n" + format_trajectory_markdown(trajectory)

    # Create the merge request via the GitLab API
    if not _dry_run:
        try:
            mr_info = create_merge_request(
                gitlab_instance=gitlab_instance,
                owner=owner,
                repo=repo,
                source_branch=branch_name,
                target_branch="main",  # Default to main, could be configurable in the future
                title=f"SWE-agent[bot] MR to fix: {issue_title}",
                description=body,
                token=token,
                token_type=token_type,
                draft=True,
            )
            logger.info(
                f"🎉 Merge Request created as a draft at {mr_info.get('web_url')}. Please review it carefully, push "
                "any required changes onto the branch and then click "
                "'Mark as ready' to bring it to the attention of the maintainers.",
            )
        except Exception as e:
            logger.error(f"Failed to create GitLab merge request: {e}")


class OpenPRConfig(BaseModel):
    # Option to be used with open_pr: Skip action if there are already commits claiming
    # to fix the issue. Please only set this to False if you are sure the commits are
    # not fixes or if this is your own repository!
    skip_if_commits_reference_issue: bool = True
    # Default target branch for PRs/MRs (if not specified, defaults to 'main')
    target_branch: str = "main"


class OpenPRHook(RunHook):
    """This hook opens a PR if the issue is solved and the user has enabled the option."""

    def __init__(self, config: OpenPRConfig):
        self.logger = get_logger("swea-open_pr", emoji="⚡️")
        self._config = config

    def on_init(self, *, run):
        self._env = run.env
        self._github_token: str = os.getenv("GITHUB_TOKEN", "")
        self._gitlab_token: str = os.getenv("GITLAB_TOKEN", "")
        self._gitlab_token_type: str = os.getenv("GITLAB_TOKEN_TYPE", "oauth").lower()
        self._problem_statement = run.problem_statement

    def on_instance_completed(self, result: AgentRunResult):
        if self.should_open_pr(result):
            # Get the issue URL based on the problem statement type
            if hasattr(self._problem_statement, "github_url"):
                issue_url = self._problem_statement.github_url
            elif hasattr(self._problem_statement, "gitlab_url"):
                issue_url = self._problem_statement.gitlab_url
            else:
                # No issue URL found, can't open PR
                self.logger.warning("No issue URL found in problem statement, can't open PR")
                return

            # Determine which token to use based on the issue URL
            if _is_github_issue_url(issue_url):
                token = self._github_token
                open_pr(
                    logger=self.logger,
                    token=token,
                    env=self._env,
                    issue_url=issue_url,
                    trajectory=result.trajectory,
                )
            elif _is_gitlab_issue_url(issue_url):
                open_pr(
                    logger=self.logger,
                    token=self._gitlab_token,  # Will be used to determine which token to use in open_gitlab_mr
                    env=self._env,
                    issue_url=issue_url,
                    trajectory=result.trajectory,
                )
            else:
                open_pr(
                    logger=self.logger,
                    token=self._github_token,  # Default to GitHub token
                    env=self._env,
                    issue_url=issue_url,
                    trajectory=result.trajectory,
                )

    def should_open_pr(self, result: AgentRunResult) -> bool:
        """Does opening a PR/MR make sense?"""
        if not result.info.get("submission"):
            self.logger.info("Not opening PR/MR because no submission was made.")
            return False
        if result.info.get("exit_status") != "submitted":
            self.logger.info(
                "Not opening PR/MR because exit status was %s and not submitted.", result.info.get("exit_status")
            )
            return False

        # Get the issue URL based on the problem statement type
        if hasattr(self._problem_statement, "github_url"):
            issue_url = self._problem_statement.github_url
        elif hasattr(self._problem_statement, "gitlab_url"):
            issue_url = self._problem_statement.gitlab_url
        else:
            # No issue URL found, can't open PR
            self.logger.warning("No issue URL found in problem statement, can't open PR")
            return False

        # Handle GitHub issues
        if _is_github_issue_url(issue_url):
            try:
                issue = _get_gh_issue_data(issue_url, token=self._github_token)
                if issue.state != "open":
                    self.logger.info(f"GitHub issue is not open (state={issue.state}. Skipping PR creation.")
                    return False
                if issue.assignee:
                    self.logger.info("GitHub issue is already assigned. Skipping PR creation. Be nice :)")
                    return False
                if issue.locked:
                    self.logger.info("GitHub issue is locked. Skipping PR creation.")
                    return False

                org, repo, issue_number = _parse_gh_issue_url(issue_url)
                associated_commits = _get_gh_associated_commit_urls(org, repo, issue_number, token=self._github_token)
                if associated_commits:
                    commit_url_strs = ", ".join(associated_commits)
                    if self._config.skip_if_commits_reference_issue:
                        self.logger.info(
                            f"GitHub issue already has associated commits (see {commit_url_strs}). Skipping PR creation."
                        )
                        return False
                    else:
                        self.logger.warning(
                            "Proceeding with PR creation even though there are already commits "
                            f"({commit_url_strs}) associated with the issue. Please only do this for your own repositories "
                            "or after verifying that the existing commits do not fix the issue.",
                        )
                return True
            except InvalidGithubURL:
                self.logger.info("Invalid GitHub URL. Checking if it's a GitLab URL.")

        # Handle GitLab issues
        if _is_gitlab_issue_url(issue_url):
            try:
                # Get the GitLab issue data
                issue = _get_gitlab_issue_data(issue_url, token=self._gitlab_token)
                # Debug log the issue data
                self.logger.debug(f"GitLab issue data: {issue}")

                # Check if the issue is open - GitLab uses 'opened' for open issues
                # Make sure to use the exact string comparison
                issue_state = issue.get("state", "")
                if issue_state != "opened":
                    self.logger.info(f"GitLab issue is not open (state='{issue_state}'). Skipping MR creation.")
                    return False
                if issue.get("assignee"):
                    self.logger.info("GitLab issue is already assigned. Skipping MR creation. Be nice :)")
                    return False
                if issue.get("discussion_locked"):
                    self.logger.info("GitLab issue is locked. Skipping MR creation.")
                    return False

                gitlab_instance, owner, repo, issue_number = _parse_gitlab_issue_url(issue_url)
                associated_commits = _get_gitlab_associated_commit_urls(
                    gitlab_instance,
                    owner,
                    repo,
                    issue_number,
                    token=self._gitlab_token,
                    token_type=self._gitlab_token_type,
                )
                if associated_commits:
                    commit_url_strs = ", ".join(associated_commits)
                    if self._config.skip_if_commits_reference_issue:
                        self.logger.info(
                            f"GitLab issue already has associated commits (see {commit_url_strs}). Skipping MR creation."
                        )
                        return False
                    else:
                        self.logger.warning(
                            "Proceeding with MR creation even though there are already commits "
                            f"({commit_url_strs}) associated with the issue. Please only do this for your own repositories "
                            "or after verifying that the existing commits do not fix the issue.",
                        )
                return True
            except InvalidGitlabURL:
                self.logger.info("Invalid GitLab URL.")

        self.logger.info("URL is neither a valid GitHub nor GitLab issue URL. Skipping PR/MR creation.")
        return False


def _remove_triple_backticks(text: str) -> str:
    return "\n".join(line.removeprefix("```") for line in text.splitlines())


def format_trajectory_markdown(trajectory: list[dict[str, str]]):
    """Format a trajectory as a markdown string for use in gh PR description."""
    prefix = [
        "<details>",
        "<summary>Thought process ('trajectory') of SWE-agent (click to expand)</summary>",
        "",
        "",
    ]
    steps = []
    for i, step in enumerate(trajectory):
        step_strs = [
            f"**🧑‍🚒 Response ({i})**: ",
            f"{step['response'].strip()}",
            f"**👀‍ Observation ({i})**:",
            "```",
            f"{_remove_triple_backticks(step['observation']).strip()}",
            "```",
        ]
        steps.append("\n".join(step_strs))
    suffix = [
        "",
        "</details>",
    ]
    return "\n".join(prefix) + "\n\n---\n\n".join(steps) + "\n".join(suffix)
